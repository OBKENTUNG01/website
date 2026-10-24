"""Uji akses tamu (Telegram & WhatsApp) dan sinkronisasi .env Hub -> perangkat."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import devices, keys, tools
from itsme.main import App


class GuestToolGateTest(unittest.TestCase):
    """Tamu tidak boleh menjalankan alat apa pun (satu gerbang di handle_tool)."""

    def test_guest_gets_no_tools(self):
        ctx = {"guest": True}
        out = tools.handle_tool("jalankan_perintah", {"perintah": "echo hai"}, ctx)
        self.assertIn("Mode tamu", out)

    def test_owner_still_gets_tools(self):
        ctx = {"website_dir": os.getcwd(), "guest": False, "full_access": True,
               "confirm": lambda _t: True, "notify": lambda _t: None}
        with mock.patch.object(tools, "_run", return_value=(0, "hai\n")):
            out = tools.handle_tool("jalankan_perintah", {"perintah": "echo hai"}, ctx)
        self.assertIn("hai", out)

    def test_ctx_marks_guest(self):
        app = App.__new__(App)  # tanpa menjalankan __init__ (tidak perlu jaringan)
        app.memory = mock.Mock()
        app.histories = {}
        app.seen_urls = {}
        app.chat_locks = {}
        ctx = app._ctx_for(123, guest=True)
        self.assertTrue(ctx["guest"])
        self.assertFalse(app._ctx_for(123)["guest"])


class HubEnvSyncTest(unittest.TestCase):
    """Perangkat menyalin .env Hub tanpa perlu dipasang ulang."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.env = os.path.join(self.dir, ".env")
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:lama\nOWNER_TELEGRAM_ID=42\n")

    def test_only_changed_keys_are_written(self):
        with mock.patch.object(keys, "ENV_FILE", self.env):
            changed = App._apply_hub_env(
                {"TELEGRAM_BOT_TOKEN": "123:baru", "OWNER_TELEGRAM_ID": "42",
                 "ITSME_PASSPHRASE": "rahasia"}
            )
        self.assertEqual(sorted(changed), ["ITSME_PASSPHRASE", "TELEGRAM_BOT_TOKEN"])
        with open(self.env, encoding="utf-8") as f:
            isi = f.read()
        self.assertIn("TELEGRAM_BOT_TOKEN=123:baru", isi)
        self.assertIn("ITSME_PASSPHRASE=rahasia", isi)

    def test_empty_bundle_is_a_noop(self):
        with mock.patch.object(keys, "ENV_FILE", self.env):
            self.assertEqual(App._apply_hub_env({}), [])
            self.assertEqual(App._apply_hub_env(None), [])

    def test_sync_bundle_carries_env(self):
        app = mock.Mock()
        app.memory.text.return_value = ""
        with mock.patch.object(keys, "ENV_FILE", self.env):
            bundle = devices.sync_bundle(app)
        self.assertEqual(bundle["env"]["TELEGRAM_BOT_TOKEN"], "123:lama")
        self.assertIn("version", bundle)
        json.dumps(bundle)  # harus bisa dikirim lewat HTTP


if __name__ == "__main__":
    unittest.main()
