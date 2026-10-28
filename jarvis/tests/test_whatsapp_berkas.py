"""Uji dukungan berkas di jembatan WhatsApp.

Tanpa jaringan & tanpa Node: proses jembatan diganti tiruan, jadi yang diuji
murni logika — keterangan berkas untuk otak, baris JSON ke jembatan, dan
penerusan berkas dari jembatan ke penangan pesan.
"""

import io
import json
import os
import tempfile
import unittest

from itsme import main as itsme_main
from itsme.whatsapp import WhatsApp


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


if __name__ == "__main__":
    unittest.main()
