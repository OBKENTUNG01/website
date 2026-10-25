"""Jembatan ke HP Android (aplikasi It's Me HP) lewat long-poll.

HP ada di balik NAT, jadi tidak bisa dihubungi dari luar. Karena itu HP yang
MENYAMBUNG KELUAR ke dashboard dan menahan satu permintaan poll; saat pemilik
membuka HP di dashboard, perintah (ambil layar, daftar berkas, baca berkas,
kontrol) dikirim lewat poll itu, lalu HP mengirim hasilnya balik.

Semua lewat dashboard milik sendiri (domain/tunnel sendiri atau WiFi sama) —
tanpa server pihak ketiga. Aman: HP harus tahu token (ditampilkan di dashboard),
dan aplikasi di HP terlihat serta izinnya diberikan pemilik (layar, Accessibility).
"""

import hashlib
import os
import secrets
import threading
import time
import uuid

from . import config

# HP dianggap online kalau poll/hasil terakhir < sekian detik lalu.
ONLINE_WINDOW = 40
# Lama dashboard menahan satu poll kalau belum ada perintah (detik).
POLL_WAIT = 25
# Paling lama menunggu hasil sebuah perintah dari HP (detik).
RESULT_WAIT = 30

_lock = threading.Condition()
_phones = {}   # nama(huruf kecil) -> {"name","caps","last_seen"}
_queues = {}   # nama -> list[perintah]
_waiters = {}  # id perintah -> threading-ish hasil (dibungkus _Result)


class _Result:
    """Kotak hasil satu perintah: menunggu sampai HP mengisinya atau waktu habis."""
    __slots__ = ("ev", "value")

    def __init__(self):
        self.ev = threading.Event()
        self.value = None

    def set(self, value):
        self.value = value
        self.ev.set()

    def wait(self, timeout):
        return self.value if self.ev.wait(timeout) else None


# --- token (ditunjukkan di dashboard, dimasukkan sekali di aplikasi HP) ---------
def _token_file():
    return os.path.join(config.DATA_DIR, "hp_token.txt")


def token():
    """Token tetap untuk aplikasi HP menyambung. Dibuat sekali, bisa dibaca pemilik
    di dashboard. Bisa diganti ITSME_HP_SECRET di .env."""
    if config.HP_SECRET:
        return config.HP_SECRET
    try:
        with open(_token_file(), encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(18)
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_token_file(), "w", encoding="utf-8") as f:
            f.write(t + "\n")
    except OSError:
        pass
    return t


def pair_code():
    """Kode pendek 6 karakter untuk diketik di aplikasi HP, supaya token 24 karakter
    tidak perlu disalin. Diturunkan dari token, jadi ikut berganti kalau token
    diganti (ITSME_HP_SECRET) — tidak ada berkas tambahan yang harus dijaga."""
    return hashlib.sha256(("itsme-pair:" + token()).encode()).hexdigest()[:6].upper()


# Kode pasang 6 karakter lebih mudah ditebak: batasi percobaan gagal.
# Hitungan PER PENEBAK (mis. alamat IP), bukan global — hitungan global berarti siapa
# pun di internet bisa mengunci HP pemilik sendiri hanya dengan salah 30 kali.
_FAILS = {}
FAIL_MAX, FAIL_WINDOW = 30, 600


def _recent(key):
    now = time.monotonic()
    hits = [t for t in _FAILS.get(key, []) if now - t < FAIL_WINDOW]
    if hits:
        _FAILS[key] = hits
    else:
        _FAILS.pop(key, None)
    return hits


def check_token(given, key=""):
    """True kalau `given` sama dengan token HP atau kode pasang pendeknya.

    `key` = identitas penebak (alamat IP). Kosong = dihitung bersama ("anonymous")."""
    if not given or len(_recent(key or "anonymous")) >= FAIL_MAX:
        return False
    got = str(given).strip()
    ok = secrets.compare_digest(got, token()) or secrets.compare_digest(got.upper(), pair_code())
    if not ok:
        _FAILS.setdefault(key or "anonymous", []).append(time.monotonic())
    return ok


# --- sisi HP (aplikasi Android) ------------------------------------------------
def poll(name, caps, wait=None, kind="hp"):
    """Perangkat (HP/komputer) menunggu perintah berikutnya (sekaligus heartbeat).
    Kembalikan perintah atau None kalau waktu habis."""
    if wait is None:
        wait = POLL_WAIT
    key = (name or "HP").strip().lower()
    with _lock:
        _phones[key] = {"name": (name or "HP").strip()[:60], "caps": list(caps or []),
                        "kind": kind or "hp", "last_seen": time.time()}
    deadline = time.monotonic() + max(0.0, wait)
    with _lock:
        while True:
            q = _queues.get(key)
            if q:
                return q.pop(0)
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            _lock.wait(left)


def result(cmd_id, value):
    """HP menyerahkan hasil sebuah perintah."""
    with _lock:
        box = _waiters.get(cmd_id)
    if box is not None:
        box.set(value)
        return True
    return False


def seen(name):
    with _lock:
        if (name or "HP").strip().lower() in _phones:
            _phones[(name or "HP").strip().lower()]["last_seen"] = time.time()


# --- sisi dashboard (pemilik) --------------------------------------------------
def listing():
    now = time.time()
    with _lock:
        return sorted(
            [{"name": p["name"], "caps": p["caps"], "kind": p.get("kind", "hp"),
              "online": (now - p["last_seen"]) < ONLINE_WINDOW,
              "last_seen": p["last_seen"]}
             for p in _phones.values()],
            key=lambda x: x["name"].lower())


def online(name):
    key = (name or "").strip().lower()
    with _lock:
        p = _phones.get(key)
        return bool(p and (time.time() - p["last_seen"]) < ONLINE_WINDOW)


def command(name, ctype, args=None, wait=None):
    """Kirim satu perintah ke HP dan tunggu hasilnya. Kembalikan hasil (dict/bytes)
    atau None kalau HP tak menjawab / tak online."""
    if wait is None:
        wait = RESULT_WAIT
    key = (name or "").strip().lower()
    if not online(key):
        return None
    cmd = {"id": uuid.uuid4().hex[:12], "type": ctype}
    if args:
        cmd.update(args)
    box = _Result()
    with _lock:
        _waiters[cmd["id"]] = box
        _queues.setdefault(key, []).append(cmd)
        _lock.notify_all()
    try:
        return box.wait(wait)
    finally:
        with _lock:
            _waiters.pop(cmd["id"], None)


def forget_stale(max_age=3600):
    now = time.time()
    with _lock:
        for k in [k for k, p in list(_phones.items()) if now - p["last_seen"] > max_age]:
            _phones.pop(k, None)
            _queues.pop(k, None)
