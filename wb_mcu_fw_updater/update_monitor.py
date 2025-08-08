#!/usr/bin/env python
# -*- coding: utf-8 -*-

import enum
import json
import logging
import os
import subprocess
import sys
import termios
import threading
import time
import urllib.parse
from collections import defaultdict, namedtuple
from contextlib import contextmanager
from copy import deepcopy
from io import open

import semantic_version
import six
import tqdm
import yaml

# rework params setting to get rid of imports-order-magic
# isort: off
from . import CONFIG, MODE_BOOTLOADER, MODE_FW, MODE_COMPONENTS, logger
import wb_modbus  # Params should be set before any wb_modbus usage! # pylint:disable=wrong-import-order

wb_modbus.ALLOWED_UNSUCCESSFUL_TRIES = CONFIG["ALLOWED_UNSUCCESSFUL_MODBUS_TRIES"]
wb_modbus.DEBUG = CONFIG["MODBUS_DEBUG"]
from wb_modbus import (  # pylint:disable=wrong-import-position, wrong-import-order
    minimalmodbus,
    bindings,
    parse_uart_settings_str,
    instruments,
)
from . import (  # pylint:disable=wrong-import-position
    fw_flasher,
    fw_downloader,
    user_log,
    jsondb,
    releases,
    die,
)

# isort: on

db = jsondb.JsonDB(CONFIG["DB_FILE_LOCATION"])


RELEASE_INFO = None


class UpdateDeviceError(Exception):
    pass


class NoReleasedFwError(UpdateDeviceError):
    pass


class ForeignDeviceError(UpdateDeviceError):
    pass


class UserCancelledError(UpdateDeviceError):
    pass


class ConfigParsingError(Exception):
    pass


DownloadedWBFW = namedtuple("DownloadedWBFW", "mode fpath version")


SkipUpdateReason = enum.Enum(value="SkipUpdateReason", names=("is_actual", "gone_ahead"))


@contextmanager
def spinner(
    estimated_time_s=float("+inf"), tdelta_s=0.1, description="", tqdm_kwargs={}
):  # pylint:disable=dangerous-default-value
    if description:
        logger.debug(description)
        tqdm_kwargs.update({"desc": description})

    pbar = tqdm.tqdm(total=estimated_time_s, **tqdm_kwargs)
    stop_event = threading.Event()

    def pbar_update_runner(pbar, interval, stop_event):
        while not stop_event.is_set():
            pbar.update(interval)
            time.sleep(interval)

    pbar_update_thread = threading.Thread(target=pbar_update_runner, args=(pbar, tdelta_s, stop_event))
    pbar_update_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        pbar_update_thread.join()
        pbar.close()


def ask_user(message, force_yes=False):
    """
    Asking user before potentionally dangerous action.
    <force_yes> removes all interactivity and hides the question

    :param message: will be printed to user
    :type message: str
    :return: is user sure or not
    :rtype: bool
    """
    loglevel = logging.DEBUG if force_yes else logging.WARNING
    message_str = f"\n{message} [Y/N]"
    for msg in message_str.split("\n"):
        logger.log(loglevel, msg)
    ret = force_yes or six.moves.input().upper().startswith("Y")
    logger.debug("Got: %s", str(ret))
    return ret


def fill_release_info():
    """
    wb-mcu-fw-updater supposed to be launched only on devices, supporting wb-releases
    incorrect wb-releases file indicates strange erroneous behavior
    """
    global RELEASE_INFO  # pylint:disable=global-statement
    releases_fname = CONFIG["RELEASES_FNAME"]
    try:
        RELEASE_INFO = releases.parse_releases(releases_fname)
    except Exception:  # pylint:disable=broad-exception-caught
        logger.error("Critical error in %s file! Contact the support!", releases_fname)
        six.reraise(*sys.exc_info())


def get_released_fw(fw_signature, release_info):
    """
    Looking for released-fw:
        version
        url on fw-releases
    By:
        fw_signature
        release suite
    """
    suite = release_info["SUITE"]
    for url in releases.get_release_file_urls(release_info):  # repo-prefix is the first, if exists
        logger.debug("Looking to %s (suite: %s)", url, str(suite))
        try:
            contents = fw_downloader.get_remote_releases_info(url)
            fw_endpoint = yaml.safe_load(contents).get("releases", {}).get(fw_signature, {}).get(suite)
            if fw_endpoint:
                fw_version = releases.parse_fw_version(fw_endpoint)
                logger.debug(
                    "FW version for %s on release %s: %s (endpoint: %s)",
                    fw_signature,
                    suite,
                    fw_version,
                    fw_endpoint,
                )
                return str(fw_version), str(fw_endpoint)
        except fw_downloader.RemoteFileReadingError:
            logger.warning('No released fw for "%s" in "%s"', fw_signature, url)
        except releases.VersionParsingError as e:
            logger.exception(e)
    raise NoReleasedFwError(
        f'Released FW not found for "{fw_signature}"\n'
        "Release info:\n"
        f"{json.dumps(release_info, indent=4)}"
    )


def download_fw_fallback(fw_signature, release_info, ask_for_latest=True, force=False):
    try:
        _, released_fw_endpoint = get_released_fw(fw_signature, release_info)
    except NoReleasedFwError:
        logger.warning(
            'Device "%s" is not supported in %s (as %s)',
            fw_signature,
            str(release_info.get("RELEASE_NAME")),
            str(release_info.get("SUITE")),
        )
        if ask_for_latest and ask_user(
            """Perform downloading from latest master anyway
        (may cause unstable behaviour; proceed at your own risk)?""",
            force_yes=force,
        ):
            downloaded_fw = fw_downloader.RemoteFileWatcher(MODE_FW, branch_name="").download(
                fw_signature, "latest"
            )
        else:
            six.reraise(*sys.exc_info())
    else:
        downloaded_fw = fw_downloader.download_remote_file(
            six.moves.urllib.parse.urljoin(CONFIG["ROOT_URL"], released_fw_endpoint)
        )
    return downloaded_fw


