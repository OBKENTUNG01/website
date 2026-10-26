"""Mode akses It's Me: "aman" (bawaan) atau "penuh".

aman  — aksi yang mengubah sesuatu, membaca di luar folder website, dan
        membuka alamat web baru minta tombol Ya/Batal di Telegram.
penuh — semuanya langsung jalan; aksi yang mengubah sesuatu dikabarkan ke
        chat. Berkas rahasia (password, token, kunci, login aplikasi &
        browser) tetap ditolak, dan perintah yang menyebutnya tetap ditanyakan.

Diganti pemilik lewat /akses_penuh dan /akses_aman; disimpan di
data/access.json supaya bertahan setelah menyala ulang atau pembaruan.
"""

import json
import os

from . import config


def _file():
    return os.path.join(config.DATA_DIR, "access.json")


def full():
    """True hanya untuk boolean JSON `true`; nilai lain (termasuk "false") = aman."""
    try:
        with open(_file(), encoding="utf-8") as f:
            return json.load(f).get("full") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_full(on):
    """Simpan mode secara atomik: tulis .tmp lalu ganti, supaya file tak pernah separuh."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    path = _file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"full": on is True}, f)
    os.replace(tmp, path)
