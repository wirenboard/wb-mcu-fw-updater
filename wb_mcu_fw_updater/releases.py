#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
from collections import defaultdict
from posixpath import join as urljoin  # py2/3 compatibility

from . import CONFIG, logger


class VersionParsingError(Exception):
    pass


def parse_releases(
    fname=CONFIG["RELEASES_FNAME"],
):  # maybe it would be better to look at wb-update-manager package
    """
    WirenBoard controllers have releases info, stored in file <CONFIG['RELEASES_FNAME']>
    Releases info file usually contains:
        RELEASE_NAME
        SUITE
        TARGET
        REPO_PREFIX
    """
    ret = defaultdict(lambda: None)

    logger.debug("Reading %s for releases info", fname)
    with open(fname, encoding="utf-8") as fp:
        ret.update({k.strip(): v.strip() for k, v in (l.split("=", 1) for l in fp)})
        logger.debug("Got releases info:")
        logger.debug("\t%s", str(ret))
        return ret


def get_release_file_urls(
    release_info, default_releases_file_url=urljoin(CONFIG["ROOT_URL"], CONFIG["FW_RELEASES_FILE_URI"])
):
    """
    Returns a list of remote release-file urls: [with-repo-prefix (if exists), default]
    """
    ret = []
    fname_suffix = release_info["REPO_PREFIX"]
    if fname_suffix:
        fname_suffix = re.sub(r"[\W_]+", "~", fname_suffix)  # changing non letters or numbers to ~
        ret.append(default_releases_file_url.replace(".yaml", f".{fname_suffix}.yaml"))
    ret.append(default_releases_file_url)
    logger.debug("FW releases files: %s", str(ret))
    return ret


def parse_fw_version(endpoint_url):
    """
    Parsing fw version from endpoint url, stored in releases file
    """
    re_str = f".+/(?P<wbfw_version>.+){CONFIG['FW_EXTENSION']}|.+/(?P<compfw_version>.+){CONFIG['COMPONENTS_FW_EXTENSION']}"  # pylint:disable=line-too-long
    mat = re.match(re_str, endpoint_url)  # matches .../*.wbfw
    if mat:
        return mat.groupdict().get("wbfw_version") or mat.groupdict().get("compfw_version")
    raise VersionParsingError(f"Could not parse fw version from {endpoint_url} by regexp {re_str}")
