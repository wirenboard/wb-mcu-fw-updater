#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
import logging
import time
from itertools import product
from functools import wraps
from . import minimalmodbus, ALLOWED_UNSUCCESSFUL_TRIES, CLOSE_PORT_AFTER_EACH_CALL, ALLOWED_PARITIES, ALLOWED_BAUDRATES, ALLOWED_STOPBITS


minimalmodbus.CLOSE_PORT_AFTER_EACH_CALL = CLOSE_PORT_AFTER_EACH_CALL


def force(errtype=IOError, tries=ALLOWED_UNSUCCESSFUL_TRIES):
    """
    A decorator, handling accidential connection errors on bus.

    :param errtypes: Error types, minimalmodbus is raising, defaults to (IOError, ValueError)
    :type errtypes: tuple, optional
    :param tries: number of tries after raising error from errtypes, defaults to ALLOWED_UNSUCCESSFUL_TRIES
    :type tries: int, optional
    """
    def real_decorator(f):
        def wrapper(*args, **kwargs):
            for _ in range(tries):
                try:
                    return f(*args, **kwargs)
                except errtype as e:
                    continue
            else:
                raise e
        return wrapper
    return real_decorator


class MinimalModbusAPIWrapper(object):
    """
    A generic wrapper around minimalmodbus's api. Handles connection errors; Allows changing serial connection settings on-the-fly.
    """
    def __init__(self, addr, port, settings):
        self.device = minimalmodbus.Instrument(port, addr)
        self.set_port_settings_raw(settings)

    def set_port_settings_raw(self, settings_dict):
        """
        Setting serial settings (baudrate, parity, etc...) on already intialized instrument.

        :param settings_dict: pyserial's port settings dictionary
        :type settings_dict: dict
        """
        self.settings = settings_dict
        self.device.serial.apply_settings(self.settings)

    def set_port_settings(self, baudrate, parity, stopbits):
        settings = {
            'baudrate' : int(baudrate),
            'parity' : parity,
            'stopbits' : int(stopbits)
        }
        self.set_port_settings_raw(settings)

    @force()
    def read_bit(self, addr):
        """
        Reading single discrete input register.

        :param addr: register address
        :type addr: int
        :return: register value (0 or 1)
        :rtype: int
        """
        return self.device.read_bit(addr, 2)

    @force()
    def write_bit(self, addr, value):
        self.device.write_bit(addr, value, 5)

    @force()
    def read_bits(self, addr, length):
        return self.device.read_bits(addr, length, 2)

    @force()
    def write_bits(self, addr, values_list):
        self.device.write_bits(addr, values_list)

    @force()
    def read_u16(self, addr):
        return self.device.read_register(addr, 0, 3, signed=False)

    @force()
    def read_s16(self, addr):
        return self.device.read_register(addr, 0, 3, signed=True)

    @force()
    def write_u16(self, addr, value):
        self.device.write_register(addr, value, 0, 6, signed=False)

    @force()
    def write_u16_regs(self, beginning, values):
        self.device.write_registers(beginning, values)

    @force()
    def read_u16_holdings(self, beginning, number_of_regs):
        return self.device.read_registers(beginning, number_of_regs, 3)

    @force()
    def read_u16_inputs(self, beginning, number_of_regs):
        return self.device.read_registers(beginning, number_of_regs, 4)

    @force()
    def write_s16(self, addr, value):
        self.device.write_register(addr, value, 0, 6, signed=True)

    @force()
    def read_u32_big_endian(self, addr, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        return self.device.read_long(addr, 3, signed=False, byteorder=order)

    @force()
    def read_u32_little_endian(self, addr, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        return self.device.read_long(addr, 3, signed=False, byteorder=order)

    @force()
    def read_s32_big_endian(self, addr, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        return self.device.read_long(addr, 3, signed=True, byteorder=order)

    @force()
    def read_s32_little_endian(self, addr, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        return self.device.read_long(addr, 3, signed=True, byteorder=order)

    @force()
    def write_u32_big_endian(self, addr, value, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        self.device.write_long(addr, value, signed=False, byteorder=order)

    @force()
    def write_u32_little_endian(self, addr, value, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        self.device.write_long(addr, value, signed=False, byteorder=order)

    @force()
    def write_s32_big_endian(self, addr, value, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        self.device.write_long(addr, value, signed=True, byteorder=order)

    @force()
    def write_s32_little_endian(self, addr, value, byteswap=False):
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        self.device.write_long(addr, value, signed=True, byteorder=order)

    @force()
    def read_string(self, addr, regs_lenght):
        ret = self.device.read_string(addr, regs_lenght, 3)
        return str(ret).replace('\x00', '')


class WBModbusDeviceBase(MinimalModbusAPIWrapper):
    """
    Common modbus-bindings for WirenBoard devices.
    """
    COMMON_REGS_MAP = {
        'uptime' : 104,
        'baudrate' : 110,
        'parity' : 111,
        'stopbits' : 112,
        'reboot' : 120,
        'v_in' : 121,
        'slaveid' : 128,
        'reboot_to_bootloader' : 129,
        'device_signature' : 200,
        'fw_signature' : 290,
        'fw_version' : 250,
        'serial_number' : 270,
        'bootloader_version' : 330
    }

    def __init__(self, addr, port, settings={
        'baudrate' : 9600,
        'parity' : 'N',
        'stopbits' : 2,
        'bytesize' : 8,
        'timeout' : 0.1,
        'write_timeout' : 2.0
    }):
        super(WBModbusDeviceBase, self).__init__(addr, port, settings)
        self.slaveid = addr
        self.port = port

    def find_uart_settings(method_to_decorate):
        """
        WirenBoard devices support a determinate set of UART params. So, trying to perform a method, iterating over all allowed UART settings.
        If succeed, successful uart settings remain on current instrument.

        :param method_to_decorate: a method, containing any operation with minimalmodbus's instrument
        :type method_to_decorate: func
        """
        @wraps(method_to_decorate)
        def wrapper(self, *args, **kwargs):
            allowed_parities = ALLOWED_PARITIES.keys()
            for settings in product(ALLOWED_BAUDRATES, allowed_parities, ALLOWED_STOPBITS):
                try:
                    return method_to_decorate(self, *args, **kwargs)
                except IOError:
                    self.set_port_settings(*settings)
                    logging.debug('Updated UART settings: %s' % str(settings))
            else:
                raise RuntimeError('All UART settings were not successful! Check device slaveid/power!')
        return wrapper

    def _validate_param(self, param, sequence):
        if param not in sequence:
            raise RuntimeError('Unsupported param %s! Try one of: %s' % (str(param), ', '.join(map(str, sequence))))

    def get_serial_number(self):
        """
        WirenBoard device's serial number is unique and stored in uint32 modbus reg.

        :return: serial number of device
        :rtype: int
        """
        return self.read_u32_big_endian(self.COMMON_REGS_MAP['serial_number'])

    def get_serial_number_map(self):
        int_values = self.read_u16_inputs(self.COMMON_REGS_MAP['serial_number'], 2)
        return ((int_values[0] % 256) * 65536) + int_values[1]

    def write_serial_number(self, sn):
        self.write_u32_big_endian(self.COMMON_REGS_MAP['serial_number'], int(sn))

    @find_uart_settings
    def get_rom_version(self):
        fw_version_regs_length = 8
        return self.read_string(self.COMMON_REGS_MAP['fw_version'], fw_version_regs_length).strip()

    def get_slave_addr(self):
        return self.read_u16(self.COMMON_REGS_MAP['slaveid'])

    @find_uart_settings
    def set_slave_addr(self, addr):
        """
        Trying to write modbus slaveid to device's reg. Checking success via initializing a new instrument with uart settings of a previous one and the new slaveid. Updating current instrument instance with set slaveid, if succeed.

        :param addr: desired slaveid
        :type addr: int
        """
        to_write = int(addr)
        reg = self.COMMON_REGS_MAP['slaveid']
        try:
            # self.device.write_register(reg, to_write, 0, 6, False)
            self.write_u16(reg, to_write)
        except IOError:
            pass
        checking_device = MinimalModbusAPIWrapper(to_write, self.port, self.settings)
        checking_device.read_u16(self.COMMON_REGS_MAP['slaveid']) #Raises IOError, if <to_write> was not written
        self.device = checking_device.device #Updating current instrument

    def set_baudrate(self, bd):
        self._validate_param(bd, ALLOWED_BAUDRATES)
        to_write = int(bd / 100)
        self.write_u16(self.COMMON_REGS_MAP['baudrate'], to_write)
        serial_settings = {
            'baudrate' : bd
        }
        self.set_port_settings_raw(serial_settings)

    def set_parity(self, parity):
        self._validate_param(parity, ALLOWED_PARITIES)
        to_write = ALLOWED_PARITIES[parity]
        self.write_u16(self.COMMON_REGS_MAP['parity'], to_write)
        serial_settings = {
            'parity' : parity
        }
        self.set_port_settings_raw(serial_settings)

    def set_stopbits(self, stopbits):
        self._validate_param(stopbits, ALLOWED_STOPBITS)
        self.write_u16(self.COMMON_REGS_MAP['stopbits'], stopbits)
        serial_settings = {
            'stopbits' : stopbits
        }
        self.set_port_settings_raw(serial_settings)

    @find_uart_settings
    def get_device_signature(self):
        coding = 'utf-8'
        signature_regs_length = 6
        ret = self.read_string(self.COMMON_REGS_MAP['device_signature'], signature_regs_length)
        return ret.encode().decode(coding).strip() #Python 2/3 compatibility

    @find_uart_settings
    def get_fw_signature(self):
        coding = 'utf-8'
        signature_regs_length = 11
        ret = self.read_string(self.COMMON_REGS_MAP['fw_signature'], signature_regs_length)
        return ret.encode().decode(coding).strip() #Python 2/3 compatibility

    @find_uart_settings
    def get_bootloader_version(self):
        bootloader_version_regs_length = 7
        return self.read_string(self.COMMON_REGS_MAP['bootloader_version'], bootloader_version_regs_length).strip()

    def get_uptime(self):
        reg = self.COMMON_REGS_MAP['uptime']
        return self.read_u32_big_endian(reg)

    def get_v_in(self):
        multiplier = 1E-3 #value is stored in mV
        ret = self.read_u16(self.COMMON_REGS_MAP['v_in'])
        return ret * multiplier

    def soft_reboot(self, bootloader_timeout=3):
        to_write = 1
        uptime_before = self.get_uptime()
        try:
            self.device.write_register(self.COMMON_REGS_MAP['reboot'], to_write, 0, 6, False)
        except IOError:
            pass #Device has rebooted and doesn't send responce (Fixed in latest FWs)
        time.sleep(bootloader_timeout)
        uptime_after = self.get_uptime()
        if uptime_after > uptime_before:
            raise RuntimeError('Device has not rebooted!')

    def reboot_to_bootloader(self):
        to_write = 1
        self.get_slave_addr() #To ensure, device has connection
        try:
            self.device.write_register(self.COMMON_REGS_MAP['reboot_to_bootloader'], to_write, 0, 6, False)
        except IOError:
            pass #Device has rebooted and doesn't send responce (Fixed in latest FWs)
        finally:
            time.sleep(1) #Delay before going to bootloader
        try:
            self.device.read_register(self.COMMON_REGS_MAP['slaveid'], 0, 3, False)
            raise RuntimeError('Device has not rebooted to bootloader!')
        except IOError:
            pass #Device is in bootloader mode and doesn't responce

    def write_port_settings(self, baudrate, parity, stopbits):
        """
        bd, parity and stopbits regs are mapped consistently (110, 111, 112)
        Writing all settings per one message
        """
        message = map(int, [(baudrate / 100), parity, stopbits])
        self.device.write_registers(self.COMMON_REGS_MAP['baudrate'], message)

