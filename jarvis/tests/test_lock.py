"""Uji gerbang kata sandi bot (lapisan kedua, dengan pengecoh)."""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from test_main import FakeTelegram

from itsme import config, lock, main

REAL = "its.me.achmad_28"


class LockUnitTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "PASSPHRASE", REAL),
                  mock.patch.object(config, "LOCK_TTL_MINUTES", 0)):
            p.start()
            self.addCleanup(p.stop)

    def test_off_when_no_passphrase(self):
        with mock.patch.object(config, "PASSPHRASE", ""):
            self.assertFalse(lock.active())
            self.assertTrue(lock.is_unlocked(7))          # gerbang mati = selalu terbuka
            self.assertTrue(lock.try_unlock(7, "apa saja"))

    def test_correct_unlocks_and_persists(self):
        self.assertTrue(lock.active())
        self.assertFalse(lock.is_unlocked(7))
        self.assertTrue(lock.try_unlock(7, REAL))
        self.assertTrue(lock.is_unlocked(7))
        # tersimpan di berkas -> tetap terbuka walau objek/baca ulang
        self.assertTrue(os.path.exists(os.path.join(self.dir, "unlocked.json")))
        self.assertTrue(lock.is_unlocked(7))
        self.assertFalse(lock.is_unlocked(8))             # chat lain tetap terkunci

    def test_wrong_never_unlocks(self):
        for guess in ("", "  ", "salah", REAL + "x", REAL.upper(), "kata sandi minimal 20 karakter yang panjang"):
            self.assertFalse(lock.try_unlock(7, guess), guess)
        self.assertFalse(lock.is_unlocked(7))

    def test_whitespace_trimmed(self):
        self.assertTrue(lock.try_unlock(7, f"  {REAL}\n"))

    def test_lock_relocks(self):
        lock.try_unlock(7, REAL)
        lock.lock(7)
        self.assertFalse(lock.is_unlocked(7))

    def test_ttl_expiry(self):
        with mock.patch.object(config, "LOCK_TTL_MINUTES", 30):
            lock.try_unlock(7, REAL)
            self.assertTrue(lock.is_unlocked(7))
            with mock.patch.object(lock.time, "time", return_value=time.time() + 31 * 60):
                self.assertFalse(lock.is_unlocked(7))


class GateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "OWNER_ID", 42),
                  mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "m.md")),
                  mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "l.log")),
                  mock.patch.object(config, "FOLLOW_NODE", False),
                  mock.patch.object(config, "PASSPHRASE", REAL),
                  mock.patch.object(config, "LOCK_TTL_MINUTES", 0),
                  mock.patch.object(main, "Telegram", FakeTelegram)):
            p.start()
            self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()
        self.tg = self.app.tg

    def _msg(self, text):
        return {"chat": {"id": 7}, "from": {"id": 42}, "text": text, "message_id": 100}

    def texts(self):
        return [t for _, t, _ in self.tg.sent]

    async def test_locked_wrong_gets_decoy_and_deletes(self):
        with mock.patch.object(main, "run_agent", side_effect=AssertionError("otak tak boleh dipanggil")):
            await self.app._handle_message(self._msg("halo bot"))
        self.assertEqual(self.texts(), [lock.DECOY])
        self.assertIn("20 karakter", self.texts()[0])
        self.assertIn((7, 100), self.tg.deleted)   # tebakan dihapus dari chat

    async def test_commands_blocked_until_unlocked(self):
        await self.app._handle_message(self._msg("/help"))     # perintah pun kena gerbang
        self.assertEqual(self.texts(), [lock.DECOY])

    async def test_correct_unlocks_then_help_works(self):
        await self.app._handle_message(self._msg(REAL))
        self.assertEqual(self.texts(), [lock.WELCOME])
        self.assertIn((7, 100), self.tg.deleted)                # kata sandi dihapus
        await self.app._handle_message(self._msg("/help"))
        self.assertIn("It's Me siap", self.texts()[-1])

    async def test_lock_command_relocks(self):
        await self.app._handle_message(self._msg(REAL))
        await self.app._handle_message(self._msg("/lock"))
        self.assertEqual(self.texts()[-1], lock.LOCKED_NOTE)
        await self.app._handle_message(self._msg("/help"))
        self.assertEqual(self.texts()[-1], lock.DECOY)          # terkunci lagi

    async def test_no_passphrase_means_no_gate(self):
        with mock.patch.object(config, "PASSPHRASE", ""):
            await self.app._handle_message(self._msg("/help"))
        self.assertIn("It's Me siap", self.texts()[-1])

    async def test_stranger_never_sees_gate(self):
        msg = self._msg("halo"); msg["from"] = {"id": 999}
        await self.app._handle_message(msg)
        self.assertEqual(self.tg.sent, [])                      # bukan pemilik: diam


if __name__ == "__main__":
    unittest.main()
