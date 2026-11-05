"""Uji modul rea.py (jembatan CLI REA) tanpa memanggil npx/REA sungguhan."""

import subprocess
import sys
import unittest
from unittest import mock

from itsme import rea


class PickCommandTest(unittest.TestCase):
    def test_exe_and_dll(self):
        self.assertEqual(rea.pick_command("app.exe"), "inspect-pe-resources")
        self.assertEqual(rea.pick_command(r"C:\path\lib.DLL"), "inspect-pe-resources")  # tidak case-sensitive

    def test_apk(self):
        self.assertEqual(rea.pick_command("app.apk"), "inspect-apk-signature")

    def test_unknown_extension_is_none(self):
        self.assertIsNone(rea.pick_command("bundle"))  # mis. folder Electron, tanpa ekstensi
        self.assertIsNone(rea.pick_command("app.js"))


class AnalyzeTest(unittest.TestCase):
    def test_no_command_raises(self):
        with self.assertRaises(rea.ReaError):
            rea.analyze("app.js")  # tidak ada bawaan, dan tidak diberi rea_command

    def test_runs_npx_with_json_flag(self):
        fake = mock.Mock(returncode=0, stdout='{"ok": true}', stderr="")
        with mock.patch("itsme.rea.subprocess.run", return_value=fake) as run:
            ok, out = rea.analyze("app.exe")
        self.assertTrue(ok)
        self.assertEqual(out, '{"ok": true}')
        argv = run.call_args.args[0]
        self.assertIn("rea-agents@latest", argv)
        self.assertIn("inspect-pe-resources", argv)
        self.assertIn("app.exe", argv)
        self.assertIn("--json", argv)
        if sys.platform == "win32":
            # npx adalah skrip .cmd: tanpa cmd.exe, subprocess tanpa shell tidak bisa menjalankannya.
            self.assertEqual(argv[:3], ["cmd.exe", "/d", "/c"])

    def test_nonzero_exit_is_reported_not_raised(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="gagal parsing")
        with mock.patch("itsme.rea.subprocess.run", return_value=fake):
            ok, out = rea.analyze("app.exe")
        self.assertFalse(ok)
        self.assertIn("gagal parsing", out)

    def test_timeout_is_reported_not_raised(self):
        with mock.patch("itsme.rea.subprocess.run", side_effect=subprocess.TimeoutExpired("rea", 180)):
            ok, out = rea.analyze("app.exe")
        self.assertFalse(ok)
        self.assertIn("batas waktu", out)

    def test_npx_missing_is_reported_not_raised(self):
        with mock.patch("itsme.rea.subprocess.run", side_effect=FileNotFoundError()):
            ok, out = rea.analyze("app.exe")
        self.assertFalse(ok)
        self.assertIn("Node.js", out)

    def test_explicit_command_overrides_auto(self):
        fake = mock.Mock(returncode=0, stdout="hasil", stderr="")
        with mock.patch("itsme.rea.subprocess.run", return_value=fake) as run:
            rea.analyze("app.exe", command="inspect-with-objdump")
        self.assertIn("inspect-with-objdump", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
