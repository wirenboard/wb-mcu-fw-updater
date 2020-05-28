#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import json
import subprocess
from pprint import pformat
from distutils.version import LooseVersion
from . import fw_flasher, fw_downloader, user_log, jsondb, die, PYTHON2, CONFIG

import wb_modbus  # Setting up module's params
wb_modbus.ALLOWED_UNSUCCESSFUL_TRIES = CONFIG['ALLOWED_UNSUCCESSFUL_MODBUS_TRIES']
wb_modbus.DEBUG = CONFIG['MODBUS_DEBUG']

from wb_modbus import minimalmodbus, parse_uart_settings_str
from wb_modbus.bindings import WBModbusDeviceBase


if PYTHON2:
    input_func = raw_input
else:
    input_func = input


db = jsondb.JsonDB(CONFIG['DB_FILE_LOCATION'])


ModbusError = minimalmodbus.ModbusException


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



def get_correct_modbus_connection(slaveid, port, uart_settings_str, uart_settings_are_unknown):
    if slaveid == 0:
        die("Slaveid %d is not allowed in this mode!" % slaveid)  # Broadcast slaveid is available only in bootloader to prevent possible harm
    modbus_connection = WBModbusDeviceBase(slaveid, port)
    try:
        uart_settings = parse_uart_settings_str(uart_settings_str)
        modbus_connection.set_port_settings(*uart_settings)
    except RuntimeError as e:
        die(e)
    if uart_settings_are_unknown:
        """
        Applying found uart settings to modbus_connection instance.
        """
        try:
            logging.warning("Serial port settings are unknown. Trying to find it...")
            uart_settings_dict = modbus_connection.find_uart_settings(modbus_connection.get_slave_addr)
        except RuntimeError as e:
            logging.error('Device is disconnected or slaveid is wrong')
            die(e)
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
    except IOError as e:
        die(e)
    for port in config_dict['ports']:
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
    if branch_name:
        logging.warning('Flashing %s v %s from unstable branch %s' % (mode, specified_fw_version, branch_name))
        _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
        return
    if specified_fw_version == 'latest':
        logging.debug('Retrieving latest %s version number for %s' % (mode, fw_signature))
        specified_fw_version = downloader.get_latest_version_number(fw_signature)
    device_fw_version = modbus_connection.get_bootloader_version() if mode == 'bootloader' else modbus_connection.get_fw_version()
    passed_fw_version = LooseVersion(specified_fw_version)
    if passed_fw_version == device_fw_version:
        if force:
            _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
            logging.info('Has reflashed %s v %s' % (mode, device_fw_version))
        else:
            logging.warning('Flashing device %s (slaveid: %s) was rejected (%s is a latest %s version). Launch with -f key, if you really need reflashing.' % (fw_signature, modbus_connection.slaveid, device_fw_version, mode))
        return
    elif passed_fw_version < device_fw_version:
        logging.warning('Will flash older %s version! (specified: %s; in-device: %s)' % (mode, str(passed_fw_version), device_fw_version))
        _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
        logging.info('Has flashed %s v %s over %s' % (mode, passed_fw_version, device_fw_version))
        return
    elif passed_fw_version > device_fw_version:
        logging.info('Will flash newer %s version! (specified: %s; in-device: %s)' % (mode, str(passed_fw_version), device_fw_version))
        _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings)
        logging.info('Has flashed %s v %s over %s' % (mode, passed_fw_version, device_fw_version))
        return
    else:
        die('Something goes wrong with version checking!')


