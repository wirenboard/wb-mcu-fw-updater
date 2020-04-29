import requests
import logging
import os
from posixpath import join as urljoin
from . import die


ROOT_URL = 'http://fw-releases.wirenboard.com/'
FW_SAVING_DIR = os.path.join(os.path.dirname(__file__), 'firmwares')


class RemoteFileWatcher(object):
    _EXTENSION = '.wbfw'
    _LATEST_FW_VERSION_FILE = 'latest.txt'
    _LATEST_FW_FILE = 'latest.wbfw'
    _SOURCE = 'stable'
    _BRANCH = ''
    """
    Downloading WB-Devices FW from remote amazon storage
    """
    def __init__(self, mode='fw', sort_by='by-signature', branch_name=None):
        if self._check_url_is_available(ROOT_URL):
            self.parrent_url_path = urljoin(ROOT_URL, mode, sort_by) 
        else:
            die('FW download server is unavailable. Check your Internet connection!')
        if branch_name:
            self._BRANCH = branch_name
            logging.debug('Looking to unstable branch: %s' % branch_name)
            self._SOURCE = urljoin('unstable', branch_name)

    def _check_url_is_available(self, url_path):
        """
        Sending HEAD reauest to <url_path> and looking into http status code
        """
        logging.debug('Checking url: %s' % url_path)
        _max_allowed_http_code = 400
        ret = requests.head(url_path)
        return True if ret.status_code < _max_allowed_http_code else False

    def _get_latest_fw_version(self, url_path):
        """
        Latest FW version number is contained into <url>/<_LATEST_FW_LOCATION> (ex: ***/***/***/latest.txt)
        Getting from full url path
        """
        logging.debug('Checking url: %s' % url_path)
        if self._check_url_is_available(url_path):
            ret = requests.get(url_path)
            return ret.content
        else:
            die('Wrong url: %s' % url_path)

    def _make_full_url(self, name):
        return urljoin(self.parrent_url_path, name, self._SOURCE)

    def get_latest_fw_version(self, name):
        url_path = urljoin(self._make_full_url(name), self._LATEST_FW_VERSION_FILE)
        return self._get_latest_fw_version(url_path)

    def get_fw_file(self, name, version='latest', fname=None):
        if not os.path.isdir(FW_SAVING_DIR):
            os.mkdir(FW_SAVING_DIR)
        fw_ver = '%s%s' % (version, self._EXTENSION)
        url_path = urljoin(self._make_full_url(name), fw_ver)
        if self._check_url_is_available(url_path):
            fname = '%s_%s_%s' % (name, self._BRANCH, fw_ver)
            fpath = os.path.join(FW_SAVING_DIR, fname)
            logging.debug('Downloading to: %s' % fpath)
            ret = requests.get(url_path)
            with open(fpath, 'wb+') as fh:
                fh.write(ret.content)
        else:
            die('Url %s is not available. Check params!' % url_path)
        return fpath
