#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import logging
from . import CONFIG


class StdoutFilter(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.ERROR


class StderrFilter(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.ERROR


class HideTracebackFormatter(logging.Formatter):  # TODO: maybe add colours?
    """Python's exceptions tracebacks are hidden from user (are not displayed in stream handlers),
    but are available in syslog.
    """

    def format(self, record):
        """We cannot copy a record.exc_info because of its references to call stack.
        => after formatting, record.exc_info will be lost. Ensure, handler with this formatter goes the last!

        :param record: a logging's log record instance
        :type record: logging's record obj
        :return: logging's record formatted string
        :rtype: str
        """
        record.exc_info = None
        record.exc_text = None
        return super(HideTracebackFormatter, self).format(record)


def setup_user_logger(least_visible_level):
    """user_logger handles programm's output, shown to user by terminal.
    Log records from error and higher are redirecting to stderr.
    Exceptions tracebacks are always hidden.

    :param least_visible_level: least loglevel, will be displayed to user
    :type least_visible_level: int
    """
    user_formatter = HideTracebackFormatter(fmt=CONFIG['USERLOG_MESSAGE_FMT'], datefmt=CONFIG['LOG_DATETIME_FMT'])

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


def setup_syslog_logger():
    """
    Writing logging messages to syslog socket.
    Default syslog socket is platform-dependent and could be absent in some development environments.
    """
    _default_syslog_sock = '/var/run/syslog' if 'darwin' in sys.platform else '/dev/log'  # For all linux systems
    if os.path.exists(_default_syslog_sock):
        syslog_handler = logging.handlers.SysLogHandler(address=_default_syslog_sock, facility='user')
        syslog_handler.setFormatter(logging.Formatter(fmt=CONFIG['SYSLOG_MESSAGE_FMT'], datefmt=CONFIG['LOG_DATETIME_FMT']))
        syslog_handler.setLevel(CONFIG['SYSLOG_LOGLEVEL'])
        logging.getLogger().handlers.insert(0, syslog_handler)  # Each message should be formatted by syslog's handler at first