def find_connection_params(
    slaveid, port, response_timeout, instrument=instruments.StopbitsTolerantInstrument
):
    modbus_connection = bindings.WBModbusDeviceBase(
        slaveid, port, response_timeout=response_timeout, instrument=instrument
    )
    desc_str = (
        f"Will find serial port settings for ({port} : {slaveid}; "
        f"response_timeout: {response_timeout:.2f})..."
    )
    uart_settings = None
    with spinner(
        description=desc_str,
        tqdm_kwargs={
            "bar_format": "{desc} (elapsed: {elapsed})",
        },
    ):
        try:
            uart_settings = modbus_connection.find_uart_settings(modbus_connection.get_slave_addr)
        except bindings.UARTSettingsNotFoundError as e:
            six.raise_from(minimalmodbus.NoResponseError, e)
    logger.info("Has found serial port settings: %s", str(uart_settings))
    return uart_settings


def find_bootloader_connection_params(
    slaveid, port, response_timeout, instrument=instruments.StopbitsTolerantInstrument
):
    modbus_connection = bindings.WBModbusDeviceBase(
        slaveid, port, response_timeout=response_timeout, instrument=instrument
    )
    # pylint:disable=line-too-long
    desc_str = f"Will find bootloader port settings for ({port} : {slaveid}; response_timeout: {response_timeout:.2f})..."
    uart_settings = None
    with spinner(
        description=desc_str,
        tqdm_kwargs={
            "bar_format": "{desc} (elapsed: {elapsed})",
        },
    ):
        try:
            uart_settings = modbus_connection.find_uart_settings(modbus_connection.probe_bootloader)
        except bindings.UARTSettingsNotFoundError as e:
            six.raise_from(minimalmodbus.NoResponseError, e)

    initial_uart_settings = deepcopy(modbus_connection.settings)
    modbus_connection._set_port_settings_raw(uart_settings)  # pylint: disable=protected-access
    try:
        modbus_connection.get_slave_addr()
    except minimalmodbus.ModbusException:
        # Device is in bootloader mode and doesn't respond
        logger.info("Has found bootloader port settings: %s", str(uart_settings))
        return uart_settings
    finally:
        modbus_connection._set_port_settings_raw(initial_uart_settings)  # pylint: disable=protected-access

    raise minimalmodbus.NoResponseError()


def check_device_is_a_wb_one(modbus_connection):
    """
    Foreign devices recognition:
        1) performing any wb-specific modbus call (get_sn for example). Could raise:
            minimalmodbus.SlaveReportedException() if foreign device;
            minimalmodbus.NoResponseError() if disconnected;
            ValueError() if minimalmodbus's slaveid check failed => device is foreign
        2) performing get_fw_signature() call. If 1) succeed, could raise:
            minimalmodbus.IllegalRequestError() (inside!!; reraises TooOldDeviceError())
                if device is a wb-one, but too old for any updates
        3) performing a set of additional wb-specific calls:
            get_device_signature(), get_fw_version(), get_uptime().
            If 1), 2) are succeed, any modbus error here means, device is foreign
    """
    try:
        sn = modbus_connection.get_serial_number()  # Will raise NoResponseError, if disconnected
        fw_sig = modbus_connection.get_fw_signature()
    except bindings.TooOldDeviceError:
        fw_sig = ""
    except (
        ValueError,
        minimalmodbus.SlaveReportedException,
    ) as e:  # minimalmodbus's slaveid check performs at _exec_command stage
        six.raise_from(ForeignDeviceError, e)

    try:  # WB devices assume to have all these regs
        logger.debug("%s %d:", modbus_connection.port, modbus_connection.slaveid)
        logger.debug(
            "\t%s %d %s %s %d",
            modbus_connection.get_device_signature(),
            sn,
            fw_sig,
            modbus_connection.get_fw_version(),
            modbus_connection.get_uptime(),
        )
    except minimalmodbus.ModbusException as e:
        raise ForeignDeviceError(
            f"Possibly, device ({modbus_connection.port} {modbus_connection.slaveid}) is not a WB-one!"
        ) from e


def get_correct_modbus_connection(
    slaveid,
    port,
    response_timeout,
    known_uart_params_str=None,
    instrument=instruments.StopbitsTolerantInstrument,
):  # maybe move to device_prober module?
    """
    Alive device only:
        1) searching device's uart settings (if not passed);
        2) checking, that device is a wb-one via reading
            device_signature, serial_number, fw_signature, fw_version;
    """
    modbus_connection = bindings.WBModbusDeviceBase(
        slaveid, port, response_timeout=response_timeout, instrument=instrument
    )

    if known_uart_params_str:
        modbus_connection.set_port_settings(*parse_uart_settings_str(known_uart_params_str))
    else:
        raw_uart_params = find_connection_params(slaveid, port, response_timeout, instrument=instrument)
        modbus_connection._set_port_settings_raw(raw_uart_params)  # pylint: disable=protected-access

    check_device_is_a_wb_one(modbus_connection)
    return modbus_connection


def get_ports_on_driver(driver_config_fname):
    ports = []
    try:
        with open(driver_config_fname, "r", encoding="utf-8") as file:
            config_dict = json.load(file)
    except (ValueError, IOError) as e:
        logger.exception("Error in %s", driver_config_fname)
        raise ConfigParsingError from e

    for port in config_dict.get("ports", []):
        if port.get("enabled", False) and port.get("path", False):
            ports.append(port["path"])
    return ports


