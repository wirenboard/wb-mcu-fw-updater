#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
import time
from binascii import unhexlify
from copy import deepcopy
from itertools import product
from functools import wraps
from . import minimalmodbus, instruments, ALLOWED_UNSUCCESSFUL_TRIES, CLOSE_PORT_AFTER_EACH_CALL, ALLOWED_PARITIES, ALLOWED_BAUDRATES, ALLOWED_STOPBITS, DEBUG, WBMAP_MARKER, logger


class TooOldDeviceError(minimalmodbus.ModbusException):
    """
    Some Wiren Board devices do not support in-filed firmware upudate, because they haven't bootloader.
    """

class UARTSettingsNotFoundError(Exception):
    pass

def apply_serial_settings(f):
    """
    A decorator, applying actual settings to serial port before communication
    """
    @wraps(f)
    def wrapper(self, *args, **kwargs):
        self._set_port_settings_raw(self.settings)
        return f(self, *args, **kwargs)
    return wrapper

def force(retries=ALLOWED_UNSUCCESSFUL_TRIES):
    """
    A decorator, applying settings to serial port and handling accidential connection errors on bus.
    """
    errtypes = (minimalmodbus.ModbusException, ValueError)
    def real_decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            tries = kwargs.pop('retries', retries)
            thrown_exc = None
            f_args = [repr(a) for a in args]
            f_kwargs = ["%s=%s" % (k, repr(v)) for k, v in kwargs.items()]
            f_signature = "%s(%s)" % (f.__name__, ", ".join(f_args + f_kwargs))
            for i in range(tries):
                try:
                    return f(*args, **kwargs)
                except errtypes as e:
                    thrown_exc = e
                    logger.debug("f = %s not succeed (try %d/%d)", f_signature, i + 1, tries)
            else:
                if thrown_exc: #python3 wants exception to be defined already
                    raise thrown_exc
                else:
                    raise RuntimeError('Decorator has not returned! Something goes wrong!')
        return wrapper
    return real_decorator


def _debug_info(message):
    """
    Redirecting minimalmodbus's _print_out to logging debug

    :param message: minimalmodbus's debug messages
    :type message: str
    """
    minimalmodbus._check_string(message, description="string to print")
    logger.debug(message)


def close_all_modbus_ports():
    for serial_instance in minimalmodbus._serialports.values():
        if serial_instance.is_open:
            logger.debug('Closing serial instance: %s' % str(serial_instance))
            serial_instance.close()
        else:
            logger.debug('Serial instance %s has already closed' % serial_instance)


def _validate_param(param, sequence):
    if param not in sequence:
        raise RuntimeError('Unsupported param %s! Try one of: %s' % (str(param), ', '.join(map(str, sequence))))


