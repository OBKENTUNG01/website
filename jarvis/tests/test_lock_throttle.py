"""Uji gerbang kata sandi: tebakan dibatasi, bukan bisa dicoba tanpa henti."""

import tempfile
import time
import unittest
from unittest import mock

from itsme import config, lock


class LockThrottleTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        lock._FAILS.clear()
        for patcher in (mock.patch.object(config, "DATA_DIR", self.dir),
                        mock.patch.object(config, "PASSPHRASE", "sandi-rahasia"),
                        mock.patch.object(config, "LOCK_TTL_MINUTES", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lock._FAILS.clear)

    def test_correct_passphrase_unlocks(self):
        self.assertTrue(lock.try_unlock("1", "sandi-rahasia"))
        self.assertTrue(lock.is_unlocked("1"))

    def test_ten_wrong_tries_lock_the_door(self):
        for _ in range(lock.FAIL_MAX):
            self.assertFalse(lock.try_unlock("2", "salah"))
        self.assertTrue(lock.locked_out("2"))
        # kata sandi BENAR pun ditolak selama jendela masih penuh
        self.assertFalse(lock.try_unlock("2", "sandi-rahasia"))

    def test_window_expires(self):
        for _ in range(lock.FAIL_MAX):
            lock.try_unlock("3", "salah")
        with mock.patch.object(lock.time, "time", return_value=time.time() + lock.FAIL_WINDOW + 1):
            lock._FAILS["3"] = [0.0]
            self.assertFalse(lock.locked_out("3"))
            self.assertTrue(lock.try_unlock("3", "sandi-rahasia"))

    def test_one_guest_cannot_lock_out_another(self):
        for _ in range(lock.FAIL_MAX):
            lock.try_unlock("4", "salah")
        self.assertTrue(lock.locked_out("4"))
        self.assertFalse(lock.locked_out("5"))
        self.assertTrue(lock.try_unlock("5", "sandi-rahasia"))

    def test_success_clears_counter(self):
        for _ in range(lock.FAIL_MAX - 1):
            lock.try_unlock("6", "salah")
        self.assertTrue(lock.try_unlock("6", "sandi-rahasia"))
        self.assertFalse(lock.locked_out("6"))


if __name__ == "__main__":
    unittest.main()
