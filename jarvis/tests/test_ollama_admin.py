"""Uji modul ollama_admin.py (copot Ollama lokal, jalur cadangan tanpa It's Me Node).

Tiga lapis, dan tak satu pun menyentuh Ollama sungguhan di komputer yang menjalankannya
(registry & nama proses diganti dengan milik uji sendiri):

  1. Uji unit dengan jam, registry, dan uninstaller PALSU (jalan di platform apa pun). Inti yang
     dijaga: uninstaller Inno Setup keluar cepat sementara uninstall sebenarnya baru selesai
     beberapa detik kemudian - hasilnya harus dinilai dari registry/berkas, bukan dari prosesnya.
  2. Uji Windows dengan komponen NYATA: baca kunci registry uji sendiri lewat winreg, dan matikan
     proses uji lewat taskkill sungguhan.
  3. Simulasi uninstaller Inno Setup di Windows dengan proses NYATA: uninstaller palsu (.cmd) yang
     langsung keluar sementara "uninstall"-nya baru selesai beberapa detik kemudian di proses lain,
     lengkap dengan kunci registry uji dan "Ollama" palsu yang harus dimatikan. Ini simulasi, BUKAN
     pencopotan Ollama sungguhan.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid
from unittest import mock

from itsme import ollama_admin

UNINS = r"C:\Ollama\unins000.exe"


class FakeClock:
    """Pengganti modul time: sleep() memajukan waktu palsu (tanpa menunggu sungguhan) dan menjalankan
    kejadian yang dijadwalkan lewat at() begitu waktunya lewat."""

    def __init__(self):
        self.now = 0.0
        self._events = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        due = sorted((e for e in self._events if e[0] <= self.now), key=lambda e: e[0])
        self._events = [e for e in self._events if e[0] > self.now]
        for _, action in due:
            action()

    def at(self, when, action):
        self._events.append((when, action))


class FakeProc:
    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


class Machine:
    """Isi komputer palsu: yang masih tersisa dari pemasangan Ollama."""

    def __init__(self, folder):
        self.folder = folder
        self.exe = os.path.join(folder, "ollama.exe")
        open(self.exe, "wb").close()
        self.registry = {"version": "0.40.1", "path": folder, "uninstall": UNINS}
        self.uninstaller = True

    def remove_exe(self):
        try:
            os.remove(self.exe)
        except OSError:
            pass

    def finish(self, keep_exe=False):
        """Uninstall tuntas: catatan registry dan unins000.exe hilang (dan programnya)."""
        self.registry = None
        self.uninstaller = False
        if not keep_exe:
            self.remove_exe()


class InstalledTest(unittest.TestCase):
    def setUp(self):
        self.win = mock.patch.object(ollama_admin, "IS_WIN", True)
        self.win.start()
        self.addCleanup(self.win.stop)

    def check(self, registry, uninstaller):
        with mock.patch.object(ollama_admin, "_registry", return_value=registry), \
             mock.patch.object(ollama_admin, "_uninstaller_path", return_value=uninstaller):
            return ollama_admin.installed()

    def test_not_installed_when_registry_and_uninstaller_missing(self):
        self.assertFalse(self.check(None, None))

    def test_installed_when_uninstaller_present(self):
        self.assertTrue(self.check(None, UNINS))

    def test_installed_when_only_registry_present(self):
        self.assertTrue(self.check({"version": "1", "path": "", "uninstall": ""}, None))

    def test_never_installed_on_non_windows(self):
        with mock.patch.object(ollama_admin, "IS_WIN", False):
            self.assertFalse(self.check({"version": "1", "path": "", "uninstall": ""}, UNINS))


class UninstallFlowTest(unittest.TestCase):
    """Alur uninstall dengan jam & uninstaller palsu. `kill_all` dan `Popen` JANGAN sampai sungguhan:
    keduanya selalu diganti di sini supaya tes tak pernah mematikan proses atau mencopot apa pun."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.m = Machine(self.dir)
        self.clock = FakeClock()
        self.log = []                      # urutan kejadian: "kill", "popen"
        self.popen_call = None
        self.exit_code = 0                 # kode keluar proses uninstaller pertama
        self.finish_after = 3.0            # detik (palsu) sampai uninstall SEBENARNYA selesai; None = tak pernah
        self.on_finish = None              # aksi saat selesai; bawaan: self.m.finish()
        for patch in (
            mock.patch.object(ollama_admin, "IS_WIN", True),
            mock.patch.object(ollama_admin, "time", self.clock),
            mock.patch.object(ollama_admin, "_registry", lambda: self.m.registry),
            mock.patch.object(ollama_admin, "_uninstaller_path", lambda: UNINS if self.m.uninstaller else None),
            mock.patch.object(ollama_admin, "_install_dir", lambda reg=None: self.dir),
            mock.patch.object(ollama_admin, "_models_dir", lambda: os.path.join(self.dir, "tidak-ada")),
            mock.patch.object(ollama_admin, "_kill_all", lambda: self.log.append("kill")),
            mock.patch.object(ollama_admin.subprocess, "Popen", self._popen),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _popen(self, argv, **kwargs):
        self.log.append("popen")
        self.popen_call = (argv, kwargs)
        if self.finish_after is not None:
            self.clock.at(self.clock.now + self.finish_after, self.on_finish or (lambda: self.m.finish()))
        return FakeProc(self.exit_code)

    # --- inti perbaikan: nilai hasilnya, bukan prosesnya -------------------------------------
    def test_waits_until_uninstall_really_finished_even_if_process_exits_at_once(self):
        """Bug asli: proses uninstaller keluar (kode 0) seketika, tapi registry dan unins000.exe
        baru hilang 3 detik kemudian. Memeriksa sesaat sesudahnya = salah melapor 'gagal'."""
        ok, msg = ollama_admin.uninstall(timeout=60)
        self.assertTrue(ok, msg)
        self.assertIn("sudah dicopot", msg)
        self.assertGreaterEqual(self.clock.now, 3.0)   # benar-benar menunggu, bukan langsung menilai
        self.assertLess(self.clock.now, 10.0)          # tapi tidak menunggu sampai batas waktu
        self.assertIsNone(self.m.registry)
        self.assertFalse(os.path.exists(self.m.exe))

    def test_does_not_report_success_before_registry_is_gone(self):
        """Kebalikannya: unins000.exe sudah hilang tapi catatan registry belum -> belum selesai."""
        self.on_finish = lambda: setattr(self.m, "uninstaller", False)
        ok, msg = ollama_admin.uninstall(timeout=10)
        self.assertFalse(ok)
        self.assertIn("belum tuntas", msg)
        self.assertIn("masih ada catatan uninstall di registry.", msg)   # hanya itu yang tersisa

    def test_never_finishing_is_reported_as_unfinished_within_the_timeout(self):
        self.finish_after = None
        ok, msg = ollama_admin.uninstall(timeout=20)
        self.assertFalse(ok)
        self.assertIn("belum tuntas", msg)
        self.assertIn("20 detik", msg)
        self.assertIn("registry", msg)
        self.assertIn("unins000.exe", msg)
        self.assertTrue(20 <= self.clock.now <= 23, self.clock.now)

    def test_exit_code_of_the_first_process_is_reported_when_it_failed(self):
        self.finish_after = None
        self.exit_code = 5
        ok, msg = ollama_admin.uninstall(timeout=5)
        self.assertFalse(ok)
        self.assertIn("kode 5", msg)

    def test_zero_exit_code_is_not_mentioned_as_error(self):
        self.finish_after = None
        ok, msg = ollama_admin.uninstall(timeout=5)
        self.assertNotIn("kode 0", msg)

    # --- program yang tersisa & proses yang hidup lagi -------------------------------------
    def test_leftover_program_file_is_not_reported_as_success(self):
        """Aplikasi tray menyala lagi dan mengunci ollama.exe: registry & unins000.exe hilang,
        tapi programnya tersisa. Jangan dilaporkan 'sudah dicopot'."""
        self.on_finish = lambda: self.m.finish(keep_exe=True)
        ok, msg = ollama_admin.uninstall(timeout=60)
        self.assertFalse(ok)
        self.assertIn(self.m.exe, msg)
        self.assertIn("masih ada", msg)
        self.assertLess(self.clock.now, 15)  # tidak menunggu 60 detik untuk berkas yang tak akan dihapus lagi

    def test_program_file_deleted_shortly_after_the_uninstaller_still_counts(self):
        def finish_then_delete():
            self.m.finish(keep_exe=True)
            self.clock.at(self.clock.now + 2, self.m.remove_exe)

        self.on_finish = finish_then_delete
        ok, msg = ollama_admin.uninstall(timeout=60)
        self.assertTrue(ok, msg)

    def test_keeps_killing_ollama_while_waiting(self):
        ollama_admin.uninstall(timeout=60)
        # sekali sebelum uninstaller jalan, lalu tiap detik selama menunggu (3 detik palsu)
        self.assertEqual(self.log[0], "kill")
        self.assertEqual(self.log[1], "popen")
        self.assertGreaterEqual(self.log[2:].count("kill"), 3)

    def test_silent_command_line_with_log(self):
        ollama_admin.uninstall(timeout=60)
        argv, kwargs = self.popen_call
        self.assertEqual(argv[0], UNINS)
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"):
            self.assertIn(flag, argv)
        self.assertTrue(any(a.startswith("/LOG=") for a in argv))
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)   # tak pernah menunggu keyboard/pipe

    # --- kegagalan & kondisi lain ------------------------------------------------------------
    def test_cannot_start_uninstaller(self):
        def boom(argv, **kwargs):
            raise OSError("akses ditolak")

        with mock.patch.object(ollama_admin.subprocess, "Popen", boom):
            ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertIn("Gagal menjalankan uninstaller", msg)
        self.assertIn("akses ditolak", msg)

    def test_without_ollama_reports_clearly(self):
        self.m.registry = None
        self.m.uninstaller = False
        ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertIn("tidak terpasang", msg)
        self.assertNotIn("popen", self.log)

    def test_registry_without_uninstaller_points_to_settings(self):
        self.m.uninstaller = False
        ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertIn("Pengaturan > Aplikasi", msg)
        self.assertNotIn("popen", self.log)

    def test_success_message_mentions_models_only_if_they_exist(self):
        ok, msg = ollama_admin.uninstall(timeout=60)
        self.assertTrue(ok)
        self.assertNotIn("tetap tersimpan", msg)
        # pasang ulang keadaan awal, kali ini folder model ada
        self.m = Machine(self.dir)
        self.clock.now = 0.0
        models = os.path.join(self.dir, "models")
        os.mkdir(models)
        with mock.patch.object(ollama_admin, "_models_dir", lambda: models):
            ok, msg = ollama_admin.uninstall(timeout=60)
        self.assertTrue(ok, msg)
        self.assertIn("tetap tersimpan di ~/.ollama", msg)

    def test_non_windows_reports_manual_steps_instead_of_guessing(self):
        with mock.patch.object(ollama_admin, "IS_WIN", False):
            ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertIn("Windows", msg)
        self.assertNotIn("popen", self.log)
        self.assertNotIn("kill", self.log)