def get_devices_on_driver(driver_config_fname):
    """
    Parsing a driver's config file to get ports, their uart params and devices, connected to.

    :return: {<port_name> : {'devices' : [devices_on_port], 'uart_params' : [uart_params_of_port]}}
    :rtype: dict
    """
    found_devices = {}

    try:
        with open(driver_config_fname, "r", encoding="utf-8") as file:
            config_dict = json.load(file)
    except (ValueError, IOError) as e:
        logger.exception("Error in %s", driver_config_fname)
        six.raise_from(ConfigParsingError, e)

    for port in config_dict.get("ports", []):
        if port.get("enabled", False) and port.get(
            "path", False
        ):  # updating devices only on active RS-485 ports
            port_name = port["path"]
            uart_params_of_port = [int(port["baud_rate"]), port["parity"], int(port["stop_bits"])]
            port_response_timeout = int(port.get("response_timeout_ms", 0)) * 1e-3
            devices_on_port = set()
            for serial_device in port.get("devices", []):
                if not serial_device.get("enabled", True):
                    continue
                device_name = serial_device.get("device_type", "Unknown")
                slaveid = serial_device["slave_id"]
                device_response_timeout = int(serial_device.get("response_timeout_ms", 0)) * 1e-3
                if device_name.startswith("WBIO-"):
                    logger.debug("Has found WBIO device: %s", device_name)
                    device_name, slaveid = "WB-MIO", slaveid.split(":")[0]  # mio_slaveid:device_order
                try:
                    parsed_slaveid = int(slaveid, 0)
                except ValueError:
                    logger.info(
                        'Device ("%s" %s) on %s seems not to be a WB-one; skipping',
                        device_name,
                        slaveid,
                        port_name,
                    )
                    continue
                devices_on_port.add((device_name, parsed_slaveid, device_response_timeout))
            if devices_on_port:
                found_devices.update(
                    {
                        port_name: {
                            "devices": list(devices_on_port),
                            "uart_params": uart_params_of_port,
                            "response_timeout": port_response_timeout,
                        }
                    }
                )

    if not found_devices:
        logger.error("No devices has found in %s", driver_config_fname)
    return found_devices


def recover_device_iteration(fw_signature, device: bindings.WBModbusDeviceBase, force=False):
    """
    A device supposed to be in "dead" state =>
    fw_signature, slaveid, port have passed instead of modbus_connection
    """
    downloaded_fw = download_fw_fallback(fw_signature, RELEASE_INFO, force=force)
    direct_flash(downloaded_fw, device, force=force)


def direct_flash(  # pylint:disable=too-many-arguments
    fw_fpath,
    device: bindings.WBModbusDeviceBase,
    erase_all_settings=False,
    erase_uart_only=False,
    force=False,
    do_check_userdata_saving=False,
):
    """
    Performing operations in bootloader (device is already into):
        flashing .wbfw
        erasing all settings or uart-only (with additional confirmation)
    """

    def _ensure(message_str):
        if ask_user(message_str, force_yes=force):
            return True
        raise UserCancelledError(
            f"Rejected by user. Device ({device.slaveid}, {device.port}) is in bootloder now; wait 120s, untill it starts."  # pylint:disable=line-too-long
        )

    default_msg = "Device's settings will be reset to defaults (1, 9600-8-N-2). Are you sure?"

    in_bl_settings = device.get_port_settings()
    if in_bl_settings != bindings.SerialSettings(9600, "N", 2):
        try:
            device.device.read_registers(
                device.COMMON_REGS_MAP["bootloader_version"], device.BOOTLOADER_VERSION_LENGTH, 3
            )
        except minimalmodbus.ModbusException:
            logger.warning("Temporarily trying 9600N2 in bootloader (because of some old bootloaders issues)")
            in_bl_settings = bindings.SerialSettings(9600, "N", 2)

    flasher = fw_flasher.ModbusFlasher(
        device.slaveid,
        device.port,
        device.response_timeout,
        in_bl_settings.baudrate,
        in_bl_settings.parity,
        in_bl_settings.stopbits,
        device.instrument,
    )

    if erase_uart_only and _ensure(default_msg):
        flasher.reset_uart()
    if erase_all_settings and _ensure(default_msg + " (it will erase ALL device's settings)"):
        flasher.reset_eeprom()

    parsed_wbfw = fw_flasher.ParsedWBFW(fw_fpath)
    if do_check_userdata_saving and (not flasher.is_userdata_preserved(parsed_wbfw)):
        _ensure("User data (such as ir commands) will be erased. Are you sure? (do a backup if not!)")
    flasher.flash(parsed_wbfw)


def is_reflash_necessary(
    actual_version, provided_version, force_reflash=False, allow_downgrade=False, debug_info=""
):
    # dirty hack to fix urlencoded versions, to be properly resolved later
    if "%" in provided_version:
        provided_version = urllib.parse.unquote(provided_version)
    # end of hack
    actual_version, provided_version = semantic_version.Version(actual_version), semantic_version.Version(
        provided_version
    )
    _do_flash = False
    _skip_reason = None

    if actual_version == provided_version:
        if force_reflash:
            logger.info(
                "%s %s -> %s %s",
                user_log.colorize("Force update:", "YELLOW"),
                actual_version,
                provided_version,
                debug_info,
            )
            _do_flash = True
        else:
            logger.info("Is actual: %s -> %s %s", actual_version, provided_version, debug_info)
            _do_flash = False
            _skip_reason = SkipUpdateReason.is_actual
    elif provided_version > actual_version:
        logger.info(
            "%s %s -> %s %s",
            user_log.colorize("Update:", "GREEN"),
            actual_version,
            provided_version,
            debug_info,
        )
        _do_flash = True
    elif allow_downgrade:
        logger.info(
            "%s %s -> %s %s",
            user_log.colorize("Downgrade:", "YELLOW"),
            actual_version,
            provided_version,
            debug_info,
        )
        _do_flash = True
    else:
        logger.info(
            "%s %s -> %s %s",
            user_log.colorize("Downgrade not allowed:", "RED"),
            actual_version,
            provided_version,
            debug_info,
        )
        _do_flash = False
        _skip_reason = SkipUpdateReason.gone_ahead

    if _do_flash and (actual_version.major != provided_version.major):
        return (
            ask_user(
                f"""Major version has changed (v{str(actual_version.major)} -> v{str(provided_version.major)});
        Backward compatibility will be broken. Are you sure?""",  # pylint:disable=line-too-long
                force_yes=force_reflash,
            ),
            _skip_reason,
        )
    return _do_flash, _skip_reason


