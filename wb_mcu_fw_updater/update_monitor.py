#!/usr/bin/env python
# -*- coding: utf-8 -*-

import termios
import json
import sys
import yaml
import subprocess
import six
import semantic_version
from io import open
from collections import namedtuple, defaultdict
from . import fw_flasher, fw_downloader, user_log, jsondb, releases, CONFIG, logger

import wb_modbus  # Setting up module's params
wb_modbus.ALLOWED_UNSUCCESSFUL_TRIES = CONFIG['ALLOWED_UNSUCCESSFUL_MODBUS_TRIES']
wb_modbus.DEBUG = CONFIG['MODBUS_DEBUG']

from wb_modbus import minimalmodbus, bindings, parse_uart_settings_str

db = jsondb.JsonDB(CONFIG['DB_FILE_LOCATION'])


RELEASE_INFO = None


class UpdateDeviceError(Exception):
    pass

class NoReleasedFwError(UpdateDeviceError):
    pass

class ForeignDeviceError(UpdateDeviceError):
    pass


def ask_user(message):  # TODO: non-blocking with timer?
    """
    Asking user before potentionally dangerous action.

    :param message: will be printed to user
    :type message: str
    :return: is user sure or not
    :rtype: bool
    """
    message_str = '\n*** %s [Y/N] *** ' % (message)
    ret = six.moves.input(user_log.colorize(message_str, 'YELLOW'))
    return ret.upper().startswith('Y')


def fill_release_info():  # TODO: make a class, storing a release-info context
    """
    wb-mcu-fw-updater supposed to be launched only on devices, supporting wb-releases
    incorrect wb-releases file indicates strange erroneous behavior
    """
    global RELEASE_INFO
    releases_fname = CONFIG['RELEASES_FNAME']
    try:
        RELEASE_INFO = releases.parse_releases(releases_fname)
    except Exception as e:
        logger.error("Critical error in %s file! Contact the support!", releases_fname)
        six.reraise(*sys.exc_info())


def get_released_fw(fw_signature, release_info):
    """
    Looking for released-fw:
        version
        url on fw-releases
    By:
        fw_signature
        release suite
    """
    suite = release_info['SUITE']
    for url in releases.get_release_file_urls(release_info):  # repo-prefix is the first, if exists
        logger.debug("Looking to %s (suite: %s)", url, str(suite))
        try:
            contents = fw_downloader.get_remote_releases_info(url)
            fw_endpoint = yaml.safe_load(contents).get('releases', {}).get(fw_signature, {}).get(suite)
            if fw_endpoint:
                fw_version = releases.parse_fw_version(fw_endpoint)  # TODO: raise error
                logger.debug("FW version for %s on release %s: %s (endpoint: %s)", fw_signature, suite, fw_version, fw_endpoint)
                return str(fw_version), str(fw_endpoint)
        except fw_downloader.RemoteFileReadingError as e:
            logger.warning("No released fw for %s in %s", fw_signature, url)
            continue
    else:
        raise NoReleasedFwError("Released FW not found for %s\nRelease info:\n%s" % (fw_signature, str(release_info)))


def download_fw_fallback(fw_signature, release_info, ask_for_latest=True):
    try:
        _, released_fw_endpoint = get_released_fw(fw_signature, release_info)
    except NoReleasedFwError as e:
        logger.warning('Device "%s" is not supported in %s (as %s)', fw_signature, str(release_info.get('RELEASE_NAME')), str(release_info.get('SUITE')))
        if (ask_for_latest) and (ask_user('Perform downloading from latest master anyway (may cause unstable behaviour; proceed at your own risk)?')):
            downloaded_fw = fw_downloader.RemoteFileWatcher('fw', branch_name='').download(fw_signature, 'latest')
        else:
            six.reraise(*sys.exc_info())
    else:
        downloaded_fw = fw_downloader.download_remote_file(six.moves.urllib.parse.urljoin(CONFIG['ROOT_URL'], released_fw_endpoint))
    return downloaded_fw


