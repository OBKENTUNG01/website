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
    try:
        with open(_file(), encoding="utf-8") as f:
            return bool(json.load(f).get("full"))
    except (OSError, ValueError, AttributeError):
        return False


def set_full(on):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_file(), "w", encoding="utf-8") as f:
        json.dump({"full": bool(on)}, f)
