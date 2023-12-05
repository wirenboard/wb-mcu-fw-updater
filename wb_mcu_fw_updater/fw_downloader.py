#!/usr/bin/env python
# -*- coding: utf-8 -*-

import errno
import os
import sys

import six
from six.moves import urllib

from . import CONFIG, MODE_FW, logger


class WBRemoteStorageError(Exception):
    pass


class RemoteFileReadingError(WBRemoteStorageError):
    pass


class RemoteFileDownloadingError(WBRemoteStorageError):
    pass


def get_request(url_path, tries=3):  # TODO: to config?
    """
    Sending GET request to url; returning responce's content.

    :param url_path: url, request will be sent to
    :type url_path: str
    :return: responce's content
    :rtype: bytestring
    """
    logger.debug("GET: %s", url_path)
    for _ in range(tries):
        try:
            return urllib.request.urlopen(url_path)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            continue
    else:
        raise WBRemoteStorageError(url_path)


def read_remote_file(url_path, coding="utf-8"):
    try:
        ret = get_request(url_path)
        return str(ret.read().decode(coding)).strip()
    except Exception as e:
        six.raise_from(RemoteFileReadingError, e)


def get_remote_releases_info(
    remote_fname=urllib.parse.urljoin(CONFIG["ROOT_URL"], CONFIG["FW_RELEASES_FILE_URI"])
):
    return read_remote_file(remote_fname)


def get_fw_signatures_list():
    ret = read_remote_file(urllib.parse.urljoin(CONFIG["ROOT_URL"], CONFIG["FW_SIGNATURES_FILE_URI"]))
    return ret.split("\n") if ret else None


def download_remote_file(url_path, saving_dir=None, fname=None):
    """
    Downloading a file from direct url
    """
    try:
        ret = get_request(url_path)
        content = ret.read()
    except Exception as e:
        six.raise_from(RemoteFileDownloadingError, e)

    saving_dir = saving_dir or CONFIG["FW_SAVING_DIR"]
    try:
        os.makedirs(saving_dir)  # py2 has not exist_ok param
    except OSError as e:
        if e.errno != errno.EEXIST:
            six.reraise(*sys.exc_info())

    if not fname:
        logger.debug("Trying to get fname from content-disposition")
        default_fname = ret.info().get("Content-Disposition")
        fname = (
            default_fname.split("filename=")[1].strip("\"'")
            if default_fname
            else "tmp%s" % CONFIG["FW_EXTENSION"]
        )
        logger.debug("Got fname: %s", str(fname))
    if fname:
        file_path = os.path.join(saving_dir, fname)
        logger.debug("%s => %s", url_path, file_path)
    else:
        raise RemoteFileDownloadingError(
            "Could not construct fpath, where to save fw. Fname should be specified!"
        )

    try:
        with open(file_path, "wb+") as fh:
            fh.write(content)
            return file_path
    except Exception as e:
        six.raise_from(RemoteFileDownloadingError, e)


class RemoteFileWatcher(object):
    """
    A class, downloading Firmware or Bootloader, found by device_signature or project_name from remote server.
    """

    def __init__(self, mode=MODE_FW, sort_by="by-signature", branch_name=""):
        """
        Could download firmware or bootloder files from stable or specified branch.

        :param mode: firmware or bootloader, defaults to 'fw'
        :type mode: str, optional
        :param sort_by: files on remote server are stored by project_name or device_signature, defaults to 'by-signature'
        :type sort_by: str, optional
        :param branch_name: looking for fw/bootloader from specified branch (instead of stable), defaults to None
        :type branch_name: str, optional
        """
        self.mode = mode
        fw_source = "unstable/%s" % branch_name if branch_name else CONFIG["DEFAULT_SOURCE"]
        self.parent_url_path = self._join(self.mode, sort_by, "%s", fw_source)  # fw_sig or device_sig

    def _join(self, *args):
        return "/".join(map(str, args))

    def get_latest_version_number(self, name):
        """
        Latest fw or bootloader version number is stored into a text file on server.

        :param name: could be a device_signature or project_name
        :type name: str
        :return: content of text file, where latest fw version number is stored
        :rtype: str
        """
        remote_path = self._join(self.parent_url_path % name, CONFIG["LATEST_FW_VERSION_FILE"])
        url_path = urllib.parse.urljoin(CONFIG["ROOT_URL"], remote_path)
        return read_remote_file(url_path)

    def download(self, name, version="latest"):
        """
        Downloading a firmware/bootloader file with specified version to specified fname.

        :param name: a project_name or device_signature
        :type name: str
        :param version: specified fw/bootloader version, defaults to 'latest'
        :type version: str, optional
        :return: path of saved file
        :rtype: str (if succeed) or None (if not)
        """
        fw_ver = "%s%s" % (version, CONFIG["FW_EXTENSION"])
        remote_path = self._join(self.parent_url_path % name, fw_ver)
        url_path = urllib.parse.urljoin(CONFIG["ROOT_URL"], remote_path)
        file_saving_dir = os.path.join(CONFIG["FW_SAVING_DIR"], self.mode)

        try:
            return download_remote_file(url_path, file_saving_dir)
        except Exception as e:
            logger.error("Could not download: %s", url_path)
            logger.error("Remote path: %s", remote_path)
            logger.error("Save to: %s", file_saving_dir)
            six.reraise(*sys.exc_info())
