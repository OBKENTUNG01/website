"""Uji pembaruan otomatis dari GitHub (tanpa jaringan)."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

from itsme import config, updater

SHA_OLD = "1" * 40
SHA_NEW = "2" * 40


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _archive(files):
    """Arsip zip seperti buatan GitHub: semua isi di bawah website-<sha>/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"website-{SHA_NEW}/{name}", text)
    return buf.getvalue()


class ArchiveUpdateTest(unittest.TestCase):
    """Pemasangan hasil skrip setup: bukan clone Git."""

    def setUp(self):
        self.app = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.app, ignore_errors=True)
        self.data = os.path.join(self.app, "data")
        _write(os.path.join(self.app, "itsme", "main.py"), "lama")
        _write(os.path.join(self.app, "requirements.txt"), "httpx\n")
        _write(os.path.join(self.app, ".env"), "TELEGRAM_BOT_TOKEN=rahasia\n")
        _write(os.path.join(self.data, "memory.md"), "ingatan\n")
        self.zip = _archive({
            "README.md": "repo",
            "jarvis/itsme/main.py": "baru",
            "jarvis/requirements.txt": "httpx\n",
            "jarvis/.env": "TELEGRAM_BOT_TOKEN=dari-github\n",
            "jarvis/data/memory.md": "kosong\n",
        })
        for patch in (
            mock.patch.object(updater, "APP_DIR", self.app),
            mock.patch.object(updater, "_git_root", return_value=None),
            mock.patch.object(updater, "_latest_commit", return_value=(SHA_NEW, "perbaikan")),
            mock.patch.object(updater.httpx, "get", side_effect=self._github),
            mock.patch.object(config, "DATA_DIR", self.data),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _github(self, url, **kwargs):
        self.assertIn(SHA_NEW, url)
        return mock.Mock(status_code=200, content=self.zip)

    def test_first_run_only_records_version(self):
        self.assertIsNone(updater.check())
        self.assertEqual(updater.installed_version(), SHA_NEW)
        self.assertEqual(_read(os.path.join(self.app, "itsme", "main.py")), "lama")

    def test_new_commit_installed_without_touching_env_and_memory(self):
        updater.remember(SHA_OLD)
        update = updater.check()
        self.assertEqual(update.sha, SHA_NEW)
        self.assertEqual(update.describe(), "2222222 — perbaikan")
        self.assertIsNone(update.requirements)  # tidak berubah, pip tidak perlu
        updater.apply(update)
        self.assertEqual(_read(os.path.join(self.app, "itsme", "main.py")), "baru")
        self.assertEqual(_read(os.path.join(self.app, ".env")), "TELEGRAM_BOT_TOKEN=rahasia\n")
        self.assertEqual(_read(os.path.join(self.data, "memory.md")), "ingatan\n")
        self.assertEqual(updater.installed_version(), SHA_NEW)
        self.assertIsNone(updater.check())  # sudah terbaru

    def test_failed_commit_skipped(self):
        updater.remember(SHA_OLD)
        self.assertIsNone(updater.check(skip={SHA_NEW}))

    def test_new_requirements_installed_first(self):
        updater.remember(SHA_OLD)
        self.zip = _archive({"jarvis/itsme/main.py": "baru", "jarvis/requirements.txt": "httpx\nbaru\n"})
        update = updater.check()
        self.assertEqual(update.requirements, "httpx\nbaru\n")
        with mock.patch.object(updater.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            updater.apply(update)
        self.assertIn("pip", run.call_args[0][0])
        self.assertEqual(_read(os.path.join(self.app, "itsme", "main.py")), "baru")

    def test_package_failure_leaves_old_code(self):
        updater.remember(SHA_OLD)
        self.zip = _archive({"jarvis/itsme/main.py": "baru", "jarvis/requirements.txt": "paket-rusak\n"})
        update = updater.check()
        failed = mock.Mock(returncode=1, stderr="ERROR: No matching distribution", stdout="")
        with mock.patch.object(updater.subprocess, "run", return_value=failed):
            with self.assertRaises(updater.UpdateError):
                updater.apply(update)
        self.assertEqual(_read(os.path.join(self.app, "itsme", "main.py")), "lama")
        self.assertEqual(updater.installed_version(), SHA_OLD)

    def test_archive_without_bot_rejected(self):
        updater.remember(SHA_OLD)
        self.zip = _archive({"README.md": "bukan It's Me"})
        with self.assertRaises(updater.UpdateError):
            updater.check()

    def test_notice_survives_restart_once(self):
        updater.save_notice("sudah diperbarui")
        self.assertEqual(updater.pop_notice(), "sudah diperbarui")
        self.assertIsNone(updater.pop_notice())


@unittest.skipUnless(shutil.which("git"), "git tidak terpasang")
class GitUpdateTest(unittest.TestCase):
    """It's Me yang jalan dari clone Git repo ini."""

    def setUp(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.origin = os.path.join(base, "obkentung01-website")
        self.work = os.path.join(base, "kerja")  # tempat commit baru dibuat
        self.clone = os.path.join(base, "clone")  # tempat It's Me jalan
        self._git(base, "init", "--quiet", "--bare", "--initial-branch=main", self.origin)
        self._git(base, "clone", "--quiet", self.origin, self.work)
        self._commit("jarvis/itsme/main.py", "lama", "awal")
        self._git(base, "clone", "--quiet", self.origin, self.clone)
        self.app = os.path.join(self.clone, "jarvis")
        for patch in (
            mock.patch.object(updater, "APP_DIR", self.app),
            # Alamat origin tiruan (nama foldernya) cocok dengan repo yang diikuti.
            mock.patch.object(config, "UPDATE_REPO", "obkentung01-website"),
            mock.patch.object(config, "UPDATE_BRANCH", "main"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", "-c", "user.name=uji", "-c", "user.email=uji@contoh.id", *args],
            cwd=cwd, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _commit(self, name, text, message):
        _write(os.path.join(self.work, name), text)
        self._git(self.work, "add", "-A")
        self._git(self.work, "commit", "--quiet", "-m", message)
        self._git(self.work, "push", "--quiet", "origin", "HEAD:main")
        return self._git(self.work, "rev-parse", "HEAD")

    def test_up_to_date(self):
        self.assertIsNone(updater.check())

    def test_new_commit_merged(self):
        sha = self._commit("jarvis/itsme/main.py", "baru", "perbaikan bot")
        update = updater.check()
        self.assertEqual(update.sha, sha)
        self.assertEqual(update.message, "perbaikan bot")
        updater.apply(update)
        self.assertEqual(self._git(self.clone, "rev-parse", "HEAD"), sha)
        self.assertEqual(_read(os.path.join(self.app, "itsme", "main.py")), "baru")

    def test_uncommitted_changes_block_update(self):
        self._commit("jarvis/itsme/main.py", "baru", "perbaikan bot")
        _write(os.path.join(self.app, "itsme", "main.py"), "eksperimen lokal")
        with self.assertRaises(updater.Blocked):
            updater.check()
        self.assertEqual(_read(os.path.join(self.app, "itsme", "main.py")), "eksperimen lokal")

    def test_local_commit_not_pushed_yet_is_left_alone(self):
        _write(os.path.join(self.app, "itsme", "main.py"), "lokal")
        self._git(self.clone, "commit", "--quiet", "-am", "belum di-push")
        self.assertIsNone(updater.check())

    def test_other_branch_left_alone(self):
        self._commit("jarvis/itsme/main.py", "baru", "perbaikan bot")
        self._git(self.clone, "checkout", "--quiet", "-b", "coba-coba")
        self.assertIsNone(updater.check())

    def test_unrelated_repo_uses_archive(self):
        self.assertIsNotNone(updater._git_root())
        with mock.patch.object(config, "UPDATE_REPO", "orang-lain/repo"):
            self.assertIsNone(updater._git_root())


if __name__ == "__main__":
    unittest.main()
