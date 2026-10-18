"""Alat (tools) yang bisa dipakai It's Me.

Tiga jenis:
  1. Alat server Anthropic (web_search, web_fetch) — hanya untuk otak Claude,
     jalan di sisi Anthropic.
  2. Alat web lokal (cari_web, baca_halaman) — pengganti nomor 1 untuk otak
     lokal (Ollama), jalan di komputermu (lihat web.py).
  3. Alat klien (di bawah) — jalan di komputer/HP tempat bot ini berjalan:
     baca/tulis berkas, jalankan perintah, deploy website lewat Git, memori,
     dan tugas latar (multitasking).

Aksi yang mengubah sesuatu (menulis berkas, perintah selain yang hanya
melihat keadaan, push Git, menyimpan memori) minta konfirmasi dulu lewat
tombol di Telegram, dan tombolnya menampilkan apa persisnya yang akan terjadi.
Membaca berkas di luar folder website dan membuka alamat web yang tidak berasal
dari hasil pencarian atau pesanmu sendiri juga minta konfirmasi, supaya isi
halaman web yang aneh tidak bisa menyuruh bot mengirim datamu ke mana-mana.
"""

import os
import re
import shlex
import subprocess
import sys
import tarfile
import zipfile

from . import config, web

IS_WINDOWS = os.name == "nt"

# --- Alat server (internet) -------------------------------------------------
SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]

_SHELL_NAME = "PowerShell (Windows)" if IS_WINDOWS else "shell"

