import logging
import os
from posixpath import join as urljoin
from . import die, PYTHON2, CONFIG

if PYTHON2:
    import urllib2 as url_handler
    HTTPError = url_handler.HTTPError
    URLError = url_handler.URLError
else:
    import urllib.request as url_handler
    import urllib.error
    HTTPError = urllib.error.HTTPError
    URLError = urllib.error.URLError


def perform_head_request(url_path):
    """
    Performing a HEAD http request to defined url.

    :param url_path: full url, request will be sent to
    :type url_path: str
    :return: a responce object
    :rtype: responce obj from urllib (or urllib2 in python2)
    """
    if PYTHON2:
        request = url_handler.Request(url_path)
        request.get_method = lambda : 'HEAD'
    else:
        request = url_handler.Request(url_path, method='HEAD')
    return url_handler.urlopen(request)


def perform_get_request(url_path):
    """
    Performing a GET http request to defined url.

    :param url_path: full url, request will be sent to
    :type url_path: str
    :return: a responce object
    :rtype: responce obj from urllib (or urllib2 in python2)
    """
    return url_handler.urlopen(url_path)


class RemoteFileWatcher(object):
    """
    A class, downloading Firmware or Bootloader, found by device_signature or project_name from remote server.
    """
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
        self.mode = mode
        self._check_url_is_available(CONFIG['ROOT_URL'])
        self.parrent_url_path = urljoin(CONFIG['ROOT_URL'], mode, sort_by)
        if branch_name:
            self._BRANCH = branch_name
            logging.debug('Looking to unstable branch: %s' % branch_name)
            self._SOURCE = urljoin('unstable', branch_name)

    def _check_url_is_available(self, url_path):
        """
        Checking url accessibility by sending HEAD request to it and catching urllib's errors.

        :param url_path: url, need to be checked
        :type url_path: str
        """
        logging.debug('Checking url: %s' % url_path)
        try:
            perform_head_request(url_path)
        except (HTTPError, URLError) as e:
            die('Error, while opening %s (%s)' % (url_path, str(e)))

    def _get_request_content(self, url_path):
        """
        Checking, is url_path available; sending GET request to it; returning responce's content.

        :param url_path: url, request will be sent to
        :type url_path: str
        :return: responce's content
        :rtype: bytestring
        """
        self._check_url_is_available(url_path)
        responce = perform_get_request(url_path)
        ret = responce.read()
        return ret.strip()

    def _construct_urlpath(self, name):
        """
        Appending url from parts (parent url, significant part, stable or feature branch), excepting filename.

        :param name: a significant part of url. Could be a project_name or device_signature
        :type name: str
        :return: constructed url without filename
        :rtype: str
        """
        return urljoin(self.parrent_url_path, name, CONFIG['DEFAULT_SOURCE'])

    def get_latest_version_number(self, name):
        """
        Latest fw or bootloader version number is stored into a text file on server.

        :param name: could be a device_signature or project_name
        :type name: str
        :return: content of text file, where latest fw version number is stored
        :rtype: str
        """
        url_path = urljoin(self._construct_urlpath(name), CONFIG['LATEST_FW_VERSION_FILE'])
        return self._get_request_content(url_path).decode('utf-8')

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
        fw_ver = '%s%s' % (version, CONFIG['FW_EXTENSION'])
        url_path = urljoin(self._construct_urlpath(name), fw_ver)
        content = self._get_request_content(url_path)
        file_saving_dir = os.path.join(CONFIG['FW_SAVING_DIR'], self.mode)
        if not fname:
            if not os.path.isdir(file_saving_dir):
                os.mkdir(file_saving_dir)
            fname = '%s_%s_%s' % (name, self._BRANCH, fw_ver)
            fpath = os.path.join(file_saving_dir, fname)
        else:
            fpath = fname
        logging.debug('Downloading to: %s' % fpath)
        with open(fpath, 'wb+') as fh:
            fh.write(content)
        return fpath
