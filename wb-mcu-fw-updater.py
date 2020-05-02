import argparse
import logging
from wb_mcu_fw_updater.update_monitor import UpdateHandler


def update_alive_device(args):
    """
    Updating device, working in normal mode. Port and slaveid are required.

    :param args: is launched by argparse and uses it's args
    """
    updater = UpdateHandler(args.port, args.slaveid, args.mode, args.branch_name)
    if updater.update_is_needed() or args.force:
        download_fpath = updater.download(updater.meaningful_str, args.specified_version, args.fname)
        updater.device.reboot_to_bootloader()
        updater.flash(updater.device.slaveid, args.port, download_fpath, args.erase_settings)


def reflash_in_bootloader(args):
    """
    Flashing device, stuck in bootloader. Actual slaveid and fw signature are required.

    :param args: is launched by argparse and uses it's args
    """
    updater = UpdateHandler(args.port, args.slaveid, args.mode, args.branch_name)
    download_fpath = updater.download(args.known_signature, args.specified_version, args.fname)
    updater.flash(args.slaveid, args.port, download_fpath, args.erase_settings)


def parse_args():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter, description='WirenBoard modbus devices firmware update tool')
    parser.add_argument('-p', '--port', type=str, dest='port', required=True, help='Serial port, device connected to')
    parser.add_argument('-a', '--slaveid', type=int, dest='slaveid', default=0, help='Slaveid of device')
    parser.add_argument('--save-to', type=str, dest='fname', default=None, help='Fpath, where download firmware')
    parser.add_argument('--version', type=str, dest='specified_version', default='latest', help='A current version could be specified')
    parser.add_argument('--erase-settings', action='store_true', dest='erase_settings', default=False, help='Erase all device settings at flash')
    parser.add_argument('--branch', type=str, dest='branch_name', default=None, help='Install FW from specified branch')
    parser.add_argument('--mode', type=str, dest='mode', default='fw', choices=('fw', 'bootloader'), help='Update firmware or bootloader')
    subparsers = parser.add_subparsers()

    parser_update = subparsers.add_parser('update', help='Check for update on alive device')
    parser_update.add_argument('--force', action='store_true', dest='force', default=False, help='Perform force device update, even if firmware is latest')
    parser_update.set_defaults(func=update_alive_device)

    parser_reflash = subparsers.add_parser('reflash', help='Flash device in bootloader mode via known slaveid and signature') #TODO: maybe get signature by modelname?
    parser_reflash.add_argument('--signature', type=str, dest='known_signature', required=True, help='Perform force device update, even if firmware is latest')
    parser_reflash.set_defaults(func=reflash_in_bootloader)
    return parser.parse_args()


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.NOTSET)
    args = parse_args()
    args.func(args)
