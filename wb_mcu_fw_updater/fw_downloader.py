#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
from posixpath import join as urljoin
from . import PYTHON2, CONFIG, die

if PYTHON2:
    import urllib2 as url_handler
    from urllib2 import HTTPError, URLError
else:
    import urllib.request as url_handler
    from urllib.error import HTTPError, URLError


def get_request_content(url_path):
    """
    Sending GET request to url; returning responce's content.

    :param url_path: url, request will be sent to
    :type url_path: str
    :return: responce's content
    :rtype: bytestring
    """
    logging.debug('Looking to: %s' % url_path)
    responce = url_handler.urlopen(url_path)
    ret = responce.read()
    return ret


def get_fw_signatures_list():
    try:
        contents = get_request_content(CONFIG['FW_SIGNATURES_FILE_URL']).decode('utf-8')
        return str(contents).strip().split('\n')
    except (URLError, HTTPError) as e:
        logging.exception(e)
        return None


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
        try:
            url_handler.urlopen(CONFIG['ROOT_URL']) # Checking, user has internet connection
        except (URLError, HTTPError) as e:
            logging.error('Check internet connection')
            die(e)
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
        try:
            content = get_request_content(url_path).decode('utf-8')
            return str(content).strip()
        except HTTPError as e:
            logging.error("Not Found: %s" % url_path)
            return None

    def download(self, name, version='latest', fname=None):
        """
        Downloading a firmware/bootloader file with specified version to specified fname.

        :param name: a project_name or device_signature
        :type name: str
        :param version: specified fw/bootloader version, defaults to 'latest'
        :type version: str, optional
        :param fname: custom path, file will be saved, defaults to None
        :type fname: str, optional
        :return: path of saved file
        :rtype: str (if succeed) or None (if not)
        """
        fw_ver = '%s%s' % (version, CONFIG['FW_EXTENSION'])
        url_path = urljoin(self._construct_urlpath(name), fw_ver)
        try:
            content = get_request_content(url_path)
        except HTTPError as e:
            logging.error('Could not find the firmware: signature %s, version %s, branch %s' % (
                name,
                version,
                self.branch_name
            ))
            logging.exception(e)
            return None
        file_saving_dir = os.path.join(CONFIG['FW_SAVING_DIR'], self.mode)
        if not fname:
            if not os.path.isdir(file_saving_dir):
                os.mkdir(file_saving_dir)
            fname = '%s_%s_%s' % (name, self.branch_name, fw_ver)
            fpath = os.path.join(file_saving_dir, fname)
        else:
            fpath = fname
        logging.debug('Downloading to: %s' % fpath)
        try:
            fh = open(fpath, 'wb+')
            fh.write(content)
            fh.close()
        except PermissionError as e:
            logging.exception(e)
            return None
        return fpath