def get_correct_modbus_connection(slaveid, port, known_uart_params_str=None):  # TODO: to device_prober module?
    """
    Alive device only:
        searching device's uart settings (if not passed);
        checking, that device is a wb-one via reading device_signature, serial_number, fw_signature, fw_version
    """
    modbus_connection = bindings.WBModbusDeviceBase(slaveid, port)

    if known_uart_params_str:
        modbus_connection.set_port_settings(*parse_uart_settings_str(known_uart_params_str))
    else:
        logger.info("Will find serial port settings for (%s : %d)...", port, slaveid)
        try:
            uart_settings_dict = modbus_connection.find_uart_settings(modbus_connection.get_slave_addr)
        except RuntimeError as e:
            raise minimalmodbus.NoResponseError(e)  # TODO: subclass the error?
        logger.info('Has found serial port settings: %s', str(uart_settings_dict))
        modbus_connection._set_port_settings_raw(uart_settings_dict)

    try:
        sn = modbus_connection.get_serial_number()  # Will raise NoResponseError, if disconnected
        fw_sig = modbus_connection.get_fw_signature()
    except bindings.TooOldDeviceError as e:
        fw_sig = ''
    except ValueError as e:  # minimalmodbus's slaveid check performs at _exec_command stage
        six.raise_from(ForeignDeviceError, e)

    try:  # WB devices assume to have all these regs
        logger.debug("%s %d:", port, slaveid)
        logger.debug("\t%s %d %s %s %d",
            modbus_connection.get_device_signature(),
            sn,
            fw_sig,
            modbus_connection.get_fw_version(),
            modbus_connection.get_uptime()
        )
    except minimalmodbus.ModbusException as e:
        raise ForeignDeviceError("Possibly, device (%s %d) is not a WB-one!" % (port, slaveid))

    return modbus_connection


def get_devices_on_driver(driver_config_fname):  # TODO: move to separate module
    """
    Parsing a driver's config file to get ports, their uart params and devices, connected to.

    :return: {<port_name> : {'devices' : [devices_on_port], 'uart_params' : [uart_params_of_port]}}
    :rtype: dict
    """
    found_devices = {}

    config_dict = json.load(open(driver_config_fname, 'r', encoding='utf-8'))

    for port in config_dict['ports']:
        if port.get('enabled', False) and port.get('path', False):  # updating devices only on active RS-485 ports
            port_name = port['path']
            uart_params_of_port = [int(port['baud_rate']), port['parity'], int(port['stop_bits'])]
            devices_on_port = set()
            for serial_device in port['devices']:
                device_name = serial_device.get('device_type', 'Unknown')
                slaveid = serial_device['slave_id']
                if device_name.startswith('WBIO-'):
                    logger.debug("Has found WBIO device: %s", device_name)
                    device_name, slaveid = 'WB-MIO', slaveid.split(':')[0]  # mio_slaveid:device_order
                devices_on_port.add((device_name, int(slaveid)))
            if devices_on_port:
                found_devices.update({port_name : {'devices' : list(devices_on_port), 'uart_params' : uart_params_of_port}})

    if not found_devices:
        logger.error("No devices has found in %s", driver_config_fname)
    return found_devices


def recover_device_iteration(fw_signature, slaveid, port):
    """
    A device supposed to be in "dead" state => fw_signature, slaveid, port have passed instead of modbus_connection
    """
    downloaded_fw = download_fw_fallback(fw_signature, RELEASE_INFO)
    direct_flash(downloaded_fw, slaveid, port)


