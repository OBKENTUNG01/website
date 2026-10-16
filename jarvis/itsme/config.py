"""Konfigurasi It's Me.

Semua nilai dibaca dari environment / file .env. Tidak ada rahasia yang
ditulis di dalam kode. Jalankan salinan .env.example menjadi .env lalu isi.
"""

import os

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
OWNER_ID = int(_get("OWNER_TELEGRAM_ID", "0") or "0")

# --- Pilihan otak -----------------------------------------------------------
# "ollama" (bawaan): model lokal di komputermu, gratis, tanpa API key.
# "claude": Claude lewat Anthropic API (butuh ANTHROPIC_API_KEY).
BACKEND = (_get("ITSME_BACKEND", "ollama") or "ollama").strip().lower()

# --- Otak lokal (Ollama) ----------------------------------------------------
OLLAMA_URL = (_get("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
OLLAMA_MODEL = _get("ITSME_OLLAMA_MODEL", "qwen3:8b")
# Panjang konteks (token). Bawaan Ollama terlalu kecil untuk system prompt +
# daftar alat + riwayat obrolan; 16384 masih muat di RAM 16 GB untuk model 8B.
OLLAMA_NUM_CTX = int(_get("ITSME_OLLAMA_NUM_CTX", "16384"))
# Berapa lama model tetap dimuat di RAM setelah dipakai ("30m", "2h", "-1" =
# selamanya). Lebih lama = jawaban pertama lebih cepat, tapi RAM terpakai terus.
OLLAMA_KEEP_ALIVE = _get("ITSME_OLLAMA_KEEP_ALIVE", "30m")
# Mode berpikir untuk model yang mendukungnya: "true"/"false", atau kosong
# untuk mengikuti bawaan model. Berpikir = lebih teliti tapi jauh lebih lambat
# di komputer tanpa GPU.
_think = (_get("ITSME_OLLAMA_THINK", "") or "").strip().lower()
OLLAMA_THINK = {"true": True, "1": True, "false": False, "0": False}.get(_think)
# Batas waktu satu jawaban model (detik). Di CPU, model bisa butuh beberapa menit.
OLLAMA_TIMEOUT = float(_get("ITSME_OLLAMA_TIMEOUT", "600"))
# Jumlah pesan riwayat yang dikirim ke model tiap giliran, supaya muat di konteks.
HISTORY_MESSAGES = int(_get("ITSME_HISTORY_MESSAGES", "24"))

# --- Otak Claude (opsional) ---------------------------------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
MODEL = _get("ITSME_MODEL", "claude-opus-5-5")
EFFORT = _get("ITSME_EFFORT", "max")
# Proses berpikir ikut dihitung di sini, dan effort max berpikir panjang —
# jadi dibuat lega. Aman karena permintaan ke Claude memakai streaming.
MAX_TOKENS = int(_get("ITSME_MAX_TOKENS", "64000"))
# Batas langkah tool dalam satu giliran, supaya tidak berputar tanpa henti.
MAX_STEPS = int(_get("ITSME_MAX_STEPS", "16"))

# --- Website (edit lewat Git) ----------------------------------------------
# Folder clone lokal dari repo website yang mau diedit It's Me.
# Kosongkan kalau belum dipakai.
WEBSITE_REPO_DIR = _get("WEBSITE_REPO_DIR", "")

# --- Memori -----------------------------------------------------------------
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
MEMORY_FILE = _get("ITSME_MEMORY_FILE", os.path.join(_DATA_DIR, "memory.md"))

# Nama persona.
NAME = _get("ITSME_NAME", "It's Me")


def check():
    """Kembalikan daftar masalah konfigurasi (kosong = siap jalan)."""
    problems = []
    if not TELEGRAM_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN belum diisi.")
    if not OWNER_ID:
        problems.append("OWNER_TELEGRAM_ID belum diisi (wajib, demi keamanan).")
    if BACKEND not in ("ollama", "claude"):
        problems.append(f"ITSME_BACKEND harus 'ollama' atau 'claude', bukan '{BACKEND}'.")
    if BACKEND == "claude" and not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY belum diisi (wajib untuk ITSME_BACKEND=claude).")
    return problems
