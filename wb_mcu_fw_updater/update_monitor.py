#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import json
import subprocess
from distutils.version import LooseVersion
import wb_modbus
from . import fw_flasher, fw_downloader, die, PYTHON2, CONFIG

wb_modbus.ALLOWED_UNSUCCESSFUL_TRIES = CONFIG['ALLOWED_UNSUCCESSFUL_MODBUS_TRIES']

from wb_modbus.bindings import WBModbusDeviceBase, find_uart_settings


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


class UpdateHandler(object):
    """
    A 'launcher' class, handling all update logic.
    """
    def __init__(self, mode, branch_name=''):
        self.downloader = fw_downloader.RemoteFileWatcher(mode, branch_name=branch_name)
        self.mode = mode

    def get_modbus_device_connection(self, port, slaveid=0):
        """
        Asking user before setting slaveid via broadcast connection.

        :param port: port, device connected to
        :type port: str
        :param slaveid: modbus address of device, defaults to 0
        :type slaveid: int, optional
        :return: minimalmodbus.Instrument instance
        """
        device = WBModbusDeviceBase(slaveid, port)
        if slaveid == 0:
            if ask_user('Will use broadcast id (0). Are ALL other devices disconnected from %s port?' % port):
                logging.warning('Trying to set slaveid %d' % CONFIG['SLAVEID_PLACEHOLDER'])
                device.set_slave_addr(CONFIG['SLAVEID_PLACEHOLDER']) # Finding uart settings here
            else:
                die('ALL other devices should be disconnected before!')
        return device

    def is_update_needed(self, modbus_connection):
        """
        Checking, whether update is needed or not by comparing version, stored in the device with remote.
        """
        meaningful_str = modbus_connection.get_fw_signature()
        latest_remote_version = self.downloader.get_latest_version_number(meaningful_str)
        current_version = modbus_connection.get_bootloader_version() if self.mode == 'bootloader' else modbus_connection.get_fw_version()
        if compare_semver(latest_remote_version, current_version):
            logging.info('Update is needed (local %s version: %s; remote version: %s)' % (self.mode, current_version, latest_remote_version))
            return True
        else:
            logging.info('Device has latest %s version (%s)!' % (self.mode, current_version))
            return False

    def get_fw_signature_by_model(self, modelname):
        """If there is no connection with device, fw_signature could be get via internal model_name <=> fw_signature conformity.

        :param modelname: a full device's model name (ex: WB-MR6HV/I)
        :type modelname: str
        :return: fw_signature of device
        :rtype: str
        """
        if modelname not in CONFIG['FW_SIGNATURES_PER_MODEL']:
            die('Model %s is unknown! Choose one from:\n%s' % (modelname, ', '.join(CONFIG['FW_SIGNATURES_PER_MODEL'].keys())))
        return CONFIG['FW_SIGNATURES_PER_MODEL'][modelname]

    def get_devices_on_driver(self):
        """
        Parsing a driver's config file to get ports and devices, connected to.

        :return: {<port_name> : [devices on this port]}
        :rtype: dict
        """
        found_devices = {}
        config_dict = json.load(open(CONFIG['SERIAL_DRIVER_CONFIG_FNAME'], 'r'))
        for port in config_dict['ports']:
            port_name = port['path']
            devices_on_port = []
            for serial_device in port['devices']:
                device_name = serial_device['device_type']
                slaveid = serial_device['slave_id']
                devices_on_port.append([device_name, int(slaveid)])
            if devices_on_port:
                found_devices.update({port_name : devices_on_port})
        if found_devices:
            return found_devices
        else:
            die('No devices has found in %s' % CONFIG['SERIAL_DRIVER_CONFIG_FNAME'])


def flash_in_bootloader(updater, port, slaveid, fw_signature, specified_fw_version, erase_settings):
    if erase_settings:
        if ask_user('All settings will be reset to defaults (1, 9600-8-N-2). Are you sure?'):
            pass
        else:
            die('Reset of settings was rejected')
    download_fpath = updater.downloader.download(fw_signature, specified_fw_version)
    flasher = fw_flasher.WBFWFlasher(port)
    flasher.flash(slaveid, download_fpath, erase_settings)


def flash_alive_device(updater, port, slaveid, specified_fw_version, force, erase_settings):
    modbus_connection = updater.get_modbus_device_connection(port, slaveid)
    if updater.is_update_needed(modbus_connection) or force:
        fw_signature = modbus_connection.get_fw_signature()
        modbus_connection.reboot_to_bootloader()
        flash_in_bootloader(updater, port, modbus_connection.slaveid, fw_signature, specified_fw_version, erase_settings)


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
