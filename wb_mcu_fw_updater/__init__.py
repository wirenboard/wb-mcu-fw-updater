#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import logging
import logging.handlers
from ast import literal_eval


if sys.version_info[0] < 3:
    PYTHON2 = True
else:
    PYTHON2 = False


logging.getLogger().setLevel(logging.NOTSET)


CONFIG = {
    'EXTERNAL_CONFIG_FNAME' : '/etc/wb-mcu-fw-updater.conf',
    'ALLOWED_UNSUCCESSFUL_MODBUS_TRIES' : 2,
    'SERIAL_DRIVER_PROCESS_NAME' : 'wb-mqtt-serial',
    'SERIAL_DRIVER_CONFIG_FNAME' : '/etc/wb-mqtt-serial.conf',
    'FLASHER_EXEC_NAME' : 'wb-mcu-fw-flasher',
    'ROOT_URL' : 'http://fw-releases.wirenboard.com/',
    'FW_SIGNATURES_FILE_URL' : 'http://fw-releases.wirenboard.com/fw/by-signature/fw_signatures.txt',
    'FW_SAVING_DIR' : '/var/lib/wb-mcu-fw-updater/',
    'LAST_FW_SIGNATURE_FNAME' : '/var/lib/wb-mcu-fw-updater/last_fw_signature.txt',
    'FW_EXTENSION' : '.wbfw',
    'LATEST_FW_VERSION_FILE' : 'latest.txt',
    'DEFAULT_SOURCE' : 'stable',
    'USERLOG_MESSAGE_FMT' : '%(asctime)s %(message)s',
    'SYSLOG_MESSAGE_FMT' : 'wb-mcu-fw-updater:%(module)s.%(funcName)s[%(process)s]: %(message)s',
    'LOG_DATETIME_FMT' : '%Y-%m-%d|%H:%M:%S',
    'SYSLOG_LOGLEVEL' : 10,
    'USER_LOGLEVEL' : 30
}


def die(err_message=None, exitcode=1):
    """
    Exits gracefully, writing <err_message> to stderr via logging.
    Call explicitly only if python's exceptions are not informative!
    """
    if err_message:
        logging.error(err_message)
        sys.stderr.flush()
    sys.exit(exitcode)


def update_config(config_fname):
    """
    Only fields, existing in CONFIG will be updated.

    :param config_fname: a full path to user config file
    :type config_fname: str
    """
    try:
        conffile = open(config_fname)
    except IOError:
        logging.warning('No user config file has found! Will use built-in default')
        return
    try:
        config_dict = literal_eval(conffile.read())
        CONFIG.update(pair for pair in config_dict.items() if pair[0] in CONFIG.keys())
    except (SyntaxError, ValueError) as e:
        die('Error in config syntax:\n%s' % str(e))


update_config(CONFIG['EXTERNAL_CONFIG_FNAME'])