def direct_flash(fw_fpath, slaveid, port, erase_all_settings=False, erase_uart_only=False):
    """
    Performing operations in bootloader (device is already into):
        flashing .wbfw
        erasing all settings or uart-only (with additional confirmation)
    """
    def _ensure(message_str):
        if ask_user(message_str):
            return True
        else:
            raise UpdateDeviceError("Reset of Device's settings was requested, but rejected after.\nDevice is in bootloder now; wait 120s, untill it starts.")

    default_msg = "Device's settings will be reset to defaults (1, 9600-8-N-2). Are you sure?"

    flasher = fw_flasher.ModbusInBlFlasher(slaveid, port)

    if (erase_uart_only and _ensure(default_msg)):
        flasher.reset_uart()
    if (erase_all_settings and _ensure(default_msg + " (it will erase ALL device's settings)")):
        flasher.reset_eeprom()

    flasher.flash_in_bl(fw_fpath)


def is_reflash_necessary(actual_version, provided_version, force_reflash=False, allow_downgrade=False, debug_info=''):
    actual_version, provided_version = semantic_version.Version(actual_version), semantic_version.Version(provided_version)
    _do_flash = False

    if actual_version == provided_version:
        if force_reflash:
            logger.info("%s %s -> %s %s", user_log.colorize('Force update:', 'YELLOW'), actual_version, provided_version, debug_info)
            _do_flash = True
        else:
            logger.info("Update skipped: %s -> %s %s", actual_version, provided_version, debug_info)
            _do_flash = False
    elif provided_version > actual_version:
        logger.info("%s %s -> %s %s", user_log.colorize('Update:', 'GREEN'), actual_version, provided_version, debug_info)
        _do_flash = True
    elif allow_downgrade:
        logger.info("%s %s -> %s %s", user_log.colorize('Downgrade:', 'YELLOW'), actual_version, provided_version, debug_info)
        _do_flash = True
    else:
        logger.info("%s %s -> %s %s", user_log.colorize('Downgrade not allowed:', 'RED'), actual_version, provided_version, debug_info)  # TODO: launch with --allow-downgrade arg?
        _do_flash = False

    if _do_flash and (actual_version.major != provided_version.major):
        return ask_user("Major version has changed (v%s -> v%s); backward compatibility will be broken. Are you sure?" % (str(actual_version.major), str(provided_version.major)))
    else:
        return _do_flash


def flash_alive_device(modbus_connection, mode, branch_name, specified_fw_version, force, erase_settings):
    """
    Checking for update, if branch is stable;
    Just flashing specified fw version, if branch is unstable.
    """
    fw_signature = modbus_connection.get_fw_signature()
    db.save(modbus_connection.slaveid, modbus_connection.port, fw_signature)
    downloader = fw_downloader.RemoteFileWatcher(mode=mode, branch_name=branch_name)
    mode_name = 'firmware' if mode == 'fw' else 'bootloader'

    downloaded_fw = None

    """
    Flashing specified fw version (without any update-checking), if branch is unstable
    """
    if branch_name:

        if specified_fw_version == 'release':  # default fw_version now is 'release'; will flash latest, if branch has specified
            specified_fw_version = 'latest'

        if ask_user('Flashing device: "%s" branch: "%s" version: "%s" is requested.\nStability cannot be guaranteed! Flash at your own risk?' % (
            fw_signature,
            branch_name,
            specified_fw_version)
        ):
            downloaded_fw = downloader.download(fw_signature, specified_fw_version)
            _do_flash(modbus_connection, downloaded_fw, mode, erase_settings)
            return

        else:
            raise UpdateDeviceError("Flashing %s has rejected" % fw_signature)

    else:
        branch_name = CONFIG['DEFAULT_SOURCE']

    """
    Retrieving, which passed version actually is
    """
    if specified_fw_version == 'release':  # triggered updating from releases
        specified_fw_version, released_fw_endpoint = get_released_fw(fw_signature, RELEASE_INFO)
        downloaded_fw = fw_downloader.download_remote_file(six.moves.urllib.parse.urljoin(CONFIG['ROOT_URL'], released_fw_endpoint))

    if specified_fw_version == 'latest':
        logger.debug('Retrieving latest %s version number for %s', mode_name, fw_signature)
        specified_fw_version = downloader.get_latest_version_number(fw_signature)  # to guess, is reflash needed or not

    downloaded_fw = downloaded_fw or downloader.download(fw_signature, specified_fw_version)  # if fw_version specified manually

    """
    Reflashing with update-checking
    """
    device_fw_version = modbus_connection.get_bootloader_version() if mode == 'bootloader' else modbus_connection.get_fw_version()

    logger.info("%s (%s %d)", modbus_connection.port, fw_signature, modbus_connection.slaveid)
    if is_reflash_necessary(
        actual_version=device_fw_version,
        provided_version=specified_fw_version,
        force_reflash=force,
        allow_downgrade=True,
        debug_info='(%s %d %s)' % (fw_signature, modbus_connection.slaveid, modbus_connection.port)
        ):
        _do_flash(modbus_connection, downloaded_fw, mode, erase_settings)


