import logging
from . import fw_flasher, device_info, fw_downloader, die, SLAVEID_PLACEHOLDER, PYTHON2


if PYTHON2:
    input_func = raw_input
else:
    input_func = input


class UpdateHandler(object):
    """
    A 'launcher' class, handling all update logic.
    """

    _ALLOWED_TASKS = ('fw', 'bootloader')

    def __init__(self, port, slaveid=0, mode='fw', branch_name=None):
        if slaveid == 0:
            if self._ensure('No slaveid was passed. Will use broadcast command! Is device alone on the bus?'):
                pass
            else:
                die('Disconnect another devices or specify slaveid!')
        self.device = device_info.SerialDeviceHandler(port, slaveid)
        if mode == 'fw':
            self.current_version = self.device.get_fw_version
        elif mode == 'bootloader':
            self.current_version = self.device.get_bootloader_version
        else:
            die('Mode %s is unsupported!\nTry one of: %s' % ', '.join('fw', 'bootloader'))
        self.remote_file_watcher = fw_downloader.RemoteFileWatcher(mode=mode, branch_name=branch_name)

    def _ensure(self, message, positive='Y', negative='N'):
        message_str = '%s [%s/%s] ' % (message, positive, negative)
        ret = input_func(message_str)
        if ret.upper().startswith(positive):
            return True
        else:
            return False

    def flash(self, slaveid, port, fname, erase_settings=False):
        flasher = fw_flasher.WBFWFlasher(slaveid, port, erase_settings)
        flasher.flash(fname)

    def download(self, name, version, fname=None):
        return self.remote_file_watcher.download(name, version, fname)

    def update_is_needed(self):
        """
        Checking, whether update is needed or not by comparing version, stored in the device with remote.

        :return: is device's version latest or not
        :rtype: bool
        """
        self.meaningful_str = self.device.get_fw_signature()
        self.latest_remote_version = self.remote_file_watcher.get_latest_version_number(self.meaningful_str)
        current_version = self.current_version()
        if device_info.parse_fw_version(current_version) < device_info.parse_fw_version(self.latest_remote_version):
            logging.debug('Update is needed! (local version: %s; remote version: %s)' % (current_version, self.latest_remote_version))
            return True
        else:
            logging.debug('Device has latest version (%s)!' % current_version)
            return False
