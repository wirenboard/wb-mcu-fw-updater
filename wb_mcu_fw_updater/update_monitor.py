#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import termios
import json
from json.decoder import JSONDecodeError
import subprocess
from pprint import pformat
from distutils.version import LooseVersion
from . import fw_flasher, fw_downloader, user_log, jsondb, die, PYTHON2, CONFIG

import wb_modbus  # Setting up module's params
wb_modbus.ALLOWED_UNSUCCESSFUL_TRIES = CONFIG['ALLOWED_UNSUCCESSFUL_MODBUS_TRIES']
wb_modbus.DEBUG = CONFIG['MODBUS_DEBUG']

from wb_modbus import minimalmodbus, bindings, parse_uart_settings_str


if PYTHON2:
    input_func = raw_input
else:
    input_func = input


db = jsondb.JsonDB(CONFIG['DB_FILE_LOCATION'])


ModbusError = minimalmodbus.ModbusException
TooOldDeviceError = bindings.TooOldDeviceError


def ask_user(message):
    """
    Asking user before potentionally dangerous action.

    :param message: will be printed to user
    :type message: str
    :return: is user sure or not
    :rtype: bool
    """
    message_str = '\n*** %s [Y/N] *** ' % (message)
    ret = input_func(user_log.colorize(message_str, 'YELLOW'))
    return ret.upper().startswith('Y')


def get_correct_modbus_connection(slaveid, port):
    if slaveid == 0:
        die("Slaveid %d is not allowed in this mode!" % slaveid)  # Broadcast slaveid is available only in bootloader to prevent possible harm
    modbus_connection = bindings.WBModbusDeviceBase(slaveid, port)
    try:
        logging.info("Will find serial port settings for (%s : %d)..." % (port, slaveid))
        uart_settings_dict = modbus_connection.find_uart_settings(modbus_connection.get_slave_addr)
    except RuntimeError as e:
        logging.error('Device is disconnected or slaveid/port is wrong')
        die()
    logging.info('Has found serial port settings: %s' % str(uart_settings_dict))
    modbus_connection._set_port_settings_raw(uart_settings_dict)
    return modbus_connection


def get_devices_on_driver(driver_config_fname):
    """
    Parsing a driver's config file to get ports, their uart params and devices, connected to.

    :return: {<port_name> : {'devices' : [devices_on_port], 'uart_params' : [uart_params_of_port]}}
    :rtype: dict
    """
    found_devices = {}
    try:
        config_dict = json.load(open(driver_config_fname, 'r'))
    except (IOError, JSONDecodeError) as e:
        die(e)
    for port in config_dict['ports']:
        if port.get('enabled', False):
            port_name = port['path']
            uart_params_of_port = [int(port['baud_rate']), port['parity'], int(port['stop_bits'])]
            devices_on_port = []
            for serial_device in port['devices']:
                device_name = serial_device.get('device_type', 'Unknown')
                slaveid = serial_device['slave_id']
                devices_on_port.append([device_name, int(slaveid)])
            if devices_on_port:
                found_devices.update({port_name : {'devices' : devices_on_port, 'uart_params' : uart_params_of_port}})
    if found_devices:
        return found_devices
    else:
        die('No devices has found in %s' % driver_config_fname)


def recover_device_iteration(fw_signature, slaveid, port, response_timeout=2.0):
    downloader = fw_downloader.RemoteFileWatcher(mode='fw', branch_name='')
    fw_version = 'latest'
    downloaded_fw = downloader.download(fw_signature, fw_version)
    if downloaded_fw is None:
        raise RuntimeError('FW file was not downloaded!')
    flash_in_bootloader(downloaded_fw, slaveid, port, erase_settings=False, response_timeout=response_timeout)


def flash_in_bootloader(downloaded_fw_fpath, slaveid, port, erase_settings, response_timeout=2.0):
    if erase_settings:
        if ask_user('All settings will be reset to defaults (1, 9600-8-N-2). Are you sure?'):
            pass
        else:
            die('Reset of settings was rejected')
    flasher = fw_flasher.WBFWFlasher(port)
    flasher.flash(slaveid, downloaded_fw_fpath, erase_settings, response_timeout)


