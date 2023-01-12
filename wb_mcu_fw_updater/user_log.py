#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
import sys

from . import CONFIG

ANSI_COLORS = {
    "GREY": "\x1b[38;2m",
    "GREEN": "\x1b[32;10m",
    "YELLOW": "\x1b[33;10m",
    "RED": "\x1b[31;10m",
    "RED_BOLD": "\x1b[31;1m",
    "RESET": "\x1b[0m",
}


def colorize(msg, color):
    if not isinstance(msg, str):
        raise RuntimeError("Only string could be colored!")
    if color not in ANSI_COLORS.keys():
        raise RuntimeError("Unsupported color %s. Try one of %s" % (color, ", ".join(ANSI_COLORS.keys())))
    return ANSI_COLORS[color] + msg + ANSI_COLORS["RESET"]


class HidingTracebackFilter(logging.Filter):
    """
    A dummy filter, hiding record's exception traceback, if was not hidden already.
    """

    def __init__(self, hide_tb=False):
        self.hide_tb = hide_tb

    def _hide_tb(self, record):
        if self.hide_tb:
            record._exc_info_hidden, record.exc_info = record.exc_info, None
            record.exc_text = None
        elif hasattr(record, "_exc_info_hidden"):  # Traceback was already hidden by another handler
            record.exc_info = record._exc_info_hidden
            del record._exc_info_hidden

    def filter(self, record):
        return True


class StdoutFilter(HidingTracebackFilter):
    def filter(self, record):
        self._hide_tb(record)
        return record.levelno < logging.ERROR


class StderrFilter(HidingTracebackFilter):
    def filter(self, record):
        self._hide_tb(record)
        return logging.CRITICAL > record.levelno >= logging.ERROR


class ColoredFormatter(logging.Formatter):
    GREY = "\x1b[38;2m"
    GREEN = "\x1b[32;10m"
    YELLOW = "\x1b[33;10m"
    RED = "\x1b[31;10m"
    RED_BOLD = "\x1b[31;1m"
    RESET_COLORS = "\x1b[0m"

    FMT = CONFIG["USERLOG_MESSAGE_FMT"]

    FORMATS = {
        logging.DEBUG: colorize(FMT, "GREY"),
        logging.WARNING: colorize(FMT, "YELLOW"),
        logging.ERROR: colorize(FMT, "RED"),
        logging.CRITICAL: colorize(FMT, "RED_BOLD"),
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.FMT)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def setup_user_logger(name, least_visible_level):
    """
    User_logger handles programm's output, shown to user by terminal.
    Log records from error and higher are redirecting to stderr.

    Exceptions tracebacks are hidden, depending of <least_visible_level>.

    :param least_visible_level: least loglevel, will be displayed to user
    :type least_visible_level: int
    """
    user_formatter = ColoredFormatter()

    hide_traceback = least_visible_level > logging.DEBUG

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(least_visible_level)
    stdout_handler.setFormatter(user_formatter)
    stdout_handler.addFilter(StdoutFilter(hide_traceback))
    logging.getLogger(name).addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(least_visible_level)
    stderr_handler.setFormatter(user_formatter)
    stderr_handler.addFilter(StderrFilter(hide_traceback))
    logging.getLogger(name).addHandler(stderr_handler)

    unhandled_exception_handler = logging.StreamHandler(stream=sys.stderr)
    unhandled_exception_handler.setLevel(logging.CRITICAL)
    unhandled_exception_handler.setFormatter(user_formatter)
    logging.getLogger(name).handlers.insert(0, unhandled_exception_handler)


def setup_syslog_logger(name):
    """
    Writing logging messages to syslog socket.
    Default syslog socket is platform-dependent and could be absent in some development environments.
    """
    _default_syslog_sock = (
        "/var/run/syslog" if "darwin" in sys.platform else "/dev/log"
    )  # For all linux systems
    if os.path.exists(_default_syslog_sock):
        syslog_handler = logging.handlers.SysLogHandler(address=_default_syslog_sock, facility="user")
        syslog_handler.setFormatter(
            logging.Formatter(fmt=CONFIG["SYSLOG_MESSAGE_FMT"], datefmt=CONFIG["LOG_DATETIME_FMT"])
        )
        syslog_handler.setLevel(CONFIG["SYSLOG_LOGLEVEL"])
        logging.getLogger(name).handlers.insert(
            0, syslog_handler
        )  # Each message should be formatted by syslog's handler at first
