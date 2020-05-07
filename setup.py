#!/usr/bin/env python

from distutils.core import setup

setup(name          =   "wb-mcu-fw-updater",
      version       =   "1.0",
      author        =   "Vladimir Romanov",
      description   =   "Wiren Board modbus devices firmware update tool",
      url           =   "https://github.com/wirenboard/wb-mcu-fw-updater",
      packages      =   ['wb_mcu_fw_updater', 'wb_modbus'],
      scripts       =   ['wb-mcu-fw-updater']
)
