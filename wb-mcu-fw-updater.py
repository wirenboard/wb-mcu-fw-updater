from wb_mcu_fw_updater.fw_downloader import RemoteFileWatcher
import logging


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.NOTSET)

    device_signature = 'msw3-48mh'
    branch_name = 'feature-th_error_cnt'

    stable_file_watcher = RemoteFileWatcher()
    logging.debug('Latest fw (by raw link): %s' % stable_file_watcher._get_latest_fw_version('http://fw-releases.wirenboard.com/fw/by-signature/msw3-48mh/stable/latest.txt')) # By raw link
    logging.debug('Latest fw (by signature): %s' % stable_file_watcher.get_latest_fw_version(device_signature)) # By device's signature
    stable_file_watcher.get_fw_file(device_signature)

    unstable_file_watcher = RemoteFileWatcher(branch_name=branch_name)
    unstable_file_watcher.get_fw_file(device_signature)