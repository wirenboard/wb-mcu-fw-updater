# wb-mcu-fw-updater
A command-line tool, updating WirenBoard modbus devices to latest firmwares. Versions 1.x.x flash devices via wb-mcu-fw-flasher binary.

### Python packages inside:
* wb_mcu_fw_updater - downloading firmwares from remote server; flashing firmwares via wb-mcu-fw-flasher binary; handling generic update logic.
* wb_modbus - safe & configurable wrappers around minimalmodbus; common (for Wiren Board devices) modbus bindings.

## Debian packages:
* python-wb-mcu-fw-updater - python2
* python3-wb-mcu-fw-updater - python3

## External dependencies:
* wb-mcu-fw-flasher
* for python2-package: python-serial
* for python3-package: python3-serial

## Building:
`dpkg-buildpackage -rfakeroot -us -uc`

## Installation:
1. Add Wiren Board repo, if doesn't exist.
2. Update list of packages:

    `apt update`
3. Install wb-mcu-fw-updater

    #### from Wiren Board repo:

    `apt install python-wb-mcu-fw-updater`

    or for python3:

    `apt install python3-wb-mcu-fw-updater`

    #### From deb package:

    `apt install ./<path to wb-mcu-fw-updater .deb>`
