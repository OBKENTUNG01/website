"""Kuota harian untuk tamu.

Tamu (ITSME_GUEST_IDS / ITSME_WA_GUESTS / ITSME_WA_OPEN) memakai otak berbayar
milik pemilik. Tanpa batas, satu tamu bisa menghabiskan kuota API dalam sehari.
Pemilik tidak dibatasi; hitungan disimpan per hari agar bisa diperiksa pemilik.
"""

import json
import os
from datetime import date

from . import config

# Ponytail: hitungan sederhana per hari kalender lokal, satu berkas JSON. Kalau
# nanti butuh jendela bergulir atau kuota per jam, ganti ke tabel waktu.
_FILE = "guest_quota.json"


def _file():
    return os.path.join(config.DATA_DIR, _FILE)


def _load(today):
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("date") == today:
            return data
    except (OSError, ValueError):
        pass
    return {"date": today, "counts": {}}


def _save(data):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = _file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _file())
    except OSError:
        pass


def allow(key):
    """True kalau tamu `key` masih boleh bertanya hari ini (dan catat pemakaiannya)."""
    limit = config.GUEST_DAILY_MAX
    if limit <= 0:
        return True
    today = date.today().isoformat()
    data = _load(today)
    used = int(data["counts"].get(str(key), 0))
    if used >= limit:
        return False
    data["counts"][str(key)] = used + 1
    _save(data)
    return True


def remaining(key):
    limit = config.GUEST_DAILY_MAX
    if limit <= 0:
        return -1  # tanpa batas
    used = int(_load(date.today().isoformat())["counts"].get(str(key), 0))
    return max(0, limit - used)
