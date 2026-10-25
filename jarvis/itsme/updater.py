"""Pembaruan otomatis It's Me dari GitHub.

Selama It's Me jalan, repo GitHub-nya diperiksa berkala (lihat
config.UPDATE_*). Kalau ada commit baru di cabang yang diikuti, kodenya
dipasang lalu It's Me menyala ulang sendiri (lihat main.py).

Cara memasangnya tergantung folder It's Me:
- hasil skrip setup (bukan clone Git): arsip commit terbaru diunduh dari
  GitHub lalu disalin ke folder It's Me. .env, data/ (memori) dan .venv tidak
  disentuh — sama seperti saat skrip setup dijalankan ulang.
- di dalam clone Git repo ini: git merge --ff-only. Ditolak kalau ada
  perubahan yang belum di-commit atau cabang lokal sudah berbeda dari GitHub,
  supaya pekerjaanmu tidak tertimpa.

Kalau requirements.txt berubah, paket Python baru dipasang lebih dulu; kalau
itu gagal, kode lama dibiarkan apa adanya.
"""

import glob
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

import httpx

from . import config
from .tools import _child_env

log = logging.getLogger("itsme")

# Folder jarvis/ tempat It's Me terpasang.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Milik pemasangan ini, tidak pernah ditimpa (sama dengan skrip setup).
_KEEP = (".env", "data", ".venv", "__pycache__")
_GITHUB_API = "https://api.github.com"
_CODELOAD = "https://codeload.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "itsme-updater"}


class UpdateError(Exception):
    """Gagal sementara (internet, GitHub, git, pip); dicoba lagi nanti."""


class Blocked(Exception):
    """Ada pembaruan, tapi sengaja tidak dipasang; pemilik perlu bertindak."""


class Update:
    """Commit baru yang siap dipasang."""

    def __init__(self, sha, message, *, git_root=None, src=None, tmp=None, requirements=None):
        self.sha = sha
        self.message = message
        self.git_root = git_root  # clone Git: dipasang dengan git merge
        self.src = src  # bukan clone: folder jarvis/ hasil ekstrak arsip
        self._tmp = tmp
        self.requirements = requirements  # isi requirements.txt baru; None = tidak berubah

    @property
    def short(self):
        return self.sha[:7]

    def describe(self):
        return f"{self.short} — {self.message}" if self.message else self.short

    def cleanup(self):
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None


# --- memeriksa ------------------------------------------------------------------
def check(skip=()):
    """Kembalikan Update kalau GitHub punya commit baru, None kalau tidak.

    Commit di `skip` (mis. yang tadi gagal dipasang) dianggap tidak ada.
    Melempar Blocked kalau pemilik perlu bertindak; error lain berarti gagal
    sementara.
    """
    root = _git_root()
    if root:
        return _check_git(root, skip)
    return _check_archive(skip)


def _check_archive(skip):
    sha, message = _latest_commit()
    if not sha or sha in skip:
        return None  # 304 Not Modified: tidak ada commit baru (dan tidak dihitung kuota)
    installed = installed_version()
    if installed is None:
        # Baru dipasang skrip setup, yang mengunduh versi terbaru: cukup dicatat.
        remember(sha)
        log.info("Versi terpasang dicatat: %s", sha[:7])
        return None
    if installed == sha:
        return None
    tmp = tempfile.mkdtemp(prefix="itsme-update-")
    try:
        src = _download(sha, tmp)
        new_reqs = _read(os.path.join(src, "requirements.txt"))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return Update(sha, message, src=src, tmp=tmp, requirements=_changed_requirements(new_reqs))


def _check_git(root, skip):
    branch = config.UPDATE_BRANCH
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        return None  # sedang memakai cabang lain: itu pilihanmu, jangan diganggu
    _git(root, "fetch", "--quiet", "origin", branch)
    sha = _git(root, "rev-parse", "FETCH_HEAD")
    if sha in skip or sha == _git(root, "rev-parse", "HEAD"):
        return None
    if not _is_ancestor(root, "HEAD", sha):
        if _is_ancestor(root, sha, "HEAD"):
            return None  # ada commit lokal yang belum di-push; GitHub tidak lebih baru
        raise Blocked(
            f"cabang {branch} di {root} sudah berbeda dari GitHub. "
            "Samakan dulu (git pull), lalu It's Me memperbarui dirinya lagi."
        )
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise Blocked(
            f"ada perubahan yang belum di-commit di {root}. Commit atau batalkan "
            "dulu supaya tidak tertimpa pembaruan."
        )
    rel = os.path.relpath(os.path.join(APP_DIR, "requirements.txt"), root).replace(os.sep, "/")
    try:
        new_reqs = _git(root, "show", f"{sha}:{rel}")
    except UpdateError:
        new_reqs = None
    message = _git(root, "log", "-1", "--format=%s", sha)
    return Update(sha, message, git_root=root, requirements=_changed_requirements(new_reqs))


