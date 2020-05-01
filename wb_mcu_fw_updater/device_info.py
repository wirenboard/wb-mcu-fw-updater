import logging
from distutils.version import LooseVersion
from wb_modbus.bindings import WBModbusDeviceBase, find_uart_settings
from . import SLAVEID_PLACEHOLDER


def parse_fw_version(fw_ver):
    """
    Parsing a fw_version string via standart-library tool to internal representation to be compared in future.
    More info about tool could be found in cPython source of distutils.version.

    :param fw_ver: a fw version string (could be '1.2.b3' fmt)
    :type fw_ver: str
    :return: a LooseVersion instance
    :rtype: obj
    """
    return LooseVersion(fw_ver)


class UnknownUARTSettingsDevice(WBModbusDeviceBase):
    """
    A modbus connection with unknown serial port settings (baudrate, parity, stopbits).
    Appropriate settings will be found via @find_uart_setings decorator at first launch of any method
    and applied to serial port.
    """

    @find_uart_settings
    def get_fw_signature(self):
        return super(UnknownUARTSettingsDevice, self).get_fw_signature()

    @find_uart_settings
    def get_rom_version(self):
        return super(UnknownUARTSettingsDevice, self).get_rom_version()

    @find_uart_settings
    def get_bootloader_version(self):
        return super(UnknownUARTSettingsDevice, self).get_bootloader_version()

    @find_uart_settings
    def get_device_signature(self):
        return super(UnknownUARTSettingsDevice, self).get_device_signature()

    @find_uart_settings
    def reboot_to_bootloader(self):
        super(UnknownUARTSettingsDevice, self).reboot_to_bootloader()

    def get_appropriate_sn(self):
        """
        WB-MAP* devices are storing serial number differently from other devices.

        :return: serial number of device
        :rtype: int
        """
        wbmap_placeholder = 'WBMAP'
        model_name = self.get_device_signature() #appropriate uart settings are applying here
        return self.get_serial_number_map() if model_name.startswith(wbmap_placeholder) else self.get_serial_number()


class SerialDeviceHandler(object):
    """
    Handles getting correct device info. If device is connected via broadcast slaveid, SLAVEID_PLACEHOLDER will be set.
    """
    def __init__(self, port, slaveid=0):
        self.device = UnknownUARTSettingsDevice(slaveid, port)
        if slaveid == 0:
            logging.info('Using broadcast connection (slaveid 0)! Will set addr to %d' % SLAVEID_PLACEHOLDER)
            slaveid = SLAVEID_PLACEHOLDER
            self.device.set_slave_addr(slaveid)

    def get_fw_signature(self):
        """
        Firmware signature is a string, defining firmware <=> bootloader conformity.

        :return: firmware signature
        :rtype: str
        """
        return self.device.get_fw_signature()

    def get_modelname(self):
        """
        Reading device model, stored in modbus regs.

        :return: device model
        :rtype: str
        """
        return self.device.get_device_signature()
    
    def get_fw_version(self):
        """
        Firmware version is a dot-terminated number.

        :return: firmware version
        :rtype: str
        """
        return self.device.get_rom_version()

    def get_bootloader_version(self):
        """
        Bootloader version is a dot-terminated number.

        :return: bootloader version
        :rtype: str
        """
        return self.device.get_bootloader_version()

    def get_serial_number(self):
        """
        Serial number is a unique id of device.

        :return: serial number
        :rtype: int
        """
        return self.device.get_appropriate_sn()

    def reboot_to_bootloader(self):
        """
        Writing 1 to special coil causes WirenBoard modbus devices to reboot into bootloader.
        """
        self.device.reboot_to_bootloader()
