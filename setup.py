#!/usr/bin/env python

from setuptools import setup

setup(name          =   "wb-mcu-fw-updater",
      version       =   "1.0",
      author        =   "Vladimir Romanov",
      author_email  =   "v.romanov@wirenboard.ru",
      description   =   "Wiren Board modbus devices firmware update tool",
      url           =   "https://github.com/wirenboard/wb-mcu-fw-updater",
      packages      =   ['wb_mcu_fw_updater', 'wb_modbus'],
      test_suite    =   "wb_mcu_fw_updater.tests"
)
