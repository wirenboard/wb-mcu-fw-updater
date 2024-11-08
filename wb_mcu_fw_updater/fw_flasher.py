#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os

import six
from tqdm import tqdm

from wb_modbus import bindings, minimalmodbus
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


class ParsedWBFW:
    INFO_BLOCK_LENGTH_U16_REGS = 16
    DATA_BLOCK_LENGTH_U16_REGS = 68

    def __init__(self, wbfw_fpath):
        self.fpath = wbfw_fpath
        self._info_values = []
        self._data_chunks = []
        self.parse_wbfw(wbfw_fpath)

    def __str__(self):
        return f"{self.fpath} ({len(self._data_chunks)} data chunks)"

    @property
    def info(self):
        return self._info_values

    @property
    def data_chunks(self):
        return self._data_chunks

    def parse_wbfw(self, fw_fpath):
        """
        Converting fw file contents to a bootloader-suitable format:
            - info: a row of u16 regs
            - data: N chunks of u16-regs rows
        """
        bs = int(os.path.getsize(fw_fpath))
        if bs % 2:
            raise IncorrectFwError(f"Fw file should be even-bytes long! Got {fw_fpath} ({bs}b)")

        u16_regs = []
        with open(fw_fpath, "rb") as fp:
            raw_bytes = fp.read()
            bytestr = str(raw_bytes, encoding="latin1")
            try:
                # pylint: disable=protected-access
                u16_regs = minimalmodbus._bytestring_to_valuelist(bytestr, int(bs / 2))
            except (TypeError, ValueError) as e:
                raise IncorrectFwError from e

        self._info_values, data_values = (
            u16_regs[: self.INFO_BLOCK_LENGTH_U16_REGS],
            u16_regs[self.INFO_BLOCK_LENGTH_U16_REGS :],
        )
        if len(self._info_values) != self.INFO_BLOCK_LENGTH_U16_REGS:
            raise IncorrectFwError(
                f"Info block size should be {self.INFO_BLOCK_LENGTH_U16_REGS} regs!"
                f"Got {len(self._info_values)} instead"
                f"\nRaw regs: {self._info_values}"
            )
        self._data_chunks = [
            data_values[i : i + self.DATA_BLOCK_LENGTH_U16_REGS]
            for i in range(0, len(data_values), self.DATA_BLOCK_LENGTH_U16_REGS)
        ]


class ModbusInBlFlasher:
    """
    Interacting with WirenBoard Modbus device's bootloader:
        flashing .wbfw files
        erasing device's connection params (slaveid, parity, stopbits, baudrate)
        erasing all device's settings (including connection params)
    """

    INFO_BLOCK_START = 0x1000
    DATA_BLOCK_START = 0x2000

    # u16 holdings (available only in bootloader; write "1" to perform a cmd)
    UART_SETTINGS_RESET_REG = 1000  # in-bl only
    EEPROM_ERASE_REG = 1001  # in-bl only

    GET_FREE_SPACE_FLASHFS_REG = 1003

    def __init__(  # pylint: disable=too-many-arguments
        self,
        addr,
        port,
        response_timeout,
        bd=9600,
        parity="N",
        stopbits=2,
        instrument=StopbitsTolerantInstrument,
    ):
        self.instrument = bindings.WBModbusDeviceBase(
            addr, port, bd, parity, stopbits, instrument=instrument, foregoing_noise_cancelling=True
        )
        self._actual_response_timeout = response_timeout
        self.instrument.set_response_timeout(self._actual_response_timeout)

    def _send_info(self, regs_row):
        """
        Writing INFO block as u16 regs
        Writing correct INFO block triggers some in-device hidden magic (leads to delay in device's response)
        """
        try:
            self.instrument.set_response_timeout(
                self._actual_response_timeout + self.instrument.BOOTLOADER_INFOBLOCK_MAGIC_TIMEOUT
            )
            self.instrument.write_u16_regs(self.INFO_BLOCK_START, regs_row)
        except minimalmodbus.IllegalRequestError as e:
            six.raise_from(NotInBootloaderError, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            six.raise_from(FlashingError, e)
        finally:
            self.instrument.set_response_timeout(self._actual_response_timeout)

    def _send_data(self, chunks):
        """
        Writing DATA block as u16 regs (split into fixed length chunks)
        """
        # Due to bootloader's behaviour, actual flashing failure is current-chunk failure + next-chunk failure
        has_previous_chunk_failed = False
        for chunk in tqdm(chunks, ascii=True, dynamic_ncols=True, bar_format="{l_bar}{bar}|{n}/{total}"):
            try:
                self.instrument.write_u16_regs(
                    self.DATA_BLOCK_START, chunk
                )  # retries wb_modbus.ALLOWED_UNSUCCESSFULL_TRIES times
                has_previous_chunk_failed = False
            except minimalmodbus.ModbusException as e:
                if has_previous_chunk_failed:
                    six.raise_from(FlashingError, e)
                else:
                    has_previous_chunk_failed = True
                    continue

        # pylint: disable=protected-access
        if has_previous_chunk_failed and self.instrument._has_bootloader_answered():
            raise FlashingError(
                "Flashing has failed at last frame (device remains in bootloader). Check device's connection!"
            )

    def perform_bootloader_cmd(self, reg):
        try:
            self.instrument.write_u16(reg, 1)
        except minimalmodbus.IllegalRequestError as e:
            six.raise_from(NotInBootloaderError, e)
        except minimalmodbus.ModbusException as e:
            six.raise_from(BootloaderCmdError, e)

    def reset_uart(self):
        logger.debug("Resetting uart params")
        self.perform_bootloader_cmd(self.UART_SETTINGS_RESET_REG)

    def reset_eeprom(self):
        logger.debug("Resetting all device's settings")
        self.perform_bootloader_cmd(self.EEPROM_ERASE_REG)

    def is_userdata_preserved(self, parsed_wbfw: ParsedWBFW):
        """
        If device's flashfs has not enough space, user data (such as ir commands)
        will be erased to perform fw update
        """
        device_str = f"{self.instrument.slaveid}, {self.instrument.port}"
        try:
            available_chunks_fs = self.instrument.read_u16(self.GET_FREE_SPACE_FLASHFS_REG)
            logger.debug("Device (%s) has available space of %d chunks", device_str, available_chunks_fs)
        except minimalmodbus.ModbusException:
            logger.error("Device (%s) has too old bootloader to save user data!", device_str)
            return False
        return available_chunks_fs > len(parsed_wbfw.data_chunks)

    def flash_in_bl(self, parsed_wbfw: ParsedWBFW):
        """
        Writing fw to device as u16 regs; device should be in bootloader mode!
        """
        logger.info("Flashing %s", str(parsed_wbfw))
        self._send_info(parsed_wbfw.info)
        self._send_data(parsed_wbfw.data_chunks)
