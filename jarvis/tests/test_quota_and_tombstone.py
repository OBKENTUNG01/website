"""Uji kuota harian tamu dan perambatan penghapusan .env ke perangkat."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import config, keys, quota
from itsme.main import App


class Dasar(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for patcher in (mock.patch.object(config, "DATA_DIR", self.d),):
            patcher.start()
            self.addCleanup(patcher.stop)


class QuotaTest(Dasar):
    def test_limit_counts_down_and_blocks(self):
        with mock.patch.object(config, "GUEST_DAILY_MAX", 2):
            self.assertTrue(quota.allow("tg:7"))
            self.assertEqual(quota.remaining("tg:7"), 1)
            self.assertTrue(quota.allow("tg:7"))
            self.assertFalse(quota.allow("tg:7"))
            self.assertEqual(quota.remaining("tg:7"), 0)

    def test_one_guest_does_not_use_up_another(self):
        with mock.patch.object(config, "GUEST_DAILY_MAX", 1):
            self.assertTrue(quota.allow("tg:1"))
            self.assertFalse(quota.allow("tg:1"))
            self.assertTrue(quota.allow("tg:2"))

    def test_zero_means_unlimited(self):
        with mock.patch.object(config, "GUEST_DAILY_MAX", 0):
            for _ in range(50):
                self.assertTrue(quota.allow("wa:628"))
            self.assertEqual(quota.remaining("wa:628"), -1)

    def test_counter_is_per_day(self):
        with mock.patch.object(config, "GUEST_DAILY_MAX", 1):
            self.assertTrue(quota.allow("tg:9"))
            path = os.path.join(self.d, "guest_quota.json")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["date"] = "1999-01-01"        # seolah kemarin
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self.assertTrue(quota.allow("tg:9"))  # hari baru, kuota baru


class EnvTombstoneTest(Dasar):
    """Menghapus kunci di Hub harus sampai ke perangkat, bukan tertahan nilai lama."""

    def setUp(self):
        super().setUp()
        self.env = os.path.join(self.d, ".env")
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:tetap\nITSME_PASSPHRASE=rahasia-lama\n")

    def test_removed_key_is_cleared_on_device(self):
        with mock.patch.object(keys, "ENV_FILE", self.env):
            changed = App._apply_hub_env({"TELEGRAM_BOT_TOKEN": "123:tetap"},
                                         managed=["TELEGRAM_BOT_TOKEN", "ITSME_PASSPHRASE"])
        self.assertEqual(changed, ["ITSME_PASSPHRASE"])
        with open(self.env, encoding="utf-8") as f:
            isi = f.read()
        self.assertIn("ITSME_PASSPHRASE=\n", isi.rstrip("\n") + "\n")
        self.assertIn("TELEGRAM_BOT_TOKEN=123:tetap", isi)

    def test_key_still_present_is_not_touched(self):
        with mock.patch.object(keys, "ENV_FILE", self.env):
            changed = App._apply_hub_env({"ITSME_PASSPHRASE": "rahasia-lama"},
                                         managed=["ITSME_PASSPHRASE"])
        self.assertEqual(changed, [])

    def test_without_managed_list_old_behaviour(self):
        with mock.patch.object(keys, "ENV_FILE", self.env):
            changed = App._apply_hub_env({"TELEGRAM_BOT_TOKEN": "123:tetap"})
        self.assertEqual(changed, [])


if __name__ == "__main__":
    unittest.main()
