import subprocess
import os
import logging
from distutils.spawn import find_executable
from . import die


class WBFWFlasher(object):
    """
    A python-wrapper around wb-mcu-fw-flasher binary (writes *.wbfw files over serial port).
    Device is assumed to be in bootloader mode already!
    """
    _EXEC_FNAME = 'wb-mcu-fw-flasher'

    _LAUNCHKEYS = {
        'port' : '-d',
        'slaveid' : '-a',
        'fw_file' : '-f',
        'erase_settings' : '-e'
    }

    def __init__(self, slaveid, port, restore_defaults=False):
        """
        A subprocess's args list is constructed at init.

        :param slaveid: device's slave address
        :type slaveid: int
        :param port: serial port, firmware will be sent over
        :type port: str
        :param restore_defaults: all uart settings could be reset at flashing, defaults to False
        :type restore_defaults: bool, optional
        """
        exec_path = find_executable(self._EXEC_FNAME)
        if exec_path:
            self.executable = exec_path
            self.cmd_args = [exec_path, self._LAUNCHKEYS['port'], port, self._LAUNCHKEYS['slaveid'], str(slaveid)]
            if restore_defaults:
                self.cmd_args.append(self._LAUNCHKEYS['erase_settings'])
        else:
            die('Executable path for %s not found!' % self._EXEC_FNAME)

    def flash(self, fpath):
        """
        Flashing a .wbfw file via calling already constructed command.

        :param fpath: path to wbfw file
        :type fpath: str
        """
        if not os.path.exists(fpath):
            die('%s not found!')
        self.cmd_args.extend([self._LAUNCHKEYS['fw_file'], fpath])
        logging.debug('Will run:\n%s' % ' '.join(self.cmd_args))
        try:
            subprocess.check_call(self.cmd_args, shell=False)
        except subprocess.CalledProcessError as e:
            die('FAILED!')