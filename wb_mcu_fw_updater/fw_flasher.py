#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import logging
import progressbar
from distutils.spawn import find_executable
from wb_modbus import minimalmodbus, bindings
from . import die, CONFIG, PYTHON2


class FlashingError(Exception):
    pass

class IncorrectFwError(FlashingError):
    pass

class NotInBootloaderError(FlashingError):
    pass

class BootloaderCmdError(FlashingError):
    pass


class ModbusInBlFlasher(object):
    """
    Interacting with WirenBoard Modbus device's bootloader:
        flashing .wbfw files
        erasing device's connection params (slaveid, parity, stopbits, baudrate)
        erasing all device's settings (including connection params)
    """
    INFO_BLOCK_START = 0x1000
    INFO_BLOCK_LENGTH = 16
    DATA_BLOCK_START = 0x2000
    DATA_BLOCK_LENGTH = 68

    # u16 holdings (available only in bootloader; write "1" to perform a cmd)
    UART_SETTINGS_RESET_REG = 1000  # in-bl only
    EEPROM_ERASE_REG = 1001  # in-bl only

    _SERIAL_TIMEOUT = 2.0

    def __init__(self, addr, port, bd=9600, parity='N', stopbits=2):
        self.instrument = bindings.WBModbusDeviceBase(addr, port, bd, parity, stopbits)
        self.instrument.device.serial.timeout = self._SERIAL_TIMEOUT

    def _read_to_u16s(self, fw_fpath):
        """
        converting fw file contents to a row of u16 modbus regs
        """
        bs = int(os.path.getsize(fw_fpath))
        if bs % 2:
            raise IncorrectFwError("Fw file should be even-bytes long!\nGot %s (%db)" % (fw_fpath, bs))

        with open(fw_fpath, 'rb') as fp:
            raw_bytes = fp.read()
            if not PYTHON2:
                raw_bytes = str(raw_bytes, encoding='latin1')
            try:
                return minimalmodbus._bytestring_to_valuelist(raw_bytes, int(bs / 2))  # u16
            except Exception as e:
                raise IncorrectFwError(e)

    def _send_info(self, regs_row):
        """
        Writing INFO block as u16 regs
        Writing correct INFO block triggers some in-device hidden magic (leads to delay in device's response)
        """
        if len(regs_row) != self.INFO_BLOCK_LENGTH:
            raise IncorrectFwError("Info block size should be %d regs! Got %d instead\nRaw regs: %s" % (self.INFO_BLOCK_LENGTH, len(regs_row), str(regs_row)))

        try:
            self.instrument.write_u16_regs(self.INFO_BLOCK_START, regs_row)
        except minimalmodbus.IllegalRequestError as e:
            raise NotInBootloaderError(e)
        except Exception as e:
            raise FlashingError(e)

    def _send_data(self, regs_row, fname=''):
        """
        Writing DATA block as u16 regs (split into fixed length chunks)
        """
        chunk_size = self.DATA_BLOCK_LENGTH  # bootloader accepts only fixed-length chunks
        chunks = [regs_row[i:i+chunk_size] for i in range(0, len(regs_row), chunk_size)]

        progress_bar = progressbar.ProgressBar(
            widgets=[progressbar.Percentage(), progressbar.Bar(left=" %s[" % fname, right="] "), progressbar.SimpleProgress()],
            term_width=79
        )

        for chunk in progress_bar(chunks):
            try:
                self.instrument.write_u16_regs(self.DATA_BLOCK_START, chunk)  # retries wb_modbus.ALLOWED_UNSUCCESSFULL_TRIES times
            except minimalmodbus.ModbusException as e:
                raise FlashingError(e)

    def _perform_bootloader_cmd(self, reg):
        try:
            self.instrument.write_u16(reg, 1)
        except minimalmodbus.IllegalRequestError as e:
            raise NotInBootloaderError(e)
        except minimalmodbus.ModbusException as e:
            raise BootloaderCmdError(e)

    def reset_uart(self):
        logging.debug("Resetting uart params")
        self._perform_bootloader_cmd(self.UART_SETTINGS_RESET_REG)

    def reset_eeprom(self):
        logging.debug("Resetting all device's settings")
        self._perform_bootloader_cmd(self.EEPROM_ERASE_REG)

    def flash_in_bl(self, fw_fpath):
        """
        Writing fw to device as u16 regs; device should be in bootloader mode!
        """
        fw_as_regs = self._read_to_u16s(fw_fpath)
        info_block, data_block = fw_as_regs[:self.INFO_BLOCK_LENGTH], fw_as_regs[self.INFO_BLOCK_LENGTH:]

        self._send_info(info_block)
        self._send_data(data_block, fname=fw_fpath)


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


    def flash(self, slaveid, fpath, restore_defaults=False, response_timeout=2.0, custom_bl_speed=None):
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
        if custom_bl_speed:
            cmd_args.extend(['-B', str(custom_bl_speed)])
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