class MinimalModbusAPIWrapper(object):
    """
    A generic wrapper around minimalmodbus's api. Handles connection errors;
    Allows changing serial connection settings on-the-fly;
    Redirects minimalmodbus's debug messages to logging.
    """
    def __init__(self, addr, port, baudrate, parity, stopbits, instrument=instruments.PyserialBackendInstrument, foregoing_noise_cancelling=False):
        minimalmodbus._print_out = _debug_info
        self.device = instrument(port, addr, debug=DEBUG, close_port_after_each_call=CLOSE_PORT_AFTER_EACH_CALL, foregoing_noise_cancelling=foregoing_noise_cancelling)
        self.slaveid = addr
        self.port = port
        self.set_port_settings(baudrate, parity, stopbits)

    def _set_port_settings_raw(self, settings_dict):
        """
        Setting serial settings (baudrate, parity, etc...) on already intialized instrument via updating pyserial's settings dict.

        :param settings_dict: pyserial's port settings dictionary
        :type settings_dict: dict
        """
        self.settings = settings_dict
        self.device.serial.apply_settings(self.settings)  # only sets params into serial's instance
        """
        Settings are writing to serial's fd (posix) at:
            - each port opening (before next call to device, if close_port_after_each_call param is set in Instrument);
            - calling serial._reconfigure_port() (raises exception, if fd is not valid)
        """
        if not self.device.close_port_after_each_call:
            self.device.serial._reconfigure_port()

    def set_port_settings(self, baudrate, parity, stopbits):
        """
        Setting baudrate, parity and stopbits to already initialized instrument.

        :param baudrate: serial port speed (baudrate)
        :type baudrate: int
        :param parity: serial port parity
        :type parity: str
        :param stopbits: serial port stopbits
        :type stopbits: int
        """
        for param, allowed_row in zip([baudrate, parity, stopbits], [ALLOWED_BAUDRATES, ALLOWED_PARITIES.keys(), ALLOWED_STOPBITS]):
            _validate_param(param, allowed_row)
        settings = {
            'baudrate' : int(baudrate),
            'parity' : parity,
            'stopbits' : int(stopbits)
        }
        self._set_port_settings_raw(settings)
        logger.debug("Set %s to %s", str(self.settings), self.port)

    @apply_serial_settings
    @force()
    def read_bit(self, addr):
        """
        Reading single discrete input register (stores 1 bit).

        :param addr: register address
        :type addr: int
        :return: register value (0 or 1)
        :rtype: int
        """
        return self.device.read_bit(addr, 2)

    @apply_serial_settings
    @force()
    def write_bit(self, addr, value):
        """
        Writing single coil register (stores 1 bit).

        :param addr: register address
        :type addr: int
        :param value: register value (could be 0 or 1)
        :type value: int
        """
        self.device.write_bit(addr, value, 5)

    @apply_serial_settings
    @force()
    def read_bits(self, addr, length):
        """
        Reading multiple consecutive discrete inputs (each one stores 1 bit) per one modbus message.

        :param addr: address of first register of a row
        :type addr: int
        :param length: number of registers, will be red
        :type length: int
        :return: a list of regs values
        :rtype: list
        """
        return self.device.read_bits(addr, length, 2)

    @apply_serial_settings
    @force()
    def write_bits(self, addr, values_list):
        """
        Writing multiple consecutive coils (each one stores 1 bit) per one modbus message.

        :param addr: address of first register of a row
        :type addr: int
        :param values_list: a list of registers values to write
        :type values_list: list
        """
        self.device.write_bits(addr, values_list)

    @apply_serial_settings
    @force()
    def read_u16(self, addr):
        """
        Reading single holding register (stores unsigned int16).

        :param addr: address of register
        :type addr: int
        :return: value, stored in register (only positive)
        :rtype: int
        """
        return self.device.read_register(addr, 0, 3, signed=False)

    @apply_serial_settings
    @force()
    def read_s16(self, addr):
        """
        Reading single holding register (stores signed int16).

        :param addr: address of register
        :type addr: int
        :return: value, stored in register (could be negative)
        :rtype: int
        """
        return self.device.read_register(addr, 0, 3, signed=True)

    @apply_serial_settings
    @force()
    def write_u16(self, addr, value):
        """
        Writing single holding register (stores unsigned int16).

        :param addr: address of register
        :type addr: int
        :param value: value, to write into register (only positive)
        :type value: int
        """
        self.device.write_register(addr, value, 0, 6, signed=False)

    @apply_serial_settings
    @force()
    def write_u16_regs(self, beginning, values):
        """
        Writing multiple consecutive holding registers (each one stores unsigned int16) per one modbus message.

        :param beginning: address of first register
        :type beginning: int
        :param values: a list of values to write to device
        :type values: list
        """
        self.device.write_registers(beginning, values)

    @apply_serial_settings
    @force()
    def read_u16_holdings(self, beginning, number_of_regs):
        """
        Reading multiple consecutive holding registers (each one stores unsigned int16) per one modbus message.

        :param beginning: address of first register
        :type beginning: int
        :param number_of_regs: numbers of registers to read
        :type number_of_regs: int
        :return: a list of registers values
        :rtype: list
        """
        return self.device.read_registers(beginning, number_of_regs, 3)

    @apply_serial_settings
    @force()
    def read_u16_inputs(self, beginning, number_of_regs):
        """
        Reading multiple consecutive input registers (each one stores unsigned int16) per one modbus message.

        :param beginning: address of first register
        :type beginning: int
        :param number_of_regs: numbers of registers to read
        :type number_of_regs: int
        :return: a list of registers values
        :rtype: list
        """
        return self.device.read_registers(beginning, number_of_regs, 4)

    @apply_serial_settings
    @force()
    def write_s16(self, addr, value):
        """
        Writing single holding register (stores signed int16).

        :param addr: address of register
        :type addr: int
        :param value: value, to write into register (only positive)
        :type value: int
        """
        self.device.write_register(addr, value, 0, 6, signed=True)

    @apply_serial_settings
    @force()
    def read_u32_big_endian(self, addr, byteswap=False):
        """
        Reading two consecutive 16 bit registers and interpreting value as one unsigned 32 bit integer with Big-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param byteswap: are bytes swapped or not, defaults to False
        :type byteswap: bool, optional
        :return: unsigned 32 bit integer
        :rtype: int
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        return self.device.read_long(addr, 3, signed=False, byteorder=order)

    @apply_serial_settings
    @force()
    def read_u32_little_endian(self, addr, byteswap=False):
        """
        Reading two consecutive 16 bit registers and interpreting value as one unsigned 32 bit integer with Little-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param byteswap: are bytes swapped or not, defaults to False
        :type byteswap: bool, optional
        :return: unsigned 32 bit integer
        :rtype: int
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        return self.device.read_long(addr, 3, signed=False, byteorder=order)

    @apply_serial_settings
    @force()
    def read_s32_big_endian(self, addr, byteswap=False):
        """
        Reading two consecutive 16 bit registers and interpreting value as one signed 32 bit integer with Big-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param byteswap: are bytes swapped or not, defaults to False
        :type byteswap: bool, optional
        :return: signed 32 bit integer
        :rtype: int
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        return self.device.read_long(addr, 3, signed=True, byteorder=order)

    @apply_serial_settings
    @force()
    def read_s32_little_endian(self, addr, byteswap=False):
        """
        Reading two consecutive 16 bit registers and interpreting value as one signed 32 bit integer with Little-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param byteswap: are bytes swapped or not, defaults to False
        :type byteswap: bool, optional
        :return: signed 32 bit integer
        :rtype: int
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        return self.device.read_long(addr, 3, signed=True, byteorder=order)

    @apply_serial_settings
    @force()
    def write_u32_big_endian(self, addr, value, byteswap=False):
        """
        Writing an unsigned 32 bit integer to two consecutive 16 bit holding registers with Big-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param value: value, will be written to regs
        :type value: int
        :param byteswap: will bytes be swapped or not, defaults to False
        :type byteswap: bool, optional
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        self.device.write_long(addr, value, signed=False, byteorder=order)

    @apply_serial_settings
    @force()
    def write_u32_little_endian(self, addr, value, byteswap=False):
        """
        Writing an unsigned 32 bit integer to two consecutive 16 bit holding registers with Little-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param value: value, will be written to regs
        :type value: int
        :param byteswap: will bytes be swapped or not, defaults to False
        :type byteswap: bool, optional
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        self.device.write_long(addr, value, signed=False, byteorder=order)

    @apply_serial_settings
    @force()
    def write_s32_big_endian(self, addr, value, byteswap=False):
        """
        Writing a signed 32 bit integer to two consecutive 16 bit holding registers with Big-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param value: value, will be written to regs
        :type value: int
        :param byteswap: will bytes be swapped or not, defaults to False
        :type byteswap: bool, optional
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_BIG_SWAP
        else:
            order = minimalmodbus.BYTEORDER_BIG
        self.device.write_long(addr, value, signed=True, byteorder=order)

    @apply_serial_settings
    @force()
    def write_s32_little_endian(self, addr, value, byteswap=False):
        """
        Writing a signed 32 bit integer to two consecutive 16 bit holding registers with Little-Endian byteorder.

        :param addr: address of first register
        :type addr: int
        :param value: value, will be written to regs
        :type value: int
        :param byteswap: will bytes be swapped or not, defaults to False
        :type byteswap: bool, optional
        """
        if byteswap:
            order = minimalmodbus.BYTEORDER_LITTLE_SWAP
        else:
            order = minimalmodbus.BYTEORDER_LITTLE
        self.device.write_long(addr, value, signed=True, byteorder=order)

    @apply_serial_settings
    @force()
    def read_string(self, addr, regs_lenght):
        """
        Reading a row of consecutive uint16 holding registers and interpreting row as a string.
        Wiren Board devices store a placeholder + char per one u16 reg (ex: '\x00A1' or '\xFFA1' in some roms)

        :param addr: address of first register
        :type addr: int
        :param regs_lenght: number of registers, will be red
        :type regs_lenght: int
        :return: a string with cut trailing null-bytes
        :rtype: str
        """
        empty_chars_placeholders = ('00', 'FF', ' ')
        ret = minimalmodbus._hexlify(self.device.read_string(addr, regs_lenght, 3))
        for placeholder in empty_chars_placeholders:  # Clearing a string to only meaningful bytes
            ret = ret.replace(placeholder, '')  # 'A1B2C3' bytes-only string
        return str(unhexlify(ret).decode('utf-8')).strip()


def auto_find_uart_settings(method_to_decorate):
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
                logger.debug('Trying serial port settings: %s' % str(settings))
        else:
            raise UARTSettingsNotFoundError('All serial port settings were not successful! Check device slaveid/power!')
    return wrapper


class WBModbusDeviceBase(MinimalModbusAPIWrapper):
    """
    Common modbus bindings for all WirenBoard devices.
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

    FIRMWARE_VERSION_LENGTH = 16  # 250-265 u16 regs
    DEVICE_SIGNATURE_LENGTH = 6  # 200-205 u16 regs
    FIRMWARE_SIGNATURE_LENGTH = 12  # 290-301 u16 regs
    BOOTLOADER_VERSION_LENGTH = 8  # 330-337 u16 regs

    BOOTLOADER_INFOBLOCK_MAGIC_TIMEOUT = 1.0  # Bl needs some time to perform info-block magic

    def __init__(self, addr, port, baudrate=9600, parity='N', stopbits=2, response_timeout=0.2, instrument=instruments.PyserialBackendInstrument, foregoing_noise_cancelling=False):
        super(WBModbusDeviceBase, self).__init__(addr=addr, port=port, baudrate=baudrate, parity=parity, stopbits=stopbits, instrument=instrument, foregoing_noise_cancelling=foregoing_noise_cancelling)
        self.set_response_timeout(response_timeout)
        self.instrument = instrument

    def set_response_timeout(self, response_timeout):
        self.response_timeout = response_timeout
        self.device.serial.timeout = response_timeout
        logger.debug("%s response_timeout -> %.2f", self.port, self.response_timeout)

    def find_uart_settings(self, probe_method_callable, *args, **kwargs):
        """
        Iterating over all allowed UART settings to find valid ones via launching connection's method.

        :param probe_method_callable: a minimalmodbus's Instrument instance
        :type probe_method_callable: func
        :raises UARTSettingsNotFoundError: all allowed uart settings were not successful
        :return: actual uart settings of connected device
        :rtype: dict
        """
        initial_uart_settings = deepcopy(self.settings)
        actual_uart_settings = []
        allowed_parities = ALLOWED_PARITIES.keys()
        for settings in product(ALLOWED_BAUDRATES, allowed_parities, ALLOWED_STOPBITS):
            try:
                probe_method_callable(*args, **kwargs)
                actual_uart_settings = deepcopy(self.settings)
                self._set_port_settings_raw(initial_uart_settings)
                return actual_uart_settings
            except IOError:
                logger.debug('Trying serial port settings: %s' % str(settings))
                self.set_port_settings(*settings)
                continue
        else:
            raise UARTSettingsNotFoundError('All serial port settings were not successful! Check device slaveid/power!')

    def get_serial_number(self):
        """
        WB-MAP* devices family calculate serial number, stored in the same regs, differently from other devices.

        :return: serial number of device
        :rtype: int
        """
        device_signature = str(self.get_device_signature())
        if WBMAP_MARKER.match(device_signature):
            logger.debug('Will calculate SN as WB-MAP*')
            return self._get_serial_number_map()
        else:
            return self.read_u32_big_endian(self.COMMON_REGS_MAP['serial_number'])

    def _get_serial_number_map(self):
        int_values = self.read_u16_inputs(self.COMMON_REGS_MAP['serial_number'], 2)
        return ((int_values[0] % 256) * 65536) + int_values[1]

    def get_fw_version(self):
        return self.read_string(self.COMMON_REGS_MAP['fw_version'], self.FIRMWARE_VERSION_LENGTH)

    def get_slave_addr(self):
        return self.read_u16(self.COMMON_REGS_MAP['slaveid'])

    def set_slave_addr(self, addr):
        """
        Trying to write modbus slaveid to device's reg. Checking success via initializing a new instrument with uart settings of a previous one and the new slaveid. Updating current instrument instance with set slaveid, if succeed.

        Typical usage:
        instrument = WBModbusDeviceBase(0, <port>)
        instrument.set_slave_addr(<desired_slaveid>)

        :param addr: desired slaveid
        :type addr: int
        """
        to_write = int(addr)
        reg = self.COMMON_REGS_MAP['slaveid']
        try:
            self.write_u16(reg, to_write)
        except minimalmodbus.ModbusException:
            pass
        baudrate, parity, stopbits = self.settings['baudrate'], self.settings['parity'], self.settings['stopbits']
        checking_device = MinimalModbusAPIWrapper(to_write, self.port, baudrate, parity, stopbits, self.instrument)
        checking_device.read_u16(self.COMMON_REGS_MAP['slaveid']) #Raises minimalmodbus.ModbusException, if <to_write> was not written
        self.device = checking_device.device #Updating current instrument
        self.slaveid = to_write

    def set_baudrate(self, bd):
        """
        Writing baudrate to device and updating UART settings of current instrument to written baudrate.

        :param bd: serial port's speed
        :type bd: int
        """
        _validate_param(bd, ALLOWED_BAUDRATES)
        to_write = int(bd / 100)
        self.write_u16(self.COMMON_REGS_MAP['baudrate'], to_write)
        serial_settings = {
            'baudrate' : bd
        }
        self._set_port_settings_raw(serial_settings)

    def set_parity(self, parity):
        """
        Writing parity to device and updating current instrument to written parity.

        :param parity: parity of serial port
        :type parity: str
        """
        _validate_param(parity, ALLOWED_PARITIES)
        to_write = ALLOWED_PARITIES[parity]
        self.write_u16(self.COMMON_REGS_MAP['parity'], to_write)
        serial_settings = {
            'parity' : parity
        }
        self._set_port_settings_raw(serial_settings)

    def set_stopbits(self, stopbits):
        """
        Writing stopbits to device and updating current instrument to written stopbits.

        :param stopbits: stopbits of serial port
        :type stopbits: int
        """
        _validate_param(stopbits, ALLOWED_STOPBITS)
        self.write_u16(self.COMMON_REGS_MAP['stopbits'], stopbits)
        serial_settings = {
            'stopbits' : stopbits
        }
        self._set_port_settings_raw(serial_settings)

    def get_device_signature(self):
        """
        Device signature is a part of model name, stored in modbus regs.

        :return: device signature string
        :rtype: str
        """
        return self.read_string(self.COMMON_REGS_MAP['device_signature'], self.DEVICE_SIGNATURE_LENGTH)

    def get_fw_signature(self):
        """
        Firmware signature is a string, defining, which firmwares are compatible with device's bootloader.

        :return: firmware signature string
        :rtype: str
        """
        try:
            return self.read_string(self.COMMON_REGS_MAP['fw_signature'], self.FIRMWARE_SIGNATURE_LENGTH)
        except minimalmodbus.IllegalRequestError:
            raise TooOldDeviceError("Device is too old and haven't fw_signature in regs!")

    def get_bootloader_version(self):
        try:
            return self.read_string(self.COMMON_REGS_MAP['bootloader_version'], self.BOOTLOADER_VERSION_LENGTH - 1)  # The last char is STM type
        except minimalmodbus.IllegalRequestError:
            raise TooOldDeviceError("Device is too old and haven't bootloader version in regs!")

    def get_uptime(self):
        """
        Uptime is a number of seconds, gone from previous reboot of device's MCU.

        :return: uptime seconds
        :rtype: int
        """
        reg = self.COMMON_REGS_MAP['uptime']
        return self.read_u32_big_endian(reg)

    def get_v_in(self):
        """
        Each Wiren Board device measures power supply voltage via MCU.

        :return: voltage (V)
        :rtype: float
        """
        multiplier = 1E-3 #value is stored in mV
        ret = self.read_u16(self.COMMON_REGS_MAP['v_in'])
        return ret * multiplier

    def soft_reboot(self, bootloader_timeout=3):
        """
        Rebooting a device's MCU without toggling power.

        :param bootloader_timeout: time, device is in bootloader after reboot, defaults to 3
        :type bootloader_timeout: int, optional
        :raises RuntimeError: device's uptime after reboot is not less than before
        """
        to_write = 1
        uptime_before = self.get_uptime()
        try:
            self.device.write_register(self.COMMON_REGS_MAP['reboot'], to_write, 0, 6, False)
        except minimalmodbus.ModbusException:
            pass #Device has rebooted and doesn't send responce (Fixed in latest FWs)
        time.sleep(bootloader_timeout)
        uptime_after = self.get_uptime()
        if uptime_after > uptime_before:
            raise RuntimeError('Device has not rebooted!')

    def reboot_to_bootloader(self):
        """
        Rebooting device into bootloader via modbus reg. After writing the reg, device stucks in bootloader for 2 minutes.

        :raises RuntimeError: device has not stuck in bootloader
        """
        self.get_slave_addr() #To ensure, device has connection
        try:
            self.write_u16(self.COMMON_REGS_MAP['reboot_to_bootloader'], 1)
        except minimalmodbus.ModbusException:
            pass #Device has rebooted and doesn't send responce (Fixed in latest FWs)
        finally:
            time.sleep(0.5) #Delay before going to bootloader
        try:
            self.get_slave_addr()
            raise TooOldDeviceError('Device has not rebooted to bootloader!')
        except minimalmodbus.ModbusException:
            pass #Device is in bootloader mode and doesn't responce

    def _has_bootloader_answered(self, baudrate=9600):
        """
        Sending a dummy-payload to bootloader and looking into minimalmodbus's errors.
        Wiren Board modbus devices, while in bootloader, could answer to a dummy-payload via modbus error 04 (Slave Device Failure).

        Devices, are not in bootloader, could raise error 04 too => combine with check, device is not answering to usual commands!

        :return: has device raised modbus error 04 or not
        :rtype: bool
        """
        initial_port_settings = deepcopy(self.settings)
        initial_response_timeout = self.device.serial.timeout

        bootloader_uart_params = [baudrate, 'N', 2]
        logger.debug('Setting params %s to port %s' % ('-'.join(map(str, bootloader_uart_params)), self.port))
        self.set_port_settings(*bootloader_uart_params)
        self.set_response_timeout(initial_response_timeout + self.BOOTLOADER_INFOBLOCK_MAGIC_TIMEOUT)

        try:
            self.write_u16_regs(0x1000, [0] * 16)  # A dummy payload
        except minimalmodbus.SlaveReportedException:  # Err 04
            return True
        except minimalmodbus.ModbusException:
            return False
        finally:
            logger.debug('Setting params to port %s back' % self.port)
            self._set_port_settings_raw(initial_port_settings)
            self.set_response_timeout(initial_response_timeout)

    def is_in_bootloader(self, baudrate=9600):
        """
        If slaveid has got => device is in normal working mode.
        If slaveid has not got and bootloader has answered (raised modbus error 04) => device is in bootloader.
        If slaveid has not got and bootloader has not answered => device is disconnected.

        :return: is device in bootloader or not
        :rtype: bool
        """
        try:
            self.get_slave_addr()
            return False  # Device is powered on and sending correct reply
        except minimalmodbus.ModbusException:
            return self._has_bootloader_answered(baudrate)  # Is device in bootloader or disconnected

    def _write_port_settings(self, baudrate, parity, stopbits):
        """
        bd, parity and stopbits regs are mapped consistently (110, 111, 112)
        Writing all settings per one message
        """
        self.device.write_registers(self.COMMON_REGS_MAP['baudrate'], [int(baudrate / 100), parity, stopbits])

    def write_uart_settings(self, baudrate, parity, stopbits):
        """
        Writing UART params to serial device and updating current instrument instance to new UART settings.

        :param baudrate: serial port speed
        :type baudrate: int
        :param parity: serial port parity
        :type parity: str
        :param stopbits: serial port stopbits
        :type stopbits: int
        """
        for param, allowed_row in zip([baudrate, parity, stopbits], [ALLOWED_BAUDRATES, ALLOWED_PARITIES.keys(), ALLOWED_STOPBITS]):
            _validate_param(param, allowed_row)
        self._write_port_settings(baudrate, ALLOWED_PARITIES[parity], stopbits)
        new_port_settings = {
            'baudrate' : baudrate,
            'parity' : parity,
            'stopbits' : stopbits
        }
        self._set_port_settings_raw(new_port_settings)
