#!/usr/bin/env python

from setuptools import setup


def get_version():
    with open("debian/changelog", "r", encoding="utf-8") as f:
        return f.readline().split()[1][1:-1]


setup(
    name="wb-mcu-fw-updater",
    version=get_version(),
    author="Vladimir Romanov",
    author_email="v.romanov@wirenboard.ru",
    maintainer="Wiren Board Team",
    maintainer_email="info@wirenboard.com",
    description="Wiren Board modbus devices firmware update tool",
    license="MIT",
    url="https://github.com/wirenboard/wb-mcu-fw-updater",
    packages=["wb_mcu_fw_updater", "wb_modbus"],
)