def is_reflash_component_necessary(actual_version, provided_version, force_reflash, component_info):
    do_flash = False
    if actual_version == provided_version:
        if force_reflash:
            logger.info(
                "%s %s -> %s %s",
                user_log.colorize("Force update:", "YELLOW"),
                actual_version,
                provided_version,
                component_info,
            )
            do_flash = True
        else:
            logger.info("Is actual: %s -> %s %s", actual_version, provided_version, component_info)
            do_flash = False
    else:
        logger.info(
            "%s %s -> %s %s",
            user_log.colorize("Update:", "GREEN"),
            actual_version,
            provided_version,
            component_info,
        )
        do_flash = True
    return do_flash


def is_bootloader_latest(mb_connection):
    fw_sig = mb_connection.get_fw_signature()
    local_version = mb_connection.get_bootloader_version()
    remote_version = fw_downloader.RemoteFileWatcher(mode=MODE_BOOTLOADER).get_latest_version_number(fw_sig)
    return semantic_version.Version(local_version) == semantic_version.Version(remote_version)


def _do_download(fw_sig, version, branch, mode, retrieve_latest_vnum=True):
    """
    Generic .wbfw downloading logic: ("release" is a default val for version)
        version=="release"; branch==None -> looking into release-versions.yaml (default case)
        version=="release"; branch==<specified_branch> -> looking into branch/latest
        version==<specified_version>; branch==None -> looking into main/version
        version==<specified_version>; branch==<specified_branch> -> looking into branch/version
        version==None/"latest"; branch==None -> looking into main/version
        version==None/"latest"; branch==<specified_branch> -> looking into branch/latest

    Retrieves "latest" version number (from latest.txt on s3); returns ("downloaded_fpath", "version_number")
    """
    downloader = fw_downloader.RemoteFileWatcher(mode=mode, branch_name=branch)
    if mode == MODE_FW:
        mode_name = "firmware"
    else:
        mode_name = mode

    downloaded_fw = None

    if branch:
        if (
            version == "release"
        ):  # default fw_version now is 'release'; will flash latest, if branch has specified
            version = "latest"
            downloaded_fw = downloader.download(fw_sig, version)
        if mode_name == "bootloader":
            retrieve_latest_vnum = False
            # maybe clear bootloader branches from s3 (or wait some time)
            # instead of "retrieve_latest_vnum" logic?

    if version == "release":  # triggered updating from releases
        version, released_fw_endpoint = get_released_fw(fw_sig, RELEASE_INFO)
        downloaded_fw = fw_downloader.download_remote_file(
            six.moves.urllib.parse.urljoin(CONFIG["ROOT_URL"], released_fw_endpoint)
        )
    else:
        logger.debug("%s version has specified manually: %s", mode_name, version)

    if version == "latest" and retrieve_latest_vnum:
        logger.debug("Retrieving latest %s version number for %s", mode_name, fw_sig)
        version = downloader.get_latest_version_number(fw_sig)  # to guess, is reflash needed or not

    downloaded_fw = downloaded_fw or downloader.download(fw_sig, version)
    return DownloadedWBFW(mode=mode, fpath=downloaded_fw, version=version)


def is_interactive_shell():
    return os.getenv("WBGSM_INTERACTIVE", "").strip() != ""  # maybe rename env var?


def is_bl_update_required(modbus_connection, force=False):
    fw_sig = modbus_connection.get_fw_signature()
    local_version = modbus_connection.get_bootloader_version()
    remote_file_watcher = fw_downloader.RemoteFileWatcher(mode=MODE_BOOTLOADER)
    latest_remote_version = remote_file_watcher.get_latest_version_number(fw_sig)

    if semantic_version.Version(local_version) == semantic_version.Version(latest_remote_version):
        return False

    if not remote_file_watcher.is_version_exist(fw_sig, local_version):
        logger.warning(
            "Local bootloader version v%s is not found on remote! (maybe was removed manually) => "
            "Will update bootloader to latest v%s anyway!",
            local_version,
            latest_remote_version,
        )
        return True

    suggestion_str = (
        f"Bootloader update (v{local_version} -> v{latest_remote_version}) for {fw_sig} "
        f"{modbus_connection.port}:{modbus_connection.slaveid} is available! "
        "(bootloader updates are highly recommended to install)"
    )
    if is_interactive_shell():
        return ask_user(suggestion_str + " Do a bootloader update?", force)
    logger.warning(suggestion_str)
    return False


