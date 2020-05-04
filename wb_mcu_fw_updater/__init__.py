import sys

if sys.version_info[0] < 3:
    PYTHON2 = True
else:
    PYTHON2 = False


SLAVEID_PLACEHOLDER = 245


DRIVER_EXEC_NAME = 'wb-mqtt-serial'


def die(err_message, exitcode=1):
    """
    Exits gracefully, writing <err_message> to stderr
    """
    sys.stderr.write('%s\n' % err_message)
    sys.stderr.flush()
    sys.exit(exitcode)