# --- Definisi alat klien ----------------------------------------------------
CLIENT_TOOLS = [
    {
        "name": "jalankan_perintah",
        "description": (
            f"Jalankan satu perintah {_SHELL_NAME} di komputer tempat It's Me "
            "berjalan. Perintah yang hanya melihat keadaan (git status, git diff, "
            "git log, git show, ls, dir, pwd, whoami, tree) langsung jalan; "
            "selain itu minta konfirmasi pemilik dulu. Untuk membaca isi berkas "
            "pakai baca_berkas, bukan cat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "perintah": {"type": "string", "description": "Perintah yang dijalankan."},
                "alasan": {
                    "type": "string",
                    "description": "Penjelasan singkat untuk apa perintah ini.",
                },
            },
            "required": ["perintah"],
            "additionalProperties": False,
        },
    },
    {
        "name": "baca_berkas",
        "description": "Baca isi sebuah berkas teks. Path relatif ke folder "
        "website kalau sudah diatur, atau path absolut. Berkas di luar folder "
        "website minta konfirmasi; berkas rahasia (.env, kunci SSH) ditolak.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tulis_berkas",
        "description": "Tulis/timpa isi sebuah berkas. Minta konfirmasi dulu.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "isi": {"type": "string"},
            },
            "required": ["path", "isi"],
            "additionalProperties": False,
        },
    },
    {
        "name": "deploy_website",
        "description": (
            "Simpan semua perubahan di folder website ke Git lalu push "
            "(git add -A, commit, push). Hostinger yang diatur auto-deploy "
            "akan menarik perubahan ini. Minta konfirmasi dulu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pesan_commit": {"type": "string"},
            },
            "required": ["pesan_commit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simpan_memori",
        "description": "Simpan sebuah catatan ke memori jangka panjang supaya "
        "It's Me mengingatnya di percakapan berikutnya. Minta konfirmasi dulu.",
        "input_schema": {
            "type": "object",
            "properties": {"catatan": {"type": "string"}},
            "required": ["catatan"],
            "additionalProperties": False,
        },
    },
    {
        "name": "buat_zip",
        "description": (
            "Kompres satu atau beberapa berkas/folder menjadi satu berkas .zip. "
            "Selalu minta konfirmasi pemilik dulu (juga saat akses penuh). "
            "Berkas rahasia (.env, kunci, login) otomatis dilewati dan dilaporkan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sumber": {
                    "type": "string",
                    "description": "Path berkas/folder yang dikompres. Beberapa path dipisah baris baru.",
                },
                "tujuan": {
                    "type": "string",
                    "description": "Path berkas .zip hasil (opsional; bawaan di sebelah sumber).",
                },
            },
            "required": ["sumber"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ekstrak",
        "description": (
            "Ekstrak isi arsip (.zip, .tar, .tar.gz, .tgz) ke sebuah folder. "
            "Selalu minta konfirmasi pemilik dulu (juga saat akses penuh)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arsip": {"type": "string", "description": "Path berkas arsip yang diekstrak."},
                "tujuan": {
                    "type": "string",
                    "description": "Folder tujuan (opsional; bawaan folder baru di sebelah arsip).",
                },
            },
            "required": ["arsip"],
            "additionalProperties": False,
        },
    },
    {
        "name": "drive_cari",
        "description": (
            "Cari berkas/folder di Google Drive pemilik berdasarkan nama. "
            "Mengembalikan daftar berisi nama, jenis, ukuran, dan id. Pakai id-nya "
            "untuk mengunduh atau mengelola."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"kata_kunci": {"type": "string"}},
            "required": ["kata_kunci"],
            "additionalProperties": False,
        },
    },
    {
        "name": "drive_isi_folder",
        "description": "Lihat isi sebuah folder Google Drive. folder_id kosong = folder utama (My Drive).",
        "input_schema": {
            "type": "object",
            "properties": {"folder_id": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "drive_unggah",
        "description": (
            "Unggah sebuah berkas dari komputer ke Google Drive. folder_id opsional "
            "(tujuan), nama opsional. Minta konfirmasi pemilik dulu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path_lokal": {"type": "string"}, "folder_id": {"type": "string"}, "nama": {"type": "string"}},
            "required": ["path_lokal"],
            "additionalProperties": False,
        },
    },
    {
        "name": "drive_unduh",
        "description": (
            "Unduh sebuah berkas Google Drive (pakai file_id dari drive_cari) ke "
            "komputer. path_lokal opsional (bawaan folder Downloads). Minta konfirmasi dulu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}, "path_lokal": {"type": "string"}},
            "required": ["file_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "drive_kelola",
        "description": (
            "Kelola berkas/folder Google Drive. aksi: 'buat_folder' (nama; folder_id "
            "opsional sebagai induk), 'ganti_nama' (file_id, nama), 'pindah' (file_id, "
            "folder_id tujuan), 'hapus' (file_id, dibuang ke sampah). Minta konfirmasi dulu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "aksi": {"type": "string"},
                "file_id": {"type": "string"},
                "folder_id": {"type": "string"},
                "nama": {"type": "string"},
            },
            "required": ["aksi"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tugas_latar",
        "description": (
            "Kerjakan sebuah tugas di latar belakang (paralel) sambil kamu "
            "tetap bisa mengobrol. Hasilnya dikirim terpisah begitu selesai. "
            "Pakai ini kalau ada beberapa tugas yang ingin jalan bersamaan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"instruksi": {"type": "string"}},
            "required": ["instruksi"],
            "additionalProperties": False,
        },
    },
]

# --- Alat web lokal (pengganti web_search/web_fetch untuk otak Ollama) -------
LOCAL_WEB_TOOLS = [
    {
        "name": "cari_web",
        "description": (
            "Cari informasi terbaru di internet (DuckDuckGo). Mengembalikan "
            "judul, alamat, dan ringkasan beberapa hasil teratas. Lanjutkan "
            "dengan baca_halaman untuk membaca isi lengkapnya."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"kata_kunci": {"type": "string"}},
            "required": ["kata_kunci"],
            "additionalProperties": False,
        },
    },
    {
        "name": "baca_halaman",
        "description": "Buka sebuah alamat web (http/https) dan ambil isinya "
        "sebagai teks biasa. Alamat dari hasil cari_web atau dari pesan pemilik "
        "langsung dibuka; alamat lain minta konfirmasi.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]

_SCHEMAS = {t["name"]: t["input_schema"] for t in CLIENT_TOOLS + LOCAL_WEB_TOOLS}

# Untuk otak Claude: alat server + alat klien. Input alat klien di-stream
# sedikit demi sedikit (berguna untuk isi berkas yang panjang); konsekuensinya
# input bisa saja tidak utuh, jadi selalu dicek dengan validate_input().
CLAUDE_TOOLS = SERVER_TOOLS + [
    dict(tool, eager_input_streaming=True) for tool in CLIENT_TOOLS
]

# Untuk otak lokal (Ollama) dan cloud (OpenRouter): format "function" yang
# sama-sama dipakai keduanya.
FUNCTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
    for tool in LOCAL_WEB_TOOLS + CLIENT_TOOLS
]

# --- Perintah yang boleh langsung jalan ---------------------------------------
# Sengaja daftar yang diizinkan, dan hanya perintah yang melihat keadaan.
# Membaca isi berkas lewat baca_berkas (yang punya batas folder dan menolak
# berkas rahasia), bukan lewat cat/type. Karakter yang punya arti khusus di
# shell (termasuk % dan ^ milik cmd) selalu wajib konfirmasi.
_SAFE_GIT = {"status", "diff", "log", "show"}
_SAFE_PLAIN = {"ls", "dir", "pwd", "whoami", "tree"}
# Opsi git yang menulis berkas, menjalankan program lain, atau membaca berkas
# di luar repo.
_GIT_BLOCKED_OPTIONS = ("--output", "--ext-diff", "--textconv", "--no-index", "-o")
_SHELL_CHARS = set(";&|<>`$%^(){}[]!\n\r")


def is_read_only(command):
    """True kalau perintah ini aman dijalankan tanpa konfirmasi."""
    if not command.strip() or any(ch in _SHELL_CHARS for ch in command):
        return False
    try:
        parts = shlex.split(command, posix=not IS_WINDOWS)
    except ValueError:
        return False
    if not parts:
        return False
    head = parts[0].lower()
    if head == "git":
        if len(parts) < 2 or parts[1] not in _SAFE_GIT:
            return False
        return not any(arg.lower().startswith(_GIT_BLOCKED_OPTIONS) for arg in parts[2:])
    return head in _SAFE_PLAIN


def validate_input(name, tool_input):
    """Cek input alat klien terhadap skemanya. Kembalikan pesan error atau None."""
    schema = _SCHEMAS.get(name)
    if schema is None:
        return None  # alat tak dikenal dilaporkan oleh handle_tool
    if not isinstance(tool_input, dict):
        return "Input alat tidak valid."
    for key in schema.get("required", []):
        if not isinstance(tool_input.get(key), str):
            return f"Input alat tidak lengkap: '{key}' wajib berupa teks."
    for key, value in tool_input.items():
        if key not in schema["properties"] or not isinstance(value, str):
            return f"Input alat tidak valid: '{key}'."
    return None


# --- berkas ----------------------------------------------------------------------
_SECRET_NAMES = {
    ".env", ".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", ".credentials.json",
    # Login & cookie browser (Chrome/Edge/aplikasi Electron, Firefox).
    "cookies", "cookies-journal", "login data", "login data-journal", "web data",
    "web data-journal", "local state", "logins.json", "key3.db", "key4.db", "cookies.sqlite",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".ppk")
_SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", "local storage", "session storage", "indexeddb"}
# Berkas data yang namanya jelas menyebut rahasia (client_secret.json,
# buddy-tokens.json, passwords.txt, ...). Kode seperti token.php tetap boleh.
_SECRET_WORDS = ("secret", "token", "password", "credential")
_SECRET_WORD_EXTS = ("", ".json", ".txt", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".xml", ".db")
# Di folder data aplikasi Claude (...\Roaming\Claude) hanya riwayat sesi dan
# folder kerja yang boleh dibaca; sisanya login, token, dan cookie aplikasi.
_CLAUDE_APP_READABLE = {"claude-code-sessions", "local-agent-mode-sessions", "scratch-workspaces"}


def is_secret_path(path):
    """True untuk berkas yang biasanya berisi token, password, atau kunci."""
    parts = [p.lower() for p in os.path.normpath(path).replace("\\", "/").split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    if name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES):
        return True
    if (name.startswith(".env") or name.endswith(".env")) and name != ".env.example":
        return True
    if any(w in name for w in _SECRET_WORDS) and os.path.splitext(name)[1] in _SECRET_WORD_EXTS:
        return True
    if any(p in _SECRET_DIRS for p in parts[:-1]):
        return True
    # Folder data Claude Code / aplikasi Claude itu sendiri (dibaca utuh = ikut rahasianya).
    if name == ".claude" or (name == "claude" and len(parts) > 1 and parts[-2] == "roaming"):
        return True
    for i, part in enumerate(parts[:-1]):
        # ~/.claude milik Claude Code: hanya riwayat proyek (projects/) yang boleh.
        if part == ".claude" and parts[i + 1] != "projects":
            return True
        if part == "claude" and i > 0 and parts[i - 1] == "roaming" and parts[i + 1] not in _CLAUDE_APP_READABLE:
            return True
    return False


def too_broad_to_search(path):
    """True kalau mencari isi berkas di folder ini bisa ikut menyedot data
    login aplikasi & browser sekaligus: seluruh folder home, satu drive, atau
    folder data aplikasi (AppData; Library di macOS) — kecuali riwayat sesi Claude."""
    full = os.path.normpath(os.path.abspath(path))
    parts = [p.lower() for p in full.replace("\\", "/").split("/") if p]
    if len(parts) <= 1 or full == os.path.normpath(os.path.expanduser("~")):
        return True
    app_data = "appdata" in parts or (sys.platform == "darwin" and "library" in parts)
    return app_data and not any(p in _CLAUDE_APP_READABLE for p in parts)


def mentions_secret(command):
    """True kalau perintah menyebut berkas rahasia (mis. Get-Content .env)."""
    words = re.split(r"[\s'\"`;|&,()<>=]+", command or "")
    return any(w and is_secret_path(w) for w in words)


# Perintah yang mengompres/mengekstrak arsip. Nomor 5: zip/ekstrak selalu minta
# persetujuan, juga di akses penuh — jadi perintah ini dipaksa bertanya.
_ARCHIVE_CMDS = {"compress-archive", "expand-archive", "tar", "unzip", "zip", "7z", "7za", "gzip", "gunzip"}


def is_archive_command(command):
    """True kalau perintah shell mengompres atau mengekstrak arsip."""
    words = re.split(r"[\s'\"`;|&,()<>=/\\]+", (command or "").lower())
    return any(w in _ARCHIVE_CMDS for w in words)


def approve(ctx, text, always_ask=False):
    """Izin pemilik untuk aksi yang mengubah sesuatu.

    Mode akses penuh: langsung jalan, pemilik dikabari lewat ctx["notify"].
    Selain itu (atau always_ask): tombol Ya/Batal di Telegram.
    """
    if ctx.get("full_access") and not always_ask:
        notify = ctx.get("notify")
        if notify:
            notify(text)
        return True
    return ctx["confirm"](text)


def _inside(path, base):
    """True kalau path berada di dalam folder base (setelah symlink diikuti)."""
    try:
        real_base = os.path.realpath(base)
        return os.path.commonpath([os.path.realpath(path), real_base]) == real_base
    except ValueError:  # beda drive di Windows
        return False


def _resolve(ctx, path):
    if os.path.isabs(path):
        return path
    base = ctx.get("website_dir") or os.getcwd()
    return os.path.join(base, path)


def _preview(text, limit=700):
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(+{len(text) - limit} karakter lagi)"


def _human(num):
    """Ukuran berkas yang enak dibaca, mis. 3.4 MB."""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


# --- menjalankan program ----------------------------------------------------------
# Rahasia milik It's Me tidak ikut diwariskan ke program yang dijalankan.
_SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def _child_env():
    env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV}
    # Git jangan pernah menunggu ketikan atau membuka jendela login di komputer
    # saat pemilik memerintah dari HP: lebih baik gagal cepat dengan pesan jelas.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _shell_args(command):
    if IS_WINDOWS:
        # PowerShell punya ls/dir/pwd/cat dan tidak menunggu ketikan seperti
        # "date" milik cmd. Keluarannya dipaksa UTF-8 supaya teks tidak rusak.
        return [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + command,
        ]
    return command


def _exec(command, cwd, timeout=180):
    """Jalankan perintah; kembalikan (exit code, stdout, stderr).

    String → lewat shell (PowerShell di Windows); list → tanpa shell (aman).
    """
    is_shell = isinstance(command, str)
    try:
        proc = subprocess.run(
            _shell_args(command) if is_shell else command,
            shell=is_shell and not IS_WINDOWS,
            cwd=cwd or None,
            stdin=subprocess.DEVNULL,  # tidak ada yang bisa mengetik di sini
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return 1, "", "⏱️ Perintah melebihi batas waktu dan dihentikan."
    except FileNotFoundError as exc:
        return 127, "", f"Program tidak ditemukan: {exc.filename or command}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _format(code, out, err):
    parts = [f"exit code: {code}"]
    if out.strip():
        parts.append("stdout:\n" + out[-4000:])
    if err.strip():
        parts.append("stderr:\n" + err[-2000:])
    return "\n".join(parts)


def _run(command, cwd, timeout=180):
    code, out, err = _exec(command, cwd, timeout)
    return code, _format(code, out, err)


def _changed_paths(status_output):
    """Daftar path dari keluaran `git status --porcelain`."""
    paths = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


_AUTH_SIGNS = (
    "authentication failed", "could not read username", "terminal prompts disabled",
    "permission denied", "403", "credential",
)


def _deploy(ctx, pesan):
    cwd = ctx.get("website_dir")
    if not cwd:
        return "WEBSITE_REPO_DIR belum diatur, jadi deploy belum bisa."
    code, out, err = _exec(["git", "status", "--porcelain"], cwd)
    if code != 0:
        return "Folder website bukan repo Git yang bisa dipakai.\n" + _format(code, out, err)
    paths = _changed_paths(out)
    if not paths:
        return "Tidak ada perubahan di folder website, jadi tidak ada yang di-deploy."
    secrets = [p for p in paths if is_secret_path(p)]
    if secrets:
        return (
            "Deploy dibatalkan: berkas rahasia ikut berubah dan akan ter-push ke GitHub:\n"
            + "\n".join(secrets)
            + "\nTambahkan berkas itu ke .gitignore dulu."
        )
    daftar = "\n".join(paths[:30]) + (f"\n…(+{len(paths) - 30} berkas lagi)" if len(paths) > 30 else "")
    if not approve(ctx, f"Deploy website sekarang?\n\nCommit: {pesan}\n\nBerkas yang ikut:\n{daftar}"):
        return "Dibatalkan oleh pemilik."
    # Argumen berupa list, tanpa shell: pesan commit tidak akan pernah
    # dieksekusi sebagai perintah, apa pun isinya.
    steps = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", pesan],
        ["git", "push"],
    ]
    log = []
    for step in steps:
        code, out, err = _exec(step, cwd)
        log.append(f"$ {shlex.join(step)}\n{_format(code, out, err)}")
        if code != 0:
            if step[1] == "push" and any(s in err.lower() for s in _AUTH_SIGNS):
                log.append(
                    "Git di komputer ini belum bisa login ke GitHub. Jalankan sekali "
                    "`git push` dari folder website langsung di komputer (bukan lewat "
                    "bot) supaya login tersimpan, lalu coba deploy lagi."
                )
            log.append("Berhenti: langkah di atas gagal.")
            break
    return "\n\n".join(log)


# --- zip & ekstrak (nomor 5) ------------------------------------------------------
# Batas aman supaya tidak tak sengaja mengompres/mengekstrak seluruh drive.
_ARCHIVE_MAX_BYTES = 2 * 1024 ** 3   # 2 GB
_ARCHIVE_MAX_FILES = 50_000
_EXTRACT_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def _too_broad_to_archive(path):
    """True kalau folder ini terlalu luas untuk di-zip sekaligus: akar drive,
    folder home, atau puncak AppData/Library. Subfolder yang spesifik di
    dalamnya tetap boleh (berkas rahasia di dalamnya dilewati per-berkas)."""
    full = os.path.normpath(os.path.abspath(path))
    parts = [p for p in full.replace("\\", "/").split("/") if p]
    if len(parts) <= 1 or full == os.path.normpath(os.path.expanduser("~")):
        return True
    tail = [p.lower() for p in parts[-2:]]
    return tail[-1] in ("appdata", "library") or tail in (["appdata", "local"], ["appdata", "roaming"])


def _zip_plan(sources, ctx):
    """Kumpulkan berkas yang akan di-zip.

    Kembalikan (files, dilewati, total_byte, error). files = (path, nama_di_zip).
    Berkas rahasia dilewati; folder yang terlalu luas atau arsip terlalu besar
    menjadi error (string siap dibaca).
    """
    files, skipped, total = [], [], 0
    for src in sources:
        if not os.path.exists(src):
            return None, None, 0, f"Tidak ditemukan: {src}"
        if _too_broad_to_archive(src):
            return None, None, 0, (
                f"Folder terlalu luas untuk di-zip sekaligus: {src}. Pilih folder yang "
                "lebih spesifik (satu folder proyek, bukan seluruh home/AppData/drive)."
            )
        if os.path.isfile(src):
            if is_secret_path(src):
                skipped.append(src)
                continue
            files.append((src, os.path.basename(src)))
            total += _size(src)
        else:
            parent = os.path.dirname(os.path.normpath(src))
            for dirpath, dirnames, filenames in os.walk(src):
                dirnames[:] = [d for d in dirnames if d.lower() not in _SECRET_DIRS]
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    if is_secret_path(full):
                        skipped.append(full)
                        continue
                    files.append((full, os.path.relpath(full, parent)))
                    total += _size(full)
                    if total > _ARCHIVE_MAX_BYTES or len(files) > _ARCHIVE_MAX_FILES:
                        return None, None, 0, (
                            "Terlalu besar untuk di-zip (batas sekitar 2 GB / 50.000 berkas). "
                            "Pilih bagian yang lebih kecil."
                        )
    return files, skipped, total, None


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _extract_plan(archive):
    """Periksa arsip tanpa mengekstrak: (nama_entri, total_byte, error)."""
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                names = [i.filename for i in infos]
                total = sum(i.file_size for i in infos)
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tf:
                members = tf.getmembers()
                names = [m.name for m in members]
                total = sum(m.size for m in members)
        else:
            return None, 0, "Format tidak didukung. Pakai .zip, .tar, .tar.gz, atau .tgz."
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        return None, 0, f"Arsip rusak atau tidak bisa dibaca: {exc}"
    for n in names:
        if os.path.isabs(n) or ".." in n.replace("\\", "/").split("/"):
            return None, 0, f"Arsip ditolak: berisi path yang keluar folder tujuan ({n})."
    if total > _ARCHIVE_MAX_BYTES or len(names) > _ARCHIVE_MAX_FILES:
        return None, 0, "Arsip terlalu besar untuk diekstrak (batas sekitar 2 GB / 50.000 berkas)."
    return names, total, None


def _safe_extract(archive, dest):
    """Ekstrak dengan penjagaan path traversal (zip-slip) berlapis."""
    dest_real = os.path.realpath(dest)

    def ok(member_name):
        target = os.path.realpath(os.path.join(dest, member_name))
        return target == dest_real or target.startswith(dest_real + os.sep)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if not ok(member):
                    raise ValueError(f"Entri keluar folder tujuan: {member}")
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if not ok(member.name):
                    raise ValueError(f"Entri keluar folder tujuan: {member.name}")
            tf.extractall(dest)


def _make_zip(sources, dest, ctx):
    files, skipped, total, err = _zip_plan(sources, ctx)
    if err:
        return "Dibatalkan: " + err
    if not files:
        return "Tidak ada berkas yang bisa dikompres (semua dilewati karena rahasia, atau folder kosong)."
    if not dest:
        dest = os.path.normpath(sources[0]).rstrip("\\/") + ".zip"
    elif not dest.lower().endswith(".zip"):
        dest += ".zip"
    if is_secret_path(dest):
        return "Ditolak: nama berkas tujuan terlihat seperti berkas rahasia."
    teks = f"Buat zip dari {len(files)} berkas ({_human(total)})?\n\nTujuan: {dest}"
    if skipped:
        teks += f"\n\n{len(skipped)} berkas rahasia DILEWATI (mis. {os.path.basename(skipped[0])})."
    # Nomor 5: zip selalu minta persetujuan, juga saat akses penuh.
    if not approve(ctx, teks, always_ask=True):
        return "Dibatalkan oleh pemilik."
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in files:
            zf.write(full, arc)
    note = f"Zip dibuat: {dest} ({len(files)} berkas, {_human(total)})."
    if skipped:
        note += f" {len(skipped)} berkas rahasia dilewati demi keamanan."
    return note


def _extract(archive, dest, ctx):
    if not os.path.isfile(archive):
        return "Arsip tidak ditemukan."
    names, total, err = _extract_plan(archive)
    if err:
        return "Dibatalkan: " + err
    if not dest:
        name = os.path.basename(archive)
        for suffix in sorted(_EXTRACT_SUFFIXES, key=len, reverse=True):
            if name.lower().endswith(suffix):
                name = name[: -len(suffix)]
                break
        dest = os.path.join(os.path.dirname(archive), name or "hasil_ekstrak")
    teks = (
        f"Ekstrak {len(names)} berkas ({_human(total)}) dari {os.path.basename(archive)}?"
        f"\n\nTujuan: {dest}"
    )
    # Nomor 5: ekstrak selalu minta persetujuan, juga saat akses penuh.
    if not approve(ctx, teks, always_ask=True):
        return "Dibatalkan oleh pemilik."
    os.makedirs(dest, exist_ok=True)
    _safe_extract(archive, dest)
    return f"Diekstrak {len(names)} berkas ke {dest}."


# --- Google Drive ------------------------------------------------------------------
_DRIVE_TOOLS = ("drive_cari", "drive_isi_folder", "drive_unggah", "drive_unduh", "drive_kelola")


def _drive_list(items):
    if not items:
        return "(tidak ada hasil)"
    lines = []
    for it in items[:50]:
        icon = "📁" if it["folder"] else "📄"
        extra = "" if it["folder"] else f" · {_human(it['size'])}"
        lines.append(f"{icon} {it['name']}{extra}  [id: {it['id']}]")
    return "\n".join(lines)


def _drive_manage(drive, inp, ctx):
    aksi = (inp.get("aksi") or "").strip().lower()
    if aksi == "buat_folder":
        nama = (inp.get("nama") or "").strip()
        if not nama:
            return "Nama folder kosong."
        if not approve(ctx, f"Buat folder di Google Drive: '{nama}'?"):
            return "Dibatalkan oleh pemilik."
        f = drive.make_folder(nama, inp.get("folder_id") or None)
        return f"Folder dibuat di Drive: {f.get('name')} (id {f.get('id')})."
    fid = (inp.get("file_id") or "").strip()
    if not fid:
        return "file_id kosong."
    nama_skrg = drive.name_of(fid)
    if aksi == "ganti_nama":
        baru = (inp.get("nama") or "").strip()
        if not baru:
            return "Nama baru kosong."
        if not approve(ctx, f"Ganti nama di Drive: '{nama_skrg}' → '{baru}'?"):
            return "Dibatalkan oleh pemilik."
        drive.rename(fid, baru)
        return f"Nama diganti menjadi '{baru}'."
    if aksi == "pindah":
        folder = (inp.get("folder_id") or "").strip()
        if not folder:
            return "folder_id tujuan kosong."
        if not approve(ctx, f"Pindahkan '{nama_skrg}' ke folder {folder} di Drive?"):
            return "Dibatalkan oleh pemilik."
        drive.move(fid, folder)
        return f"'{nama_skrg}' dipindahkan."
    if aksi == "hapus":
        # Penghapusan selalu minta konfirmasi, juga di akses penuh.
        if not approve(ctx, f"Buang '{nama_skrg}' ke sampah Google Drive?", always_ask=True):
            return "Dibatalkan oleh pemilik."
        drive.delete(fid)
        return f"'{nama_skrg}' dibuang ke sampah Drive (masih bisa dipulihkan)."
    return f"Aksi Drive tidak dikenal: {aksi}"


def handle_drive_tool(name, inp, ctx):
    from . import drive

    try:
        if name == "drive_cari":
            return _drive_list(drive.search(inp.get("kata_kunci", "")))
        if name == "drive_isi_folder":
            return _drive_list(drive.list_folder(inp.get("folder_id") or "root"))
        if name == "drive_unggah":
            path = _resolve(ctx, inp["path_lokal"])
            if is_secret_path(path):
                return "Ditolak: berkas rahasia tidak diunggah lewat bot."
            dest = f"\n→ folder {inp['folder_id']}" if inp.get("folder_id") else ""
            if not approve(ctx, f"Unggah ke Google Drive?\n\n{path}{dest}"):
                return "Dibatalkan oleh pemilik."
            f = drive.upload(path, inp.get("folder_id") or None, inp.get("nama") or None)
            return f"Terunggah ke Drive: {f.get('name')} (id {f.get('id')})."
        if name == "drive_unduh":
            fid = inp["file_id"]
            nama = drive.name_of(fid)
            dest = (inp.get("path_lokal") or "").strip()
            dest = _resolve(ctx, dest) if dest else os.path.join(config.DRIVE_DOWNLOAD_DIR, nama)
            if is_secret_path(dest):
                return "Ditolak: tujuan terlihat seperti berkas rahasia."
            if not approve(ctx, f"Unduh dari Drive '{nama}' ke:\n\n{dest}"):
                return "Dibatalkan oleh pemilik."
            return f"Terunduh dari Drive: {drive.download(fid, dest)['path']}"
        if name == "drive_kelola":
            return _drive_manage(drive, inp, ctx)
    except drive.DriveError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - laporkan error apa pun ke pemakai
        return f"Google Drive error: {type(exc).__name__}: {exc}"
    return f"Alat Drive tidak dikenal: {name}"


def handle_tool(name, tool_input, ctx):
    """Jalankan satu alat klien dan kembalikan hasilnya sebagai teks.

    ctx berisi: website_dir, memory, confirm(teks)->bool, notify(teks),
    full_access (mode akses penuh), schedule_background(instruksi)->str,
    seen_urls (set alamat yang boleh langsung dibuka), dan in_background
    (True di dalam tugas latar).
    """
    try:
        if name == "jalankan_perintah":
            perintah = tool_input.get("perintah", "").strip()
            if not perintah:
                return "Perintah kosong."
            if not is_read_only(perintah):
                alasan = tool_input.get("alasan", "")
                teks = f"Jalankan perintah ini?\n\n{perintah}"
                if alasan:
                    teks += f"\n\nAlasan: {alasan}"
                # Perintah yang menyebut berkas rahasia atau mengompres/
                # mengekstrak arsip selalu ditanyakan (nomor 5).
                always = mentions_secret(perintah) or is_archive_command(perintah)
                if not approve(ctx, teks, always_ask=always):
                    return "Dibatalkan oleh pemilik."
            return _run(perintah, ctx.get("website_dir"))[1]

        if name == "baca_berkas":
            path = _resolve(ctx, tool_input["path"])
            if is_secret_path(path):
                return "Ditolak: berkas itu biasanya berisi rahasia (token, password, kunci)."
            base = ctx.get("website_dir")
            if not (base and _inside(path, base)) and not ctx.get("full_access"):
                if not ctx["confirm"](f"Baca berkas di luar folder website?\n\n{path}"):
                    return "Dibatalkan oleh pemilik."
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()[:12000]

        if name == "tulis_berkas":
            path = _resolve(ctx, tool_input["path"])
            if is_secret_path(path):
                return "Ditolak: berkas rahasia (.env, kunci) tidak boleh ditulis lewat bot."
            isi = tool_input.get("isi", "")
            keadaan = "menimpa berkas yang sudah ada" if os.path.exists(path) else "berkas baru"
            teks = (
                f"Tulis berkas ini? ({keadaan}, {len(isi)} karakter)\n\n{path}\n\n"
                f"Isi:\n{_preview(isi)}"
            )
            if not approve(ctx, teks):
                return "Dibatalkan oleh pemilik."
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(isi)
            return f"Berhasil ditulis: {path} ({len(isi)} karakter)."

        if name == "buat_zip":
            sources = [_resolve(ctx, s.strip()) for s in tool_input.get("sumber", "").splitlines() if s.strip()]
            if not sources:
                return "Tidak ada sumber yang disebutkan."
            dest = tool_input.get("tujuan", "").strip()
            return _make_zip(sources, _resolve(ctx, dest) if dest else "", ctx)

        if name == "ekstrak":
            archive = _resolve(ctx, tool_input.get("arsip", "").strip())
            dest = tool_input.get("tujuan", "").strip()
            return _extract(archive, _resolve(ctx, dest) if dest else "", ctx)

        if name == "deploy_website":
            pesan = tool_input.get("pesan_commit", "").strip() or "update dari It's Me"
            return _deploy(ctx, pesan)

        if name == "cari_web":
            return web.search(tool_input.get("kata_kunci", ""), seen=ctx.get("seen_urls"))

        if name == "baca_halaman":
            url = tool_input.get("url", "").strip()
            seen = ctx.get("seen_urls")
            if (seen is None or url not in seen) and not ctx.get("full_access"):
                if not ctx["confirm"](f"Buka halaman ini?\n\n{url}"):
                    return "Dibatalkan oleh pemilik."
            return web.fetch(url)

        if name == "simpan_memori":
            catatan = tool_input.get("catatan", "").strip()
            if not catatan:
                return "Catatan kosong, tidak disimpan."
            if not approve(ctx, f"Simpan ke memori jangka panjang?\n\n{_preview(catatan, 1000)}"):
                return "Dibatalkan oleh pemilik."
            return ctx["memory"].add(catatan)

        if name == "tugas_latar":
            if ctx.get("in_background"):
                return "Tugas latar tidak bisa membuat tugas latar lagi. Kerjakan langsung di sini."
            return ctx["schedule_background"](tool_input.get("instruksi", ""))

        if name in _DRIVE_TOOLS:
            return handle_drive_tool(name, tool_input, ctx)

        return f"Alat tidak dikenal: {name}"
    except FileNotFoundError:
        return "Berkas tidak ditemukan."
    except Exception as exc:  # noqa: BLE001 - laporkan error apa pun ke pemakai
        return f"Terjadi error saat menjalankan {name}: {exc}"
