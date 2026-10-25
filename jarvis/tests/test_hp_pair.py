"""Uji kode pasang HP: 6 karakter, bukan token 24 karakter."""

import tempfile
import time
import unittest
from unittest import mock

from itsme import config, hp


class PairCodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        hp._FAILS.clear()
        self.patches = [
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "HP_SECRET", ""),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(hp._FAILS.clear)

    def test_code_is_six_chars_and_stable(self):
        self.assertEqual(len(hp.pair_code()), 6)
        self.assertEqual(hp.pair_code(), hp.pair_code())
        self.assertNotEqual(hp.pair_code(), hp.token())

    def test_code_unlocks_like_token(self):
        self.assertTrue(hp.check_token(hp.pair_code()))
        self.assertTrue(hp.check_token(hp.pair_code().lower()))  # diketik kecil juga boleh
        self.assertTrue(hp.check_token(hp.token()))

    def test_wrong_code_rejected(self):
        self.assertFalse(hp.check_token("ZZZZZZ", "1.1.1.1"))
        self.assertFalse(hp.check_token("", "1.1.1.1"))

    def test_brute_force_is_throttled(self):
        """Kode pendek gampang ditebak: setelah 30 gagal, kode benar pun ditolak."""
        for _ in range(hp.FAIL_MAX):
            hp.check_token("000000", "6.6.6.6")
        self.assertFalse(hp.check_token(hp.pair_code(), "6.6.6.6"))

    def test_attacker_cannot_lock_out_the_real_phone(self):
        """Hitungan per penebak: banjir salah dari satu IP tidak mengunci IP lain."""
        for _ in range(hp.FAIL_MAX * 2):
            hp.check_token("000000", "6.6.6.6")
        self.assertFalse(hp.check_token(hp.pair_code(), "6.6.6.6"))
        self.assertTrue(hp.check_token(hp.pair_code(), "192.168.1.9"))

    def test_counter_clears_when_window_passes(self):
        for _ in range(hp.FAIL_MAX):
            hp.check_token("000000", "7.7.7.7")
        self.assertEqual(len(hp._recent("7.7.7.7")), hp.FAIL_MAX)
        with mock.patch.object(hp.time, "monotonic",
                               return_value=hp.time.monotonic() + hp.FAIL_WINDOW + 1):
            self.assertEqual(hp._recent("7.7.7.7"), [])


if __name__ == "__main__":
    unittest.main()
