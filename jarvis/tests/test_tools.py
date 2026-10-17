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

    def test_command_naming_a_secret(self):
        self.assertTrue(tools.mentions_secret(r"Get-Content C:\Users\x\ItsMe\.env"))
        self.assertTrue(tools.mentions_secret(f'Get-ChildItem "{self.APP}"'))
        self.assertFalse(tools.mentions_secret(r"Get-ChildItem C:\Users\x\Downloads -Recurse"))

    def test_searching_everything_at_once_is_too_broad(self):
        home = os.path.expanduser("~")
        for path in (home, "C:\\", os.path.join(home, "AppData", "Local")):
            self.assertTrue(tools.too_broad_to_search(path), path)
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


if __name__ == "__main__":
    unittest.main()
