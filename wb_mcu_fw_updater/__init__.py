import sys
import logging
from ast import literal_eval

if sys.version_info[0] < 3:
    PYTHON2 = True
else:
    PYTHON2 = False


CONFIG = {
    'EXTERNAL_CONFIG_FNAME' : '/etc/wb-mcu-fw-updater.conf',
    'SLAVEID_PLACEHOLDER' : 245,
    'DRIVER_EXEC_NAME' : 'wb-mqtt-serial',
    'FLASHER_FNAME' : 'wb-mcu-fw-flasher',
    'ROOT_URL' : 'http://fw-releases.wirenboard.com/',
    'FW_SAVING_DIR' : '/usr/share/wb_mcu_fw_updater/',
    'FW_EXTENSION' : '.wbfw',
    'LATEST_FW_VERSION_FILE' : 'latest.txt',
    'DEFAULT_SOURCE' : 'stable',
    'FW_SIGNATURES_PER_MODEL': {

    }
}


def die(err_message, exitcode=1):
    """
    Exits gracefully, writing <err_message> to stderr
    """
    sys.stderr.write('%s\n' % err_message)
    sys.stderr.flush()
    sys.exit(exitcode)


def update_config(config_fname):
    try:
        conffile = open(config_fname)
    except IOError:
        logging.warn('No config (%s) found! Using default built-in instead' % config_fname)
        return
    try:
        config_dict = literal_eval(conffile.read())
        CONFIG.update(pair for pair in config_dict.items() if pair[0] in CONFIG.keys())
    except (SyntaxError, ValueError) as e:
        die('Error in config syntax:\n%s' % str(e))


update_config(CONFIG['EXTERNAL_CONFIG_FNAME'])
