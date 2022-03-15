# wb-mcu-fw-updater
A command-line tool, updating WirenBoard modbus devices to latest firmwares. Versions 1.1.x flash devices via wb-mcu-fw-flasher binary; versions 1.2+ use own implementation of fw-update protocol.

### Python packages inside:
* wb_mcu_fw_updater - downloading firmwares from remote server; flashing firmwares; handling generic update logic.
* wb_modbus - safe & configurable wrappers around minimalmodbus; common (for Wiren Board devices) modbus bindings.

## Debian packages:
* python2-wb-mcu-fw-updater - python2 library (wb_mcu_fw_updater + wb_modbus)
* python3-wb-mcu-fw-updater - python3 library (wb_mcu_fw_updater + wb_modbus)
* wb-mcu-fw-updater - python3-library-dependent binary

## External dependencies:
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

    `apt install wb-mcu-fw-updater`

    #### From deb package:

    `apt install ./<path to python3-lib .deb>`

    `apt install ./<path to wb-mcu-fw-updater .deb>`
