import sys


def die(err_message, exitcode=1):
    """
    Exits gracefully, writing <err_message> to stderr
    """
    sys.stderr.write('%s\n' % err_message)
    sys.stderr.flush()
    sys.exit(exitcode)