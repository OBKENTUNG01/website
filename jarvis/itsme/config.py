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

# --- Anthropic / otak -------------------------------------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
MODEL = _get("ITSME_MODEL", "claude-opus-5-5")
EFFORT = _get("ITSME_EFFORT", "max")
MAX_TOKENS = int(_get("ITSME_MAX_TOKENS", "16000"))
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
    if not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY belum diisi.")
    return problems
