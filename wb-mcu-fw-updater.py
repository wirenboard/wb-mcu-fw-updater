import argparse
import logging
import subprocess
import sys
from wb_mcu_fw_updater import update_monitor, user_log, CONFIG


def update_alive_device(updater, args):
    """
    Updating device, working in normal mode. Port name is compulsory.

    :param args: is launched by argparse and uses it's args
    """
    modbus_connection = updater.get_modbus_device_connection(args.slaveid)
    if updater.update_is_needed(modbus_connection) or args.force:
        fw_signature = modbus_connection.get_fw_signature()
        download_fpath = updater.download(fw_signature, args.specified_version, args.fname)
        modbus_connection.reboot_to_bootloader()
        updater.flash(modbus_connection.slaveid, download_fpath, args.erase_settings)  # Slaveid is internal modbus_connection's one!


def reflash_in_bootloader(updater, args):
    """
    Flashing device, stuck in bootloader. Actual slaveid and fw signature are required.

    :param args: is launched by argparse and uses it's args
    """
    if not args.known_signature:
        fw_signature = updater.get_fw_signature_by_model(args.device_model)
    else:
        fw_signature = args.known_signature
    download_fpath = updater.download(fw_signature, args.specified_version, args.fname)
    if args.slaveid != 0:
        slaveid = args.slaveid
    else:
        slaveid = updater.find_slaveid_in_bootloader()
    updater.flash(slaveid, download_fpath, args.erase_settings)


def update_all(updater, args): # TODO: not fail after first unconnected device; collect statistics
    """Parsing driver_config for a list of slaveids. Trying to update each device.
    """
    args.erase_settings = False
    subprocess.call('service %s stop' % CONFIG['DRIVER_EXEC_NAME'], shell=True)
    for device_slaveid in updater.get_devices_on_port(args.driver_config):
        args.slaveid = device_slaveid
        logging.info('Trying to update device with slaveid %d:' % args.slaveid)
        update_alive_device(updater, args)
    subprocess.call('service %s restart' % CONFIG['DRIVER_EXEC_NAME'], shell=True)


def parse_args():
    main_parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter, description='WirenBoard modbus devices firmware update tool')
    main_parser.add_argument('-p', '--port', type=str, dest='port', required=True, help='Serial port, device connected to')
    main_parser.add_argument('-a', '--slaveid', type=int, dest='slaveid', default=0, choices=range(0, 248), help='Slaveid of device') #TODO: remove choices
    main_parser.add_argument('--save-to', type=str, dest='fname', default=None, help='Fpath, where download firmware')
    main_parser.add_argument('--debug', dest='user_loglevel', default=None, action='store_const', const=10, help='Set log priority to lowest')
    main_parser.add_argument('--version', type=str, dest='specified_version', default='latest', help='A current version could be specified')
    main_parser.add_argument('--erase-settings', action='store_true', dest='erase_settings', default=False, help='Erase all device settings at flash')
    main_parser.add_argument('--branch', type=str, dest='branch_name', default=None, help='Install FW from specified branch')
    main_parser.add_argument('--mode', type=str, dest='mode', default='fw', choices=('fw', 'bootloader'), help='Update firmware or bootloader')
    subparsers = main_parser.add_subparsers()

    update_parser = subparsers.add_parser('update', help='Check for update on alive device')
    update_parser.add_argument('--force', action='store_true', dest='force', default=False, help='Perform force device update, even if firmware is latest')
    update_parser.set_defaults(func=update_alive_device)

    recover_parser = subparsers.add_parser('recover', help='Flash device in bootloader mode via possibly knonw slaveid and signature')
    recover_parser.add_argument('--model-name', type=str, dest='device_model', required=True, help='Flash device in bootloader to latest FW via its modelname')
    recover_parser.add_argument('--signature', type=str, dest='known_signature', default=None, help='Force specify device FW signature')
    recover_parser.set_defaults(func=reflash_in_bootloader)

    update_all_parser = subparsers.add_parser('update-all', help='Trying to update all devices from wb-mqtt-serial config')
    update_all_parser.add_argument('--config', type=str, dest='driver_config', default='/etc/wb-mqtt-serial.conf', help="Specify driver's config fname")
    update_all_parser.add_argument('--force', action='store_true', dest='force', default=False, help='Perform force device update, even if firmware is latest')
    update_all_parser.set_defaults(func=update_all)

    return main_parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    user_log.setup_user_logger((args.user_loglevel or CONFIG['USER_LOGLEVEL']))

    updater = update_monitor.UpdateHandler(args.port, args.mode, args.branch_name)
    args.func(updater, args)
