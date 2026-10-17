"""Uji unduhan berkas kiriman di klien Telegram (tanpa jaringan)."""

import os
import shutil
import tempfile
import unittest

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
