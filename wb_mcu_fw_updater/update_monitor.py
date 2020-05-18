#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import json
import subprocess
from distutils.version import LooseVersion
from . import fw_flasher, fw_downloader, jsondb, die, PYTHON2, CONFIG

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
    ret = input_func(message_str)
    return ret.upper().startswith('Y')


def compare_semver(first, second):
    """
    Comparing versions strings in semver.
    Second is converted implicitly via LooseVersion.

    :return: is first semver > second
    :rtype: bool
    """
    return LooseVersion(vstring=str(first)) > str(second)


def is_update_needed(modbus_connection, mode, branch_name=''):
    """
    Checking, whether update is needed or not by comparing version, stored in the device with remote.
    """
    downloader = fw_downloader.RemoteFileWatcher(mode, branch_name=branch_name)
    meaningful_str = modbus_connection.get_fw_signature()
    latest_remote_version = downloader.get_latest_version_number(meaningful_str)
    current_version = modbus_connection.get_bootloader_version() if mode == 'bootloader' else modbus_connection.get_fw_version()
    if compare_semver(latest_remote_version, current_version):
        logging.info('Update is needed (local %s version: %s; remote version: %s)' % (mode, current_version, latest_remote_version))
        return True
    else:
        logging.info('Device has latest %s version (%s)!' % (mode, current_version))
        return False


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
            logging.warning("UART settings are unknown. Trying to find it...")
            uart_settings_dict = modbus_connection.find_uart_settings(modbus_connection.get_slave_addr)
        except RuntimeError as e:
            logging.error('Device is disconnected or slaveid is wrong')
            die(e)
        logging.info('Has found UART settings: %s' % str(uart_settings_dict))
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
    except FileNotFoundError as e:
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
    try:
        flash_in_bootloader(downloaded_fw, slaveid, port, erase_settings=False, response_timeout=response_timeout)
    except subprocess.CalledProcessError as e:
        logging.error("Flashing has failed!")
        die(e)


def flash_in_bootloader(downloaded_fw_fpath, slaveid, port, erase_settings, response_timeout=2.0):
    if erase_settings:
        if ask_user('All settings will be reset to defaults (1, 9600-8-N-2). Are you sure?'):
            pass
        else:
            die('Reset of settings was rejected')
    flasher = fw_flasher.WBFWFlasher(port)
    flasher.flash(slaveid, downloaded_fw_fpath, erase_settings, response_timeout)


def flash_alive_device(modbus_connection, mode, branch_name, specified_fw_version, force, erase_settings):
    if is_update_needed(modbus_connection, mode, branch_name) or force:
        fw_signature = modbus_connection.get_fw_signature()
        db.save(modbus_connection.slaveid, modbus_connection.port, fw_signature)
        downloaded_fw = fw_downloader.RemoteFileWatcher(mode, branch_name=branch_name).download(fw_signature, specified_fw_version)
        modbus_connection.reboot_to_bootloader()
        flash_in_bootloader(downloaded_fw, modbus_connection.slaveid, modbus_connection.port, erase_settings)
        if mode == 'bootloader':
            logging.warning("Now flashing the latest FW:")
            downloaded_fw = fw_downloader.RemoteFileWatcher('fw', branch_name=branch_name).download(fw_signature, 'latest')
            flash_in_bootloader(downloaded_fw, modbus_connection.slaveid, modbus_connection.port, erase_settings)


def all_devices_on_driver(die_on_failure=True):
    def real_decorator(f):
        def wrapper(*args, **kwargs):
            overall_fails = {}
            for port, port_params in get_devices_on_driver(CONFIG['SERIAL_DRIVER_CONFIG_FNAME']).items():
                uart_params = port_params['uart_params']
                devices_on_port = port_params['devices']
                failed_devices = []
                for device_name, device_slaveid in devices_on_port:
                    logging.warn('Trying device %s (port: %s, slaveid: %d)...' % (device_name, port, device_slaveid))
                    try:
                        f(device_slaveid, port, uart_params, *args, **kwargs)
                        logging.info('Successful!')
                    except Exception as e:
                        logging.warn('Failed', exc_info=True)
                        failed_devices.append([device_name, device_slaveid])
                if failed_devices:
                    overall_fails.update({port : failed_devices})
            if overall_fails:
                if die_on_failure:
                    die('Operation has failed for:\n%s\nCheck syslog for more info' % (str(overall_fails)))
        return wrapper
    return real_decorator


@all_devices_on_driver(die_on_failure=True)
def _update_all(slaveid, port, uart_params, force):
    modbus_connection = WBModbusDeviceBase(slaveid, port, *uart_params)
    flash_alive_device(modbus_connection, 'fw', '', 'latest', force, False)


@all_devices_on_driver(die_on_failure=False)
def _recover_all(slaveid, port, uart_params):
    fw_signature = db.get_fw_signature(slaveid, port)
    if fw_signature is None:
        raise RuntimeError("Could not get fw_signature from db. Recover in manual mode, if needed!")
    modbus_connection = WBModbusDeviceBase(slaveid, port, *uart_params)
    if not modbus_connection.is_in_bootloader():
        raise RuntimeError('Device %s (port: %s, slaveid: %d) is not in bootloader' % (fw_signature, port, slaveid))
    downloaded_fw = fw_downloader.RemoteFileWatcher(mode='fw', branch_name='').download(fw_signature, version='latest')
    flash_in_bootloader(downloaded_fw, slaveid, port, False)


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
