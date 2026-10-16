"""Uji alur bot Telegram It's Me dengan Telegram tiruan (tanpa jaringan)."""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from itsme import config, main


class FakeTelegram:
    def __init__(self, *args, **kwargs):
        self.sent = []
        self.actions = 0
        self.answers = []
        self.markups = []

    async def send(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    async def send_chat_action(self, chat_id, action="typing"):
        self.actions += 1

    async def answer_callback(self, callback_id, text=None):
        self.answers.append(text)

    async def edit_markup(self, chat_id, message_id, reply_markup=None):
        self.markups.append(message_id)

    async def close(self):
        pass

    async def get_updates(self):
        return []


class AppTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for patch in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(main, "Telegram", FakeTelegram),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        # Error tiruan di uji ini memang sengaja; jangan penuhi layar dengan traceback.
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()
        self.tg = self.app.tg

    def _msg(self, text=None, **extra):
        message = {"chat": {"id": 7}, "from": {"id": 42}}
        if text is not None:
            message["text"] = text
        message.update(extra)
        return message

    async def test_reply_sent(self):
        with mock.patch.object(main, "run_agent", return_value="Halo Tuan"):
            await self.app._handle_message(self._msg("halo"))
        self.assertEqual(self.tg.sent[-1][1], "Halo Tuan")

    async def test_error_is_reported_not_silent(self):
        with mock.patch.object(main, "run_agent", side_effect=RuntimeError("rusak")):
            await self.app._handle_message(self._msg("halo"))
        self.assertIn("rusak", self.tg.sent[-1][1])

    async def test_message_gets_timestamp(self):
        got = {}

        def fake(messages, ctx):
            got["content"] = messages[-1]["content"]
            return "ok"

        with mock.patch.object(main, "run_agent", side_effect=fake):
            await self.app._handle_message(self._msg("jam berapa?"))
        self.assertRegex(got["content"], r"^\[\w+, \d{2}-\d{2}-\d{4} \d{2}\.\d{2}\] jam berapa\?$")

    async def test_owner_url_is_openable(self):
        with mock.patch.object(main, "run_agent", return_value="ok"):
            await self.app._handle_message(self._msg("tolong baca https://contoh.id/x."))
        self.assertIn("https://contoh.id/x", self.app.seen_urls[7])

    async def test_voice_message_gets_answer(self):
        await self.app._handle_message(self._msg(voice={"file_id": "x"}))
        self.assertIn("pesan teks", self.tg.sent[-1][1])

    async def test_stranger_ignored(self):
        message = self._msg("halo")
        message["from"] = {"id": 99}
        with mock.patch.object(main, "run_agent") as agent:
            await self.app._handle_message(message)
        agent.assert_not_called()
        self.assertEqual(self.tg.sent, [])

    async def test_stale_button(self):
        await self.app._handle_callback({
            "id": "c1", "data": "ok:lama", "from": {"id": 42},
            "message": {"message_id": 5, "chat": {"id": 7}},
        })
        self.assertEqual(self.tg.answers[-1], "Konfirmasi ini sudah tidak berlaku.")
        self.assertIn(5, self.tg.markups)

    async def test_slow_answer_keeps_owner_informed(self):
        def slow(messages, ctx):
            time.sleep(0.4)
            return "selesai"

        with mock.patch.object(main, "_TYPING_EVERY", 0.05), \
                mock.patch.object(main, "_SLOW_NOTICE_AFTER", 0.1), \
                mock.patch.object(main, "run_agent", side_effect=slow):
            await self.app._handle_message(self._msg("tugas berat"))
        texts = [t for _, t, _ in self.tg.sent]
        self.assertTrue(any("Masih kukerjakan" in t for t in texts), texts)
        self.assertEqual(texts[-1], "selesai")
        self.assertGreater(self.tg.actions, 1)

    async def test_background_task_error_reported(self):
        with mock.patch.object(main, "run_agent", side_effect=ValueError("gagal latar")):
            await self.app._run_background(7, "cek sesuatu")
        self.assertIn("gagal latar", self.tg.sent[-1][1])

    async def test_memory_commands(self):
        self.app.memory.add("ingat ini")
        await self.app._handle_message(self._msg("/memori"))
        self.assertIn("ingat ini", self.tg.sent[-1][1])

        task = asyncio.create_task(self.app._handle_message(self._msg("/hapus_memori")))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if self.app.pending:
                break
        cid = next(iter(self.app.pending))
        await self.app._handle_callback({"id": "c2", "data": f"ok:{cid}", "from": {"id": 42}})
        await task
        self.assertNotIn("ingat ini", self.app.memory.text())


class SingleInstanceTest(unittest.TestCase):
    def test_second_instance_refused(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with mock.patch.object(config, "DATA_DIR", folder):
            first = main._single_instance_lock()
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(main._single_instance_lock())
            finally:
                first.close()
            again = main._single_instance_lock()
            self.assertIsNotNone(again)
            again.close()


if __name__ == "__main__":
    unittest.main()
