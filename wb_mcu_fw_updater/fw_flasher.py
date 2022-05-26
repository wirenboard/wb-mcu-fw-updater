#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import six
from tqdm import tqdm
from wb_modbus import minimalmodbus, bindings
from wb_modbus.instruments import StopbitsTolerantInstrument
from . import logger


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

    MINIMAL_RESPONSE_TIMEOUT = 5.0  # should be relatively huge (for wireless devices)

    def __init__(self, addr, port, bd=9600, parity='N', stopbits=2, response_timeout=1.0):
        self.instrument = bindings.WBModbusDeviceBase(addr, port, bd, parity, stopbits, instrument=StopbitsTolerantInstrument, foregoing_noise_cancelling=True)
        self._actual_response_timeout = max(self.MINIMAL_RESPONSE_TIMEOUT, response_timeout)
        self.instrument.set_response_timeout(self._actual_response_timeout)

    def _read_to_u16s(self, fw_fpath):
        """
        converting fw file contents to a row of u16 modbus regs
        """
        coding = 'latin1'

        bs = int(os.path.getsize(fw_fpath))
        if bs % 2:
            raise IncorrectFwError("Fw file should be even-bytes long!\nGot %s (%db)" % (fw_fpath, bs))

        with open(fw_fpath, 'rb') as fp:
            raw_bytes = fp.read()
            if six.PY2:
                bytestr = raw_bytes.decode(coding).encode(coding)
            else:
                bytestr = str(raw_bytes, encoding=coding)
            try:
                return minimalmodbus._bytestring_to_valuelist(bytestr, int(bs / 2))  # u16
            except Exception as e:
                six.raise_from(IncorrectFwError, e)

    def _send_info(self, regs_row):
        """
        Writing INFO block as u16 regs
        Writing correct INFO block triggers some in-device hidden magic (leads to delay in device's response)
        """
        if len(regs_row) != self.INFO_BLOCK_LENGTH:
            raise IncorrectFwError("Info block size should be %d regs! Got %d instead\nRaw regs: %s" % (self.INFO_BLOCK_LENGTH, len(regs_row), str(regs_row)))

        info_block_delay = 1.0  # bootloader needs some additional time to perform info-command-magic

        try:
            self.instrument.set_response_timeout(self._actual_response_timeout + info_block_delay)
            self.instrument.write_u16_regs(self.INFO_BLOCK_START, regs_row)
        except minimalmodbus.IllegalRequestError as e:
            six.raise_from(NotInBootloaderError, e)
        except Exception as e:
            six.raise_from(FlashingError, e)
        finally:
            self.instrument.set_response_timeout(self._actual_response_timeout)

    def _send_data(self, regs_row):
        """
        Writing DATA block as u16 regs (split into fixed length chunks)
        """
        chunk_size = self.DATA_BLOCK_LENGTH  # bootloader accepts only fixed-length chunks
        chunks = [regs_row[i:i+chunk_size] for i in range(0, len(regs_row), chunk_size)]

        _has_previous_chunk_failed = False  # Due to bootloader's behaviour, actual flashing failure is current-chunk failure + next-chunk failure
        for chunk in tqdm(chunks, ascii=True, dynamic_ncols=True, bar_format="{l_bar}{bar}|{n}/{total}"):
            try:
                self.instrument.write_u16_regs(self.DATA_BLOCK_START, chunk)  # retries wb_modbus.ALLOWED_UNSUCCESSFULL_TRIES times
                _has_previous_chunk_failed = False
            except minimalmodbus.ModbusException as e:
                if _has_previous_chunk_failed:
                    six.raise_from(FlashingError, e)
                else:
                    _has_previous_chunk_failed = True
                    continue

        if _has_previous_chunk_failed and self.instrument._has_bootloader_answered():
            raise FlashingError("Flashing has failed at last frame (device remains in bootloader). Check device's connection!")

    def _perform_bootloader_cmd(self, reg):
        try:
            self.instrument.write_u16(reg, 1)
        except minimalmodbus.IllegalRequestError as e:
            six.raise_from(NotInBootloaderError, e)
        except minimalmodbus.ModbusException as e:
            six.raise_from(BootloaderCmdError, e)

    def reset_uart(self):
        logger.debug("Resetting uart params")
        self._perform_bootloader_cmd(self.UART_SETTINGS_RESET_REG)

    def reset_eeprom(self):
        logger.debug("Resetting all device's settings")
        self._perform_bootloader_cmd(self.EEPROM_ERASE_REG)

    def flash_in_bl(self, fw_fpath):
        """
        Writing fw to device as u16 regs; device should be in bootloader mode!
        """
        fw_as_regs = self._read_to_u16s(fw_fpath)
        info_block, data_block = fw_as_regs[:self.INFO_BLOCK_LENGTH], fw_as_regs[self.INFO_BLOCK_LENGTH:]

        logger.info("Flashing %s", fw_fpath)
        self._send_info(info_block)
        self._send_data(data_block)
