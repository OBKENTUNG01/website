"""Uji alat buat_zip & ekstrak (nomor 5): persetujuan, lindungi rahasia, aman."""

import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

from itsme import tools


class Ctx(dict):
    """ctx alat; mencatat teks konfirmasi, menjawab sesuai `answer`."""

    def __init__(self, answer=True, full_access=False):
        self.asked = []
        self.notified = []
        self.answer = answer
        super().__init__(
            website_dir=None, memory=mock.Mock(), seen_urls=set(), in_background=False,
            full_access=full_access, confirm=self._confirm, notify=self.notified.append,
        )

    def _confirm(self, text):
        self.asked.append(text)
        return self.answer.pop(0) if isinstance(self.answer, list) else self.answer


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class MakeZipTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.proyek = os.path.join(self.d, "proyek")
        _write(os.path.join(self.proyek, "index.html"), "<h1>Halo</h1>")
        _write(os.path.join(self.proyek, "app", "main.py"), "print(1)")
        _write(os.path.join(self.proyek, ".env"), "TOKEN=rahasia")
        _write(os.path.join(self.proyek, ".ssh", "id_rsa"), "KUNCI")

    def _zip(self, ctx, dest="hasil.zip"):
        return tools.handle_tool("buat_zip", {"sumber": self.proyek, "tujuan": os.path.join(self.d, dest)}, ctx)

    def test_zip_asks_first_and_skips_secrets(self):
        ctx = Ctx(answer=True)
        out = self._zip(ctx)
        self.assertEqual(len(ctx.asked), 1, "harus minta konfirmasi dulu")
        self.assertIn("DILEWATI", ctx.asked[0])
        zpath = os.path.join(self.d, "hasil.zip")
        self.assertTrue(os.path.exists(zpath))
        names = zipfile.ZipFile(zpath).namelist()
        self.assertTrue(any(n.endswith("index.html") for n in names))
        self.assertTrue(any(n.endswith("main.py") for n in names))
        self.assertFalse(any(".env" in n or "id_rsa" in n for n in names), names)
        self.assertIn("dilewati", out)

    def test_declined_makes_no_file(self):
        ctx = Ctx(answer=False)
        out = self._zip(ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertFalse(os.path.exists(os.path.join(self.d, "hasil.zip")))

    def test_full_access_still_asks(self):
        ctx = Ctx(answer=False, full_access=True)
        self._zip(ctx)
        self.assertEqual(len(ctx.asked), 1, "nomor 5: zip tetap minta tombol di akses penuh")
        self.assertEqual(ctx.notified, [])

    def test_secret_destination_refused(self):
        ctx = Ctx(answer=True)
        # ".env" -> ".env.zip": nama hasil terlihat seperti berkas rahasia.
        out = tools.handle_tool("buat_zip", {"sumber": self.proyek, "tujuan": os.path.join(self.d, ".env")}, ctx)
        self.assertIn("Ditolak", out)
        self.assertEqual(ctx.asked, [])

    def test_too_broad_source_refused(self):
        ctx = Ctx(answer=True)
        out = tools.handle_tool("buat_zip", {"sumber": os.path.expanduser("~")}, ctx)
        self.assertIn("terlalu luas", out)
        self.assertEqual(ctx.asked, [])

    def test_only_secret_files_nothing_to_zip(self):
        onlysecret = os.path.join(self.d, "rahasia")
        _write(os.path.join(onlysecret, ".env"), "x")
        ctx = Ctx(answer=True)
        out = tools.handle_tool("buat_zip", {"sumber": onlysecret}, ctx)
        self.assertIn("Tidak ada berkas", out)
        self.assertEqual(ctx.asked, [])


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def _make(self, name, entries):
        path = os.path.join(self.d, name)
        with zipfile.ZipFile(path, "w") as zf:
            for arc, data in entries.items():
                zf.writestr(arc, data)
        return path

    def test_extract_asks_then_writes(self):
        archive = self._make("data.zip", {"a.txt": "satu", "sub/b.txt": "dua"})
        ctx = Ctx(answer=True)
        out = tools.handle_tool("ekstrak", {"arsip": archive}, ctx)
        self.assertEqual(len(ctx.asked), 1)
        dest = os.path.join(self.d, "data")
        with open(os.path.join(dest, "a.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "satu")
        with open(os.path.join(dest, "sub", "b.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "dua")
        self.assertIn("Diekstrak", out)

    def test_declined_extracts_nothing(self):
        archive = self._make("data.zip", {"a.txt": "satu"})
        ctx = Ctx(answer=False)
        out = tools.handle_tool("ekstrak", {"arsip": archive, "tujuan": os.path.join(self.d, "keluar")}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertFalse(os.path.exists(os.path.join(self.d, "keluar", "a.txt")))

    def test_full_access_still_asks(self):
        archive = self._make("data.zip", {"a.txt": "satu"})
        ctx = Ctx(answer=False, full_access=True)
        tools.handle_tool("ekstrak", {"arsip": archive}, ctx)
        self.assertEqual(len(ctx.asked), 1)
        self.assertEqual(ctx.notified, [])

    def test_zip_slip_is_refused_and_nothing_written(self):
        # Arsip jahat: entri mencoba keluar folder tujuan.
        path = os.path.join(self.d, "jahat.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("../../tertimpa.txt", "jahat")
        ctx = Ctx(answer=True)
        out = tools.handle_tool("ekstrak", {"arsip": path, "tujuan": os.path.join(self.d, "amankan")}, ctx)
        self.assertIn("keluar folder", out)
        self.assertEqual(ctx.asked, [])  # ditolak sebelum konfirmasi
        self.assertFalse(os.path.exists(os.path.join(self.d, "tertimpa.txt")))

    def test_tar_gz_supported(self):
        inner = os.path.join(self.d, "c.txt")
        _write(inner, "isi-tar")
        archive = os.path.join(self.d, "data.tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(inner, arcname="c.txt")
        ctx = Ctx(answer=True)
        out = tools.handle_tool("ekstrak", {"arsip": archive}, ctx)
        self.assertIn("Diekstrak", out)
        self.assertEqual(open(os.path.join(self.d, "data", "c.txt"), encoding="utf-8").read(), "isi-tar")

    def test_unknown_format_refused(self):
        bogus = os.path.join(self.d, "bukan.arsip")
        _write(bogus, "cuma teks biasa")
        ctx = Ctx(answer=True)
        out = tools.handle_tool("ekstrak", {"arsip": bogus}, ctx)
        self.assertIn("tidak didukung", out.lower())
        self.assertEqual(ctx.asked, [])

    def test_missing_archive(self):
        out = tools.handle_tool("ekstrak", {"arsip": os.path.join(self.d, "hilang.zip")}, Ctx())
        self.assertIn("tidak ditemukan", out.lower())


if __name__ == "__main__":
    unittest.main()
