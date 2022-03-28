#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
import errno
from posixpath import join as urljoin  # py2/3 compatibility
from . import PYTHON2, CONFIG, die

if PYTHON2:
    import urllib2 as url_handler
    from urllib2 import HTTPError, URLError
else:
    import urllib.request as url_handler
    from urllib.error import HTTPError, URLError


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
    logging.debug('GET: %s' % url_path)
    for _ in range(tries):
        try:
            return url_handler.urlopen(url_path)
        except (URLError, HTTPError) as e:
            continue
    else:
        raise WBRemoteStorageError(url_path)


"""
Ensuring, User has Internet connection
executes at first import
"""
try:
    get_request(CONFIG['ROOT_URL'])
except WBRemoteStorageError as e:
    die("%s is not accessible. Check Internet connection!" % CONFIG['ROOT_URL'])


def read_remote_file(url_path, coding='utf-8'):
    try:
        ret = get_request(url_path)
        return str(ret.read().decode(coding)).strip()
    except Exception as e:
        raise RemoteFileReadingError(e)


def get_remote_releases_info(remote_fname=urljoin(CONFIG['ROOT_URL'], CONFIG['FW_RELEASES_FILE_URI'])):
    return read_remote_file(remote_fname)


def get_fw_signatures_list():
    ret = read_remote_file(urljoin(CONFIG['ROOT_URL'], CONFIG['FW_SIGNATURES_FILE_URI']))
    return ret.split('\n') if ret else None


def download_remote_file(url_path, saving_dir=None, fname=None):
    """
    Downloading a file from direct url
    """
    try:
        ret = get_request(url_path)
        content = ret.read()
    except Exception as e:
        raise RemoteFileDownloadingError(e)

    saving_dir = saving_dir or CONFIG['FW_SAVING_DIR']
    try:
        os.makedirs(saving_dir)  # py2 has not exist_ok param
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise RemoteFileDownloadingError(e)

    if not fname:
        logging.debug("Trying to get fname from content-disposition")
        default_fname = ret.info().get('Content-Disposition')
        fname = default_fname.split('filename=')[1].strip('"\'') if default_fname else None
        logging.debug("Got fname from content-disposition: %s" % str(fname))
    if fname:
        file_path = os.path.join(saving_dir, fname)
        logging.debug("%s => %s" % (url_path, file_path))
    else:
        raise RemoteFileDownloadingError("Could not construct fpath, where to save fw. Fname should be specified!")

    try:
        with open(file_path, 'wb+') as fh:
            fh.write(content)
            return file_path
    except Exception as e:
        raise RemoteFileDownloadingError(e)


class RemoteFileWatcher(object):
    """
    A class, downloading Firmware or Bootloader, found by device_signature or project_name from remote server.
    """
    def __init__(self, mode='fw', sort_by='by-signature', branch_name=''):
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
        self.parent_url_path = urljoin(CONFIG['ROOT_URL'], mode, sort_by)
        self.fw_source = CONFIG['DEFAULT_SOURCE']
        self.branch_name = branch_name
        if branch_name:
            self.fw_source = urljoin('unstable', branch_name)

    def _construct_urlpath(self, name):
        """
        Appending url from parts (parent url, significant part, stable or feature branch), excepting filename.

        :param name: a significant part of url. Could be a project_name or device_signature
        :type name: str
        :return: constructed url without filename
        :rtype: str
        """
        return urljoin(self.parent_url_path, name, self.fw_source)

    def get_latest_version_number(self, name):
        """
        Latest fw or bootloader version number is stored into a text file on server.

        :param name: could be a device_signature or project_name
        :type name: str
        :return: content of text file, where latest fw version number is stored
        :rtype: str
        """
        url_path = urljoin(self._construct_urlpath(name), CONFIG['LATEST_FW_VERSION_FILE'])
        return read_remote_file(url_path)

    def download(self, name, version='latest'):
        """
        Downloading a firmware/bootloader file with specified version to specified fname.

        :param name: a project_name or device_signature
        :type name: str
        :param version: specified fw/bootloader version, defaults to 'latest'
        :type version: str, optional
        :return: path of saved file
        :rtype: str (if succeed) or None (if not)
        """
        fw_ver = '%s%s' % (version, CONFIG['FW_EXTENSION'])
        url_path = urljoin(self._construct_urlpath(name), fw_ver)
        file_saving_dir = os.path.join(CONFIG['FW_SAVING_DIR'], self.mode)

        try:
            return download_remote_file(url_path, file_saving_dir)
        except Exception as e:
            logging.error('Could not download:\n\tURL: %s (%s %s %s)\n\tSave to: %s' % (
                url_path,
                name,
                version,
                self.branch_name,
                file_saving_dir
            ))
            raise