def flash_alive_device(modbus_connection, mode, branch_name, specified_fw_version, force, erase_settings):
    """
    Checking for update, if branch is stable;
    Just flashing specified fw version, if branch is unstable.
    """
    fw_signature = modbus_connection.get_fw_signature()
    db.save(modbus_connection.slaveid, modbus_connection.port, fw_signature)
    downloader = fw_downloader.RemoteFileWatcher(mode=mode, branch_name=branch_name)
    mode_name = 'firmware' if mode == 'fw' else 'bootloader'

    """
    Flashing specified fw version (without any update-checking), if branch is unstable
    """
    if branch_name:
        logging.warn("Flashing unstable %s from branch \"%s\" is requested!" % (
            mode_name,
            branch_name)
        )
        try:
            _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
            return
        except RuntimeError as e:
            die(e)
    else:
        branch_name = 'stable'

    """
    Retrieving, which <latest> version actually is
    """
    if specified_fw_version == 'latest':
        logging.debug('Retrieving latest %s version number for %s' % (mode_name, fw_signature))
        specified_fw_version = downloader.get_latest_version_number(fw_signature)
        if specified_fw_version is None:  # No latest.txt file
            die('Could not retrieve latest %s version in branch: %s' % (mode_name, branch_name))

    """
    Reflashing with update-checking
    """
    device_fw_version = modbus_connection.get_bootloader_version() if mode == 'bootloader' else modbus_connection.get_fw_version()
    passed_fw_version = LooseVersion(specified_fw_version)
    try:
        if passed_fw_version == device_fw_version:
            if force:
                _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
                logging.info('Successfully reflashed %s (%s)' % (mode_name, device_fw_version))
            else:
                logging.warning('%s is already the newest version (%s), will not update. Use -f to force.' % (mode_name.capitalize(), device_fw_version,))
            return
        elif passed_fw_version < device_fw_version:
            logging.warning('%s will be downgraded! Will flash (%s) over (%s).' % (mode_name.capitalize(), str(passed_fw_version), device_fw_version))
            _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
            logging.info('Successfully flashed %s (%s) over (%s)' % (mode_name, passed_fw_version, device_fw_version))
            return
        elif passed_fw_version > device_fw_version:
            logging.info('%s will be upgraded. Will flash (%s) over (%s).' % (mode_name.capitalize(), str(passed_fw_version), device_fw_version))
            _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
            logging.info('Successfully flashed %s (%s) over (%s)' % (mode_name, passed_fw_version, device_fw_version))
            return
        else:
            die('Something goes wrong with version checking!')
    except RuntimeError as e:  # TODO: maybe separate errors for downloader and flasher?
        die(e)


def _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings):
    logging.debug('Flashing approved for %s : %d' % (modbus_connection.port, modbus_connection.slaveid))
    fw_file = downloader.download(fw_signature, specified_fw_version)
    if fw_file is None:
        raise RuntimeError("%s file was not downloaded!" % mode)
    modbus_connection.reboot_to_bootloader()
    flash_in_bootloader(fw_file, modbus_connection.slaveid, modbus_connection.port, erase_settings)
    if mode == 'bootloader':
        logging.info("Bootloader was successfully flashed. Will flash the latest stable firmware.")
        downloaded_fw = fw_downloader.RemoteFileWatcher('fw', branch_name='').download(fw_signature, 'latest')
        if downloaded_fw is None:
            raise RuntimeError("fw file was not downloaded!")
        flash_in_bootloader(downloaded_fw, modbus_connection.slaveid, modbus_connection.port, erase_settings)


class DeviceInfo(object):
    """
    Generic representation of found-in-driver-config Wiren Board modbus device.
    """

    def __init__(self, name, slaveid, port, **kwargs):
        self.PROPS = {'slaveid' : int(slaveid), 'port' : port, 'name' : name}
        self.PROPS.update(kwargs)

    def __str__(self):
        return '%s (port: %s; slaveid: %d)' % (self.PROPS['name'], self.PROPS['port'], self.PROPS['slaveid'])

    def _get(self, property):
        if property not in self.PROPS.keys():
            raise RuntimeError('No property %s was set! Choose from %s' % (property, ', '.join(self.PROPS.keys())))
        return self.PROPS[property]

    def get_multiple_props(self, *props_names):
        return [self._get(prop_name) for prop_name in props_names]

    def _set_asdict(self, properties_dict):
        self.PROPS.update(properties_dict)