@unittest.skipUnless(sys.platform == "win32", "registry dan taskkill hanya ada di Windows")
class RealWindowsPartsTest(unittest.TestCase):
    """Komponen NYATA: winreg dan taskkill sungguhan, dengan kunci registry dan nama proses milik uji."""

    def setUp(self):
        import winreg

        self.winreg = winreg
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.parent = r"Software\ItsMeUji-" + uuid.uuid4().hex[:10]
        self.key = self.parent + r"\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1"
        patch = mock.patch.object(ollama_admin, "PRODUCT_KEY", self.key)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._delete_keys)

    def _delete_keys(self):
        for path in (self.key, self.parent):
            try:
                self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass

    def _write_key(self, **values):
        with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, self.key) as k:
            for name, value in values.items():
                self.winreg.SetValueEx(k, name, 0, self.winreg.REG_SZ, value)

    def test_registry_entry_is_read_for_real(self):
        self.assertIsNone(ollama_admin._registry())
        self._write_key(DisplayVersion="0.40.1", **{"Inno Setup: App Path": self.dir,
                                                     "UninstallString": f'"{self.dir}\\unins000.exe"'})
        reg = ollama_admin._registry()
        self.assertEqual(reg["version"], "0.40.1")
        self.assertEqual(reg["path"], self.dir)
        self.assertEqual(reg["uninstall"], f"{self.dir}\\unins000.exe")   # tanda kutip dibuang
        self.assertEqual(ollama_admin._install_dir(), self.dir)
        with mock.patch.object(ollama_admin, "_default_dir", lambda: self.dir):
            self.assertTrue(ollama_admin.installed())        # tercatat di registry walau berkasnya tak ada
            self.assertEqual(ollama_admin._remaining(), ["catatan uninstall di registry"])
        self._delete_keys()
        self.assertIsNone(ollama_admin._registry())
        with mock.patch.object(ollama_admin, "_default_dir", lambda: self.dir):
            self.assertFalse(ollama_admin.installed())

    def test_uninstaller_file_found_through_registry_or_default_folder(self):
        custom = os.path.join(self.dir, "kustom")
        os.mkdir(custom)
        unins = os.path.join(custom, "unins000.exe")
        open(unins, "wb").close()
        self._write_key(**{"UninstallString": f'"{unins}"'})
        with mock.patch.object(ollama_admin, "_default_dir", lambda: os.path.join(self.dir, "kosong")):
            self.assertEqual(ollama_admin._uninstaller_path(), unins)    # lokasi kustom dari registry
        self._delete_keys()
        default = os.path.join(self.dir, "bawaan")
        os.mkdir(default)
        open(os.path.join(default, "unins000.exe"), "wb").close()
        with mock.patch.object(ollama_admin, "_default_dir", lambda: default):
            self.assertEqual(ollama_admin._uninstaller_path(), os.path.join(default, "unins000.exe"))

    def test_kill_all_really_kills_a_process_by_image_name(self):
        name = f"itsme-uji-ollama-{uuid.uuid4().hex[:8]}.exe"
        fake = os.path.join(self.dir, name)
        shutil.copy(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"), fake)
        proc = subprocess.Popen([fake, "/c", "ping -n 60 127.0.0.1 >nul"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=ollama_admin._NO_WINDOW)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        time.sleep(0.5)
        self.assertIsNone(proc.poll(), "proses uji harus hidup sebelum dimatikan")
        with mock.patch.object(ollama_admin, "_OLLAMA_PROCS", (name,)):
            ollama_admin._kill_all()
        proc.wait(timeout=10)   # TimeoutExpired = taskkill tidak bekerja
        self.assertIsNotNone(proc.poll())

    def test_kill_all_tolerates_nothing_to_kill(self):
        with mock.patch.object(ollama_admin, "_OLLAMA_PROCS", (f"tidak-ada-{uuid.uuid4().hex[:8]}.exe",)):
            ollama_admin._kill_all()   # tidak boleh melempar


@unittest.skipUnless(sys.platform == "win32", "simulasi uninstaller Inno Setup memakai registry dan proses Windows")
class InnoUninstallerSimulationTest(unittest.TestCase):
    """Simulasi dengan PROSES NYATA (bukan Ollama sungguhan): uninstaller palsu meniru kebiasaan Inno
    Setup - proses yang dijalankan LANGSUNG keluar, sementara uninstall-nya sendiri baru selesai
    beberapa detik kemudian di proses lain (menghapus kunci registry, program, lalu dirinya)."""

    DELAY = 3.0

    def setUp(self):
        import winreg

        self.winreg = winreg
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.folder = os.path.join(self.dir, "Ollama")
        os.mkdir(self.folder)
        self.exe = os.path.join(self.folder, "ollama.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ")
        self.unins = os.path.join(self.folder, "unins000.cmd")
        self.parent = r"Software\ItsMeUji-" + uuid.uuid4().hex[:10]
        self.key = self.parent + r"\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key) as k:
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, "0.40.1")
            winreg.SetValueEx(k, "Inno Setup: App Path", 0, winreg.REG_SZ, self.folder)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{self.unins}"')
        self.addCleanup(self._delete_keys)
        self.marker = os.path.join(self.dir, "fase-dua-selesai.txt")
        second = os.path.join(self.dir, "fase_dua.py")
        with open(second, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(f"""
                import os, time, winreg
                time.sleep({self.DELAY})
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, {self.key!r})
                except OSError:
                    pass
                for path in ({self.exe!r}, {self.unins!r}):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                with open({self.marker!r}, "w") as f:
                    f.write("selesai")
            """))
        with open(self.unins, "w", encoding="utf-8") as f:
            f.write("@echo off\r\n"
                    "rem uninstaller palsu: fase pertama langsung keluar, fase kedua jalan di proses lain\r\n"
                    f'start "" /b "{sys.executable}" "{second}"\r\n'
                    "exit /b 0\r\n")
        self.fake_name = f"itsme-uji-ollama-{uuid.uuid4().hex[:8]}.exe"
        self.fake_proc = None
        for patch in (
            mock.patch.object(ollama_admin, "PRODUCT_KEY", self.key),
            mock.patch.object(ollama_admin, "_default_dir", lambda: self.folder),
            mock.patch.object(ollama_admin, "_OLLAMA_PROCS", (self.fake_name,)),   # BUKAN proses Ollama asli
            mock.patch.object(ollama_admin, "_models_dir", lambda: os.path.join(self.dir, "tidak-ada")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _delete_keys(self):
        for path in (self.key, self.parent):
            try:
                self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass
        if self.fake_proc is not None and self.fake_proc.poll() is None:
            self.fake_proc.kill()

    def _start_fake_ollama_app(self):
        fake = os.path.join(self.dir, self.fake_name)
        shutil.copy(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"), fake)
        self.fake_proc = subprocess.Popen([fake, "/c", "ping -n 120 127.0.0.1 >nul"], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=ollama_admin._NO_WINDOW)
        time.sleep(0.5)
        self.assertIsNone(self.fake_proc.poll())

    def test_premise_first_process_exits_long_before_uninstall_is_finished(self):
        """Pastikan simulasinya memang meniru bug aslinya: prosesnya sudah keluar, installed() masih True."""
        proc = subprocess.Popen([self.unins, "/VERYSILENT"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=ollama_admin._NO_WINDOW)
        started = time.monotonic()
        self.assertEqual(proc.wait(timeout=20), 0)
        self.assertLess(time.monotonic() - started, self.DELAY)          # keluar lebih dulu dari uninstall-nya
        self.assertTrue(ollama_admin.installed(), "tepat sesudah proses keluar, uninstall belum selesai")
        deadline = time.monotonic() + 30
        while not os.path.exists(self.marker) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertTrue(os.path.exists(self.marker))
        self.assertFalse(ollama_admin.installed())

    def test_uninstall_waits_for_the_real_finish_and_kills_the_respawned_app(self):
        self._start_fake_ollama_app()
        started = time.monotonic()
        ok, msg = ollama_admin.uninstall(timeout=60)
        elapsed = time.monotonic() - started
        self.assertTrue(ok, msg)
        self.assertIn("sudah dicopot", msg)
        self.assertGreaterEqual(elapsed, self.DELAY)                       # menunggu fase kedua, bukan proses pertama
        self.assertTrue(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(self.exe))
        self.assertFalse(os.path.exists(self.unins))
        self.assertIsNone(ollama_admin._registry())
        self.assertFalse(ollama_admin.installed())
        self.assertIsNotNone(self.fake_proc.wait(timeout=10), "proses 'Ollama' palsu harus sudah dimatikan")

    def test_unfinished_uninstall_is_reported_honestly(self):
        """Uninstaller yang tak pernah menyelesaikan fase keduanya: 'belum tuntas', bukan 'berhasil'."""
        with open(self.unins, "w", encoding="utf-8") as f:
            f.write("@echo off\r\nexit /b 0\r\n")
        started = time.monotonic()
        ok, msg = ollama_admin.uninstall(timeout=4)
        self.assertFalse(ok)
        self.assertIn("belum tuntas", msg)
        self.assertIn("registry", msg)
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(os.path.exists(self.exe))


@unittest.skipIf(sys.platform == "win32", "jalur non-Windows")
class NonWindowsTest(unittest.TestCase):
    def test_reports_manual_steps_instead_of_guessing(self):
        ok, msg = ollama_admin.uninstall()
        self.assertFalse(ok)
        self.assertFalse(ollama_admin.installed())
        self.assertIn("Windows", msg)


if __name__ == "__main__":
    unittest.main()
