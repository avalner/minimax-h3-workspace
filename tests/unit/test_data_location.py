"""Where the data folder is: a share or a synchronised folder is a risk the user must hear about.

    python tests/unit/test_data_location.py
"""

from __future__ import annotations

import unittest

import support

import datadir


class DataLocationTest(unittest.TestCase):
    def test_a_local_folder_is_fine(self):
        self.assertIsNone(datadir.check_location(support.DATA))
        self.assertIsNone(datadir.check_location(r"C:\Projects\minimax-h3-workspace\app\data"))

    def test_a_network_share_is_reported(self):
        text = datadir.check_location(r"\\server\share\h3studio-data")
        self.assertIsNotNone(text)
        self.assertIn("сетев", text.lower())

    def test_a_synchronised_folder_is_reported(self):
        cases = ((r"C:\Users\me\OneDrive\H3", "OneDrive"),
                 (r"D:\Dropbox\H3", "Dropbox"),
                 (r"C:\Users\me\Google Drive\H3", "Google Drive"),
                 (r"C:\Users\me\YandexDisk\H3", "Яндекс.Диск"))
        for path, name in cases:
            text = datadir.check_location(path)
            self.assertIsNotNone(text, path)
            self.assertIn(name, text, path)
            self.assertTrue(len(text) > 40, "предупреждение должно объяснять, что делать")

    def test_the_check_does_not_stop_on_a_folder_that_does_not_exist_yet(self):
        self.assertIsNone(datadir.check_location(r"C:\Projects\not-created-yet\h3studio"))


if __name__ == "__main__":
    unittest.main()
