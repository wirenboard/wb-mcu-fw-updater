from wb_mcu_fw_updater.fw_downloader import RemoteFileWatcher
from wb_mcu_fw_updater.device_info import SerialDeviceHandler, parse_fw_version
import logging
from sys import argv


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.NOTSET)

    port = argv[1]
    slaveid = int(argv[2])
   
    device = SerialDeviceHandler(port, slaveid)
    fw_signature = device.get_fw_signature()
    fw_version = device.get_fw_version()
    bootloader_version = device.get_bootloader_version()
    sn = device.get_serial_number()
    modelname = device.get_modelname()
    logging.info('Device: %s\nSignature: %s\nFW: %s\nBootloader: %s\nSN: %d' % (modelname, fw_signature, fw_version, bootloader_version, sn))

    stable_fw_watcher = RemoteFileWatcher(mode='fw')
    latest_remote_fw = stable_fw_watcher.get_latest_version_number(fw_signature)
    logging.debug('Latest remote FW for device: %s' % latest_remote_fw)

    if parse_fw_version(latest_remote_fw) > parse_fw_version(fw_version):
        logging.info('FW on device should be updated! (local: %s; remote: %s)' % (fw_version, latest_remote_fw))
    else:
        logging.info('FW version on device is latest!')

    device.reboot_to_bootloader()