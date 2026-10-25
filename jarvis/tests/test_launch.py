"""Uji skrip peluncur yang menyegarkan diri sendiri.

Konteks: updater hanya memasang paket `itsme/`, sedangkan skrip peluncur dibuat
sekali oleh setup.ps1. Dua keputusan di skrip lama terbalik — kode 3 (sudah jalan)
membuat launcher menyerah, kode 0 (berhenti disengaja) membuatnya menyalakan ulang.
Uji ini mengunci arti kode-kode itu.
"""

import os
import tempfile
import unittest

from itsme import launch


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def read(self, name):
        with open(os.path.join(self.d, name), encoding="utf-8") as f:
            return f.read()

    def test_writes_both_launchers(self):
        self.assertEqual(sorted(launch.ensure(self.d)), ["run-itsme.ps1", "start-itsme.cmd"])
        self.assertIn("itsme.main", self.read("start-itsme.cmd"))
        self.assertIn("itsme.main", self.read("run-itsme.ps1"))

    def test_exit_codes_are_not_inverted(self):
        launch.ensure(self.d)
        cmd = self.read("start-itsme.cmd")
        # kode 5 = pembaruan: langsung nyalakan lagi
        self.assertIn("EQU 5 goto loop", cmd)
        # kode 0 = berhenti disengaja: keluar, jangan nyalakan lagi
        self.assertIn("EQU 0 exit /b 0", cmd)
        # kode 3 = masih ada It's Me lain: TUNGGU lalu coba lagi, jangan menyerah
        self.assertIn("EQU 3", cmd)
        self.assertNotIn("EQU 3 exit /b 0", cmd)

        ps1 = self.read("run-itsme.ps1")
        self.assertIn("$code -eq 5", ps1)
        self.assertIn("$code -eq 0) { break }", ps1)
        self.assertIn("$code -eq 3", ps1)
        self.assertNotIn("$code -eq 3) { break }", ps1)

    def test_idempotent(self):
        launch.ensure(self.d)
        self.assertEqual(launch.ensure(self.d), [])  # isi sudah benar -> tidak disentuh

    def test_heals_stale_launcher(self):
        launch.ensure(self.d)                                  # dua berkas sudah benar
        path = os.path.join(self.d, "start-itsme.cmd")
        with open(path, "w", encoding="utf-8") as f:
            f.write("@echo off\r\n:loop\r\npython -m itsme.main\r\nif %ERRORLEVEL% EQU 3 exit /b 0\r\n")
        self.assertEqual(launch.ensure(self.d), ["start-itsme.cmd"])
        self.assertNotIn("EQU 3 exit /b 0", self.read("start-itsme.cmd"))

    def test_read_only_folder_does_not_raise(self):
        missing = os.path.join(self.d, "tidak-ada", "sub")
        self.assertEqual(launch.ensure(missing), [])  # gagal menulis, tapi tidak meledak


if __name__ == "__main__":
    unittest.main()