def _latest_commit():
    """(sha, baris pertama pesan commit) terbaru di cabang yang diikuti."""
    url = f"{_GITHUB_API}/repos/{config.UPDATE_REPO}/commits/{config.UPDATE_BRANCH}"
    headers = dict(_HEADERS)
    etag = _etag_read()
    if etag:
        # Permintaan bersyarat: jawaban 304 TIDAK dihitung ke batas 60/jam GitHub.
        # Tanpa ini, pemeriksaan tiap 2 menit menghabiskan kuota < 1 jam lalu
        # pembaruan berhenti sampai berjam-jam (gejala: "GitHub menjawab 403").
        headers["If-None-Match"] = etag
    r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
    if r.status_code == 304:
        return None, ""
    if r.status_code != 200:
        # 403/429 = batas pemeriksaan GitHub per jam habis; 404 = repo/cabang salah.
        raise UpdateError(f"GitHub menjawab {r.status_code} untuk {url}")
    _etag_write(r.headers.get("ETag"))
    data = r.json()
    message = ((data.get("commit") or {}).get("message") or "").strip()
    return data["sha"], (message.splitlines() or [""])[0]


def _etag_file():
    return os.path.join(config.DATA_DIR, "update_etag.txt")


def _etag_read():
    try:
        with open(_etag_file(), encoding="utf-8") as f:
            return f.read().strip() or ""
    except OSError:
        return ""


def _etag_write(etag):
    if not etag:
        return
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_etag_file(), "w", encoding="utf-8") as f:
            f.write(etag)
    except OSError:
        pass  # hanya penghematan kuota; gagal menulis bukan alasan menghentikan bot


def _download(sha, tmp):
    """Unduh & ekstrak arsip commit; kembalikan folder jarvis/ di dalamnya."""
    url = f"{_CODELOAD}/{config.UPDATE_REPO}/zip/{sha}"
    r = httpx.get(url, headers=_HEADERS, timeout=120, follow_redirects=True)
    if r.status_code != 200:
        raise UpdateError(f"GitHub menjawab {r.status_code} untuk {url}")
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            archive.extractall(tmp)
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"arsip dari GitHub rusak: {exc}") from exc
    found = glob.glob(os.path.join(tmp, "*", "jarvis", "itsme", "main.py"))
    if not found:
        raise UpdateError("arsip dari GitHub tidak berisi jarvis/itsme/main.py")
    return os.path.dirname(os.path.dirname(found[0]))


def _changed_requirements(new):
    """Isi requirements.txt baru kalau berbeda dari yang terpasang, selain itu None."""
    if new is None:
        return None
    old = _read(os.path.join(APP_DIR, "requirements.txt")) or ""
    return new if new.strip() != old.strip() else None


# --- memasang -------------------------------------------------------------------
def apply(update):
    """Pasang pembaruan. Kalau paket Python baru gagal dipasang, kode tidak diubah."""
    try:
        if update.requirements is not None:
            _install_requirements(update.requirements)
        if update.git_root:
            _git(update.git_root, "merge", "--ff-only", "--quiet", update.sha)
        else:
            _copy_code(update.src, APP_DIR)
            remember(update.sha)
    finally:
        update.cleanup()


def _install_requirements(text):
    fd, path = tempfile.mkstemp(prefix="itsme-req-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900, env=_child_env(), stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"paket Python baru gagal dipasang: {exc}") from exc
    finally:
        os.remove(path)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise UpdateError("paket Python baru gagal dipasang: " + " | ".join(tail))


def _copy_code(src, dst):
    for name in os.listdir(src):
        if name in _KEEP:
            continue
        source, target = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(source):
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)


# --- git --------------------------------------------------------------------------
def _git_root():
    """Akar clone Git repo It's Me yang berisi APP_DIR, atau None."""
    if not shutil.which("git"):
        return None
    try:
        root = _git(APP_DIR, "rev-parse", "--show-toplevel")
        url = _git(root, "remote", "get-url", "origin")
    except UpdateError:
        return None
    # Folder di dalam repo Git lain (mis. seluruh folder home di-Git-kan)
    # bukan clone ini: perlakukan seperti hasil skrip setup.
    return root if config.UPDATE_REPO.lower() in url.lower() else None


def _git(cwd, *args):
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=_child_env(), stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"git {args[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise UpdateError(f"git {args[0]}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def _is_ancestor(root, older, newer):
    try:
        _git(root, "merge-base", "--is-ancestor", older, newer)
        return True
    except UpdateError:
        return False


# --- catatan di data/ ---------------------------------------------------------------
def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _version_file():
    return os.path.join(config.DATA_DIR, "version.json")


def _notice_file():
    return os.path.join(config.DATA_DIR, "update_notice.txt")


def installed_version():
    """Commit yang terpasang menurut catatan (hanya untuk pemasangan tanpa Git)."""
    try:
        return json.loads(_read(_version_file()) or "{}").get("sha") or None
    except (ValueError, AttributeError):
        return None


def remember(sha):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_version_file(), "w", encoding="utf-8") as f:
        json.dump({"sha": sha}, f)


def save_notice(text):
    """Pesan untuk pemilik yang dikirim setelah It's Me menyala lagi."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_notice_file(), "w", encoding="utf-8") as f:
        f.write(text)


def pop_notice():
    text = _read(_notice_file())
    if text is not None:
        try:
            os.remove(_notice_file())
        except OSError:
            pass
    return text
