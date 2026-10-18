"""Uji "satu bot, banyak perangkat": daftar perangkat, pendaftaran ke Hub,
dan pemeriksaan konfigurasi mode perangkat (agent)."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import httpx

from itsme import config, devices


class DevicesRegistryTest(unittest.TestCase):
    """Sisi Hub: catat, daftar, dan lupakan perangkat."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        p = mock.patch.object(config, "DATA_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_register_dedups_case_insensitively_and_updates_url(self):
        self.assertTrue(devices.register("Laptop", "http://a:8750"))    # baru
        self.assertFalse(devices.register("laptop", "http://a:8751"))   # sama (tak peduli huruf) -> bukan baru
        items = devices.listing()
        self.assertEqual(len(items), 1)                                 # tidak dobel
        self.assertEqual(items[0]["url"], "http://a:8751")              # alamat diperbarui

    def test_listing_online_flag(self):
        devices.register("PC", "http://a:8750")
        self.assertTrue(devices.listing()[0]["online"])                 # baru daftar -> online
        with mock.patch.object(devices, "ONLINE_WINDOW", -1):
            self.assertFalse(devices.listing()[0]["online"])            # ambang -1 -> offline

    def test_forget(self):
        devices.register("PC", "http://a:8750")
        self.assertTrue(devices.forget("pc"))                           # tak peduli huruf
        self.assertEqual(devices.listing(), [])
        self.assertFalse(devices.forget("pc"))                          # sudah tidak ada

    def test_name_trimmed_and_capped(self):
        long_name = "x" * 200
        devices.register(long_name, "http://a:8750")
        self.assertEqual(len(devices.listing()[0]["name"]), 60)

    def test_corrupt_file_tolerated(self):
        with open(os.path.join(self.dir, "devices.json"), "w", encoding="utf-8") as f:
            f.write("{bukan json")
        self.assertEqual(devices.listing(), [])                         # tidak meledak


class RegisterToHubTest(unittest.TestCase):
    """Sisi Perangkat (agent): mendaftar/heartbeat ke Hub lewat HTTP."""

    def test_no_hub_url_skips(self):
        with mock.patch.object(config, "HUB_URL", ""), \
             mock.patch.object(config, "HUB_SECRET", "s"), \
             mock.patch.object(devices.httpx, "post") as post:
            self.assertFalse(devices.register_to_hub())
            post.assert_not_called()

    def test_posts_registration_with_secret(self):
        resp = mock.Mock(status_code=200)
        with mock.patch.object(config, "HUB_URL", "http://hub:8750"), \
             mock.patch.object(config, "HUB_SECRET", "s3cr3t"), \
             mock.patch.object(config, "DEVICE_NAME", "laptop"), \
             mock.patch.object(config, "DASHBOARD_PORT", 8750), \
             mock.patch.object(devices.httpx, "post", return_value=resp) as post:
            self.assertTrue(devices.register_to_hub())
        self.assertEqual(post.call_args[0][0], "http://hub:8750/api/devices/register")
        body = post.call_args[1]["json"]
        self.assertEqual(body["name"], "laptop")
        self.assertEqual(body["secret"], "s3cr3t")
        self.assertEqual(body["port"], 8750)
        self.assertEqual(post.call_args[1]["headers"]["X-Itsme-Dashboard"], "1")

    def test_http_error_returns_false(self):
        with mock.patch.object(config, "HUB_URL", "http://hub:8750"), \
             mock.patch.object(config, "HUB_SECRET", "s"), \
             mock.patch.object(devices.httpx, "post", side_effect=httpx.ConnectError("mati")):
            self.assertFalse(devices.register_to_hub())


class ConfigAgentModeTest(unittest.TestCase):
    """config.check() di mode perangkat: tanpa Telegram, wajib secret + dashboard."""

    def test_agent_mode_requires_secret(self):
        with mock.patch.object(config, "AGENT_MODE", True), \
             mock.patch.object(config, "HUB_SECRET", ""), \
             mock.patch.object(config, "DASHBOARD_ENABLED", True):
            problems = config.check()
        self.assertTrue(any("HUB_SECRET" in p for p in problems))

    def test_agent_mode_requires_dashboard(self):
        with mock.patch.object(config, "AGENT_MODE", True), \
             mock.patch.object(config, "HUB_SECRET", "s"), \
             mock.patch.object(config, "DASHBOARD_ENABLED", False):
            problems = config.check()
        self.assertTrue(any("Dashboard" in p for p in problems))

    def test_agent_mode_ok_without_telegram(self):
        with mock.patch.object(config, "AGENT_MODE", True), \
             mock.patch.object(config, "HUB_SECRET", "s"), \
             mock.patch.object(config, "DASHBOARD_ENABLED", True), \
             mock.patch.object(config, "TELEGRAM_TOKEN", ""), \
             mock.patch.object(config, "OWNER_ID", 0):
            self.assertEqual(config.check(), [])  # perangkat tak butuh token/owner

    def test_hub_mode_still_needs_telegram(self):
        with mock.patch.object(config, "AGENT_MODE", False), \
             mock.patch.object(config, "TELEGRAM_TOKEN", ""), \
             mock.patch.object(config, "OWNER_ID", 123), \
             mock.patch.object(config, "BACKEND", "ollama"):
            problems = config.check()
        self.assertTrue(any("TELEGRAM_BOT_TOKEN" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
