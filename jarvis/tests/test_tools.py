"""Uji alat It's Me tanpa jaringan.

Jalankan dari folder jarvis/:  python -m unittest discover -s tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from itsme import memory, tools, web


class FakeCtx(dict):
    """ctx alat dengan konfirmasi palsu yang mencatat teksnya."""

    def __init__(self, website_dir=None, answer=True, **extra):
        self.asked = []
        self.answer = answer
        super().__init__(
            website_dir=website_dir,
            memory=mock.Mock(),
            confirm=self._confirm,
            schedule_background=mock.Mock(return_value="dijadwalkan"),
            seen_urls=set(),
            in_background=False,
        )
        self.update(extra)

    def _confirm(self, text):
        self.asked.append(text)
        return self.answer


class ReadOnlyCommandTest(unittest.TestCase):
    def test_allowed_without_confirmation(self):
        for cmd in ("git status", "git log --oneline -5", "git diff", "git show HEAD",
                    "ls", "dir", "pwd", "whoami", "tree"):
            self.assertTrue(tools.is_read_only(cmd), cmd)

    def test_needs_confirmation(self):
        for cmd in (
            "git branch -D main",
            "git branch -f main HEAD~5",
            "git diff --output=../index.php",
            "git log --output=notes.txt",
            "git diff --no-index a b",
            "git push",
            "git -c core.pager=x status",
            "cat .env",
            "type .env",
            "date",
            "tail -f log.txt",
            "ls; rm -rf x",
            "echo %PATH%",
            "rm -rf x",
            "",
        ):
            self.assertFalse(tools.is_read_only(cmd), cmd)

    def test_confirmation_shown_for_other_commands(self):
        ctx = FakeCtx(answer=False)
        with mock.patch.object(tools, "_exec") as run:
            out = tools.handle_tool("jalankan_perintah", {"perintah": "git branch -D main"}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertIn("git branch -D main", ctx.asked[0])
        run.assert_not_called()

    def test_read_only_runs_directly(self):
        ctx = FakeCtx()
        with mock.patch.object(tools, "_exec", return_value=(0, "bersih", "")) as run:
            out = tools.handle_tool("jalankan_perintah", {"perintah": "git status"}, ctx)
        self.assertEqual(ctx.asked, [])
        run.assert_called_once()
        self.assertIn("bersih", out)


class SecretPathTest(unittest.TestCase):
    def test_secret_paths(self):
        for path in (".env", "jarvis/.env", "a/.env.local", "C:/Users/x/.ssh/config",
                     "/home/x/.ssh/id_ed25519", "id_rsa", "server.pem", "~/.git-credentials",
                     r"C:\Users\x\.aws\config"):
            self.assertTrue(tools.is_secret_path(path), path)

    def test_normal_paths(self):
        for path in ("index.html", "jarvis/.env.example", "src/app.py", "README.md"):
            self.assertFalse(tools.is_secret_path(path), path)

    APP = r"C:\Users\x\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude"

    def test_login_of_claude_and_browsers_is_secret(self):
        for path in (
            r"C:\Users\x\.claude\.credentials.json", r"C:\Users\x\.claude\settings.json",
            r"C:\Users\x\.claude\session-env\abc", r"C:\Users\x\.claude",
            self.APP, self.APP + r"\buddy-tokens.json", self.APP + r"\config.json",
            self.APP + r"\claude_desktop_config.json", self.APP + r"\Local Storage\leveldb\000003.log",
            r"C:\Users\x\AppData\Roaming\Claude\Network\Cookies",
            r"C:\Users\x\AppData\Local\Google\Chrome\User Data\Default\Login Data",
            r"C:\Users\x\AppData\Local\Google\Chrome\User Data\Local State",
            "client_secret.json", "passwords.txt", "api-tokens.json",
        ):
            self.assertTrue(tools.is_secret_path(path), path)

    def test_owner_work_history_is_readable(self):
        for path in (
            r"C:\Users\x\.claude\projects\C--Users-x-proyek\abc.jsonl",
            self.APP + r"\claude-code-sessions\a\b\local_123.json",
            r"C:\Users\x\AppData\Roaming\Claude\scratch-workspaces\s\index.php",
            r"C:\Users\x\Downloads\Compressed\domains\token.php",
        ):
            self.assertFalse(tools.is_secret_path(path), path)

    def test_symlink_to_secret_is_still_secret(self):
        # Nama polos (catatan.txt) tak boleh lolos cuma karena namanya tidak
        # mencurigakan - tujuan SEBENARNYA (lewat symlink) yang diperiksa.
        # Sama seperti fix(remote) #7 di itsme-node, bug yang sama persis.
        with tempfile.TemporaryDirectory() as d:
            rahasia = os.path.join(d, ".env")
            with open(rahasia, "w", encoding="utf-8") as f:
                f.write("TOKEN=rahasia")
            tautan = os.path.join(d, "catatan.txt")
            try:
                os.symlink(rahasia, tautan)
            except (OSError, NotImplementedError):
                self.skipTest("perlu hak symlink (Developer Mode / admin di Windows)")
            self.assertTrue(tools.is_secret_path(tautan), tautan)

    def test_hardlink_to_secret_is_detected(self):
        # realpath() tidak bisa membedakan hardlink dari berkas aslinya, jadi
        # ini pemeriksaan TERPISAH dari is_secret_path (lihat is_hardlinked).
        # Sama seperti fix(remote) #8 di itsme-node, bug yang sama persis.
        with tempfile.TemporaryDirectory() as d:
            rahasia = os.path.join(d, ".env")
            with open(rahasia, "w", encoding="utf-8") as f:
                f.write("TOKEN=rahasia")
            tautan = os.path.join(d, "foto.txt")
            try:
                os.link(rahasia, tautan)
            except OSError:
                self.skipTest("perlu hak hardlink di sistem berkas ini")
            self.assertFalse(tools.is_secret_path(tautan), tautan)  # namanya sendiri aman
            self.assertTrue(tools.is_hardlinked(tautan), tautan)  # tapi hardlink -> ditolak terpisah
            # Berkas BIASA (tanpa hardlink apa pun) tak pernah kena pemeriksaan ini.
            sendiri = os.path.join(d, "sendirian.txt")
            with open(sendiri, "w", encoding="utf-8") as f:
                f.write("biasa saja")
            self.assertFalse(tools.is_hardlinked(sendiri), sendiri)

    def test_baca_berkas_refuses_symlink_to_secret(self):
        with tempfile.TemporaryDirectory() as d:
            rahasia = os.path.join(d, ".env")
            with open(rahasia, "w", encoding="utf-8") as f:
                f.write("TOKEN=rahasia")
            tautan = os.path.join(d, "catatan.txt")
            try:
                os.symlink(rahasia, tautan)
            except (OSError, NotImplementedError):
                self.skipTest("perlu hak symlink (Developer Mode / admin di Windows)")
            ctx = FakeCtx(full_access=True)
            out = tools.handle_tool("baca_berkas", {"path": tautan}, ctx)
            self.assertIn("Ditolak", out)
            self.assertNotIn("TOKEN=rahasia", out)  # isi berkas tak boleh ikut bocor

    def test_baca_berkas_refuses_hardlink_to_secret(self):
        with tempfile.TemporaryDirectory() as d:
            rahasia = os.path.join(d, ".env")
            with open(rahasia, "w", encoding="utf-8") as f:
                f.write("TOKEN=rahasia")
            tautan = os.path.join(d, "foto.txt")
            try:
                os.link(rahasia, tautan)
            except OSError:
                self.skipTest("perlu hak hardlink di sistem berkas ini")
            ctx = FakeCtx(full_access=True)
            out = tools.handle_tool("baca_berkas", {"path": tautan}, ctx)
            self.assertIn("Ditolak", out)
            self.assertNotIn("TOKEN=rahasia", out)

    def test_command_naming_a_secret(self):
        self.assertTrue(tools.mentions_secret(r"Get-Content C:\Users\x\ItsMe\.env"))
        self.assertTrue(tools.mentions_secret(f'Get-ChildItem "{self.APP}"'))
        self.assertFalse(tools.mentions_secret(r"Get-ChildItem C:\Users\x\Downloads -Recurse"))

    def test_searching_everything_at_once_is_too_broad(self):
        home = os.path.expanduser("~")
        for path in (home, os.path.join(home, "AppData", "Local")):
            self.assertTrue(tools.too_broad_to_search(path), path)
        if sys.platform == "win32":
            # "C:\\" hanya berarti "seluruh disk" di Windows; di Linux itu nama
            # folder biasa, jadi premisnya tidak berlaku di runner CI.
            self.assertTrue(tools.too_broad_to_search("C:\\"))
        for path in (os.path.join(home, "Downloads", "proyek"), os.path.join(home, ".claude", "projects"),
                     self.APP + r"\claude-code-sessions"):
            self.assertFalse(tools.too_broad_to_search(path), path)


class FullAccessTest(unittest.TestCase):
    """Mode akses penuh: langsung jalan dan dikabari; rahasia tetap dijaga."""

    def setUp(self):
        self.outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        self.notes = []
        self.ctx = FakeCtx(website_dir=None, answer=False, full_access=True, notify=self.notes.append)

    def test_command_runs_without_button_and_owner_is_told(self):
        with mock.patch.object(tools, "_run", return_value=(0, "selesai")) as run:
            out = tools.handle_tool("jalankan_perintah", {"perintah": "mkdir baru"}, self.ctx)
        self.assertEqual(out, "selesai")
        run.assert_called_once()
        self.assertEqual(self.ctx.asked, [])
        self.assertIn("mkdir baru", self.notes[0])

    def test_command_naming_a_secret_still_asks(self):
        with mock.patch.object(tools, "_run", return_value=(0, "isi")) as run:
            out = tools.handle_tool("jalankan_perintah", {"perintah": "type C:\\ItsMe\\.env"}, self.ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        run.assert_not_called()
        self.assertEqual(len(self.ctx.asked), 1)

    def test_reads_anywhere_silently_but_not_secrets(self):
        path = os.path.join(self.outside, "kerjaan.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("laporan")
        self.assertEqual(tools.handle_tool("baca_berkas", {"path": path}, self.ctx), "laporan")
        self.assertEqual((self.ctx.asked, self.notes), ([], []))
        secret = os.path.join(self.outside, ".env")
        self.assertIn("Ditolak", tools.handle_tool("baca_berkas", {"path": secret}, self.ctx))

    def test_safe_mode_still_asks(self):
        safe = FakeCtx(website_dir=None, answer=False, notify=self.notes.append)
        with mock.patch.object(tools, "_run") as run:
            tools.handle_tool("jalankan_perintah", {"perintah": "mkdir baru"}, safe)
        run.assert_not_called()
        self.assertEqual(len(safe.asked), 1)
        self.assertEqual(self.notes, [])


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        self.site = tempfile.mkdtemp()
        self.outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.site, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        with open(os.path.join(self.site, "index.html"), "w", encoding="utf-8") as f:
            f.write("<h1>Halo</h1>")
        with open(os.path.join(self.outside, "catatan.txt"), "w", encoding="utf-8") as f:
            f.write("rahasia kecil")
        with open(os.path.join(self.site, ".env"), "w", encoding="utf-8") as f:
            f.write("TOKEN=abc")

    def test_read_inside_site_without_confirmation(self):
        ctx = FakeCtx(self.site)
        out = tools.handle_tool("baca_berkas", {"path": "index.html"}, ctx)
        self.assertEqual(out, "<h1>Halo</h1>")
        self.assertEqual(ctx.asked, [])

    def test_read_outside_site_asks(self):
        ctx = FakeCtx(self.site, answer=False)
        out = tools.handle_tool("baca_berkas", {"path": os.path.join(self.outside, "catatan.txt")}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertEqual(len(ctx.asked), 1)

    def test_read_secret_refused(self):
        ctx = FakeCtx(self.site)
        out = tools.handle_tool("baca_berkas", {"path": ".env"}, ctx)
        self.assertIn("Ditolak", out)
        self.assertEqual(ctx.asked, [])

    def test_write_shows_content_and_respects_no(self):
        ctx = FakeCtx(self.site, answer=False)
        out = tools.handle_tool("tulis_berkas", {"path": "baru.txt", "isi": "isi yang akan ditulis"}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertIn("isi yang akan ditulis", ctx.asked[0])
        self.assertFalse(os.path.exists(os.path.join(self.site, "baru.txt")))

    def test_write_after_yes(self):
        ctx = FakeCtx(self.site)
        tools.handle_tool("tulis_berkas", {"path": "baru.txt", "isi": "oke"}, ctx)
        with open(os.path.join(self.site, "baru.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "oke")

    def test_write_secret_refused(self):
        ctx = FakeCtx(self.site)
        out = tools.handle_tool("tulis_berkas", {"path": ".env", "isi": "X=1"}, ctx)
        self.assertIn("Ditolak", out)
        self.assertEqual(ctx.asked, [])


class MemoryAndBackgroundTest(unittest.TestCase):
    def test_memory_needs_confirmation(self):
        ctx = FakeCtx(answer=False)
        out = tools.handle_tool("simpan_memori", {"catatan": "Nama kucingku Mimi"}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertIn("Mimi", ctx.asked[0])
        ctx["memory"].add.assert_not_called()

    def test_memory_saved_after_yes(self):
        ctx = FakeCtx()
        tools.handle_tool("simpan_memori", {"catatan": "Nama kucingku Mimi"}, ctx)
        ctx["memory"].add.assert_called_once_with("Nama kucingku Mimi")

    def test_memory_clear(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        mem = memory.Memory(os.path.join(folder, "memory.md"))
        mem.add("satu")
        mem.clear()
        self.assertNotIn("satu", mem.text())

    def test_no_nested_background_tasks(self):
        ctx = FakeCtx(in_background=True)
        out = tools.handle_tool("tugas_latar", {"instruksi": "cek cuaca"}, ctx)
        self.assertIn("tidak bisa", out)
        ctx["schedule_background"].assert_not_called()


class WebToolsTest(unittest.TestCase):
    def test_search_results_become_openable(self):
        fake = types.ModuleType("ddgs")

        class DDGS:
            def __init__(self, timeout=10):
                pass

            def text(self, query, **kwargs):
                return [{"title": "Berita", "href": "https://contoh.id/a", "body": "isi"}]

        fake.DDGS = DDGS
        seen = set()
        with mock.patch.dict(sys.modules, {"ddgs": fake}):
            out = web.search("berita", seen=seen)
        self.assertIn("https://contoh.id/a", out)
        self.assertEqual(seen, {"https://contoh.id/a"})

    def test_known_url_opens_without_confirmation(self):
        ctx = FakeCtx()
        ctx["seen_urls"].add("https://contoh.id/a")
        with mock.patch.object(web, "fetch", return_value="isi halaman") as fetch:
            out = tools.handle_tool("baca_halaman", {"url": "https://contoh.id/a"}, ctx)
        self.assertEqual(out, "isi halaman")
        self.assertEqual(ctx.asked, [])
        fetch.assert_called_once()

    def test_unknown_url_asks(self):
        ctx = FakeCtx(answer=False)
        with mock.patch.object(web, "fetch") as fetch:
            out = tools.handle_tool("baca_halaman", {"url": "https://lain.example/x?d=1"}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertIn("https://lain.example/x?d=1", ctx.asked[0])
        fetch.assert_not_called()


@unittest.skipUnless(shutil.which("git"), "git tidak terpasang")
class DeployTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@e")
        for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@e"]):
            subprocess.run(["git", *args], cwd=self.repo, check=True, env=env)
        with open(os.path.join(self.repo, "index.html"), "w", encoding="utf-8") as f:
            f.write("a")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "awal"], cwd=self.repo, check=True, env=env)

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_nothing_to_deploy(self):
        ctx = FakeCtx(self.repo)
        out = tools.handle_tool("deploy_website", {"pesan_commit": "x"}, ctx)
        self.assertIn("Tidak ada perubahan", out)
        self.assertEqual(ctx.asked, [])

    def test_secret_file_blocks_deploy(self):
        self._write("index.html", "b")
        self._write(".env", "TOKEN=abc")
        ctx = FakeCtx(self.repo)
        out = tools.handle_tool("deploy_website", {"pesan_commit": "x"}, ctx)
        self.assertIn("berkas rahasia", out)
        self.assertEqual(ctx.asked, [])

    def test_confirmation_lists_files(self):
        self._write("index.html", "b")
        ctx = FakeCtx(self.repo, answer=False)
        out = tools.handle_tool("deploy_website", {"pesan_commit": "ganti judul"}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertIn("index.html", ctx.asked[0])
        self.assertIn("ganti judul", ctx.asked[0])

    def test_push_without_remote_reports_failure(self):
        self._write("index.html", "b")
        ctx = FakeCtx(self.repo)
        out = tools.handle_tool("deploy_website", {"pesan_commit": "ganti"}, ctx)
        self.assertIn("Berhenti", out)


class ExecTest(unittest.TestCase):
    def test_secrets_not_passed_to_programs(self):
        cmd = "$env:TELEGRAM_BOT_TOKEN" if tools.IS_WINDOWS else "echo $TELEGRAM_BOT_TOKEN"
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "rahasia-123"}):
            code, out, err = tools._exec(cmd, None, timeout=60)
        self.assertNotIn("rahasia-123", out + err)

    def test_never_waits_for_keyboard(self):
        cmd = "Read-Host 'ketik'" if tools.IS_WINDOWS else "read x; echo selesai"
        code, out, err = tools._exec(cmd, None, timeout=60)
        self.assertNotIn("melebihi batas waktu", err)

    @unittest.skipUnless(tools.IS_WINDOWS, "khusus Windows")
    def test_powershell_commands_work(self):
        code, out, err = tools._exec("Get-Date -Format yyyy", None, timeout=60)
        self.assertEqual(code, 0, err)
        self.assertRegex(out.strip(), r"^\d{4}$")


class ReaToolDispatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.exe = os.path.join(self.dir, "app.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ")

    def test_missing_target(self):
        ctx = FakeCtx()
        out = tools.handle_tool("analisis_rea", {"path_atau_url": ""}, ctx)
        self.assertIn("kosong", out)

    def test_file_not_found(self):
        ctx = FakeCtx()
        out = tools.handle_tool("analisis_rea", {"path_atau_url": os.path.join(self.dir, "nope.exe")}, ctx)
        self.assertIn("tidak ditemukan", out)

    def test_secret_path_refused(self):
        env = os.path.join(self.dir, ".env")
        open(env, "w", encoding="utf-8").close()
        ctx = FakeCtx()
        out = tools.handle_tool("analisis_rea", {"path_atau_url": env}, ctx)
        self.assertIn("rahasia", out)

    def test_runs_with_auto_command_after_confirmation(self):
        from itsme import rea as rea_mod
        ctx = FakeCtx()
        with mock.patch.object(rea_mod, "analyze", return_value=(True, "hasil REA")) as analyze:
            out = tools.handle_tool("analisis_rea", {"path_atau_url": self.exe}, ctx)
        self.assertEqual(out, "hasil REA")
        self.assertIn("inspect-artifact", ctx.asked[0])
        analyze.assert_called_once_with(self.exe, None)

    def test_explicit_rea_command_is_passed_through(self):
        from itsme import rea as rea_mod
        ctx = FakeCtx()
        with mock.patch.object(rea_mod, "analyze", return_value=(True, "ok")) as analyze:
            tools.handle_tool("analisis_rea", {"path_atau_url": self.exe, "rea_command": "inspect-pe-resources"}, ctx)
        analyze.assert_called_once_with(self.exe, "inspect-pe-resources")

    def test_declined_does_not_run(self):
        from itsme import rea as rea_mod
        ctx = FakeCtx(answer=False)
        with mock.patch.object(rea_mod, "analyze") as analyze:
            out = tools.handle_tool("analisis_rea", {"path_atau_url": self.exe}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        analyze.assert_not_called()

    def test_full_access_still_asks_confirmation(self):
        """Seperti zip/ekstrak/hapus-drive: analisis REA selalu tanya, juga di akses penuh."""
        from itsme import rea as rea_mod
        ctx = FakeCtx(full_access=True)
        with mock.patch.object(rea_mod, "analyze", return_value=(True, "hasil REA")):
            out = tools.handle_tool("analisis_rea", {"path_atau_url": self.exe}, ctx)
        self.assertTrue(ctx.asked)
        self.assertEqual(out, "hasil REA")

    def test_web_address_is_refused_before_asking_the_owner(self):
        """REA 6.7.0 tak punya perintah untuk URL: jangan minta persetujuan lalu gagal."""
        from itsme import rea as rea_mod
        ctx = FakeCtx()
        with mock.patch.object(rea_mod, "analyze") as analyze:
            out = tools.handle_tool("analisis_rea", {"path_atau_url": "https://example.com/app.exe"}, ctx)
        self.assertIn("alamat web", out)
        self.assertEqual(ctx.asked, [])
        analyze.assert_not_called()

    def test_module_refusal_is_returned_as_text(self):
        from itsme import rea as rea_mod
        ctx = FakeCtx()
        with mock.patch.object(rea_mod, "analyze", side_effect=rea_mod.ReaError("Node.js belum terpasang")):
            out = tools.handle_tool("analisis_rea", {"path_atau_url": self.exe}, ctx)
        self.assertIn("Node.js", out)

    def test_still_running_answer_is_passed_on_with_the_job_id(self):
        from itsme import rea as rea_mod
        ctx = FakeCtx()
        text = "REA masih berjalan (id ab12cd34, perintah analyze-javascript-application)"
        with mock.patch.object(rea_mod, "analyze", return_value=(True, text)):
            out = tools.handle_tool("analisis_rea", {"path_atau_url": self.exe}, ctx)
        self.assertIn("ab12cd34", out)

    def test_hasil_rea_reads_a_job_without_asking(self):
        from itsme import rea as rea_mod
        ctx = FakeCtx()
        with mock.patch.object(rea_mod, "hasil", return_value=(True, "ringkasan job")) as hasil:
            out = tools.handle_tool("hasil_rea", {"id": "ab12cd34"}, ctx)
        self.assertEqual(out, "ringkasan job")
        self.assertEqual(ctx.asked, [])
        hasil.assert_called_once_with("ab12cd34")

    def test_tool_definitions_mention_commands_and_that_rea_has_no_brain(self):
        names = {t["name"]: t for t in tools.CLIENT_TOOLS}
        self.assertIn("hasil_rea", names)
        desc = names["analisis_rea"]["description"]
        self.assertIn("inspect-artifact", desc)
        self.assertIn("TIDAK punya otak", desc)
        self.assertIn("alamat web TIDAK didukung", desc)


class OllamaUninstallToolDispatchTest(unittest.TestCase):
    """copot_ollama TANPA It's Me Node: jalur lama lewat ollama_admin (node ditiru tidak terjangkau)."""

    def setUp(self):
        from itsme import node
        patch = mock.patch.object(node, "ollama_overview", return_value=(None, []))
        patch.start()
        self.addCleanup(patch.stop)

    def test_not_installed_skips_confirmation(self):
        from itsme import ollama_admin
        ctx = FakeCtx()
        with mock.patch.object(ollama_admin, "installed", return_value=False):
            out = tools.handle_tool("copot_ollama", {}, ctx)
        self.assertIn("tidak terpasang", out)
        self.assertEqual(ctx.asked, [])

    def test_confirms_then_uninstalls(self):
        from itsme import ollama_admin
        ctx = FakeCtx()
        with mock.patch.object(ollama_admin, "installed", return_value=True), \
             mock.patch.object(ollama_admin, "uninstall", return_value=(True, "sudah dicopot")) as uninst:
            out = tools.handle_tool("copot_ollama", {}, ctx)
        self.assertTrue(ctx.asked)
        self.assertEqual(out, "sudah dicopot")
        uninst.assert_called_once()

    def test_declined_does_not_uninstall(self):
        from itsme import ollama_admin
        ctx = FakeCtx(answer=False)
        with mock.patch.object(ollama_admin, "installed", return_value=True), \
             mock.patch.object(ollama_admin, "uninstall") as uninst:
            out = tools.handle_tool("copot_ollama", {}, ctx)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        uninst.assert_not_called()

    def test_full_access_still_asks(self):
        from itsme import ollama_admin
        ctx = FakeCtx(full_access=True)
        with mock.patch.object(ollama_admin, "installed", return_value=True), \
             mock.patch.object(ollama_admin, "uninstall", return_value=(True, "ok")):
            tools.handle_tool("copot_ollama", {}, ctx)
        self.assertEqual(len(ctx.asked), 1)

    def test_other_computer_cannot_be_reached_without_the_node(self):
        from itsme import ollama_admin
        ctx = FakeCtx()
        with mock.patch.object(ollama_admin, "installed", return_value=True), \
             mock.patch.object(ollama_admin, "uninstall") as uninst:
            out = tools.handle_tool("copot_ollama", {"komputer": "Laptop-Entah-Mana"}, ctx)
        self.assertIn("tidak bisa dijangkau", out)
        self.assertEqual(ctx.asked, [])
        uninst.assert_not_called()

    def test_install_and_list_without_the_node(self):
        from itsme import ollama_admin
        ctx = FakeCtx()
        with mock.patch.object(ollama_admin, "installed", return_value=False):
            self.assertIn("butuh It's Me Node", tools.handle_tool("pasang_ollama", {}, ctx))
            self.assertIn("hanya komputer ini", tools.handle_tool("daftar_ollama", {}, ctx))
        self.assertEqual(ctx.asked, [])


