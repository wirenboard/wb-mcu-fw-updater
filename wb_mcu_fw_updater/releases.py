#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
import re
from collections import defaultdict
from . import CONFIG


def parse_releases(fname=CONFIG['RELEASES_FNAME']):
    """
    WirenBoard controllers have releases info, stored in file <CONFIG['RELEASES_FNAME']>
    Releases info file usually contains:
        RELEASE_NAME
        SUITE
        TARGET
        REPO_PREFIX
    """
    ret = defaultdict(default_factory=lambda: None)

    logging.debug("Reading %s for releases info" % fname)
    if os.path.exists(fname):
        ret = {k.strip(): v.strip() for k, v in (l.split('=', 1) for l in open(fname))}
        logging.debug("Got releases info:\n%s" % str(ret))
    else:
        logging.warning("Releases file %s not found" % fname)

    return ret


def get_release_file_urls(release_info, default_releases_file_url=CONFIG['FW_RELEASES_FILE_URL']):
    """
    Returns a list of remote release-file urls: [with-repo-prefix (if exists), default]
    """
    ret = []
    fname_suffix = release_info['REPO_PREFIX']
    if fname_suffix:
        fname_suffix = re.sub('[\W_]+', '~', fname_suffix)  # changing non letters or numbers to ~
        ret.append(default_releases_file_url.replace('.yaml', '.%s.yaml' % fname_suffix))
    ret.append(default_releases_file_url)
    return ret


def parse_fw_version(endpoint_url):
    """
    Parsing fw version from endpoint url, stored in releases file
    """
    extension = CONFIG['FW_EXTENSION']
    re_str = '.+/(.+)%s' % extension
    mat = re.match(re_str, endpoint_url)  # matches .../*.wbfw
    if mat:
        return mat.group(1)
    else:
        logging.warning("Could not parse fw version from %s by regexp %s" % (endpoint_url, re_str))
        return None
