#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import subprocess
import logging
from distutils.spawn import find_executable
from . import die, CONFIG


class WBFWFlasher(object):
    """
    A python-wrapper around wb-mcu-fw-flasher binary (writes *.wbfw files over serial port).
    Device is assumed to be in bootloader mode already!
    """

    def __init__(self, port):
        exec_path = find_executable(CONFIG['FLASHER_FNAME'])
        if exec_path:
            self.known_cmd_part = '%s -d %s' % (exec_path, port)
        else:
            die('Executable %s not found!' % CONFIG['FLASHER_FNAME'])

    def flash(self, slaveid, fpath, restore_defaults=False):
        """Flashing .wbfw file via constructing and calling wb-mcu-fw-flasher command.

        :param slaveid: slave addr of device
        :type slaveid: int
        :param fpath: .wbfw file
        :type fpath: str
        :param restore_defaults: will all settings be erased during flashing or not, defaults to False
        :type restore_defaults: bool, optional
        """
        if 0 <= slaveid <= 247:
            pass
        else:
            die('Slaveid %d is not allowed!' % slaveid)
        if not os.path.exists(fpath):
            die('%s not found!' % fpath)
        cmd_str = '%s -a %d -f %s' % (self.known_cmd_part, slaveid, fpath)
        if restore_defaults:
            cmd_str += ' -e'
        logging.debug('Will run: %s' % cmd_str)
        subprocess.check_call(cmd_str, shell=True)