def _do_flash(downloader, modbus_connection, mode, fw_signature, specified_fw_version, erase_settings):
    logging.debug('Flashing approved for %s : %d' % (modbus_connection.port, modbus_connection.slaveid))
    fw_file = downloader.download(fw_signature, specified_fw_version)
    modbus_connection.reboot_to_bootloader()
    flash_in_bootloader(fw_file, modbus_connection.slaveid, modbus_connection.port, erase_settings)
    if mode == 'bootloader':
        logging.info("Bootloader flashing was successful. Now flashing the latest stable FW:")
        downloaded_fw = fw_downloader.RemoteFileWatcher('fw', branch_name='').download(fw_signature, 'latest')
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
    store_device = lambda name, slaveid, port, uart_params: DeviceInfo(name, slaveid, port, uart_settings=uart_params)
    logging.info('Will scan %s for states of all devices in' % driver_config_fname)
    for port, port_params in get_devices_on_driver(driver_config_fname).items():
        uart_params = port_params['uart_params']
        devices_on_port = port_params['devices']
        for device_name, device_slaveid in devices_on_port:
            logging.debug('Probing device %s (port: %s, slaveid: %d)...' % (device_name, port, device_slaveid))
            modbus_connection = WBModbusDeviceBase(device_slaveid, port, *uart_params)
            if modbus_connection.is_in_bootloader():
                in_bootloader.append(store_device(device_name, device_slaveid, port, uart_params))
            else:
                try:
                    modbus_connection.get_slave_addr()
                    alive.append(store_device(device_name, device_slaveid, port, uart_params))
                    db.save(modbus_connection.slaveid, modbus_connection.port, modbus_connection.get_fw_signature())
                except ModbusError:
                    disconnected.append(store_device(device_name, device_slaveid, port, uart_params))
    return alive, in_bootloader, disconnected


def _update_all(force):
    alive, in_bootloader, dummy_records = probe_all_devices(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])
    update_was_skipped = [] # Device_info dicts
    to_update = [] # modbus_connection clients
    downloader = fw_downloader.RemoteFileWatcher(mode='fw', branch_name='')
    for device_info in alive:
        slaveid, port, name, uart_settings = device_info.get_multiple_props('slaveid', 'port', 'name', 'uart_settings')
        modbus_connection = WBModbusDeviceBase(slaveid, port, *uart_settings)
        fw_signature = modbus_connection.get_fw_signature()
        latest_remote_version = LooseVersion(downloader.get_latest_version_number(fw_signature))
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

    if to_update:
        logging.info('Begin flashing:')
        for device_info in to_update:
            slaveid, port, mb_client, latest_remote_fw, fw_signature = device_info.get_multiple_props('slaveid', 'port', 'mb_client', 'latest_remote_fw', 'fw_signature')
            try:
                _do_flash(downloader, mb_client, 'fw', fw_signature, latest_remote_fw, False)
            except subprocess.CalledProcessError as e:
                logging.exception(e)
                in_bootloader.append(device_info)
            except ModbusError as e:
                logging.exception(e)
                dummy_records.append(device_info)
        logging.info('Done')

    if update_was_skipped:
        logging.warning('Update was skipped for:\n\t%s\nLaunch update-all with -f key to force update all devices!' % '\n\t'.join([str(device_info) for device_info in update_was_skipped]))

    if dummy_records:
        logging.warning('Possibly, some devices are disconnected from bus:\n\t%s' % '\n\t'.join([str(device_info) for device_info in dummy_records]))

    if in_bootloader:
        die('Possibly, some devices are in bootloader:\n\t%s' % '\n\t'.join([str(device_info) for device_info in in_bootloader]))


def _recover_all():
    alive, in_bootloader, dummy_records = probe_all_devices(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])
    recover_was_skipped = []
    to_recover = []
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
        logging.info('Begin recovering:')
        for device_info in to_recover:
            fw_signature, slaveid, port = device_info.get_multiple_props('fw_signature', 'slaveid', 'port')
            try:
                recover_device_iteration(fw_signature, slaveid, port)
            except subprocess.CalledProcessError as e:
                logging.exception(e)
                recover_was_skipped.append(device_info)
        logging.info('Done')

    if dummy_records:
        logging.debug('Possibly, some devices are disconnected from bus:\n\t%s' % '\n\t'.join([str(device_info) for device_info in dummy_records]))

    if recover_was_skipped:
        die('Could not recover:\n\t%s\nTry again or launch single recover with --fw-sig <fw_signature> key for each device!' % '\n\t'.join([str(device_info) for device_info in recover_was_skipped]))


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