def _do_flash(modbus_connection, fw_fpath, mode, erase_settings):
    fw_signature = modbus_connection.get_fw_signature()
    logger.debug('Flashing approved for "%s" (%s : %d)', fw_signature, modbus_connection.port, modbus_connection.slaveid)
    modbus_connection.reboot_to_bootloader()
    direct_flash(fw_fpath, modbus_connection.slaveid, modbus_connection.port, erase_settings)

    if mode == 'bootloader':
        logger.info('Bootloader was successfully flashed. Will flash released firmware for "%s"', fw_signature)
        downloaded_fw = download_fw_fallback(fw_signature, RELEASE_INFO)
        direct_flash(downloaded_fw, modbus_connection.slaveid, modbus_connection.port, erase_settings)


class DeviceInfo(namedtuple('DeviceInfo', ['name', 'modbus_connection'])):
    __slots__ = ()

    def __str__(self):
        return "%s (%d, %s)" % (self.name, self.modbus_connection.slaveid, self.modbus_connection.port)


def probe_all_devices(driver_config_fname):  # TODO: to separate module
    """
    Acquiring states of all devies, added to config.
    States could be:
        alive - device is working in normal mode and answering to modbus commands
        in_bootloader - device could not boot it's rom
        disconnected - a dummy-record in config
        too_old_to_update - old wb devices, haven't bootloader
        foreign_devices - non-wb devices, defined in config
    """
    result = defaultdict(list)

    logger.info('Will probe all devices defined in %s', driver_config_fname)
    for port, port_params in get_devices_on_driver(driver_config_fname).items():
        uart_params = ''.join(map(str, port_params['uart_params']))  # 9600N2
        devices_on_port = port_params['devices']
        for device_name, device_slaveid in devices_on_port:
            logger.debug('Probing device %s (port: %s, slaveid: %d, uart_params: %s)...', device_name, port, device_slaveid, uart_params)
            device_info = DeviceInfo(name=device_name, modbus_connection=bindings.WBModbusDeviceBase(device_slaveid, port, *parse_uart_settings_str(uart_params)))
            try:
                device_info = DeviceInfo(name=device_name, modbus_connection=get_correct_modbus_connection(device_slaveid, port, uart_params))
            except ForeignDeviceError as e:
                result['foreign'].append(device_info)
                continue
            except minimalmodbus.NoResponseError as e:
                bootloader_connection = bindings.WBModbusDeviceBase(device_slaveid, port)
                if bootloader_connection.is_in_bootloader():
                    result['in_bootloader'].append(DeviceInfo(name=device_name, modbus_connection=bootloader_connection))
                else:
                    result['disconnected'].append(device_info)
                continue

            try:
                mb_connection = device_info.modbus_connection
                db.save(mb_connection.slaveid, mb_connection.port, mb_connection.get_fw_signature()) # old devices haven't fw_signatures
                result['alive'].append(device_info)
            except bindings.TooOldDeviceError:
                logger.error('%s is too old and does not support firmware updates!', str(device_info))
                result['too_old_to_update'].append(device_info)

    return result


