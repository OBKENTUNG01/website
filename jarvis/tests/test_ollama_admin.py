"""Uji modul ollama_admin.py (copot Ollama) tanpa benar-benar menjalankan uninstaller."""

import subprocess
import sys
import unittest
from unittest import mock

from itsme import ollama_admin


@unittest.skipUnless(sys.platform == "win32", "uninstall Ollama hanya diimplementasikan di Windows")
class WindowsTest(unittest.TestCase):
    def test_not_installed_when_uninstaller_missing(self):
        with mock.patch.object(ollama_admin, "_uninstaller_path", return_value=None):
            self.assertFalse(ollama_admin.installed())

    def test_installed_when_uninstaller_present(self):
        with mock.patch.object(ollama_admin, "_uninstaller_path", return_value=r"C:\x\unins000.exe"):
            self.assertTrue(ollama_admin.installed())

    def test_uninstall_without_ollama_reports_clearly(self):
        with mock.patch.object(ollama_admin, "_uninstaller_path", return_value=None):
            ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertIn("tidak terpasang", msg)

    def test_uninstall_kills_processes_then_runs_silently(self):
        calls = []
        with mock.patch.object(ollama_admin, "_uninstaller_path", return_value=r"C:\x\unins000.exe"), \
             mock.patch.object(ollama_admin, "_pids", return_value=[111]), \
             mock.patch.object(ollama_admin, "_kill_all") as kill_all, \
             mock.patch.object(ollama_admin.time, "sleep"), \
             mock.patch.object(ollama_admin.subprocess, "run",
                                side_effect=lambda argv, **kw: calls.append(argv)), \
             mock.patch.object(ollama_admin, "installed", return_value=False):
            ok, msg = ollama_admin.uninstall()
        self.assertTrue(ok)
        kill_all.assert_called_once()
        argv = calls[0]
        self.assertEqual(argv[0], r"C:\x\unins000.exe")
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"):
            self.assertIn(flag, argv)

    def test_uninstall_timeout_is_not_treated_as_failure(self):
        with mock.patch.object(ollama_admin, "_uninstaller_path", return_value=r"C:\x\unins000.exe"), \
             mock.patch.object(ollama_admin, "_pids", return_value=[]), \
             mock.patch.object(ollama_admin.time, "sleep"), \
             mock.patch.object(ollama_admin.subprocess, "run",
                                side_effect=subprocess.TimeoutExpired("unins000.exe", 60)), \
             mock.patch.object(ollama_admin, "installed", return_value=False):
            ok, msg = ollama_admin.uninstall()
        self.assertTrue(ok)

    def test_still_installed_after_run_is_reported_as_failure(self):
        with mock.patch.object(ollama_admin, "_uninstaller_path", return_value=r"C:\x\unins000.exe"), \
             mock.patch.object(ollama_admin, "_pids", return_value=[]), \
             mock.patch.object(ollama_admin.time, "sleep"), \
             mock.patch.object(ollama_admin.subprocess, "run", return_value=None), \
             mock.patch.object(ollama_admin, "installed", return_value=True):
            ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertIn("masih terpasang", msg)


@unittest.skipIf(sys.platform == "win32", "jalur non-Windows")
class NonWindowsTest(unittest.TestCase):
    def test_reports_manual_steps_instead_of_guessing(self):
        ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertFalse(ollama_admin.installed())


if __name__ == "__main__":
    unittest.main()