def probe_all_devices(driver_config_fname):
    """
    Acquiring states of all devies, added to config.
    States could be:
        alive - device is working in normal mode and answering to modbus commands
        in_bootloader - device could not boot it's rom
        disconnected - a dummy-record in config
    """
    alive = []
    in_bootloader = []
    disconnected = []
    too_old_to_update = []
    store_device = lambda name, slaveid, port, uart_params: DeviceInfo(name, slaveid, port, uart_settings=uart_params)
    logging.info('Will probe all devices defined in %s' % driver_config_fname)
    for port, port_params in get_devices_on_driver(driver_config_fname).items():
        uart_params = port_params['uart_params']
        devices_on_port = port_params['devices']
        for device_name, device_slaveid in devices_on_port:
            logging.debug('Probing device %s (port: %s, slaveid: %d)...' % (device_name, port, device_slaveid))
            modbus_connection = bindings.WBModbusDeviceBase(device_slaveid, port, *uart_params)
            if modbus_connection.is_in_bootloader():
                in_bootloader.append(store_device(device_name, device_slaveid, port, uart_params))
            else:
                try:
                    modbus_connection.get_slave_addr()
                except ModbusError: # Device is really disconnected
                    disconnected.append(store_device(device_name, device_slaveid, port, uart_params))
                    continue
                try:
                    db.save(modbus_connection.slaveid, modbus_connection.port, modbus_connection.get_fw_signature()) # old devices haven't fw_signatures
                    alive.append(store_device(device_name, device_slaveid, port, uart_params))
                except TooOldDeviceError:
                    logging.error('%s (slaveid: %d; port: %s) is too old and does not support firmware updates!' % (device_name, device_slaveid, port))
                    too_old_to_update.append(store_device(device_name, device_slaveid, port, uart_params))
    return alive, in_bootloader, disconnected, too_old_to_update


def _update_all(force):
    alive, in_bootloader, dummy_records, too_old_devices = probe_all_devices(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])
    ok_records = []
    update_was_skipped = [] # Device_info dicts
    to_update = [] # modbus_connection clients
    downloader = fw_downloader.RemoteFileWatcher(mode='fw', branch_name='')
    for device_info in alive:
        slaveid, port, name, uart_settings = device_info.get_multiple_props('slaveid', 'port', 'name', 'uart_settings')
        modbus_connection = bindings.WBModbusDeviceBase(slaveid, port, *uart_settings)
        fw_signature = modbus_connection.get_fw_signature()
        _latest_remote_version = downloader.get_latest_version_number(fw_signature)
        if _latest_remote_version is None:
            update_was_skipped.append(device_info)
            continue
        latest_remote_version = LooseVersion(_latest_remote_version)
        local_device_version = modbus_connection.get_fw_version()
        if latest_remote_version == local_device_version:
            if force:
                logging.info("%s %s (has already latest fw)" % (user_log.colorize('Force update:', 'YELLOW'), str(device_info)))
                device_info._set_asdict({'mb_client' : modbus_connection, 'latest_remote_fw' : str(latest_remote_version), 'fw_signature' : fw_signature})
                to_update.append(device_info)
            else:
                logging.info("Update skipped: %s (has already latest fw %s)" % (str(device_info), local_device_version))
                update_was_skipped.append(device_info)
        elif latest_remote_version > local_device_version:
            logging.info("%s %s (from %s to %s)" % (user_log.colorize('Update available:', 'GREEN'), str(device_info), local_device_version, str(latest_remote_version)))
            device_info._set_asdict({'mb_client' : modbus_connection, 'latest_remote_fw' : str(latest_remote_version), 'fw_signature' : fw_signature})
            to_update.append(device_info)
        else:
            logging.error("Remote fw version (%s) is less than local on %s (%s)" % (str(latest_remote_version), str(device_info), local_device_version))
            update_was_skipped.append(device_info)

    if to_update:  # Devices, were alive and supported fw_updates
        for device_info in to_update:
            name, slaveid, port, mb_client, latest_remote_fw, fw_signature = device_info.get_multiple_props('name', 'slaveid', 'port', 'mb_client', 'latest_remote_fw', 'fw_signature')
            logging.info('Flashing firmware to %s' % str(device_info))
            try:
                _do_flash(downloader, mb_client, 'fw', fw_signature, latest_remote_fw, False)
            except subprocess.CalledProcessError as e:
                logging.exception(e)
                in_bootloader.append(device_info)
            except ModbusError as e:  # Device was connected at the probing time, but is disconnected now
                logging.exception(e)
                dummy_records.append(device_info)
            except RuntimeError as e:
                logging.exception(e)
                update_was_skipped.append(device_info)
            else:
                ok_records.append(device_info)

    if update_was_skipped:
        logging.warning('The following devices have already the most recent firmware.\nRun "wb-mcu-fw-updater update-all -f" to force update:\n\t%s' % '\n\t'.join([str(device_info) for device_info in update_was_skipped]))

    if dummy_records:
        logging.warning('No answer from the following devices:\n\t%s\nDevices are possibly disconnected.' % '\n\t'.join([str(device_info) for device_info in dummy_records]))

    if in_bootloader:
        logging.error('The following devices are in bootloader mode.\nTry "wb-mcu-fw-updater recover-all":\n\t%s' % '\n\t'.join([str(device_info) for device_info in in_bootloader]))

    if too_old_devices:
        logging.error("Devices, which are too old for firmware updates:\n\t%s" % '\n\t'.join([str(device_info) for device_info in too_old_devices]))

    logging.info("%s upgraded, %s skipped upgrade, %s stuck in bootloader, %s disconnected and %s too old for any updates." % (
        user_log.colorize(str(len(ok_records)), 'GREEN' if ok_records else 'RED'),
        user_log.colorize(str(len(update_was_skipped)), 'YELLOW' if update_was_skipped else 'GREEN'),
        user_log.colorize(str(len(in_bootloader)), 'RED' if in_bootloader else 'GREEN'),
        user_log.colorize(str(len(dummy_records)), 'RED' if dummy_records else 'GREEN'),
        user_log.colorize(str(len(too_old_devices)), 'RED' if too_old_devices else 'GREEN')
    ))


