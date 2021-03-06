from collections import OrderedDict
import re


#TODO: fill from config
ALLOWED_UNSUCCESSFUL_TRIES = 5
CLOSE_PORT_AFTER_EACH_CALL = True
ALLOWED_BAUDRATES = [9600, 115200, 1200, 2400, 4800, 19200, 38400, 57600]
ALLOWED_STOPBITS = [2, 1]
ALLOWED_PARITIES = OrderedDict([('N', 0), ('O', 1), ('E', 2)])
DEBUG = False


WBMAP_MARKER = re.compile('\S*MAP\d+\S*')  # *MAP%d* matches


def parse_uart_settings_str(settings_str):
    """
    A unified one-launchkey uart settings standart for Wiren Board software is like 9600N2

    :return: [baudrate, parity, stopbits]
    :rtype: list
    """
    if re.match('\d*[A-Z]\d*', settings_str):
        baudrate, stopbits = re.split('[A-Z]', settings_str)
        parity = settings_str.replace(baudrate, '').replace(stopbits, '').strip()
        return [int(baudrate), parity, int(stopbits)]
    else:
        raise RuntimeError('Incorrect format of serial port settings string. Should be like 9600N2')
