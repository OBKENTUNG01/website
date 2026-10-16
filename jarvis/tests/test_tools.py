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