def _update_all(force, allow_downgrade=False):  # TODO: maybe store fw endpoint in device_info? (to prevent multiple releases-parsing)
    probing_result = probe_all_devices(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])
    cmd_status = defaultdict(list)

    for device_info in probing_result['alive']:
        fw_signature = device_info.modbus_connection.get_fw_signature()
        try:
            latest_remote_version, released_fw_endpoint = get_released_fw(fw_signature, RELEASE_INFO)  # auto-updating only from releases
        except NoReleasedFwError as e:
            logger.error(e)
            cmd_status['no_fw_release'].append(device_info)
            continue
        if latest_remote_version == 'latest':  # Could be written in release
            latest_remote_version = fw_downloader.RemoteFileWatcher(mode='fw', branch_name='').get_latest_version_number(fw_signature)  # to guess, is reflash needed or not
        local_device_version = device_info.modbus_connection.get_fw_version()

        if is_reflash_necessary(
            actual_version=local_device_version,
            provided_version=latest_remote_version,
            force_reflash=force,
            allow_downgrade=allow_downgrade,
            debug_info="(%s)" % str(device_info)
            ):
            cmd_status['to_perform'].append([device_info, released_fw_endpoint])
        else:
            cmd_status['skipped'].append(device_info)

    for device_info, released_fw_endpoint in cmd_status['to_perform']: # Devices, were alive and supported fw_updates
        logger.info('Flashing firmware to %s', str(device_info))
        downloaded_file = fw_downloader.download_remote_file(six.moves.urllib.parse.urljoin(CONFIG['ROOT_URL'], released_fw_endpoint))
        try:
            _do_flash(device_info.modbus_connection, downloaded_file, 'fw', False)
        except fw_flasher.FlashingError as e:
            logger.exception(e)
            probing_result['in_bootloader'].append(device_info)
        except minimalmodbus.ModbusException as e:  # Device was connected at the probing time, but is disconnected now
            logger.exception(e)
            probing_result['disconnected'].append(device_info)
        else:
            cmd_status['ok'].append(device_info)

    if cmd_status['skipped']:  # TODO: maybe split by reasons?
        logger.warning("Not updated:")
        logger.warning("\t%s", "; ".join([str(device_info) for device_info in cmd_status['skipped']]))
        logger.warning('You may try to run with "-f" or "--allow-downgrade" arg')

    if cmd_status['no_fw_release']:
        logger.warning("Not supported in current (%s) release:", str(RELEASE_INFO))
        logger.warning("\t%s", "; ".join([str(device_info) for device_info in cmd_status['no_fw_release']]))
        logger.warning("You may try to switch to newer release")

    if probing_result['disconnected']:
        logger.warning("No answer from:")
        logger.warning("\t%s", "; ".join([str(device_info) for device_info in probing_result['disconnected']]))
        logger.warning("Devices are possibly disconnected")

    if probing_result['in_bootloader']:
        logger.error("Now in bootloader:")
        logger.error("\t%s", "; ".join([str(device_info) for device_info in probing_result['in_bootloader']]))
        logger.error('Try wb-mcu-fw-updater recover-all')

    if probing_result['too_old_to_update']:
        logger.error("Too old for any updates:")
        logger.error("\t%s", "; ".join([str(device_info) for device_info in probing_result['too_old_to_update']]))

    logger.info("%s upgraded, %s skipped upgrade, %s stuck in bootloader, %s disconnected and %s too old for any updates.",
        user_log.colorize(str(len(cmd_status['ok'])), 'GREEN' if cmd_status['ok'] else 'RED'),
        user_log.colorize(str(len(cmd_status['skipped'])), 'YELLOW' if cmd_status['skipped'] else 'GREEN'),
        user_log.colorize(str(len(probing_result['in_bootloader'])), 'RED' if probing_result['in_bootloader'] else 'GREEN'),
        user_log.colorize(str(len(probing_result['disconnected'])), 'RED' if probing_result['disconnected'] else 'GREEN'),
        user_log.colorize(str(len(probing_result['too_old_to_update'])), 'RED' if probing_result['too_old_to_update'] else 'GREEN')
    )


