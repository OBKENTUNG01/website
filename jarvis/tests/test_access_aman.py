"""Uji mode akses: hanya boolean `true` yang berarti penuh, dan tulisan atomik."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import access, config, dashboard


class AccessModeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patch = mock.patch.object(config, "DATA_DIR", self.dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _tulis(self, isi):
        with open(access._file(), "w", encoding="utf-8") as f:
            json.dump(isi, f)

    def test_string_false_bukan_penuh(self):
        """access.json berisi "false" (string) tidak boleh menyalakan mode penuh."""
        self._tulis({"full": "false"})
        self.assertFalse(access.full())
        self._tulis({"full": 1})
        self.assertFalse(access.full())

    def test_boolean_true_penuh(self):
        self._tulis({"full": True})
        self.assertTrue(access.full())

    def test_set_full_menolak_nilai_bukan_boolean(self):
        access.set_full("false")
        with open(access._file(), encoding="utf-8") as f:
            self.assertIs(json.load(f)["full"], False)
        self.assertFalse(access.full())

    def test_tulisan_atomik_tanpa_sisa_tmp(self):
        access.set_full(True)
        self.assertEqual(os.listdir(self.dir), ["access.json"])


class ProxyPeerTest(unittest.TestCase):
    def test_hanya_tunnel_lokal(self):
        for peer in ("127.0.0.1", "::1", "::1%0"):
            self.assertTrue(dashboard._proxy_peer_ok(peer), peer)
        for peer in ("8.8.8.8", "203.0.113.7", "", None, "192.168.1.5"):
            self.assertFalse(dashboard._proxy_peer_ok(peer), peer)


if __name__ == "__main__":
    unittest.main()
