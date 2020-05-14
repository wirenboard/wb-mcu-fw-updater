#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import json
import subprocess
from distutils.version import LooseVersion
from . import fw_flasher, fw_downloader, die, PYTHON2, CONFIG
import wb_modbus

wb_modbus.ALLOWED_UNSUCCESSFUL_TRIES = CONFIG['ALLOWED_UNSUCCESSFUL_MODBUS_TRIES']

from wb_modbus.bindings import WBModbusDeviceBase, close_all_modbus_ports


if PYTHON2:
    input_func = raw_input
else:
    input_func = input


def ask_user(message):
    """
    Asking user before potentionally dangerous action.

    :param message: will be printed to user
    :type message: str
    :return: is user sure or not
    :rtype: bool
    """
    message_str = '*** %s [Y/N] *** ' % (message)
    ret = input_func(message_str)
    return ret.upper().startswith('Y')


def compare_semver(first, second):
    """
    Comparing versions strings in semver.
    Second is converted implicitly via LooseVersion.

    :return: is first semver > second
    :rtype: bool
    """
    return LooseVersion(first) > second


def _parse_uart_params_str(uart_params_str, delimiter='-'):
    baudrate, parity, stopbits = uart_params_str.strip().split(delimiter)
    return [int(baudrate), parity, int(stopbits)]


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


def get_devices_on_driver(driver_config_fname):
    """
    Parsing a driver's config file to get ports, their uart params and devices, connected to.

    :return: {<port_name> : {'devices' : [devices_on_port], 'uart_params' : [uart_params_of_port]}}
    :rtype: dict
    """
    found_devices = {}
    config_dict = json.load(open(driver_config_fname, 'r'))
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


def flash_in_bootloader(downloaded_fw_fpath, modbus_connection, erase_settings, response_timeout=2.0):
    # if modbus_connection.is_in_bootloader():  # TODO: fix later
    #     logging.debug('Device is in bootloader')
    # else:
    #     die("Seems, device is not in bootloader or is disconnected.")
    if erase_settings:
        if ask_user('All settings will be reset to defaults (1, 9600-8-N-2). Are you sure?'):
            pass
        else:
            die('Reset of settings was rejected')
    flasher = fw_flasher.WBFWFlasher(modbus_connection.port)
    flasher.flash(modbus_connection.slaveid, downloaded_fw_fpath, erase_settings, response_timeout)


def flash_alive_device(modbus_connection, mode, branch_name, specified_fw_version, force, erase_settings):
    if is_update_needed(modbus_connection, mode, branch_name) or force:
        fw_signature = modbus_connection.get_fw_signature()
        downloaded_fw = fw_downloader.RemoteFileWatcher(mode, branch_name=branch_name).download(fw_signature, specified_fw_version)
        modbus_connection.reboot_to_bootloader()
        try:
            flash_in_bootloader(downloaded_fw, modbus_connection, erase_settings)
        except subprocess.CalledProcessError as e:
            fpath = CONFIG['LAST_FW_SIGNATURE_FNAME']
            logging.debug('Saving %s to %s' % (fw_signature, fpath))
            with open(fpath, 'w') as f:
                f.write(fw_signature)
            logging.error('Flashing has failed!')
            die(e)


def _send_signal_to_driver(signal):
    """
    Use pausing/resuming of process, found by name (instead of killing/starting)
    to handle cases, like <wb-mqtt-serial -c config.conf>

    :type signal: str
    """
    if CONFIG['SERIAL_DRIVER_PROCESS_NAME']:
        cmd_str = 'killall %s %s' % (signal, CONFIG['SERIAL_DRIVER_PROCESS_NAME'])
        logging.debug('Will run: %s' % cmd_str)
        subprocess.call(cmd_str, shell=True)


def pause_driver():
    _send_signal_to_driver('-STOP')


def resume_driver():
    _send_signal_to_driver('-CONT')
