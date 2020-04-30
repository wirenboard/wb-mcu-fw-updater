import requests
import logging
import os
from posixpath import join as urljoin
from . import die


ROOT_URL = 'http://fw-releases.wirenboard.com/' #TODO: fill from config/env vars
FW_SAVING_DIR = os.path.join(os.path.dirname(__file__), 'firmwares')


class RemoteFileWatcher(object):
    """
    A class, downloading Firmware from remote server.

    """
    _EXTENSION = '.wbfw'
    _LATEST_FW_VERSION_FILE = 'latest.txt'
    _LATEST_FW_FILE = 'latest.wbfw'
    _SOURCE = 'stable'
    _BRANCH = ''

    def __init__(self, mode='fw', sort_by='by-signature', branch_name=None):
        """
        Could download firmware or bootloder files from stable or specified branch.

        :param mode: firmware or bootloader, defaults to 'fw'
        :type mode: str, optional
        :param sort_by: files on remote server are stored by project_name or device_signature, defaults to 'by-signature'
        :type sort_by: str, optional
        :param branch_name: looking for fw/bootloader from specified branch (instead of stable), defaults to None
        :type branch_name: str, optional
        """
        if self._check_url_is_available(ROOT_URL):
            self.parrent_url_path = urljoin(ROOT_URL, mode, sort_by) 
        else:
            die('FW download server is unavailable. Check your Internet connection!')
        if branch_name:
            self._BRANCH = branch_name
            logging.debug('Looking to unstable branch: %s' % branch_name)
            self._SOURCE = urljoin('unstable', branch_name)

    def _check_url_is_available(self, url_path, max_allowed_retcode=400):
        """
        Checking url accessibility by sending HEAD request to it and anallyzing http return code.

        :param url_path: url, need to be checked
        :type url_path: str
        :param max_allowed_retcode: http request code should be less, than. Defaults to 400
        :type max_allowed_retcode: int, optional
        :return: has url accessible or not
        :rtype: bool
        """
        logging.debug('Checking url: %s' % url_path)
        ret = requests.head(url_path)
        return True if ret.status_code < max_allowed_retcode else False

    def _get_request_content(self, url_path):
        """
        Checking, is url_path available; sending GET request to it; returning responce's content.

        :param url_path: url, request will be sent to
        :type url_path: str
        :return: responce's content
        :rtype: bytestring
        """
        if self._check_url_is_available(url_path):
            ret = requests.get(url_path)
            return ret.content.strip()
        else:
            die('Wrong url: %s' % url_path)

    def _construct_urlpath(self, name):
        """
        Appending url from parts (parent url, significant part, stable or feature branch), excepting filename.

        :param name: a significant part of url. Could be a project_name or device_signature
        :type name: str
        :return: constructed url without filename
        :rtype: str
        """
        return urljoin(self.parrent_url_path, name, self._SOURCE)

    def get_latest_version_number(self, name):
        """
        Latest fw or bootloader version number is stored into a text file on server.

        :param name: could be a device_signature or project_name
        :type name: str
        :return: content of text file, where latest fw version number is stored
        :rtype: bytestring
        """
        url_path = urljoin(self._construct_urlpath(name), self._LATEST_FW_VERSION_FILE)
        return self._get_request_content(url_path)

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
        :rtype: str
        """
        fw_ver = '%s%s' % (version, self._EXTENSION)
        url_path = urljoin(self._construct_urlpath(name), fw_ver)
        content = self._get_request_content(url_path)
        if not fname:
            if not os.path.isdir(FW_SAVING_DIR):
                os.mkdir(FW_SAVING_DIR)
            fname = '%s_%s_%s' % (name, self._BRANCH, fw_ver)
            fpath = os.path.join(FW_SAVING_DIR, fname)
        else:
            fpath = fname
        logging.debug('Downloading to: %s' % fpath)
        with open(fpath, 'wb+') as fh:
            fh.write(content)
        return fpath
