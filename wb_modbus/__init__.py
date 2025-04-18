# pylint: skip-file
import logging
import re
from collections import OrderedDict

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.NullHandler())


# TODO: fill from config
ALLOWED_UNSUCCESSFUL_TRIES = 2
CLOSE_PORT_AFTER_EACH_CALL = True
ALLOWED_BAUDRATES = [9600, 115200, 1200, 2400, 4800, 19200, 38400, 57600]
ALLOWED_STOPBITS = [2, 1]
ALLOWED_PARITIES = OrderedDict([("N", 0), ("O", 1), ("E", 2)])
DEBUG = False


WBMAP_MARKER = re.compile(r"\S*MAP\d+\S*")  # *MAP%d* matches


class SettingsParsingError(Exception):
    pass


def parse_uart_settings_str(settings_str):
    """
    A unified one-launchkey uart settings standart for Wiren Board software is like 9600N2

    :return: [baudrate, parity, stopbits]
    :rtype: list
    """
    if re.match(r"\d*[A-Z]\d*", settings_str):
        baudrate, stopbits = re.split("[A-Z]", settings_str)
        parity = settings_str.replace(baudrate, "").replace(stopbits, "").strip()
        if (
            (int(baudrate) in ALLOWED_BAUDRATES)
            and (int(stopbits) in ALLOWED_STOPBITS)
            and (parity in ALLOWED_PARITIES.keys())
        ):
            return [int(baudrate), parity, int(stopbits)]
        else:
            raise SettingsParsingError(
                "Got invalid uart params str: %s\nAllowed values:\n\tBAUDRATES: %s\n\tSTOPBITS: %s\n\tPARITIES: %s"
                % (settings_str, str(ALLOWED_BAUDRATES), str(ALLOWED_STOPBITS), str(ALLOWED_PARITIES.keys()))
            )
    else:
        raise SettingsParsingError(
            "Incorrect format of serial port settings string (got: %s). Should be like 9600N2" % settings_str
        )
