"""Menjalankan tes JavaScript logika jembatan WhatsApp (itsme/whatsapp/wa_core.test.js).

Logika murninya (QR / tersambung / putus / dicabut dari HP, memindahkan sesi lama) dipisah ke
wa_core.js justru supaya bisa diuji tanpa Baileys & tanpa jaringan. Dilewati kalau Node.js tak ada.
"""

import os
import re
import shutil
import subprocess
import unittest

from itsme import whatsapp as wa_module


@unittest.skipUnless(shutil.which("node"), "Node.js tidak terpasang")
class WaCoreJsTest(unittest.TestCase):
    def test_node_test_runner_passes(self):
        # UTF-8 tegas: laporan spec Node memakai simbol (✔ ℹ) yang tidak terbaca oleh code page Windows.
        r = subprocess.run([shutil.which("node"), "--test", "wa_core.test.js"], cwd=wa_module.DIR,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # Format laporan Node berbeda antar versi ("# fail 0" di TAP, "ℹ fail 0" di spec - bawaan Node baru
        # di GitHub Actions), jadi periksa angkanya, bukan bentuk barisnya.
        out = r.stdout
        self.assertRegex(out, r"\bfail 0\b")
        ran = re.search(r"\btests (\d+)\b", out)
        self.assertTrue(ran and int(ran.group(1)) >= 20, f"tes JS tidak berjalan semua:\n{out[-800:]}")

    def test_bridge_script_parses(self):
        for name in ("wa.js", "wa_core.js"):
            r = subprocess.run([shutil.which("node"), "--check", name], cwd=wa_module.DIR,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(r.returncode, 0, name + "\n" + r.stderr)

    def test_bridge_files_exist_where_python_expects_them(self):
        for name in ("wa.js", "wa_core.js", "wa_core.test.js", "package.json"):
            self.assertTrue(os.path.isfile(os.path.join(wa_module.DIR, name)), name)


if __name__ == "__main__":
    unittest.main()
