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

_DEFAULT_SYSLOG_SOCKET = '/dev/log'  # For all Linuxes
if 'darwin' in sys.platform:
    _DEFAULT_SYSLOG_SOCKET = '/var/run/syslog' # For OSX


CONFIG = {
    'EXTERNAL_CONFIG_FNAME' : '/etc/wb-mcu-fw-updater.conf',
    'SLAVEID_PLACEHOLDER' : 245,
    'ALLOWED_UNSUCCESSFUL_MODBUS_TRIES' : 2,
    'DRIVER_EXEC_NAME' : 'wb-mqtt-serial',
    'FLASHER_FNAME' : 'wb-mcu-fw-flasher',
    'ROOT_URL' : 'http://fw-releases.wirenboard.com/',
    'FW_SAVING_DIR' : '/usr/share/wb_mcu_fw_updater/',
    'FW_EXTENSION' : '.wbfw',
    'LATEST_FW_VERSION_FILE' : 'latest.txt',
    'DEFAULT_SOURCE' : 'stable',
    'USERLOG_MESSAGE_FMT' : '%(asctime)s %(message)s',
    'SYSLOG_MESSAGE_FMT' : 'wb-mcu-fw-updater:%(module)s.%(funcName)s[%(process)s]: %(message)s',
    'LOG_DATETIME_FMT' : '%Y-%m-%d|%H:%M:%S',
    'SYSLOG_LOGLEVEL' : 10,
    'USER_LOGLEVEL' : 30,
    'FW_SIGNATURES_PER_MODEL': {
        # Filling from config file
    }
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


def tb_to_syslog_exc_hook(exc_type, value, traceback):
    logging.error('Error occured: %s' % str(value), exc_info=(exc_type, value, traceback))
    die()


def update_config(config_fname):
    try:
        conffile = open(config_fname)
    except IOError:
        return
    try:
        config_dict = literal_eval(conffile.read())
        CONFIG.update(pair for pair in config_dict.items() if pair[0] in CONFIG.keys())
    except (SyntaxError, ValueError) as e:
        die('Error in config syntax:\n%s' % str(e))


update_config(CONFIG['EXTERNAL_CONFIG_FNAME'])


logging.getLogger().setLevel(logging.NOTSET)
syslog_handler = logging.handlers.SysLogHandler(address=_DEFAULT_SYSLOG_SOCKET, facility='user')
syslog_handler.setFormatter(logging.Formatter(fmt=CONFIG['SYSLOG_MESSAGE_FMT'], datefmt=CONFIG['LOG_DATETIME_FMT']))
syslog_handler.setLevel(CONFIG['SYSLOG_LOGLEVEL'])
logging.getLogger().addHandler(syslog_handler)

sys.excepthook = tb_to_syslog_exc_hook  # Exception's traceback is written only to syslog