class OllamaToolRegistryTest(unittest.TestCase):
    NAMES = ("copot_ollama", "pasang_ollama", "daftar_ollama")

    def test_registered_for_every_brain(self):
        client = [t["name"] for t in tools.CLIENT_TOOLS]
        claude = [t["name"] for t in tools.CLAUDE_TOOLS]
        function = [t["function"]["name"] for t in tools.FUNCTION_TOOLS]
        for name in self.NAMES:
            self.assertEqual(client.count(name), 1, name)
            self.assertEqual(claude.count(name), 1, name)
            self.assertEqual(function.count(name), 1, name)
        self.assertEqual(len(client), len(set(client)))

    def test_schemas(self):
        by_name = {t["name"]: t["input_schema"] for t in tools.CLIENT_TOOLS}
        for name in ("copot_ollama", "pasang_ollama"):
            schema = by_name[name]
            self.assertEqual(schema["properties"]["komputer"]["type"], "string")
            self.assertEqual(schema["required"], [])
            self.assertFalse(schema["additionalProperties"])
        self.assertEqual(by_name["daftar_ollama"]["properties"], {})
        self.assertEqual(by_name["daftar_ollama"]["required"], [])

    def test_descriptions_tell_the_model_not_to_guess_the_computer(self):
        desc = {t["name"]: t["description"] for t in tools.CLIENT_TOOLS}
        for name in ("copot_ollama", "pasang_ollama"):
            self.assertIn("jangan menebak", desc[name])
            self.assertIn("semua", desc[name])
            self.assertIn("konfirmasi", desc[name])
        self.assertIn("tidak minta konfirmasi", desc["daftar_ollama"])

    def test_input_validation(self):
        for name in ("copot_ollama", "pasang_ollama"):
            self.assertIsNone(tools.validate_input(name, {}))
            self.assertIsNone(tools.validate_input(name, {"komputer": "Laptop"}))
            self.assertIsNotNone(tools.validate_input(name, {"komputer": 3}))
            self.assertIsNotNone(tools.validate_input(name, {"komputer": None}))
            self.assertIsNotNone(tools.validate_input(name, {"nama": "Laptop"}))
        self.assertIsNone(tools.validate_input("daftar_ollama", {}))
        self.assertIsNotNone(tools.validate_input("daftar_ollama", {"komputer": "Laptop"}))

    def test_guests_get_none_of_them_and_the_node_is_not_even_asked(self):
        from itsme import node
        ctx = FakeCtx(guest=True)
        with mock.patch.object(node, "ollama_overview", side_effect=AssertionError("node tidak boleh dihubungi")):
            for name in self.NAMES:
                self.assertIn("Mode tamu", tools.handle_tool(name, {}, ctx))
        self.assertEqual(ctx.asked, [])