def _do_flash(modbus_connection, downloaded_wbfw: DownloadedWBFW, erase_settings, force=False):
    fw_signature = modbus_connection.get_fw_signature()
    device_str = f"{fw_signature} {modbus_connection.port}:{modbus_connection.slaveid}"
    logger.debug("Flashing approved for %s", device_str)
    bl_to_flash = None
    actual_bl_version = modbus_connection.get_bootloader_version()
    if downloaded_wbfw.mode == MODE_FW:
        if is_bl_update_required(modbus_connection, force):
            bl_to_flash = fw_downloader.RemoteFileWatcher(MODE_BOOTLOADER).download(fw_signature, "latest")
    elif downloaded_wbfw.mode == MODE_BOOTLOADER:
        if semantic_version.Version(downloaded_wbfw.version) < semantic_version.Version(actual_bl_version):
            raise UpdateDeviceError(
                f"Bootloader downgrade (v{actual_bl_version} -> v{downloaded_wbfw.version}) is not allowed!"
            )

    do_check_userdata_saving = semantic_version.Version(actual_bl_version) >= semantic_version.Version(
        "1.2.0"
    )

    initial_port_settings = modbus_connection.settings
    initial_response_timeout = modbus_connection.response_timeout
    modbus_connection.reboot_to_bootloader()
    if bl_to_flash:
        logger.debug("Performing bootloader update for %s", device_str)
        direct_flash(
            bl_to_flash, modbus_connection, force=force, do_check_userdata_saving=False
        )  # bl is relatively small in chunk-size
    direct_flash(
        downloaded_wbfw.fpath,
        modbus_connection,
        erase_settings,
        force=force,
        do_check_userdata_saving=do_check_userdata_saving,
    )

    if downloaded_wbfw.mode == MODE_BOOTLOADER:
        logger.info(
            'Bootloader was successfully flashed. Will flash released firmware for "%s"', fw_signature
        )
        downloaded_fw = download_fw_fallback(fw_signature, RELEASE_INFO, force=force)
        direct_flash(
            downloaded_fw,
            modbus_connection,
            erase_settings,
            force=force,
            do_check_userdata_saving=do_check_userdata_saving,
        )
    modbus_connection._set_port_settings_raw(initial_port_settings)  # pylint: disable=protected-access
    modbus_connection.set_response_timeout(initial_response_timeout)


def flash_alive_device(  # pylint:disable=too-many-arguments
    modbus_connection,
    mode,
    branch_name,
    specified_fw_version,
    force,
    erase_settings,
):
    """
    Checking for update, if branch is stable;
    Just flashing specified fw version, if branch is unstable.
    """
    fw_signature = modbus_connection.get_fw_signature()
    db.save(modbus_connection.slaveid, modbus_connection.port, fw_signature)

    device_str = f"({fw_signature} {modbus_connection.slaveid} on {modbus_connection.port})"

    # Flashing specified fw version (without any update-checking), if branch is unstable

    if branch_name:
        if ask_user(
            f"""Flashing device: "{fw_signature}" branch: "{branch_name}" version: "{specified_fw_version}" is requested.
        Stability cannot be guaranteed. Flash at your own risk?""",  # pylint:disable=line-too-long
            force_yes=force,
        ):
            downloaded_wbfw = _do_download(fw_signature, specified_fw_version, branch_name, mode)
            logger.info(
                "%s %s -> %s",
                user_log.colorize("Confirmed update:", "GREEN"),
                modbus_connection.get_fw_version(),
                downloaded_wbfw.version,
            )
            _do_flash(modbus_connection, downloaded_wbfw, erase_settings, force=force)
            return
        raise UserCancelledError(f"Flashing {fw_signature} has rejected")

    # Reflashing with update-checking

    device_fw_version = (
        modbus_connection.get_bootloader_version()
        if mode == MODE_BOOTLOADER
        else modbus_connection.get_fw_version()
    )
    downloaded_wbfw = _do_download(fw_signature, specified_fw_version, branch_name, mode)

    logger.info("%s %s:", mode, device_str)
    do_reflash, _ = is_reflash_necessary(
        actual_version=device_fw_version,
        provided_version=downloaded_wbfw.version,
        force_reflash=force,
        allow_downgrade=True,
        debug_info=f"({fw_signature} {modbus_connection.slaveid} {modbus_connection.port})",
    )
    if do_reflash:
        _do_flash(modbus_connection, downloaded_wbfw, erase_settings, force=force)


def flash_alive_device_components(  # pylint:disable=too-many-arguments
    modbus_connection, mode, branch_name, specified_fw_version, force
):
    fw_signature = modbus_connection.get_fw_signature()
    component_str = f"({fw_signature} {modbus_connection.slaveid} on {modbus_connection.port})"

    if (mode != MODE_COMPONENTS and specified_fw_version not in ["latest", "release"]) or branch_name:
        logger.debug(
            "Skip components update, due to branch is specified (%s) or "
            "fw version is not latest/release (%s), mode: %s",
            branch_name,
            specified_fw_version,
            mode,
        )
        return
    if not wait_for_wake_up(modbus_connection, timeout=2):
        logger.info("Device did not wake up after flashing")
        return
    components_list = modbus_connection.get_available_components()
    if components_list is None:
        logger.debug("Device does not support components update feature")
        return
    if len(components_list) == 0:
        logger.debug("No components available")
        return

    logger.info("Check updates for components %s", component_str)
    downloaded_firmwares = []
    for component_number in components_list:
        info = modbus_connection.get_component_info(component_number)
        compfw = _do_download(info["signature"], specified_fw_version, branch_name, mode=MODE_COMPONENTS)
        if not is_reflash_component_necessary(info["fw_version"], compfw.version, force, info["signature"]):
            continue

        downloaded_firmwares.append(compfw.fpath)

    if (
        downloaded_firmwares
        and mode == MODE_FW
        and not ask_user("Found updates for device components. Do you want to continue?", force)
    ):
        logger.info("Components update skipped by user")
        return

    flash_components_with_files(modbus_connection, downloaded_firmwares)


