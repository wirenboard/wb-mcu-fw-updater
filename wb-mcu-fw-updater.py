from wb_mcu_fw_updater.fw_downloader import RemoteFileWatcher
import logging


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.NOTSET)

    device_signature = 'msw3-48mh'
    branch_name = 'feature-th_error_cnt'

    stable_file_watcher = RemoteFileWatcher()
    logging.debug('Latest fw (by raw link): %s' % stable_file_watcher._get_request_content('http://fw-releases.wirenboard.com/fw/by-signature/msw3-48mh/stable/latest.txt')) # By raw link
    logging.debug('Latest fw (by signature): %s' % stable_file_watcher.get_latest_version_number(device_signature)) # By device's signature
    stable_file_watcher.download(device_signature)

    unstable_file_watcher = RemoteFileWatcher(branch_name=branch_name)
    unstable_file_watcher.download(device_signature)

    stable_bootloader = RemoteFileWatcher(mode='bootloader')
    latest_bootloader_version = stable_bootloader.get_latest_version_number(device_signature)
    logging.debug('Latest bootloader (by signature): %s' % latest_bootloader_version) # By device's signature
    stable_bootloader.download(device_signature, version=latest_bootloader_version)
    stable_bootloader.download(device_signature, version=latest_bootloader_version, fname='latest_bootloader.wbfw')
