import sys
import logging
from . import CONFIG


class StdoutFilter(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.ERROR


class StderrFilter(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.ERROR


# TODO: maybe colored formatter?


def setup_user_logger(least_visible_level):
    """user_logger handles programm's output, shown to user by terminal.
    Log records from error and higher are redirecting to stderr.

    :param least_visible_level: least loglevel, will be displayed to user
    :type least_visible_level: int
    """
    user_formatter = logging.Formatter(CONFIG['USERLOG_MESSAGE_FMT'])

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(least_visible_level)
    stdout_handler.setFormatter(user_formatter)
    stdout_handler.addFilter(StdoutFilter())
    logging.getLogger().addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(least_visible_level)
    stderr_handler.setFormatter(user_formatter)
    stderr_handler.addFilter(StderrFilter())
    logging.getLogger().addHandler(stderr_handler)
