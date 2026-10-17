"""Uji alur bot Telegram It's Me dengan Telegram tiruan (tanpa jaringan)."""

import asyncio
import contextlib
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
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
        self.incoming = []  # batch update yang akan dikembalikan get_updates
        self.acked = False
        self.deleted = []

    async def send(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    async def send_chat_action(self, chat_id, action="typing"):
        self.actions += 1

    async def answer_callback(self, callback_id, text=None):
        self.answers.append(text)

    async def edit_markup(self, chat_id, message_id, reply_markup=None):
        self.markups.append(message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True

    async def close(self):
        pass

    async def get_updates(self):
        await asyncio.sleep(0.01)  # seperti long-polling: beri giliran ke task lain
        return self.incoming.pop(0) if self.incoming else []

    async def ack_updates(self):
        self.acked = True


class FakeUpdate:
    sha = "abc1234def5678"
    short = "abc1234"

    def describe(self):
        return "abc1234 — perbaikan"


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

    # --- ganti otak -----------------------------------------------------------
    async def test_claude_command_switches_brain(self):
        with mock.patch.dict(main.brain._state, {"backend": "ollama"}), mock.patch.object(
            config, "ANTHROPIC_API_KEY", "kunci-uji"
        ):
            self.app.histories[7] = [{"role": "user", "content": "lama"}]
            await self.app._handle_message(self._msg("/claude"))
            self.assertEqual(main.brain.current_backend(), "claude")
            self.assertIn("Claude", self.tg.sent[-1][1])
            self.assertNotIn(7, self.app.histories)  # format riwayat tiap otak berbeda

            await self.app._handle_message(self._msg("/otak"))
            self.assertIn("/claude — Claude", self.tg.sent[-1][1])

            await self.app._handle_message(self._msg("/lokal"))
            self.assertEqual(main.brain.current_backend(), "ollama")

    async def test_claude_command_without_key_explains(self):
        with mock.patch.dict(main.brain._state, {"backend": "ollama"}), mock.patch.object(
            config, "ANTHROPIC_API_KEY", None
        ):
            await self.app._handle_message(self._msg("/claude"))
            self.assertEqual(main.brain.current_backend(), "ollama")
        self.assertIn("ANTHROPIC_API_KEY", self.tg.sent[-1][1])

    async def test_claudepc_without_login_explains_how(self):
        with mock.patch.dict(main.brain._state, {"backend": "ollama"}), mock.patch.object(
            config, "CLAUDE_CODE_OAUTH_TOKEN", None
        ), mock.patch.object(main.keys, "ENV_FILE", os.path.join(self.dir, "tidak-ada.env")):
            await self.app._handle_message(self._msg("/claudepc"))
            self.assertEqual(main.brain.current_backend(), "ollama")
        self.assertIn("itsme.claude_login", self.tg.sent[-1][1])

    async def test_claudepc_and_claude_are_different_commands(self):
        with mock.patch.dict(main.brain._state, {"backend": "ollama"}), mock.patch.object(
            config, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "t" * 40
        ), mock.patch.object(config, "ANTHROPIC_API_KEY", "kunci-uji"):
            await self.app._handle_message(self._msg("/claudepc"))
            self.assertEqual(main.brain.current_backend(), "claude_code")
            self.assertIn("akun Claude-mu", self.tg.sent[-1][1])
            await self.app._handle_message(self._msg("/claude"))
            self.assertEqual(main.brain.current_backend(), "claude")

    # --- API key lewat chat ----------------------------------------------------
    KEY = "sk-ant-api03-" + "x" * 40 + "a1b2"

    def _keys_patched(self, **verify):
        stack = contextlib.ExitStack()
        self.env = os.path.join(self.dir, ".env")
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\n# ANTHROPIC_API_KEY=\n")
        stack.enter_context(mock.patch.object(main.keys, "ENV_FILE", self.env))
        stack.enter_context(mock.patch.object(config, "ANTHROPIC_API_KEY", None))
        stack.enter_context(mock.patch.object(main.keys, "verify", **verify))
        stack.enter_context(mock.patch.dict(main.brain._state, {"backend": "ollama"}))
        stack.enter_context(mock.patch.object(main, "run_agent", side_effect=AssertionError("key sampai ke otak AI")))
        return stack

    def _texts(self):
        return [t for _, t, _ in self.tg.sent]

    async def test_key_saved_deleted_and_never_shown(self):
        with self._keys_patched(return_value=None):
            await self.app._handle_message(self._msg(f"/kunci {self.KEY}", message_id=55))
            self.assertEqual(config.ANTHROPIC_API_KEY, self.KEY)  # langsung aktif
            await self.app._handle_message(self._msg("/claude"))
            self.assertEqual(main.brain.current_backend(), "claude")
        self.assertEqual(self.tg.deleted, [(7, 55)])
        with open(self.env, encoding="utf-8") as f:
            self.assertEqual(f.read(), f"TELEGRAM_BOT_TOKEN=t\nANTHROPIC_API_KEY={self.KEY}\n")
        self.assertIn("sk-ant-…a1b2", self._texts()[0])
        self.assertFalse(any(self.KEY in t for t in self._texts()))
        self.assertNotIn(7, self.app.histories)  # tidak masuk riwayat obrolan

    async def test_account_token_points_to_claudepc(self):
        token = "sk-ant-oat01-" + "t" * 40 + "e5f6"
        with self._keys_patched(return_value=None), mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", None):
            await self.app._handle_message(self._msg(f"/kunci {token}", message_id=60))
            self.assertEqual(config.CLAUDE_CODE_OAUTH_TOKEN, token)
            self.assertIsNone(config.ANTHROPIC_API_KEY)  # bukan API key
        self.assertIn("/claudepc", self._texts()[-1])
        self.assertFalse(any(token in t for t in self._texts()))

    async def test_rejected_key_not_saved(self):
        with self._keys_patched(side_effect=main.keys.KeyRejected("Anthropic menolak key ini.")):
            await self.app._handle_message(self._msg(f"/kunci {self.KEY}", message_id=56))
            self.assertIsNone(config.ANTHROPIC_API_KEY)
        self.assertEqual(self.tg.deleted, [(7, 56)])  # tetap dihapus
        self.assertIn("tidak disimpan", self._texts()[-1])
        with open(self.env, encoding="utf-8") as f:
            self.assertNotIn(self.KEY, f.read())

    async def test_unchecked_key_not_saved(self):
        with self._keys_patched(side_effect=OSError("internet putus")):
            await self.app._handle_message(self._msg(f"/kunci {self.KEY}", message_id=57))
            self.assertIsNone(config.ANTHROPIC_API_KEY)
        self.assertIn("belum bisa diperiksa", self._texts()[-1])

    async def test_unknown_key_deleted_anyway(self):
        with self._keys_patched(return_value=None):
            await self.app._handle_message(self._msg("/kunci rahasia-lain-12345678901234567890", message_id=58))
        self.assertEqual(self.tg.deleted, [(7, 58)])
        self.assertIn("tidak dikenali", self._texts()[-1])

    async def test_kunci_without_key_explains(self):
        await self.app._handle_message(self._msg("/kunci"))
        self.assertEqual(self.tg.deleted, [])
        self.assertIn("/kunci <key>", self._texts()[-1])

    async def test_key_from_stranger_ignored(self):
        with self._keys_patched(return_value=None):
            message = self._msg(f"/kunci {self.KEY}", message_id=59)
            message["from"] = {"id": 999}
            await self.app._handle_message(message)
            self.assertIsNone(config.ANTHROPIC_API_KEY)
        self.assertEqual(self.tg.deleted, [])
        self.assertEqual(self.tg.sent, [])

    # --- pembaruan otomatis ---------------------------------------------------
    def _updates_patched(self, **check):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(config, "AUTO_UPDATE", True))
        stack.enter_context(mock.patch.object(config, "UPDATE_INTERVAL", 0.01))
        stack.enter_context(mock.patch.object(main, "_FIRST_UPDATE_CHECK", 0.05))
        stack.enter_context(mock.patch.object(main.updater, "check", **check))
        return stack

    async def test_update_waits_for_running_work_then_restarts(self):
        self.tg.incoming.append([{"update_id": 1, "message": self._msg("halo")}])

        def slow_agent(messages, ctx):
            time.sleep(0.3)
            return "Halo Tuan"

        with self._updates_patched(return_value=FakeUpdate()), mock.patch.object(
            main, "run_agent", side_effect=slow_agent
        ), mock.patch.object(main.updater, "apply") as apply:
            code = await asyncio.wait_for(self.app.run(), 10)
        self.assertEqual(code, main.EXIT_UPDATED)
        texts = [t for _, t, _ in self.tg.sent]
        restart = next(i for i, t in enumerate(texts) if t.startswith("🔄"))
        self.assertLess(texts.index("Halo Tuan"), restart)  # jawaban dulu, baru menyala ulang
        self.assertTrue(self.tg.acked)
        apply.assert_called_once()
        # Setelah menyala lagi, pemilik dikabari versi barunya.
        restarted = main.App()
        restarted.loop = asyncio.get_running_loop()
        with mock.patch.object(config, "AUTO_UPDATE", False):
            serving = asyncio.create_task(restarted.run())
            await asyncio.sleep(0.05)
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        self.assertIn("abc1234", restarted.tg.sent[0][1])

    async def test_failed_update_keeps_running_and_is_not_retried(self):
        with mock.patch.object(main.updater, "apply", side_effect=RuntimeError("pip gagal")):
            restart = await self.app._install(FakeUpdate())
        self.assertFalse(restart)
        self.assertIn("pip gagal", self.tg.sent[-1][1])
        self.assertIn(FakeUpdate.sha, self.app._failed_updates)

    async def test_blocked_update_reported_once(self):
        blocked = main.updater.Blocked("ada perubahan yang belum di-commit")
        with self._updates_patched(side_effect=[blocked, blocked, FakeUpdate()]):
            update = await asyncio.wait_for(self.app._watch_updates(), 5)
        self.assertEqual(update.sha, FakeUpdate.sha)
        warnings = [t for _, t, _ in self.tg.sent if "belum di-commit" in t]
        self.assertEqual(len(warnings), 1)

    async def test_update_check_error_is_not_fatal(self):
        with self._updates_patched(side_effect=[OSError("internet putus"), FakeUpdate()]):
            update = await asyncio.wait_for(self.app._watch_updates(), 5)
        self.assertEqual(update.sha, FakeUpdate.sha)
        self.assertEqual(self.tg.sent, [])


class ConsoleTest(unittest.TestCase):
    def test_blocked_console_does_not_block_bot(self):
        """Jendela cmd yang tertahan (QuickEdit) tidak boleh menahan bot."""
        gate = threading.Event()

        class BlockingStream(io.StringIO):
            def write(self, text):
                gate.wait(5)
                return super().write(text)

        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        stream = BlockingStream()
        with mock.patch.object(config, "LOG_FILE", os.path.join(folder, "itsme.log")), \
                mock.patch.object(main.log, "handlers", []):
            with mock.patch.object(sys, "stderr", stream):  # StreamHandler() menulis ke sys.stderr
                listener = main._setup_logging()
            try:
                started = time.monotonic()
                main.log.info("halo dari uji")
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                gate.set()
                listener.stop()
        self.assertIn("halo dari uji", stream.getvalue())

    def test_disable_quick_edit_is_safe_anywhere(self):
        main._disable_quick_edit()  # tanpa jendela konsol pun tidak boleh error


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