def _comp(name, installed=True, version="0.40.1", online=True, job=None, cid=None):
    """Satu elemen node.ollama_fleet()."""
    return {"id": cid or name.lower().replace(" ", "-"), "name": name, "online": online, "asleep": not online,
            "installed": installed if online else None,
            "version": version if (installed and online) else None, "job": job}


class FakeOllamaApi:
    """Pengganti node.ollama_overview/status/start/wait: yang diuji keputusan alatnya, bukan jaringannya."""

    def __init__(self, *computers, me=None):
        self.fleet = list(computers)
        self.me = me or computers[0]["id"]
        self.started = []       # (id, aksi)
        self.waited = []        # (id, batas waktu)
        self.start_result = {}  # id -> (kode, dict) atau Exception
        self.wait_result = {}   # id -> dict pekerjaan
        self.fresh = {}         # id -> dict status segar atau None (menimpa yang diturunkan dari fleet)
        self.action = {}

    def overview(self, timeout=3):
        return {"me": self.me}, [dict(c) for c in self.fleet]

    def status(self, node_id=None, timeout=30):
        if node_id in self.fresh:
            return self.fresh[node_id]
        c = next((c for c in self.fleet if c["id"] == node_id), None)
        if c is None or not c["online"] or c["installed"] is None:
            return None
        return {"supported": True, "installed": c["installed"], "version": c["version"], "path": None,
                "leftover": False, "job": c["job"]}

    def start(self, node_id, action, timeout=30):
        self.started.append((node_id, action))
        self.action[node_id] = action
        res = self.start_result.get(node_id, (200, {"ok": True, "job": {"action": action, "state": "running"}}))
        if isinstance(res, Exception):
            raise res
        return res

    def wait(self, node_id, timeout=150.0, every=2.0):
        self.waited.append((node_id, timeout))
        done = {"action": self.action.get(node_id), "state": "done",
                "message": "Ollama sudah dicopot. Model yang sudah diunduh tetap tersimpan di ~/.ollama."
                if self.action.get(node_id) == "uninstall" else "Ollama 0.40.1 terpasang lewat winget."}
        return self.wait_result.get(node_id, done)


