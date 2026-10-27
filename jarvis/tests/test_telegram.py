"""Uji unduhan berkas kiriman di klien Telegram (tanpa jaringan)."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import telegram


class FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    """Pengganti httpx.AsyncClient: jawab getFile, lalu kirim isi berkasnya."""

    def __init__(self, ok=True):
        self.ok = ok

    async def get(self, url, params=None):
        if url.endswith("/getFile"):
            return FakeResponse({"ok": self.ok, "result": {"file_path": "documents/file_1.pdf"}})
        return FakeResponse(content=b"isi")

    async def aclose(self):
        pass


class DownloadFileTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.tg = telegram.Telegram("TOKEN", "https://contoh.invalid")
        await self.tg.close()  # klien httpx asli tidak dipakai
        self.tg._client = FakeClient()

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()

    async def test_saves_file_in_folder(self):
        path = await self.tg.download_file("f1", self.dir, "laporan.pdf")
        self.assertEqual(os.path.dirname(path), self.dir)
        self.assertTrue(path.endswith("_laporan.pdf"))
        self.assertEqual(self._read(path), b"isi")

    async def test_forbidden_characters_are_replaced(self):
        # Nama seperti ini boleh di HP, tapi dilarang di Windows.
        path = await self.tg.download_file("f1", self.dir, 'Laporan: Q3? "final" <v2>|*.pdf')
        self.assertTrue(path.endswith("_Laporan_ Q3_ _final_ _v2___.pdf"))
        self.assertEqual(self._read(path), b"isi")

    async def test_name_cannot_leave_folder(self):
        path = await self.tg.download_file("f1", self.dir, "../../jahat.txt")
        self.assertEqual(os.path.dirname(path), self.dir)
        self.assertTrue(path.endswith("_jahat.txt"))

    async def test_long_name_is_shortened_keeping_extension(self):
        path = await self.tg.download_file("f1", self.dir, "a" * 300 + ".pdf")
        self.assertTrue(path.endswith(".pdf"))
        self.assertLessEqual(len(os.path.basename(path)), 120)
        self.assertEqual(self._read(path), b"isi")

    async def test_save_failure_returns_none(self):
        not_a_folder = os.path.join(self.dir, "berkas_biasa")
        with open(not_a_folder, "w", encoding="utf-8") as f:
            f.write("x")
        with self.assertLogs("itsme", level="WARNING"):
            path = await self.tg.download_file("f1", not_a_folder, "a.pdf")
        self.assertIsNone(path)

    async def test_getfile_refused_returns_none(self):
        self.tg._client = FakeClient(ok=False)
        self.assertIsNone(await self.tg.download_file("f1", self.dir, "a.pdf"))


if __name__ == "__main__":
    unittest.main()


class FakeConflictClient:
    """Selalu menjawab Conflict, seperti dua poller memakai token yang sama."""

    def __init__(self):
        self.calls = 0

    async def get(self, url, params=None):
        self.calls += 1
        return FakeResponse({"ok": False, "description": "Conflict: terminated by other getUpdates request"})

    async def aclose(self):
        pass


class ConflictLogTest(unittest.IsolatedAsyncioTestCase):
    async def test_konflik_dicatat_sekali_per_lima_menit(self):
        """409 berulang tidak boleh membanjiri log: 700 baris dalam 1,5 jam."""
        tg = telegram.Telegram("TOKEN", "https://contoh.invalid")
        tg._client = FakeConflictClient()
        with mock.patch.object(telegram.log, "warning") as warn, \
                mock.patch.object(telegram.asyncio, "sleep", return_value=None):
            for _ in range(5):
                await tg.get_updates()
        self.assertEqual(tg._conflicts, 5)
        self.assertEqual(warn.call_count, 1)  # hanya sekali, lalu diem 5 menit
        pesan = warn.call_args[0][0] % warn.call_args[0][1:]
        self.assertIn("kejadian ke-", pesan)

    async def test_poll_tertunda_oleh_kunci(self):
        """get_updates memakai kunci: ack_updates tidak bisa menabrak long-poll."""
        tg = telegram.Telegram("TOKEN", "https://contoh.invalid")
        self.assertTrue(hasattr(tg, "_poll_lock"))
        tg._client = FakeConflictClient()
        with mock.patch.object(telegram.asyncio, "sleep", return_value=None):
            await tg.get_updates()
        self.assertFalse(tg._poll_lock.locked())  # dilepas setelah selesai


class WebhookSecretTest(unittest.TestCase):
    """Rahasia webhook = turunan token; dipakai di URL dan header Telegram."""

    def test_panjang_dan_stabil(self):
        from unittest import mock
        from itsme import config, main as itsme_main

        with mock.patch.object(config, "TELEGRAM_TOKEN", "123:ABC"):
            a = itsme_main.tg_webhook_secret()
            b = itsme_main.tg_webhook_secret()
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in a))

    def test_berbeda_untuk_token_berbeda(self):
        from unittest import mock
        from itsme import config, main as itsme_main

        with mock.patch.object(config, "TELEGRAM_TOKEN", "satu"):
            a = itsme_main.tg_webhook_secret()
        with mock.patch.object(config, "TELEGRAM_TOKEN", "dua"):
            b = itsme_main.tg_webhook_secret()
        self.assertNotEqual(a, b)
