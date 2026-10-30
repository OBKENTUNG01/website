"""Uji akses tamu (Telegram & WhatsApp) dan sinkronisasi .env Hub -> perangkat."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import devices, keys, node, tools
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


NSK_OLD = "nsk_" + "o" * 32
NSK_NEW = ["nsk_" + c * 32 for c in "abc"]


class NusaGateKeysStayOnTheHubTest(unittest.TestCase):
    """Permintaan pemilik: key NusaGate hanya di komputer yang memegang bot, tidak disalin ke komputer lain."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.hub_env = os.path.join(self.dir, "hub.env")
        self.pc_env = os.path.join(self.dir, "pc.env")
        with open(self.hub_env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:abc\nOWNER_TELEGRAM_ID=42\nOPENROUTER_API_KEY=sk-or-x\n"
                    f"DEEPSEEK_API_KEY={NSK_OLD}\nDEEPSEEK_API_KEYS={','.join(NSK_NEW)}\n"
                    "ITSME_NUSAGATE_MODELS=glm-5.3-flash,deepseek-v4.1-flash\nITSME_DEVICE_NAME=HUB\n")

    def bundle(self):
        app = mock.Mock()
        app.memory.text.return_value = ""
        with mock.patch.object(keys, "ENV_FILE", self.hub_env):
            return devices.sync_bundle(app)

    def apply_on_pc(self, bundle):
        with mock.patch.object(keys, "ENV_FILE", self.pc_env):
            return App._apply_hub_env(bundle["env"], bundle["env_managed"])

    def pc_text(self):
        with open(self.pc_env, encoding="utf-8") as f:
            return f.read()

    def test_the_keys_never_travel_to_a_new_computer(self):
        env = node.invite_env(self.hub_env)
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        self.assertNotIn("DEEPSEEK_API_KEYS", env)
        self.assertEqual(env["OPENROUTER_API_KEY"], "sk-or-x")  # rahasia lain tidak diubah
        self.assertEqual(env["ITSME_NUSAGATE_MODELS"], "glm-5.3-flash,deepseek-v4.1-flash")  # daftar model bukan rahasia

    def test_the_sync_bundle_holds_no_nusagate_key_anywhere(self):
        text = json.dumps(self.bundle())
        for key in [NSK_OLD, *NSK_NEW]:
            self.assertNotIn(key, text)

    def test_an_online_device_never_receives_them(self):
        with open(self.pc_env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:abc\n")
        self.apply_on_pc(self.bundle())
        text = self.pc_text()
        for key in [NSK_OLD, *NSK_NEW]:
            self.assertNotIn(key, text)
        self.assertIn("OPENROUTER_API_KEY=sk-or-x", text)  # sisanya tetap diselaraskan

    def test_an_old_copy_already_on_a_device_is_cleared(self):
        # Dulu seluruh .env Hub disalin ke tiap komputer: salinan key lama harus ikut dibersihkan.
        with open(self.pc_env, "w", encoding="utf-8") as f:
            f.write(f"TELEGRAM_BOT_TOKEN=123:abc\nDEEPSEEK_API_KEY={NSK_OLD}\nDEEPSEEK_API_KEYS={NSK_NEW[0]}\n")
        changed = self.apply_on_pc(self.bundle())
        self.assertIn("DEEPSEEK_API_KEY", changed)
        self.assertIn("DEEPSEEK_API_KEYS", changed)
        self.assertNotIn(NSK_OLD, self.pc_text())
        self.assertNotIn(NSK_NEW[0], self.pc_text())
        self.assertEqual(self.apply_on_pc(self.bundle()).count("DEEPSEEK_API_KEY"), 0)  # kedua kali: tak ada yang berubah

    def test_the_old_copy_is_cleared_even_when_the_hub_has_no_such_line(self):
        with open(self.hub_env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:abc\n")
        with open(self.pc_env, "w", encoding="utf-8") as f:
            f.write(f"DEEPSEEK_API_KEY={NSK_OLD}\n")
        self.apply_on_pc(self.bundle())
        self.assertNotIn(NSK_OLD, self.pc_text())

    def test_machine_specific_values_on_a_device_are_never_blanked_by_the_hub(self):
        # Hub kebetulan punya baris ITSME_DEVICE_NAME: perangkat tetap memakai namanya sendiri.
        with open(self.pc_env, "w", encoding="utf-8") as f:
            f.write("ITSME_DEVICE_NAME=ARIF-COMP\nITSME_HUB_SECRET=rahasia-perangkat\n")
        bundle = self.bundle()
        self.assertNotIn("ITSME_DEVICE_NAME", bundle["env_managed"])
        self.apply_on_pc(bundle)
        self.assertIn("ITSME_DEVICE_NAME=ARIF-COMP", self.pc_text())
        self.assertIn("ITSME_HUB_SECRET=rahasia-perangkat", self.pc_text())

    def test_managed_keys_name_the_secrets_without_their_values(self):
        names = node.managed_keys(self.hub_env)
        self.assertIn("DEEPSEEK_API_KEY", names)
        self.assertIn("DEEPSEEK_API_KEYS", names)
        self.assertIn("OPENROUTER_API_KEY", names)
        self.assertNotIn("ITSME_DEVICE_NAME", names)
        self.assertEqual(names, sorted(set(names)))

    def test_keys_added_through_telegram_stay_on_the_hub_too(self):
        with open(self.hub_env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:abc\n")
        extra = ["nsk_" + c * 32 for c in "xyz"]
        with mock.patch.object(keys, "ENV_FILE", self.hub_env), \
             mock.patch.object(keys.config, "DEEPSEEK_API_KEY", None), \
             mock.patch.object(keys.config, "DEEPSEEK_API_KEYS", []):
            keys.pool_add_many(extra)
        text = json.dumps(self.bundle())
        for key in extra:
            self.assertNotIn(key, text)
        with open(self.hub_env, encoding="utf-8") as f:
            self.assertIn(extra[0], f.read())  # tetap tersimpan di Hub


if __name__ == "__main__":
    unittest.main()
