"""Konfigurasi It's Me.

Semua nilai dibaca dari environment / file .env. Tidak ada rahasia yang
ditulis di dalam kode. Jalankan salinan .env.example menjadi .env lalu isi.
"""

import os
import re
import socket

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # python-dotenv opsional; kalau tidak ada, environment biasa tetap terbaca.
    pass


def _get(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


# --- Telegram ---------------------------------------------------------------
TELEGRAM_TOKEN = _get("TELEGRAM_BOT_TOKEN")
# Hanya pemilik (user id Telegram ini) yang boleh memerintah bot.
# Wajib diisi — tanpa ini siapa pun yang menemukan bot bisa mengendalikannya.
try:
    OWNER_ID = int(_get("OWNER_TELEGRAM_ID", "0") or "0")
except ValueError:
    OWNER_ID = 0  # isian bukan angka → dilaporkan oleh check()
# Alamat Bot API Telegram. Biarkan bawaan, kecuali kamu memakai server Bot API
# sendiri.
TELEGRAM_API_URL = (_get("TELEGRAM_API_URL", "https://api.telegram.org") or "").rstrip("/")

# --- Pilihan otak -----------------------------------------------------------
# "ollama" (bawaan): model lokal di komputermu, gratis, tanpa API key.
# "openrouter": model cloud lewat OpenRouter (butuh OPENROUTER_API_KEY).
# "claude": Claude lewat Anthropic API (butuh ANTHROPIC_API_KEY).
# "claude_code": Claude di komputer dengan akun Claude-mu (butuh CLAUDE_CODE_OAUTH_TOKEN).
# Bisa diganti saat bot berjalan lewat perintah Telegram /lokal, /cloud,
# /claude, dan /claudepc.
BACKEND = (_get("ITSME_BACKEND", "ollama") or "ollama").strip().lower()

# --- Otak cloud lewat OpenRouter (opsional) ----------------------------------
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_URL = (_get("OPENROUTER_URL", "https://openrouter.ai/api/v1") or "").rstrip("/")
# Bawaan aman tanpa biaya: model gratis (maks. 50 permintaan/hari). Skrip setup
# memilih anthropic/claude-haiku-5.5 kalau akunmu sudah pernah isi saldo.
OPENROUTER_MODEL = _get("ITSME_OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
# Model cadangan (dipisah koma) yang dicoba OpenRouter dalam permintaan yang
# sama kalau model utama penuh atau bermasalah. Bawaan untuk model ":free":
# openrouter/free, router yang memilih model gratis lain yang mendukung alat.
# Isi "off" untuk mematikan.
_fallbacks = _get("ITSME_OPENROUTER_FALLBACKS")
if _fallbacks is None:
    _fallbacks = "openrouter/free" if OPENROUTER_MODEL.endswith(":free") else ""
OPENROUTER_FALLBACKS = [
    m.strip()
    for m in _fallbacks.split(",")
    if m.strip() and m.strip().lower() not in ("0", "off", "none", "false") and m.strip() != OPENROUTER_MODEL
]
# Seberapa keras model cloud berpikir: none | minimal | low | medium | high |
# xhigh | max, atau "default" (ikuti model). Lebih tinggi = lebih teliti,
# lebih lambat, dan lebih banyak token terpakai.
OPENROUTER_REASONING = (_get("ITSME_OPENROUTER_REASONING", "medium") or "medium").strip().lower()
OPENROUTER_TIMEOUT = float(_get("ITSME_OPENROUTER_TIMEOUT", "300"))
# Kalau otak cloud gagal (saldo habis, batas harian, internet), jawab pakai
# otak lokal. Isi 0 untuk mematikan.
FALLBACK_LOCAL = (_get("ITSME_FALLBACK_LOCAL", "1") or "1").strip().lower() not in ("0", "false", "no")

# --- Otak lokal (Ollama) ----------------------------------------------------
OLLAMA_URL = (_get("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
# qwen3.5:9b: skor multibahasa & pemanggilan alat terbaik yang muat di RAM
# 16 GB (unduhan 6,6 GB, ±8 GB RAM). Lebih ringan: qwen3.5:4b.
OLLAMA_MODEL = _get("ITSME_OLLAMA_MODEL", "qwen3.5:9b")
# Panjang konteks (token). Bawaan Ollama hanya 4096 di komputer tanpa GPU besar
# — terlalu kecil untuk system prompt + daftar alat + riwayat obrolan.
OLLAMA_NUM_CTX = int(_get("ITSME_OLLAMA_NUM_CTX", "16384"))
# Jumlah lapisan model yang ditaruh di GPU. Kosong = otomatis (bawaan).
# 0 = CPU saja: lebih lambat, tapi berguna kalau driver GPU bermasalah.
_num_gpu = (_get("ITSME_OLLAMA_NUM_GPU", "") or "").strip()
OLLAMA_NUM_GPU = int(_num_gpu) if re.fullmatch(r"-?\d+", _num_gpu) else None
# Berapa lama model tetap dimuat di RAM setelah dipakai ("30m", "2h", atau -1 =
# selamanya). Lebih lama = jawaban pertama lebih cepat, tapi RAM terpakai terus.
# Ollama menolak angka berbentuk teks ("-1"), jadi angka dikirim sebagai angka.
_keep_alive = (_get("ITSME_OLLAMA_KEEP_ALIVE", "30m") or "30m").strip()
OLLAMA_KEEP_ALIVE = int(_keep_alive) if re.fullmatch(r"-?\d+", _keep_alive) else _keep_alive
# Mode berpikir: "false" (bawaan), "true", atau "default" (ikuti model).
# Tanpa ini, model yang bisa berpikir akan berpikir dulu setiap kali — jauh
# lebih teliti tapi bisa bermenit-menit di komputer tanpa GPU. Mengirim false
# aman untuk model apa pun; true hanya untuk model yang mendukungnya.
_think = (_get("ITSME_OLLAMA_THINK", "false") or "false").strip().lower()
OLLAMA_THINK = {"true": True, "1": True, "false": False, "0": False}.get(_think)
# Wilayah pencarian web (format negara-bahasa), mis. "id-id" atau "us-en".
SEARCH_REGION = _get("ITSME_SEARCH_REGION", "id-id")
# Batas waktu satu jawaban model (detik). Di CPU, model bisa butuh beberapa menit.
OLLAMA_TIMEOUT = float(_get("ITSME_OLLAMA_TIMEOUT", "600"))
# Jumlah pesan riwayat yang dikirim ke model tiap giliran, supaya muat di konteks.
HISTORY_MESSAGES = int(_get("ITSME_HISTORY_MESSAGES", "24"))

# --- Otak Claude (opsional) ---------------------------------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
MODEL = _get("ITSME_MODEL", "claude-opus-5-5")
# medium cukup untuk asisten obrolan dan jauh lebih hemat; "max" membuat
# setiap pesan (bahkan "halo") memakai setelan termahal. Naikkan ke high/max
# hanya kalau tugasnya memang berat.
EFFORT = _get("ITSME_EFFORT", "medium")
# Proses berpikir ikut dihitung di sini — jadi dibuat lega. Aman karena
# permintaan ke Claude memakai streaming.
MAX_TOKENS = int(_get("ITSME_MAX_TOKENS", "64000"))
# Batas langkah tool dalam satu giliran, supaya tidak berputar tanpa henti.
MAX_STEPS = int(_get("ITSME_MAX_STEPS", "16"))

# --- Otak Claude di komputer (akun Claude-mu, tanpa API key) -------------------
# Token akun dari `claude setup-token`. Paling gampang diisi dengan menjalankan
# `python -m itsme.claude_login` di komputer, atau /kunci dari Telegram.
CLAUDE_CODE_OAUTH_TOKEN = _get("CLAUDE_CODE_OAUTH_TOKEN")
# Kosong = model bawaan akunmu. Bisa juga: sonnet, opus, haiku.
CLAUDE_CODE_MODEL = _get("ITSME_CLAUDE_CODE_MODEL")
# Batas langkah Claude Code dalam satu giliran. 0 = tanpa batas.
CLAUDE_CODE_MAX_TURNS = int(_get("ITSME_CLAUDE_CODE_MAX_TURNS", "0")) or None

# --- Dashboard web (nomor 4) -------------------------------------------------
# Halaman web untuk melihat status & mengatur It's Me (ganti otak, akses, memori,
# log). Jalan di dalam proses bot. Isi 0 untuk mematikan.
DASHBOARD_ENABLED = (_get("ITSME_DASHBOARD", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
# Bawaan 127.0.0.1 = hanya bisa dibuka dari komputer ini (paling aman). Isi
# 0.0.0.0 untuk bisa dibuka dari HP di WiFi yang sama; saat itu wajib pakai token.
DASHBOARD_HOST = (_get("ITSME_DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1").strip()
DASHBOARD_PORT = int(_get("ITSME_DASHBOARD_PORT", "8750"))
# Login dashboard (disarankan kalau dibuka lewat Tailscale/Cloudflare Tunnel).
# Kalau ITSME_DASHBOARD_PASSWORD diisi, dashboard minta login user+password dan
# token tidak dipakai lagi. Kosong = perilaku lama (localhost bebas, LAN pakai token).
DASHBOARD_USER = (_get("ITSME_DASHBOARD_USER", "admin") or "admin").strip()
DASHBOARD_PASSWORD = _get("ITSME_DASHBOARD_PASSWORD", "")

# --- Banyak perangkat, satu bot (Hub + Perangkat) ----------------------------
# Satu komputer jadi "Hub" (memakai token Telegram). Komputer lain jadi
# "Perangkat": jalan TANPA Telegram (mode dashboard saja) lalu mendaftar ke Hub.
# Nama perangkat ini (muncul di daftar perangkat Hub).
DEVICE_NAME = _get("ITSME_DEVICE_NAME", socket.gethostname() or "perangkat")
# Alamat dashboard Hub, mis. http://100.x.y.z:8750 (lewat Tailscale). Kalau
# DIISI, komputer ini menjadi PERANGKAT (agent) dan tidak memakai Telegram.
HUB_URL = (_get("ITSME_HUB_URL", "") or "").strip().rstrip("/")
# Secret bersama Hub & Perangkat (biar hanya perangkatmu yang bisa mendaftar).
# JANGAN pernah ditaruh di repo publik; isi di .env tiap komputer.
HUB_SECRET = _get("ITSME_HUB_SECRET", "")
# Komputer ini = perangkat (agent)? True kalau HUB_URL diisi.
AGENT_MODE = bool(HUB_URL)
# Jeda heartbeat perangkat ke Hub (detik).
HUB_HEARTBEAT = float(_get("ITSME_HUB_HEARTBEAT", "60"))

# --- It's Me Node: banyak komputer tanpa hub ----------------------------------
# Kalau It's Me Node terpasang di komputer ini, bot Telegram hanya aktif di
# komputer yang sedang menjadi pemimpin jaringan node; komputer lain siaga dan
# otomatis mengambil alih kalau pemimpin mati. 0 = abaikan node.
FOLLOW_NODE = (_get("ITSME_FOLLOW_NODE", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
NODE_URL = (_get("ITSME_NODE_URL", "http://127.0.0.1:7171") or "").strip().rstrip("/")
# Opsional: API key Tailscale (tskey-api-…). Kalau diisi, perintah /pasang ikut
# membawa izin Tailscale sekali pakai, jadi komputer baru tidak perlu login.
TAILSCALE_API_KEY = _get("TAILSCALE_API_KEY", "")

# --- Website (edit lewat Git) ----------------------------------------------
# Folder clone lokal dari repo website yang mau diedit It's Me.
# Kosongkan kalau belum dipakai.
WEBSITE_REPO_DIR = _get("WEBSITE_REPO_DIR", "")

# --- Pembaruan otomatis dari GitHub -------------------------------------------
# Selama jalan, It's Me memeriksa repo GitHub-nya. Kalau ada commit baru di
# cabang ini, kodenya dipasang (.env & memori tidak disentuh) lalu It's Me
# menyala ulang sendiri. Isi 0 untuk mematikan.
AUTO_UPDATE = (_get("ITSME_AUTO_UPDATE", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
UPDATE_REPO = _get("ITSME_UPDATE_REPO", "OBKENTUNG01/website")
UPDATE_BRANCH = _get("ITSME_UPDATE_BRANCH", "main")
# Jeda antar pemeriksaan (detik). Minimal 60: tanpa login, GitHub hanya
# melayani 60 pemeriksaan per jam.
UPDATE_INTERVAL = max(60.0, float(_get("ITSME_UPDATE_INTERVAL", "120")))

# --- Memori & berkas data -----------------------------------------------------
DATA_DIR = _get("ITSME_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
INBOX_DIR = os.path.join(DATA_DIR, "inbox")  # foto & berkas kiriman pemilik
MEMORY_FILE = _get("ITSME_MEMORY_FILE", os.path.join(DATA_DIR, "memory.md"))
# Catatan jalannya bot (error, otak yang dipakai). Berguna saat bot "diam saja"
# dan jendelanya terkecilkan, sehingga pesan error tidak terlihat.
LOG_FILE = _get("ITSME_LOG_FILE", os.path.join(DATA_DIR, "itsme.log"))
# Pengaturan aman otak lokal yang terbukti jalan (mis. CPU saja karena GPU
# crash), supaya tidak crash lagi setiap kali It's Me dinyalakan ulang.
OLLAMA_STATE_FILE = os.path.join(DATA_DIR, "ollama_state.json")

# --- Google Drive (opsional) --------------------------------------------------
# Berkas kredensial OAuth dari Google Cloud (client_secret). Pemilik mengunduh
# dan menyimpannya di sini, lalu menjalankan: python -m itsme.drive_login
DRIVE_CREDENTIALS_FILE = _get("ITSME_DRIVE_CREDENTIALS", os.path.join(DATA_DIR, "drive_credentials.json"))
# Token hasil login (dibuat otomatis oleh drive_login). Rahasia.
DRIVE_TOKEN_FILE = os.path.join(DATA_DIR, "drive_token.json")
# Folder tujuan bawaan saat mengunduh berkas dari Drive ke komputer.
DRIVE_DOWNLOAD_DIR = _get("ITSME_DRIVE_DOWNLOAD_DIR", os.path.join(os.path.expanduser("~"), "Downloads"))

# Nama persona.
NAME = _get("ITSME_NAME", "It's Me")


def check():
    """Kembalikan daftar masalah konfigurasi (kosong = siap jalan)."""
    problems = []
    if AGENT_MODE:
        # Mode perangkat: tanpa Telegram. Yang wajib: secret Hub & dashboard aktif.
        if not HUB_SECRET:
            problems.append("ITSME_HUB_SECRET belum diisi (wajib untuk mode perangkat).")
        if not DASHBOARD_ENABLED:
            problems.append("Dashboard harus aktif untuk mode perangkat (jangan ITSME_DASHBOARD=0).")
        return problems
    if not TELEGRAM_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN belum diisi.")
    if not OWNER_ID:
        problems.append("OWNER_TELEGRAM_ID belum diisi (wajib, demi keamanan).")
    if BACKEND not in ("ollama", "openrouter", "claude", "claude_code"):
        problems.append(
            "ITSME_BACKEND harus 'ollama', 'openrouter', 'claude', atau 'claude_code', "
            f"bukan '{BACKEND}'."
        )
    if BACKEND == "openrouter" and not OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY belum diisi (wajib untuk ITSME_BACKEND=openrouter).")
    if BACKEND == "claude" and not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY belum diisi (wajib untuk ITSME_BACKEND=claude).")
    if BACKEND == "claude_code" and not CLAUDE_CODE_OAUTH_TOKEN:
        problems.append(
            "CLAUDE_CODE_OAUTH_TOKEN belum diisi (wajib untuk ITSME_BACKEND=claude_code; "
            "jalankan python -m itsme.claude_login)."
        )
    return problems