def _recover_all():
    alive, in_bootloader, dummy_records, _ = probe_all_devices(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])
    recover_was_skipped = []
    to_recover = []
    ok_records = []
    downloader = fw_downloader.RemoteFileWatcher(mode='fw', branch_name='')
    for device_info in in_bootloader:
        slaveid, port, name = device_info.get_multiple_props('slaveid', 'port', 'name')
        fw_signature = db.get_fw_signature(slaveid, port)
        if fw_signature is None:
            logging.info('%s %s' % (user_log.colorize('Unknown fw_signature:', 'RED'), str(device_info)))
            recover_was_skipped.append(device_info)
        else:
            logging.info('%s %s' % (user_log.colorize('Known fw_signature:', 'GREEN'), str(device_info)))
            device_info._set_asdict({'fw_signature' : fw_signature})
            to_recover.append(device_info)

    if to_recover:
        logging.info('Flashing the most recent stable firmware:')
        for device_info in to_recover:
            fw_signature, slaveid, port = device_info.get_multiple_props('fw_signature', 'slaveid', 'port')
            try:
                recover_device_iteration(fw_signature, slaveid, port)
            except (subprocess.CalledProcessError, RuntimeError) as e:
                logging.exception(e)
                recover_was_skipped.append(device_info)
            else:
                ok_records.append(device_info)
        logging.info('Done')

    if dummy_records:
        logging.debug('No answer from the following devices:\n\t%s' % '\n\t'.join([str(device_info) for device_info in dummy_records]))

    if recover_was_skipped:
        logging.error('Could not recover:\n\t%s\nTry again or launch single recover with --fw-sig <fw_signature> key for each device!' % '\n\t'.join([str(device_info) for device_info in recover_was_skipped]))

    logging.info("%s recovered, %s was already working, %s not recovered and %s not answered to recover cmd." % (
        user_log.colorize(str(len(ok_records)), 'GREEN' if (ok_records or (not to_recover and not recover_was_skipped)) else 'RED'),
        user_log.colorize(str(len(alive)), 'GREEN') if alive else '0',
        user_log.colorize(str(len(recover_was_skipped)), 'RED' if recover_was_skipped else 'GREEN'),
        user_log.colorize(str(len(dummy_records)), 'RED' if dummy_records else 'GREEN')
    ))


def _send_signal_to_driver(signal):
    """
    Use pausing/resuming of process, found by name (instead of killing/starting)
    to handle cases, like <wb-mqtt-serial -c config.conf>

    :type signal: str
    """
    if CONFIG['SERIAL_DRIVER_PROCESS_NAME']:
        cmd_str = 'killall %s %s' % (signal, CONFIG['SERIAL_DRIVER_PROCESS_NAME'])
        logging.debug('Will run: %s' % cmd_str)
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
    try:
        with open(port_fname) as port:
            fd = port.fileno()
            return termios.tcgetattr(fd)
    except Exception as e:
        die(e)


def set_port_settings(port_fname, termios_settings):
    try:
        with open(port_fname) as port:
            termios.tcsetattr(port.fileno(), termios.TCSANOW, termios_settings)
    except Exception as e:
        die(e)