def flash_components_with_files(modbus_connection, downloaded_firmwares):
    settings = modbus_connection.get_port_settings()
    initial_response_timeout = modbus_connection.response_timeout
    # Flashing components requires bigger response timeout than default flashing timeout
    # which is 0.2s by default. On default time flashing components is unstable.
    minimal_response_timeout = 0.3
    if modbus_connection.response_timeout <= minimal_response_timeout:
        modbus_connection.set_response_timeout(minimal_response_timeout)
    flasher = fw_flasher.ModbusFlasher(
        modbus_connection.slaveid,
        modbus_connection.port,
        modbus_connection.response_timeout,
        settings.baudrate,
        settings.parity,
        settings.stopbits,
        modbus_connection.instrument,
    )
    for compfw in downloaded_firmwares:
        parsed_compfw = fw_flasher.ParsedWBFW(compfw)
        flasher.flash(parsed_compfw)
    modbus_connection.set_response_timeout(initial_response_timeout)


class DeviceInfo(namedtuple("DeviceInfo", ["name", "modbus_connection"])):
    __slots__ = ()

    def __str__(self):
        return f"{self.name} ({self.modbus_connection.slaveid}, {self.modbus_connection.port})"


def probe_all_devices(  # pylint:disable=too-many-locals
    driver_config_fname, minimal_response_timeout, instrument=instruments.StopbitsTolerantInstrument
):  # maybe rework entire data model (to get rid of passing lists)
    """
    Acquiring states of all devices, added to config.
    States could be:
        alive - device is working in normal mode and answering to modbus commands
        in_bootloader - device could not boot it's rom
        disconnected - a dummy-record in config
        too_old_to_update - old wb devices, haven't bootloader
        foreign_devices - non-wb devices, defined in config
    """
    result = defaultdict(list)

    logger.info("Will probe all devices on enabled serial ports of %s:", driver_config_fname)
    for port, port_params in get_devices_on_driver(driver_config_fname).items():
        uart_params = "".join(map(str, port_params["uart_params"]))  # 9600N2
        port_response_timeout = port_params["response_timeout"]
        devices_on_port = port_params["devices"]
        for device_name, device_slaveid, device_response_timeout in devices_on_port:
            actual_response_timeout = max(
                minimal_response_timeout, port_response_timeout, device_response_timeout
            )
            # pylint:disable=line-too-long
            desc_str = f"Probing {device_name} (port: {port}, slaveid: {device_slaveid}, uart_params: {uart_params}, response_timeout: {actual_response_timeout:.2f})..."
            with spinner(description=desc_str, tqdm_kwargs={"bar_format": "{desc} (elapsed: {elapsed})"}):
                device_info = DeviceInfo(
                    name=device_name,
                    modbus_connection=bindings.WBModbusDeviceBase(
                        device_slaveid,
                        port,
                        *parse_uart_settings_str(uart_params),
                        response_timeout=actual_response_timeout,
                        instrument=instrument,
                    ),
                )
                try:
                    device_info = DeviceInfo(
                        name=device_name,
                        modbus_connection=get_correct_modbus_connection(
                            device_slaveid, port, actual_response_timeout, uart_params, instrument=instrument
                        ),
                    )
                except ForeignDeviceError:
                    result["foreign"].append(device_info)
                    continue
                except minimalmodbus.NoResponseError:
                    # check current configured port settings
                    if device_info.modbus_connection.is_in_bootloader():
                        result["in_bootloader"].append(device_info)
                        continue
                    # could be old bootloader with fixed 9600N2 config
                    if device_info.modbus_connection.get_port_settings() != bindings.SerialSettings(
                        9600, "N", 2
                    ):
                        device_info.modbus_connection.set_port_settings(9600, "N", 2)
                        if device_info.modbus_connection.is_in_bootloader():
                            result["in_bootloader"].append(device_info)
                            continue
                    result["disconnected"].append(device_info)
                    continue

                try:
                    mb_connection = device_info.modbus_connection
                    db.save(
                        mb_connection.slaveid, mb_connection.port, mb_connection.get_fw_signature()
                    )  # old devices haven't fw_signatures
                    result["alive"].append(device_info)
                except bindings.TooOldDeviceError:
                    logger.error("%s is too old and does not support firmware updates!", str(device_info))
                    result["too_old_to_update"].append(device_info)

    return result


def print_status(
    loglevel, status="", devices_list=[], additional_info=""
):  # pylint:disable=dangerous-default-value
    logger.log(loglevel, status)
    logger.log(loglevel, "\t%s", "; ".join([str(device_info) for device_info in devices_list]))
    logger.log(loglevel, additional_info)


