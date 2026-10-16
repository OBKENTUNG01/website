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
import shlex
import subprocess

from . import web

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
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".ppk")
_SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube"}


def is_secret_path(path):
    """True untuk berkas yang biasanya berisi token, password, atau kunci."""
    parts = [p.lower() for p in os.path.normpath(path).replace("\\", "/").split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    if name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES):
        return True
    if name.startswith(".env") and name != ".env.example":
        return True
    return any(p in _SECRET_DIRS for p in parts[:-1])


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


# --- menjalankan program ----------------------------------------------------------
# Rahasia milik It's Me tidak ikut diwariskan ke program yang dijalankan.
_SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


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
    if not ctx["confirm"](f"Deploy website sekarang?\n\nCommit: {pesan}\n\nBerkas yang ikut:\n{daftar}"):
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


def handle_tool(name, tool_input, ctx):
    """Jalankan satu alat klien dan kembalikan hasilnya sebagai teks.

    ctx berisi: website_dir, memory, confirm(teks)->bool,
    schedule_background(instruksi)->str, seen_urls (set alamat yang boleh
    langsung dibuka), dan in_background (True di dalam tugas latar).
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
                if not ctx["confirm"](teks):
                    return "Dibatalkan oleh pemilik."
            return _run(perintah, ctx.get("website_dir"))[1]

        if name == "baca_berkas":
            path = _resolve(ctx, tool_input["path"])
            if is_secret_path(path):
                return "Ditolak: berkas itu biasanya berisi rahasia (token, password, kunci)."
            base = ctx.get("website_dir")
            if not (base and _inside(path, base)):
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
            if not ctx["confirm"](teks):
                return "Dibatalkan oleh pemilik."
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(isi)
            return f"Berhasil ditulis: {path} ({len(isi)} karakter)."

        if name == "deploy_website":
            pesan = tool_input.get("pesan_commit", "").strip() or "update dari It's Me"
            return _deploy(ctx, pesan)

        if name == "cari_web":
            return web.search(tool_input.get("kata_kunci", ""), seen=ctx.get("seen_urls"))

        if name == "baca_halaman":
            url = tool_input.get("url", "").strip()
            seen = ctx.get("seen_urls")
            if seen is None or url not in seen:
                if not ctx["confirm"](f"Buka halaman ini?\n\n{url}"):
                    return "Dibatalkan oleh pemilik."
            return web.fetch(url)

        if name == "simpan_memori":
            catatan = tool_input.get("catatan", "").strip()
            if not catatan:
                return "Catatan kosong, tidak disimpan."
            if not ctx["confirm"](f"Simpan ke memori jangka panjang?\n\n{_preview(catatan, 1000)}"):
                return "Dibatalkan oleh pemilik."
            return ctx["memory"].add(catatan)

        if name == "tugas_latar":
            if ctx.get("in_background"):
                return "Tugas latar tidak bisa membuat tugas latar lagi. Kerjakan langsung di sini."
            return ctx["schedule_background"](tool_input.get("instruksi", ""))

        return f"Alat tidak dikenal: {name}"
    except FileNotFoundError:
        return "Berkas tidak ditemukan."
    except Exception as exc:  # noqa: BLE001 - laporkan error apa pun ke pemakai
        return f"Terjadi error saat menjalankan {name}: {exc}"
