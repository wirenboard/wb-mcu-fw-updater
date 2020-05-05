import logging
import json
from . import fw_flasher, device_info, fw_downloader, die, PYTHON2, CONFIG


if PYTHON2:
    input_func = raw_input
else:
    input_func = input


class UpdateHandler(object):
    """
    A 'launcher' class, handling all update logic.
    """

    _ALLOWED_TASKS = ('fw', 'bootloader')

    _DRIVER_CONFIG_MAP = {
        'ports_list' : 'ports',
        'port_fname' : 'path',
        'devices_list' : 'devices',
        'model' : 'device_type',
        'slaveid' : 'slave_id'
    }

    def __init__(self, port, mode, branch_name):
        self.port = port
        if mode in self._ALLOWED_TASKS:
            self.mode = mode
        else:
            die('Mode <%s> is unsupported. Try one of: %s' % (mode, ', '.join(self._ALLOWED_TASKS)))
        self.branch_name = branch_name
        self.flasher = fw_flasher.WBFWFlasher(port)
        self.remote_file_watcher = fw_downloader.RemoteFileWatcher(mode, branch_name=branch_name)

    def _ensure(self, message, positive='Y', negative='N'):
        """Asking, is user sure or not.

        :param message: message, will be printed to user
        :type message: str
        :param positive: beginning of positive user's answer, defaults to 'Y'
        :type positive: str, optional
        :param negative: beginning of negative user's answer, defaults to 'N'
        :type negative: str, optional
        :return: was user sure or not
        :rtype: bool
        """
        message_str = '%s [%s/%s] ' % (message, positive, negative)
        ret = input_func(message_str)
        if ret.upper().startswith(positive):
            return True
        else:
            return False

    def _parse_driver_config(self, driver_config_fname):
        config_file = open(driver_config_fname, 'r')
        config_dict = json.load(config_file)
        return config_dict

    def get_modbus_device_connection(self, slaveid):
        """Connection to device with possibly known slaveid and unknown UART settings.

        :param slaveid: slave address of device
        :type slaveid: int
        :return: a connection instance
        :rtype: device_info.SerialDeviceHandler object
        """
        if 0 < slaveid <= 247:
            pass
        elif slaveid == 0:
            if self._ensure('No slaveid has passed. Will use broadcast command! Is device alone on the bus?'):
                pass
            else:
                die('Disconnect ALL other devices from the bus!')
        else:
            die('Slaveid %d is not allowed!' % slaveid)
        device = device_info.SerialDeviceHandler(self.port, slaveid)
        return device

    def flash(self, slaveid, fname, erase_settings=False):
        """Flashing .wbfw file over already known port.

        :param slaveid: slave addr of device
        :type slaveid: int
        :param fname: a .wbfw file to flash
        :type fname: str
        :param erase_settings: will all settings be erased after flashing or not, defaults to False
        :type erase_settings: bool, optional
        """
        self.flasher.flash(slaveid, fname, erase_settings)

    def download(self, name, version='latest', fname=None):
        """Downloading .wbfw file from remote server.

        :param name: a wb-device's fw signature
        :type name: str
        :param version: a specific version of fw, defaults to 'latest'
        :type version: str, optional
        :param fname: a specific filepath, download will be performed to, defaults to None
        :type fname: str, optional
        :return: filepath, download has performed to
        :rtype: str
        """
        return self.remote_file_watcher.download(name, version, fname)

    def update_is_needed(self, instrument):
        """
        Checking, whether update is needed or not by comparing version, stored in the device with remote.

        :param instrument: a modbus connection to device
        :type instrument: a device_info.SerialDeviceHandler' instance
        """
        meaningful_str = instrument.get_fw_signature()
        latest_remote_version = self.remote_file_watcher.get_latest_version_number(meaningful_str)
        current_version = instrument.get_bootloader_version() if self.mode == 'bootloader' else instrument.get_fw_version()
        if device_info.parse_fw_version(current_version) < device_info.parse_fw_version(latest_remote_version):
            logging.info('Update is needed! (local %s version: %s; remote version: %s)' % (self.mode, current_version, latest_remote_version))
            return True
        else:
            logging.info('Device has latest %s version (%s)!' % (self.mode, current_version))
            return False

    def find_slaveid_in_bootloader(self):
        """Iterating over all possible slaveaddrs and probing connection on it.

        :return: found slaveid
        :rtype: int
        """
        if self._ensure("Is device in bootloader now? All device's settings will be restored to defaults!"):
            pass
        else:
            die('Refused erasing settings')
        for slaveid in range(0, 248):
            if self.flasher.probe_connection(slaveid):
                return slaveid
        else:
            die('No valid slaveid was found. Check physical connection to device!')

    def get_fw_signature_by_model(self, modelname):
        """If there is no connection with device, fw_signature could be get via internal model_name <=> fw_signature conformity.

        :param modelname: a full device's model name (ex: WB-MR6HV/I)
        :type modelname: str
        :return: fw_signature of device
        :rtype: str
        """
        if modelname not in CONFIG['FW_SIGNATURES_PER_MODEL']:
            die('Model %s is unknown! Choose one from:\n%s' % (modelname, ', '.join(CONFIG['FW_SIGNATURES_PER_MODEL'].keys())))
        return CONFIG['FW_SIGNATURES_PER_MODEL'][modelname]

    def get_devices_on_port(self, driver_config_fname):
        """Parsing <driver_config_fname> for a list of pairs device_model & slaveid.

        :param driver_config_fname: wb-mqtt-serial's config file
        :type driver_config_fname: str
        :return: a list of device models and their slaveids
        :rtype: list
        """
        found_devices = []
        config_dict = self._parse_driver_config(driver_config_fname)
        for port in config_dict[self._DRIVER_CONFIG_MAP['ports_list']]:
            if port[self._DRIVER_CONFIG_MAP['port_fname']] == self.port:
                for serial_device in port[self._DRIVER_CONFIG_MAP['devices_list']]:
                    device_name = serial_device[self._DRIVER_CONFIG_MAP['model']]
                    slaveid = serial_device[self._DRIVER_CONFIG_MAP['slaveid']]
                    found_devices.append([device_name, int(slaveid)])
        if found_devices:
            return found_devices
        else:
            die('Looks, like there is no devices on port %s. Aborted.' % self.port)