def _restore_fw_signature(slaveid, port):
    """
    Getting fw_signature of devices in bootloader
    """
    try:
        logger.debug("Will ask a bootloader for fw_signature")
        fw_signature = bindings.WBModbusDeviceBase(slaveid, port).get_fw_signature()  # latest bootloaders could answer a fw_signature
    except minimalmodbus.ModbusException as e:
        logger.debug("Will try to restore fw_signature from db by slaveid: %d and port %s", slaveid, port)
        fw_signature = db.get_fw_signature(slaveid, port)
    logger.debug("FW signature for %d : %s is %s", slaveid, port, str(fw_signature))
    return fw_signature


def _recover_all():
    probing_result = probe_all_devices(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])
    cmd_status = defaultdict(list)

    for device_info in probing_result['in_bootloader']:
        fw_signature = _restore_fw_signature(device_info.modbus_connection.slaveid, device_info.modbus_connection.port)
        if fw_signature is None:
            logger.info('%s %s', user_log.colorize('Unknown fw_signature:', 'RED'), str(device_info))
            cmd_status['skipped'].append(device_info)
        else:
            logger.info('%s %s', user_log.colorize('Known fw_signature:', 'GREEN'), str(device_info))
            cmd_status['to_perform'].append([device_info, fw_signature])

    if cmd_status['to_perform']:
        logger.info('Flashing the most recent stable firmware:')
        for device_info, fw_signature in cmd_status['to_perform']:
            try:
                recover_device_iteration(fw_signature, device_info.modbus_connection.slaveid, device_info.modbus_connection.port)
            except (fw_flasher.FlashingError, fw_downloader.WBRemoteStorageError) as e:
                logger.exception(e)
                cmd_status['skipped'].append(device_info)
            else:
                cmd_status['ok'].append(device_info)
        logger.info('Done')

    if probing_result['disconnected']:
        logger.debug("No answer:")
        logger.debug("\t%s", "; ".join([str(device_info) for device_info in probing_result['disconnected']]))

    if cmd_status['skipped']:
        logger.error("Not recovered:")
        logger.error("\t%s", "; ".join([str(device_info) for device_info in cmd_status['skipped']]))
        logger.error("Try again or launch single recover with --fw-sig <fw_signature> key for each device!")

    logger.info("%s recovered, %s was already working, %s not recovered and %s not answered to recover cmd.",
        user_log.colorize(str(len(cmd_status['ok'])), 'GREEN' if (cmd_status['ok'] or (not cmd_status['to_perform'] and not cmd_status['skipped'])) else 'RED'),
        user_log.colorize(str(len(probing_result['alive'])), 'GREEN') if probing_result['alive'] else '0',
        user_log.colorize(str(len(cmd_status['skipped'])), 'RED' if cmd_status['skipped'] else 'GREEN'),
        user_log.colorize(str(len(probing_result['disconnected'])), 'RED' if probing_result['disconnected'] else 'GREEN')
    )


def _send_signal_to_driver(signal):
    """
    Use pausing/resuming of process, found by name (instead of killing/starting)
    to handle cases, like <wb-mqtt-serial -c config.conf>

    :type signal: str
    """
    if CONFIG['SERIAL_DRIVER_PROCESS_NAME']:
        cmd_str = 'killall %s %s' % (signal, CONFIG['SERIAL_DRIVER_PROCESS_NAME'])
        logger.debug('Will run: %s', cmd_str)
        subprocess.call(cmd_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def pause_driver():
    _send_signal_to_driver('-STOP')


def resume_driver():
    _send_signal_to_driver('-CONT')


def get_port_settings(port_fname):
    """
    python-serial does not remember initial port settings (bd, parity, etc...)
    => restoring it manually after all operations to let wb-mqtt-serial work again
    """
    with open(port_fname) as port:
        fd = port.fileno()
        return termios.tcgetattr(fd)


def set_port_settings(port_fname, termios_settings):
    with open(port_fname) as port:
        termios.tcsetattr(port.fileno(), termios.TCSANOW, termios_settings)
