#! /usr/bin/env python
"""Functions for working with the DLRN API"""

import csv
import os.path

from collections import namedtuple

import requests

from toolchest import yaml
from atkinson.config.manager import ConfigManager
from atkinson.logging.logger import getLogger


def _raw_fetch(url, logger):
    """
    Fetch remote data and return the text output.

    :param url: The URL to fetch the data from
    :param logger: A logger instance to use.
    :return: Raw text data, None otherwise
    """
    ret_data = None
    try:
        req = requests.get(url)
        if req.status_code == requests.codes.ok:
            ret_data = req.text
    except requests.exceptions.ConnectionError as error:
        logger.warning(error.request)

    return ret_data


def _fetch_yaml(url, logger):
    """
    Fetch remote data and process the text as yaml.

    :param url: The URL to fetch the data from
    :param logger: A logger instance to use.
    :return: Parsed yaml data in the form of a dictionary
    """
    ret_data = None
    raw_data = _raw_fetch(url, logger)
    if raw_data is not None:
        ret_data = yaml.parse(raw_data)

    return ret_data


def dlrn_http_factory(host, config_file=None, link_name=None,
                      logger=getLogger()):
    """
    Create a DlrnData instance based on a host.

    :param host: A host name string to build instances
    :param config_file: A dlrn config file(s) to use in addition to
                        the default.
    :param link_name: A dlrn symlink to use. This overrides the config files
                      link parameter.
    :param logger: An atkinson logger to use. Default is the base logger.
    :return: A DlrnData instance
    """
    manager = None
    files = ['dlrn.yml']
    if config_file is not None:
        if isinstance(config_file, list):
            files.extend(config_file)
        else:
            files.append(config_file)

    local_path = os.path.realpath(os.path.dirname(__file__))
    manager = ConfigManager(filenames=files, paths=local_path)

    if manager is None:
        return None

    config = manager.config
    if host not in config:
        return None

    link = config[host]['link']
    if link_name is not None:
        link = link_name

    return DlrnHttpData(config[host]['url'],
                        config[host]['release'],
                        link_name=link,
                        logger=logger)


class DlrnHttpData():
    """A class used to interact with the dlrn API"""

    hashes = namedtuple('DlrnHashes', ['commit', 'dist', 'extended'])

    def __init__(self, url, release, link_name='current', logger=getLogger()):
        """
        Class constructor

        :param url: The URL to the host to obtain data.
        :param releases: The release name to use for lookup.
        :param link_name: The name of the dlrn symlink to fetch data from.
        :param logger: An atkinson logger to use. Default is the base logger.
        """
        self.url = os.path.join(url, release)
        self.release = release
        self._logger = logger
        self._link_name = link_name
        self._commit_data = {}
        self._fetch_commit()

    def _fetch_commit(self):
        """
        Fetch the commit data from dlrn
        """
        full_url = os.path.join(self.url,
                                self._link_name,
                                'commit.yaml')
        data = _fetch_yaml(full_url, self._logger)
        if data is not None and 'commits' in data:
            pkg = data['commits'][0]
            if pkg['status'] == 'SUCCESS':
                self._commit_data = {'name': pkg['project_name'],
                                     'dist_hash': pkg['distro_hash'],
                                     'commit_hash': pkg['commit_hash'],
                                     'extended_hash': pkg.get('extended_hash')}
            else:
                msg = '{0} has a status of error'.format(str(pkg))
                self._logger.warning(msg)

    def _build_url(self, hash_data):
        """
        Generate a url given a commit hash and distgit hash to match the format
        base/AB/CD/ABCD123_XYZ987 where ABCD123 is the commit hash and XYZ987
        is a portion of the distgit hash.

        :param hashes hash_data: A namedtuple of hashes that make up a dlrn url
        :return: A string with the full URL.
        """
        first = hash_data.commit[0:2]
        second = hash_data.commit[2:4]
        third = hash_data.commit
        for key in ['dist', 'extended']:
            # Note 'None' is a valid entry in the dlrn data
            # for dist and extended hash
            data = getattr(hash_data, key, 'None')
            if data != 'None':
                third += '_' + data[0:8]
        return os.path.join(self.url,
                            first,
                            second,
                            third)

    def _parse_csv(self, url):
        """
        Generator that parsers a CVS file and returns each row

        :param str url: The url to fetch the CSV file to parse
        :return: A row from the CSV file
        """
        data = _raw_fetch(url, self._logger)
        if data is not None:
            data = data.replace(' ', '_')
            split_data = data.split()
            reader = csv.DictReader(split_data)
            for row in reader:
                yield row
        else:
            self._logger.error(f"Could not fetch {url}!")

    @property
    def commit(self):
        """
        Get the dlrn commit information

        :return: A dictionary of name, dist-git hash, commit hash and
                 extended hash.
                 An empty dictionary is returned otherwise.
        """
        return self._commit_data

    @property
    def versions(self):
        """
        Get the version data for the versions.csv file and return the
        data in a dictionary

        :return: A dictionary of packages with commit and dist-git hashes
        """
        ret_dict = {}
        version_hash = self.hashes(self._commit_data.get('commit_hash'),
                                   self._commit_data.get('dist_hash'),
                                   self._commit_data.get('extended_hash'))
        full_url = os.path.join(self._build_url(version_hash),
                                'versions.csv')
        for row in self._parse_csv(full_url):
            ret_dict[row['Project']] = {'source': row['Source_Sha'],
                                        'state': row['Status'],
                                        'distgit': row['Dist_Sha'],
                                        'nvr': row['Pkg_NVR']}
        return ret_dict

    def get_failures(self):
        """
        Get a dictionary of failed packages and links to build information
        """
        failed = {}

        # Pull status report data to determine what is currently failing
        status_url = os.path.join(self.url, 'status_report.csv')
        for row in self._parse_csv(status_url):
            if row['Status'] == 'FAILED':
                failed_hash = self.hashes(row['Source_Sha'],
                                          row['Dist_Sha'],
                                          row['Extended_Sha'])
                failed[row['Project']] = self._build_url(failed_hash)
        return failed
