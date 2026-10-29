"""Uji dukungan berkas di jembatan WhatsApp.

Tanpa jaringan & tanpa Node: proses jembatan diganti tiruan, jadi yang diuji
murni logika — keterangan berkas untuk otak, baris JSON ke jembatan, dan
penerusan berkas dari jembatan ke penangan pesan.
"""

import asyncio
import io
import json
import logging
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from itsme import config
from itsme import main as itsme_main
from itsme import whatsapp as wa_module
from itsme.whatsapp import WhatsApp
from test_main import FakeTelegram


class FakeProc:
    """Pengganti proses node: stdout bisa dibaca, stdin bisa ditulis."""

    def __init__(self, lines=(), running=True):
        self.stdout = iter(lines)
        self.stdin = io.StringIO()
        self._running = running

    def poll(self):
        return None if self._running else 0


class FileNoteTest(unittest.TestCase):
    def test_small_text_file_is_read_for_the_brain(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "catatan.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("isi penting")
            nota = itsme_main._wa_file_note(
                {"name": "catatan.txt", "kind": "dokumen", "size": 11, "path": path})
        self.assertIn("catatan.txt", nota)
        self.assertIn("isi penting", nota)

    def test_binary_file_is_only_described(self):
        nota = itsme_main._wa_file_note(
            {"name": "foto.jpg", "kind": "gambar", "size": 2 * 1048576, "path": "/tidak/ada"})
        self.assertIn("foto.jpg", nota)
        self.assertIn("2.0 MB", nota)
        self.assertNotIn("Isi berkas", nota)

    def test_size_is_human_readable(self):
        self.assertEqual(itsme_main._wa_mb(2 * 1048576), "2.0 MB")
        self.assertTrue(itsme_main._wa_mb(2048).endswith("KB"))


class BridgeSendTest(unittest.TestCase):
    def _wa(self, running=True):
        wa = WhatsApp(lambda *a, **k: None)
        wa._proc = FakeProc(running=running)
        return wa

    def test_text_is_written_as_json_line(self):
        wa = self._wa()
        self.assertTrue(wa.send("62811@s.whatsapp.net", "halo"))
        obj = json.loads(wa._proc.stdin.getvalue().strip())
        self.assertEqual(obj["jid"], "62811@s.whatsapp.net")
        self.assertEqual(obj["text"], "halo")

    def test_file_is_written_with_name(self):
        wa = self._wa()
        self.assertTrue(wa.send_file("62811@s.whatsapp.net",
                                     os.path.join("folder", "laporan.pdf")))
        obj = json.loads(wa._proc.stdin.getvalue().strip())
        self.assertTrue(obj["file"].endswith("laporan.pdf"))
        self.assertEqual(obj["name"], "laporan.pdf")

    def test_send_fails_when_bridge_is_down(self):
        wa = self._wa(running=False)
        self.assertFalse(wa.send("62811@s.whatsapp.net", "halo"))
        self.assertFalse(wa.send_file("62811@s.whatsapp.net", "x.pdf"))


class ReadDispatchTest(unittest.TestCase):
    def test_incoming_file_reaches_the_handler(self):
        got = []
        wa = WhatsApp(lambda jid, text, guest, file=None: got.append((jid, text, guest, file)))
        line = json.dumps({
            "jid": "62811@s.whatsapp.net", "text": "lihat ini", "guest": True,
            "file": {"name": "a.txt", "path": "/tmp/a.txt", "kind": "dokumen", "size": 10},
        })
        wa._proc = FakeProc([line], running=False)
        wa._stop = True  # jangan menyalakan ulang jembatan di dalam uji
        wa._read()
        self.assertEqual(len(got), 1)
        jid, text, guest, file = got[0]
        self.assertEqual(jid, "62811@s.whatsapp.net")
        self.assertEqual(text, "lihat ini")
        self.assertTrue(guest)
        self.assertEqual(file["name"], "a.txt")

    def test_plain_text_still_works(self):
        got = []
        wa = WhatsApp(lambda jid, text, guest, file=None: got.append((jid, text, guest, file)))
        line = json.dumps({"jid": "62822@s.whatsapp.net", "text": "halo", "guest": False})
        wa._proc = FakeProc([line], running=False)
        wa._stop = True
        wa._read()
        self.assertEqual(len(got), 1)
        self.assertIsNone(got[0][3])


class GiveUpTest(unittest.TestCase):
    """_RETRY_MAX kali gagal berturut-turut dalam _RETRY_WINDOW detik harus
    memanggil on_giveup, supaya pemiliknya diberi tahu (lihat App._wa_gave_up
    di main.py) — bukan cuma diam di log."""

    def test_on_giveup_called_after_max_retries(self):
        calls = []
        wa = WhatsApp(lambda *a, **k: None, on_giveup=lambda: calls.append(1))
        wa._proc = FakeProc(running=False)
        wa._tries = wa_module._RETRY_MAX
        wa._last = time.time()
        wa._read()
        self.assertEqual(calls, [1])


class WaAppIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "AGENT_MODE", False),
            mock.patch.object(itsme_main, "Telegram", FakeTelegram),
        ):
            p.start()
            self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = itsme_main.App()
        self.app.loop = asyncio.get_running_loop()

    async def test_wa_confirm_sends_heads_up_before_telegram_prompt(self):
        sent_wa = []
        self.app.wa = type("FakeWa", (), {
            "send": staticmethod(lambda jid, text: sent_wa.append((jid, text)) or True),
        })()
        with mock.patch.object(self.app, "_confirm_blocking", return_value=True) as inner:
            result = await asyncio.to_thread(
                self.app._confirm_blocking_wa, "62811@s.whatsapp.net", "Lanjutkan?")
        self.assertTrue(result)
        inner.assert_called_once_with(config.OWNER_ID, "Lanjutkan?")
        self.assertEqual(len(sent_wa), 1)
        jid, text = sent_wa[0]
        self.assertEqual(jid, "62811@s.whatsapp.net")
        self.assertIn("konfirmasi", text)

    async def test_gave_up_clears_wa_and_notifies_owner(self):
        self.app.wa = WhatsApp(lambda *a, **k: None)  # jembatan "menyala" sebelumnya
        self.app._wa_gave_up()
        await asyncio.sleep(0.05)  # biar task dari call_soon_threadsafe jalan
        self.assertIsNone(self.app.wa)
        self.assertTrue(any("berhenti total" in t for _, t, _ in self.app.tg.sent))


if __name__ == "__main__":
    unittest.main()
