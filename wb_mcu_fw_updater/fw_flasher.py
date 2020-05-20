#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import logging
from distutils.spawn import find_executable
from . import die, CONFIG, PYTHON2


class WBFWFlasher(object):
    """
    A python-wrapper around wb-mcu-fw-flasher binary (writes *.wbfw files over serial port).
    Device is assumed to be in bootloader mode already!
    """

    def __init__(self, port):
        exec_path = find_executable(CONFIG['FLASHER_EXEC_NAME'])
        if exec_path:
            self.known_cmd_args = [exec_path, '-d', port]
        else:
            die('Executable %s not found!' % CONFIG['FLASHER_EXEC_NAME'])
        if PYTHON2:
            self.out_buffer = sys.stdout
        else:
            self.out_buffer = sys.stdout.buffer


    def flash(self, slaveid, fpath, restore_defaults=False, response_timeout=2.0):
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
            die('FW file %s not found!' % fpath)
        cmd_args = self.known_cmd_args[::]
        cmd_args.extend(['-a', str(slaveid), '-f', fpath, '-t', str(response_timeout)])
        if restore_defaults:
            cmd_args.append('-e')
        logging.debug('Will run: %s' % str(cmd_args))
        proc = subprocess.Popen(args=cmd_args, stdout=subprocess.PIPE, bufsize=0)
        self._show_clean_output(proc)
        proc.stdout.close()
        retcode = proc.wait()
        if retcode:
            raise subprocess.CalledProcessError(retcode, cmd_args)

    def _show_clean_output(self, proc):
        """
        wb-mcu-fw-flasher produces a lot of annoying output.
        Catching proc's stdout and printing only "Sending data block <block> of <max_blocks>..." str.

        Py2/3 compatible by writing bytes to stdout buffer.
        """
        onebyte = lambda: proc.stdout.read(1)
        b_output = bytearray()
        for char in iter(onebyte, b''):
            b = ord(char)
            if b == ord('\n'):
                b_output = bytearray()
            if b == ord('\r'):
                b_output.insert(0, b)
                self.out_buffer.write(b_output)
                self.out_buffer.flush()
                b_output = bytearray()
            else:
                b_output.append(b)
        else:
            b_output = bytearray()
            b_output.append(ord('\n'))
            self.out_buffer.write(b_output)
            self.out_buffer.flush()