def _update_all(  # pylint:disable=too-many-branches,too-many-statements
    force, minimal_response_timeout, allow_downgrade=False, instrument=instruments.StopbitsTolerantInstrument
):  # maybe store fw endpoint in device_info? (to prevent multiple releases-parsing)
    probing_result = probe_all_devices(
        CONFIG["SERIAL_DRIVER_CONFIG_FNAME"], minimal_response_timeout, instrument=instrument
    )
    cmd_status = defaultdict(list)

    for device_info in probing_result["alive"]:
        fw_signature = device_info.modbus_connection.get_fw_signature()
        try:
            latest_remote_version, released_fw_endpoint = get_released_fw(
                fw_signature, RELEASE_INFO
            )  # auto-updating only from releases
        except NoReleasedFwError as e:
            logger.error(e)
            cmd_status["no_fw_release"].append(device_info)
            continue
        if latest_remote_version == "latest":  # Could be written in release
            latest_remote_version = fw_downloader.RemoteFileWatcher(
                mode=MODE_FW, branch_name=""
            ).get_latest_version_number(
                fw_signature
            )  # to guess, is reflash needed or not
        local_device_version = device_info.modbus_connection.get_fw_version()

        do_reflash, skip_reason = is_reflash_necessary(
            actual_version=local_device_version,
            provided_version=latest_remote_version,
            force_reflash=force,
            allow_downgrade=allow_downgrade,
            debug_info=f"({device_info})",
        )
        if do_reflash:
            downloaded_wbfw = DownloadedWBFW(
                mode=MODE_FW,
                fpath=fw_downloader.download_remote_file(
                    urllib.parse.urljoin(CONFIG["ROOT_URL"], released_fw_endpoint)
                ),
                version=latest_remote_version,
            )
            cmd_status["to_perform"].append([device_info, downloaded_wbfw])
        else:
            if skip_reason == SkipUpdateReason.gone_ahead:
                cmd_status["skipped"].append(device_info)

    for device_info, downloaded_wbfw in cmd_status[
        "to_perform"
    ]:  # Devices, were alive and supported fw_updates
        logger.info("Flashing firmware to %s", str(device_info))
        try:
            _do_flash(device_info.modbus_connection, downloaded_wbfw, False, force=force)
            # map12 takes more time to wake up, so wait
            if not wait_for_wake_up(device_info.modbus_connection, 0.5):
                logger.info("Device %s is not responding after flashing", str(device_info))
                probing_result["in_bootloader"].append(device_info)
                continue
            if not is_bootloader_latest(device_info.modbus_connection):
                cmd_status["bl_update_available"].append(device_info)
            flash_alive_device_components(
                modbus_connection=device_info.modbus_connection,
                mode=downloaded_wbfw.mode,
                branch_name="feature-ns8-fw-update",
                specified_fw_version=downloaded_wbfw.version,
                force=force,
            )
        except fw_flasher.FlashingError as e:
            logger.exception(e)
            probing_result["in_bootloader"].append(device_info)
        except (
            minimalmodbus.ModbusException
        ) as e:  # Device was connected at the probing time, but is disconnected now
            logger.exception(e)
            probing_result["disconnected"].append(device_info)
        else:
            cmd_status["ok"].append(device_info)

    for device_info in probing_result["in_bootloader"][:]:
        fw_signature = _restore_fw_signature(device_info.modbus_connection)
        logger.info("Found in bootloader: %s; fw_signature: %s", str(device_info), str(fw_signature))
        if not fw_signature:
            continue  # remain as in-bootloader
        try:
            recover_device_iteration(fw_signature, device_info.modbus_connection, force)
            if not is_bootloader_latest(device_info.modbus_connection):
                cmd_status["bl_update_available"].append(device_info)
        except (fw_flasher.FlashingError, fw_downloader.WBRemoteStorageError) as e:
            logger.exception(e)
        else:
            cmd_status["ok"].append(device_info)
            probing_result["in_bootloader"].remove(device_info)

    if cmd_status["skipped"]:
        print_status(
            logging.WARNING,
            status=f'Not updated (fw version gone ahead of release {RELEASE_INFO.get("SUITE", "")}):',
            devices_list=cmd_status["skipped"],
            additional_info='You may try to run with "--allow-downgrade" arg',
        )

    if cmd_status["no_fw_release"]:
        print_status(
            logging.WARNING,
            status=f'Not supported in current {RELEASE_INFO.get("RELEASE_NAME", "")} release:',
            devices_list=cmd_status["no_fw_release"],
            additional_info="You may try to switch to newer release",
        )

    if cmd_status["bl_update_available"]:
        print_status(
            logging.WARNING,
            status="Bootloader update available:",
            devices_list=cmd_status["bl_update_available"],
            additional_info="Try 'wb-mcu-fw-updater update-bl -a <addr> <port>' for each device",
        )

    if probing_result["disconnected"]:
        print_status(
            logging.WARNING,
            status="No answer from:",
            devices_list=probing_result["disconnected"],
            additional_info="Devices are possibly disconnected",
        )

    if probing_result["in_bootloader"]:
        print_status(
            logging.ERROR,
            status="Now in bootloader:",
            devices_list=probing_result["in_bootloader"],
            additional_info="Try wb-mcu-fw-updater recover-all",
        )

    if probing_result["too_old_to_update"]:
        print_status(
            logging.ERROR, status="Too old for any updates:", devices_list=probing_result["too_old_to_update"]
        )

    logger.info(
        "%s upgraded, %s skipped upgrade, %s bootloader updates available, %s stuck in bootloader, "
        "%s disconnected and %s too old for any updates.",
        user_log.colorize(str(len(cmd_status["ok"])), "GREEN" if cmd_status["ok"] else "RED"),
        user_log.colorize(str(len(cmd_status["skipped"])), "YELLOW" if cmd_status["skipped"] else "GREEN"),
        user_log.colorize(
            str(len(cmd_status["bl_update_available"])),
            "YELLOW" if cmd_status["bl_update_available"] else "GREEN",
        ),
        user_log.colorize(
            str(len(probing_result["in_bootloader"])), "RED" if probing_result["in_bootloader"] else "GREEN"
        ),
        user_log.colorize(
            str(len(probing_result["disconnected"])), "RED" if probing_result["disconnected"] else "GREEN"
        ),
        user_log.colorize(
            str(len(probing_result["too_old_to_update"])),
            "RED" if probing_result["too_old_to_update"] else "GREEN",
        ),
    )


def _restore_fw_signature(modbus_device: bindings.WBModbusDeviceBase):
    """
    Getting fw_signature of devices in bootloader
    """
    try:
        logger.debug("Will ask a bootloader for fw_signature")
        fw_signature = modbus_device.get_fw_signature()  # latest bootloaders could answer a fw_signature
    except minimalmodbus.ModbusException:
        logger.debug(
            "Will try to restore fw_signature from db by slaveid: %d and port %s",
            modbus_device.slaveid,
            modbus_device.port,
        )
        fw_signature = db.get_fw_signature(modbus_device.slaveid, modbus_device.port)
    logger.debug(
        "FW signature for %d : %s is %s", modbus_device.slaveid, modbus_device.port, str(fw_signature)
    )
    return fw_signature


