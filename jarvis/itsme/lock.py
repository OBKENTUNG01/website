"""Gerbang kata sandi bot.

Walau hanya pemilik (user id Telegram tertentu) yang bisa memerintah bot, gerbang
ini menambah satu lapis: sebelum memakai bot, kata sandi harus dimasukkan. Yang
salah selalu dijawab sama — "kata sandi minimal 20 karakter" — sebagai PENGECOH,
padahal kata sandi sebenarnya (ITSME_PASSPHRASE) boleh lebih pendek. Jadi orang
yang mengakses sembarangan menebak-nebak panjang yang salah.

Kata sandi hanya di .env (tidak pernah di kode/riwayat/log). Kosong = gerbang mati.
Status "terbuka" per chat disimpan supaya tidak ditanya setiap bot menyala ulang;
/lock menguncinya lagi, dan ITSME_LOCK_TTL (menit) bisa mengunci otomatis.
"""

import hmac
import json
import os
import time

from . import config

# Jawaban pengecoh: panjang "minimal 20" sengaja tidak sama dengan kata sandi asli.
DECOY = "🔒 Akses terkunci. Kirim kata sandi (minimal 20 karakter) untuk membuka."
WELCOME = "🔓 Kata sandi benar. Akses terbuka. Kirim /lock untuk mengunci lagi."
LOCKED_NOTE = "🔒 Terkunci lagi. Kirim kata sandi untuk membuka."


def active():
    """True kalau kata sandi diatur (gerbang menyala)."""
    return bool(config.PASSPHRASE)


def _file():
    return os.path.join(config.DATA_DIR, "unlocked.json")


def _load():
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = _file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _file())
    except OSError:
        pass


def is_unlocked(chat_id):
    if not active():
        return True
    rec = _load().get(str(chat_id))
    if not rec:
        return False
    ttl = config.LOCK_TTL_MINUTES * 60
    if ttl and time.time() - float(rec) > ttl:
        return False
    return True


# Pembatas tebakan: tamu (ITSME_GUEST_IDS) juga harus memasukkan kata sandi ini,
# jadi gerbang yang sama bisa ditebak terus-menerus kalau tidak dibatasi.
FAIL_MAX, FAIL_WINDOW = 10, 15 * 60
_FAILS = {}  # chat_id -> [waktu tebakan salah]


def _recent_fails(chat_id):
    now = time.time()
    hits = [t for t in _FAILS.get(str(chat_id), []) if now - t < FAIL_WINDOW]
    _FAILS[str(chat_id)] = hits
    return hits


def locked_out(chat_id):
    """True kalau tebakan salah untuk chat ini sudah terlalu banyak."""
    return len(_recent_fails(chat_id)) >= FAIL_MAX


def try_unlock(chat_id, text):
    """Buka kalau text sama persis dengan kata sandi. True kalau berhasil."""
    if not active():
        return True
    if locked_out(chat_id):
        return False
    given = (text or "").strip()
    if not given or not hmac.compare_digest(given, config.PASSPHRASE):
        _recent_fails(chat_id).append(time.time())
        return False
    data = _load()
    data[str(chat_id)] = time.time()
    _save(data)
    _FAILS.pop(str(chat_id), None)
    return True


def lock(chat_id):
    data = _load()
    if data.pop(str(chat_id), None) is not None:
        _save(data)