class OllamaToolRunTest(unittest.TestCase):
    """copot_ollama / pasang_ollama / daftar_ollama dengan It's Me Node ditiru (tanpa jaringan)."""

    def use(self, *computers, me=None):
        from itsme import node
        self.api = FakeOllamaApi(*computers, me=me)
        for name, replacement in (("ollama_overview", self.api.overview), ("ollama_status", self.api.status),
                                  ("ollama_start", self.api.start), ("ollama_wait", self.api.wait)):
            patch = mock.patch.object(node, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        return self.api

    def run_tool(self, name, args=None, **ctx_kwargs):
        ctx = FakeCtx(**ctx_kwargs)
        return tools.handle_tool(name, args or {}, ctx), ctx

    # --- persetujuan: sekali untuk seluruh batch, menyebut semua komputernya -----------------
    def test_one_approval_for_the_batch_names_every_computer(self):
        api = self.use(_comp("PC-1"), _comp("PC-2"), _comp("PC-3"))
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        self.assertEqual(len(ctx.asked), 1)
        self.assertIn("PC-1, PC-2, dan PC-3", ctx.asked[0])
        self.assertIn("Copot Ollama dari", ctx.asked[0])
        self.assertIn("Model yang sudah diunduh tetap tersimpan", ctx.asked[0])
        self.assertEqual(sorted(api.started), [("pc-1", "uninstall"), ("pc-2", "uninstall"), ("pc-3", "uninstall")])

    def test_install_approval_wording(self):
        self.use(_comp("Laptop", installed=False))
        out, ctx = self.run_tool("pasang_ollama")
        self.assertIn("Pasang Ollama di Laptop", ctx.asked[0])
        self.assertIn("1,7 GB", ctx.asked[0])

    def test_declined_starts_nothing(self):
        api = self.use(_comp("PC-1"), _comp("PC-2"))
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"}, answer=False)
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertEqual(api.started, [])

    def test_full_access_never_skips_the_question(self):
        self.use(_comp("PC-1"))
        for name in ("copot_ollama",):
            out, ctx = self.run_tool(name, full_access=True)
            self.assertEqual(len(ctx.asked), 1)
        self.use(_comp("PC-1", installed=False))
        out, ctx = self.run_tool("pasang_ollama", full_access=True)
        self.assertEqual(len(ctx.asked), 1)

    # --- memilih komputer -------------------------------------------------------------------
    def test_blank_name_with_one_online_computer_uses_it_even_if_others_sleep(self):
        api = self.use(_comp("PC-Utama"), _comp("Dapur", online=False, cid="dapur"))
        for blank in (None, "", "   "):
            api.started.clear()
            args = {} if blank is None else {"komputer": blank}
            out, ctx = self.run_tool("copot_ollama", args)
            self.assertEqual(api.started, [("pc-utama", "uninstall")], blank)

    def test_blank_name_with_several_online_computers_lists_them_and_does_not_guess(self):
        api = self.use(_comp("PC-1"), _comp("PC-2", installed=False), _comp("PC-3", installed=None),
                       _comp("Dapur", online=False, cid="dapur"))
        out, ctx = self.run_tool("pasang_ollama")
        for fragment in ("PC-1: Ollama terpasang v0.40.1", "PC-2: Ollama belum terpasang",
                         "PC-3: status Ollama tidak diketahui", "Dapur: sedang tidur/mati", "'semua'"):
            self.assertIn(fragment, out)
        self.assertIn("mau dipasang Ollama-nya", out)
        self.assertEqual(ctx.asked, [])
        self.assertEqual(api.started, [])

    def test_nobody_online(self):
        api = self.use(_comp("Dapur", online=False, cid="dapur"))
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        self.assertIn("Tidak ada komputer yang terhubung", out)
        self.assertEqual(api.started, [])

    def test_ini_is_the_node_of_this_computer(self):
        api = self.use(_comp("PC-1"), _comp("PC-2"), me="pc-2")
        out, ctx = self.run_tool("copot_ollama", {"komputer": "Komputer Ini"})
        self.assertEqual(api.started, [("pc-2", "uninstall")])

    def test_ini_when_this_computer_is_not_in_the_node_list(self):
        api = self.use(_comp("PC-1"), _comp("PC-2"), me="tidak-ada")
        out, ctx = self.run_tool("copot_ollama", {"komputer": "ini"})
        self.assertIn("belum terdaftar", out)
        self.assertEqual(api.started, [])

    def test_name_matching_exact_prefix_ambiguous_missing_asleep(self):
        api = self.use(_comp("Laptop-Budi"), _comp("Laptop-Ani"), _comp("PC-Utama"),
                       _comp("Dapur", online=False, cid="dapur"))
        self.run_tool("copot_ollama", {"komputer": "pc"})
        self.assertEqual(api.started, [("pc-utama", "uninstall")])
        api.started.clear()
        out, ctx = self.run_tool("copot_ollama", {"komputer": "laptop"})
        self.assertIn("cocok dengan beberapa komputer: Laptop-Budi dan Laptop-Ani", out)
        out, ctx = self.run_tool("copot_ollama", {"komputer": "Meja-Makan"})
        self.assertIn("'Meja-Makan' tidak ketemu", out)
        self.assertIn("Laptop-Budi, Laptop-Ani, PC-Utama, Dapur", out)
        out, ctx = self.run_tool("copot_ollama", {"komputer": "dapur"})
        self.assertIn("Dapur sedang tidur/mati", out)
        self.assertIn("/nyalakan Dapur", out)
        self.assertEqual(api.started, [])
        self.assertEqual(ctx.asked, [])

    # --- 'semua' -----------------------------------------------------------------------------
    def test_semua_skips_and_explains(self):
        busy = {"action": "install", "state": "running", "step": "mengunduh"}
        api = self.use(_comp("Ada"), _comp("Belum", installed=False), _comp("Lama", installed=None),
                       _comp("Sibuk", job=busy), _comp("Tidur", online=False, cid="tidur"))
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        self.assertEqual(api.started, [("ada", "uninstall")])
        for fragment in ("Belum: Ollama belum terpasang", "Lama: status Ollama tidak diketahui",
                         "Sibuk: masih ada pekerjaan pasang yang berjalan"):
            self.assertIn(fragment, out)
        self.assertNotIn("Tidur", out)

    def test_semua_install_skips_computers_that_already_have_it(self):
        api = self.use(_comp("Ada"), _comp("Belum", installed=False))
        out, ctx = self.run_tool("pasang_ollama", {"komputer": "semua"})
        self.assertEqual(api.started, [("belum", "install")])
        self.assertIn("Ada: Ollama sudah terpasang v0.40.1", out)

    def test_semua_with_nothing_qualifying(self):
        api = self.use(_comp("Ada"), _comp("Juga"))
        out, ctx = self.run_tool("pasang_ollama", {"komputer": "semua"})
        self.assertIn("Tidak ada komputer online yang perlu dipasangi Ollama", out)
        self.assertIn("Ada: Ollama sudah terpasang", out)
        self.assertEqual(api.started, [])
        self.assertEqual(ctx.asked, [])

    # --- satu komputer: keadaan SEGAR menentukan --------------------------------------------
    def test_fresh_status_beats_stale_gossip(self):
        api = self.use(_comp("PC-1", installed=True))
        api.fresh["pc-1"] = {"supported": True, "installed": False, "version": None, "job": None}
        out, ctx = self.run_tool("copot_ollama")
        self.assertIn("memang tidak terpasang di PC-1", out)
        self.assertEqual((api.started, ctx.asked), ([], []))

    def test_computer_that_cannot_be_controlled(self):
        api = self.use(_comp("PC-1", installed=None))
        out, ctx = self.run_tool("copot_ollama", {"komputer": "pc-1"})
        self.assertIn("belum bisa dikontrol", out)
        self.assertEqual((api.started, ctx.asked), ([], []))

    def test_busy_computer_is_not_started_twice(self):
        api = self.use(_comp("PC-1", job={"action": "uninstall", "state": "running", "step": "menunggu"}))
        out, ctx = self.run_tool("copot_ollama")
        self.assertIn("Masih ada pekerjaan copot yang berjalan di PC-1 (menunggu)", out)
        self.assertEqual((api.started, ctx.asked), ([], []))

    def test_leftover_files_are_explained(self):
        api = self.use(_comp("PC-1", installed=False))
        api.fresh["pc-1"] = {"supported": True, "installed": False, "version": None, "job": None,
                             "leftover": True, "path": r"C:\Ollama\ollama.exe"}
        out, ctx = self.run_tool("copot_ollama")
        self.assertIn(r"C:\Ollama\ollama.exe", out)
        self.assertIn("tanpa uninstaller", out)

    # --- laporan hasil -----------------------------------------------------------------------
    def test_every_started_computer_is_waited_for_with_the_configured_limit(self):
        api = self.use(_comp("PC-1"), _comp("PC-2"))
        self.run_tool("copot_ollama", {"komputer": "semua"})
        self.assertEqual(sorted(api.waited), [("pc-1", tools._OLLAMA_WAIT), ("pc-2", tools._OLLAMA_WAIT)])
        self.assertEqual(tools._OLLAMA_WAIT, 150)

    def test_final_states_are_reported_per_computer(self):
        api = self.use(_comp("A"), _comp("B"), _comp("C"), _comp("D"), _comp("E"))
        api.wait_result["b"] = {"action": "uninstall", "state": "failed", "message": "Uninstall belum tuntas"}
        api.wait_result["c"] = {"action": "uninstall", "state": "running", "step": "menunggu uninstall selesai"}
        api.wait_result["d"] = {"state": "unknown", "message": "komputer itu tidak melaporkan pekerjaan apa pun"}
        api.wait_result["e"] = {"action": "uninstall", "state": "done", "message": ""}
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        lines = out.splitlines()
        self.assertEqual(lines[0], "Hasil copot Ollama:")
        self.assertIn("- A: Ollama sudah dicopot. Model yang sudah diunduh tetap tersimpan di ~/.ollama.", lines)
        self.assertIn("- B: gagal - Uninstall belum tuntas", lines)
        self.assertTrue(any(line.startswith("- C: masih berjalan (menunggu uninstall selesai)") and
                            line.endswith("cek lagi dengan daftar_ollama.") for line in lines), out)
        self.assertTrue(any(line.startswith("- D: hasilnya belum bisa dipastikan - komputer itu tidak melaporkan")
                            and "daftar_ollama" in line for line in lines), out)
        self.assertIn("- E: sudah dicopot, model tetap tersimpan", lines)    # node tak memberi pesan: kalimat bawaan

    def test_install_without_a_message_still_says_installed(self):
        api = self.use(_comp("A", installed=False))
        api.wait_result["a"] = {"action": "install", "state": "done", "message": ""}
        out, ctx = self.run_tool("pasang_ollama")
        self.assertIn("- A: terpasang", out)
        self.assertTrue(out.startswith("Hasil pasang Ollama:"))

    def test_refused_start_is_reported_and_the_others_continue(self):
        api = self.use(_comp("A"), _comp("B"), _comp("C"))
        api.start_result["b"] = (502, {"ok": False, "error": "masih ada pekerjaan 'install' yang berjalan di komputer itu"})
        api.start_result["c"] = (0, {"ok": False})
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        self.assertIn("- A: Ollama sudah dicopot", out)
        self.assertIn("- B: gagal dimulai - masih ada pekerjaan 'install' yang berjalan di komputer itu", out)
        self.assertIn("- C: gagal dimulai - HTTP 0", out)
        self.assertEqual([w[0] for w in api.waited], ["a"])        # yang gagal dimulai tidak ditunggu

    def test_an_error_on_one_computer_does_not_lose_the_other_reports(self):
        api = self.use(_comp("A"), _comp("B"))
        api.start_result["a"] = RuntimeError("meledak")
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        self.assertIn("- A: gagal - RuntimeError: meledak", out)
        self.assertIn("- B: Ollama sudah dicopot", out)

    def test_skipped_computers_are_listed_after_the_results(self):
        self.use(_comp("A"), _comp("B", installed=False))
        out, ctx = self.run_tool("copot_ollama", {"komputer": "semua"})
        head, _, tail = out.partition("Dilewati:")
        self.assertIn("A:", head)
        self.assertIn("- B: Ollama belum terpasang", tail)

    # --- daftar_ollama -----------------------------------------------------------------------
    def test_list_shows_state_version_jobs_and_marks_this_computer(self):
        busy = {"action": "install", "state": "running", "step": "mengunduh"}
        failed = {"action": "uninstall", "state": "failed", "step": "selesai"}
        done = {"action": "install", "state": "done", "step": "selesai"}
        api = self.use(_comp("PC-1", job=failed), _comp("PC-2", installed=False, job=busy),
                       _comp("PC-3", job=done), _comp("PC-4", installed=None),
                       _comp("Dapur", online=False, cid="dapur"), me="pc-1")
        out, ctx = self.run_tool("daftar_ollama")
        self.assertIn("(4 online, 1 tidur/mati)", out)
        self.assertIn("- PC-1 (komputer ini): Ollama terpasang v0.40.1; percobaan copot terakhir gagal", out)
        self.assertIn("- PC-2: Ollama belum terpasang; sedang pasang (mengunduh)", out)
        self.assertIn("- PC-3: Ollama terpasang v0.40.1\n", out + "\n")     # pekerjaan yang sudah selesai tidak disebut
        self.assertNotIn("PC-3: Ollama terpasang v0.40.1;", out)
        self.assertIn("- PC-4: status Ollama tidak diketahui", out)
        self.assertIn("- Dapur: sedang tidur/mati", out)
        self.assertIn("versi lama", out)
        self.assertEqual((ctx.asked, api.started), ([], []))

    def test_list_without_unknown_computers_has_no_update_hint(self):
        self.use(_comp("PC-1"), _comp("PC-2", installed=False))
        out, ctx = self.run_tool("daftar_ollama")
        self.assertNotIn("versi lama", out)
        self.assertNotIn("Dilewati", out)

    def test_list_is_read_only_even_when_full_access_is_off(self):
        api = self.use(_comp("PC-1"))
        out, ctx = self.run_tool("daftar_ollama", {})
        self.assertEqual((ctx.asked, api.started, api.waited), ([], [], []))


if __name__ == "__main__":
    unittest.main()
