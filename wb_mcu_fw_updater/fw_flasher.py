import subprocess
import os
import logging
from copy import copy
from distutils.spawn import find_executable
from . import die, CONFIG


class WBFWFlasher(object):
    """
    A python-wrapper around wb-mcu-fw-flasher binary (writes *.wbfw files over serial port).
    Device is assumed to be in bootloader mode already!
    """

    _LAUNCHKEYS = {
        'port' : '-d',
        'slaveid' : '-a',
        'fw_file' : '-f',
        'erase_settings' : '-e',
        'response_timeout' : '-t'
    }

    def __init__(self, port):
        exec_path = find_executable(CONFIG['FLASHER_FNAME'])
        if exec_path:
            self.executable = exec_path
            self.compulsory_cmd_args = [exec_path, self._LAUNCHKEYS['port'], port]
        else:
            die('Executable path for %s not found!' % CONFIG['FLASHER_FNAME'])

    def _run_cmd(self, args_list):
        logging.debug('Will run:\n%s' % ' '.join(args_list))
        subprocess.check_call(args_list, shell=False)

    def _make_args_list(self, slaveid, additional_args_list):
        cmd_args = copy(self.compulsory_cmd_args)
        cmd_args.extend([self._LAUNCHKEYS['slaveid'], str(slaveid)])
        cmd_args.extend(additional_args_list)
        return cmd_args

    def flash(self, slaveid, fpath, restore_defaults=False):
        """Flashing .wbfw file via constructing and calling wb-mcu-fw-flasher command.

        :param slaveid: slave addr of device
        :type slaveid: int
        :param fpath: .wbfw file
        :type fpath: str
        :param restore_defaults: will all settings be erased during flashing or not, defaults to False
        :type restore_defaults: bool, optional
        """
        if not os.path.exists(fpath):
            die('%s not found!')
        args_list = self._make_args_list(slaveid, [self._LAUNCHKEYS['fw_file'], fpath])
        if restore_defaults:
            args_list.append(self._LAUNCHKEYS['erase_settings'])
        try:
            self._run_cmd(args_list)
        except subprocess.CalledProcessError as e:
            die('FAILED!')

    def probe_connection(self, slaveid, desired_response_timeout=0.2):
        """Assumed, device in bootloader and slaveid is unknown. Launching <erase_eeprom> cmd with short response timeout.

        :param slaveid: slave address of device
        :type slaveid: int
        :param desired_response_timeout: timeout, wb-mcu-fw-flasher will wait response for, defaults to 0.2
        :type desired_response_timeout: float, optional
        :return: was probe successful, or not
        :rtype: bool
        """
        cmd_args = self._make_args_list(slaveid, [self._LAUNCHKEYS['erase_settings'], self._LAUNCHKEYS['response_timeout'], str(desired_response_timeout)])
        try:
            self._run_cmd(cmd_args)
            return True
        except subprocess.CalledProcessError:
            return False
