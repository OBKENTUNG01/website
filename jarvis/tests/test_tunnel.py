"""Uji Cloudflare Quick Tunnel: tangkap alamat trycloudflare & temukan cloudflared."""

import os
import unittest
from unittest import mock

from itsme import config, tunnel


class FindUrlTest(unittest.TestCase):
    def test_finds_trycloudflare_url(self):
        line = "2026-10-23T00:00Z INF |  https://sweet-river-1234.trycloudflare.com  |"
        self.assertEqual(tunnel.find_url(line), "https://sweet-river-1234.trycloudflare.com")

    def test_none_when_absent(self):
        self.assertIsNone(tunnel.find_url("INF Starting tunnel connection"))
        self.assertIsNone(tunnel.find_url(""))
        self.assertIsNone(tunnel.find_url(None))


class CloudflaredPathTest(unittest.TestCase):
    def test_uses_config_bin_if_exists(self):
        with mock.patch.object(config, "TUNNEL_BIN", __file__):  # berkas ini pasti ada
            self.assertEqual(tunnel.cloudflared_path(), __file__)

    def test_falls_back_to_which(self):
        with mock.patch.object(config, "TUNNEL_BIN", ""), \
             mock.patch.object(tunnel.shutil, "which", return_value="C:/cf/cloudflared.exe"):
            self.assertEqual(tunnel.cloudflared_path(), "C:/cf/cloudflared.exe")

    def test_none_when_missing(self):
        with mock.patch.object(config, "TUNNEL_BIN", ""), \
             mock.patch.object(tunnel.shutil, "which", return_value=None), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": os.path.join(os.sep, "no-such-xyz")}, clear=False):
            self.assertIsNone(tunnel.cloudflared_path())


class QuickTunnelAnnounceTest(unittest.TestCase):
    def test_announce_called_once_per_new_url(self):
        # Proses palsu yang mengeluarkan satu baris berisi alamat; callback
        # menghentikan loop supaya cloudflared tidak dijalankan lagi.
        urls = []
        qt = tunnel.QuickTunnel(8750, None)

        def cb(u):
            urls.append(u)
            qt._stop.set()
        qt.on_url = cb

        class FakeProc:
            def __init__(self):
                self.stdout = iter(["INF booting",
                                    "|  https://abc-def.trycloudflare.com  |",
                                    "INF ready"])
            def wait(self, timeout=None):
                return 0
            def poll(self):
                return 0

        with mock.patch.object(tunnel, "cloudflared_path", return_value="cf"), \
             mock.patch.object(tunnel.subprocess, "Popen", return_value=FakeProc()):
            qt._run()
        self.assertEqual(urls, ["https://abc-def.trycloudflare.com"])
        self.assertIsNone(qt.url)   # di-reset setelah proses mati


if __name__ == "__main__":
    unittest.main()