def _recover_all(minimal_response_timeout, force=False, instrument=instruments.StopbitsTolerantInstrument):
    probing_result = probe_all_devices(
        CONFIG["SERIAL_DRIVER_CONFIG_FNAME"], minimal_response_timeout, instrument=instrument
    )
    cmd_status = defaultdict(list)

    for device_info in probing_result["in_bootloader"]:
        fw_signature = _restore_fw_signature(device_info.modbus_connection)
        if fw_signature is None:
            logger.info("%s %s", user_log.colorize("Unknown fw_signature:", "RED"), str(device_info))
            cmd_status["skipped"].append(device_info)
        else:
            logger.info("%s %s", user_log.colorize("Known fw_signature:", "GREEN"), str(device_info))
            cmd_status["to_perform"].append([device_info, fw_signature])

    if cmd_status["to_perform"]:
        logger.info("Flashing the most recent stable firmware:")
        for device_info, fw_signature in cmd_status["to_perform"]:
            try:
                recover_device_iteration(fw_signature, device_info.modbus_connection, force)
            except (fw_flasher.FlashingError, fw_downloader.WBRemoteStorageError) as e:
                logger.exception(e)
                cmd_status["skipped"].append(device_info)
            else:
                cmd_status["ok"].append(device_info)
        logger.info("Done")

    if probing_result["disconnected"]:
        print_status(logging.DEBUG, status="No answer:", devices_list=probing_result["disconnected"])

    if cmd_status["skipped"]:
        print_status(
            logging.ERROR,
            status="Not recovered:",
            devices_list=cmd_status["skipped"],
            additional_info="Try again or launch single recover with --fw-sig <fw_signature> key for each device!",  # pylint:disable=line-too-long
        )

    logger.info(
        "%s recovered, %s was already working, %s not recovered and %s not answered to recover cmd.",
        user_log.colorize(
            str(len(cmd_status["ok"])),
            (
                "GREEN"
                if (cmd_status["ok"] or (not cmd_status["to_perform"] and not cmd_status["skipped"]))
                else "RED"
            ),
        ),
        user_log.colorize(str(len(probing_result["alive"])), "GREEN") if probing_result["alive"] else "0",
        user_log.colorize(str(len(cmd_status["skipped"])), "RED" if cmd_status["skipped"] else "GREEN"),
        user_log.colorize(
            str(len(probing_result["disconnected"])), "RED" if probing_result["disconnected"] else "GREEN"
        ),
    )


def _get_clients(*ports):
    ports = " ".join(ports)
    cmd_str = f"fuser {ports}"
    logger.debug("Will run: %s", cmd_str)
    try:
        pids = str(subprocess.check_output(cmd_str, shell=True, stderr=subprocess.DEVNULL), encoding="utf-8")
    except subprocess.CalledProcessError:
        logger.debug("No clients for %s found", ports)
        return []
    pids = [pid.strip() for pid in pids.split()]
    pids = " ".join(set(pids))
    logger.debug("Clients of %s: %s", ports, pids)
    cmd_str = f"ps -o cmd= {pids}"
    logger.debug("Will run: %s", cmd_str)
    try:
        procs = str(subprocess.check_output(cmd_str, shell=True, stderr=subprocess.DEVNULL), encoding="utf-8")
        proc_names = []
        for proc in procs.split("\n"):
            stripped_proc = proc.strip()
            if stripped_proc and not stripped_proc.startswith("socat"):
                proc_names.append(stripped_proc)
        return proc_names
    except subprocess.CalledProcessError:
        logger.debug("No pid from %s is alive now", pids)
        return []


def _send_signal(signal, *ports):
    """
    Use pausing/resuming of processes, accessing port
    to handle cases, like <wb-mqtt-serial -c config.conf>
    """
    ports = " ".join(ports)
    cmd_str = f"fuser -k {signal} {ports}"
    logger.debug("Will run: %s", cmd_str)
    subprocess.call(cmd_str, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_clients(force, *ports):
    default_clients = set(
        [
            "/usr/bin/wb-mqtt-serial",
        ]
    )
    actual_clients = set(_get_clients(*ports))
    if actual_clients.difference(default_clients):
        if not ask_user(
            f"{', '.join(ports)} used by {', '.join(actual_clients)}; "
            + "Will be paused and resumed after finish",
            force,
        ):
            die(f'Stop {" ".join(actual_clients)} manually!')
    if actual_clients:
        _send_signal("-STOP", *ports)


def resume_clients(*ports):
    _send_signal("-CONT", *ports)


def get_port_settings(port_fname):
    """
    python-serial does not remember initial port settings (bd, parity, etc...)
    => restoring it manually after all operations to let wb-mqtt-serial work again
    """
    with open(port_fname, encoding="utf-8") as port:
        fd = port.fileno()
        return termios.tcgetattr(fd)


def set_port_settings(port_fname, termios_settings):
    with open(port_fname, encoding="utf-8") as port:
        termios.tcsetattr(port.fileno(), termios.TCSANOW, termios_settings)


def wait_for_wake_up(modbus_connection, timeout):
    """
    Wait for device to wake up after flashing.
    """
    min_tries = 2
    tries = 0
    start_time = time.time()
    while (time.time() - start_time < timeout) or (tries < min_tries):
        tries += 1
        if not modbus_connection.is_in_bootloader():
            return True
    logger.error("Device did not wake up after %d tries", tries)
    return False
