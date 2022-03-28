import unittest
from wb_mcu_fw_updater import fw_downloader


class S3InteractingTest(unittest.TestCase):

    def test_request(self, dummy_url='http://wb-test-dummy-url.com/'):
        working_url = 'http://fw-releases.wirenboard.com/'
        dummy_url = 'http://wb-test-dummy-url.com/'
        fw_downloader.get_request(working_url)
        self.assertRaises(fw_downloader.WBRemoteStorageError, lambda: fw_downloader.get_request(dummy_url))
