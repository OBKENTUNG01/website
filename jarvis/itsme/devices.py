"""Satu bot, banyak perangkat.

Hub (komputer yang memakai token Telegram) menyimpan daftar perangkat yang
mendaftar. Perangkat (agent) berbicara dengan Hub lewat HTTP memakai secret
bersama (config.HUB_SECRET). Semua koneksi dibuka dari PERANGKAT ke Hub, jadi
perangkat cukup bisa menjangkau dashboard Hub (mis. lewat Tailscale):

- poll    : perangkat menunggu tugas dari Hub (long-poll) sekaligus heartbeat.
- result  : jawaban sebuah tugas dikirim balik ke Hub, lalu ke Telegram.
- message : kabar lepas dari perangkat (tugas latar selesai, akses penuh, ...).
- confirm : perangkat minta tombol Ya/Batal; Hub menampilkannya di Telegram.
- memory  : catatan memori yang dibuat di perangkat disimpan di memori Hub.
- sync    : perangkat mengambil memori, otak, dan mode akses Hub supaya cara
            kerjanya sama persis dengan komputer utama.

Pemilik memilih perangkat tujuan di Telegram dengan /pakai <nama>; pesan
berikutnya dikerjakan perangkat itu sampai /pakai (tanpa nama) dikirim.
"""

import concurrent.futures
import hashlib
import json
import os
import threading
import time
import uuid

import httpx

from . import config
from .memory import Memory

# Perangkat dianggap online kalau kontak terakhir < sekian detik lalu.
ONLINE_WINDOW = 180
# Lama Hub menahan satu permintaan poll kalau belum ada tugas (detik).
POLL_WAIT = 25

_lock = threading.Condition()
_queues = {}  # nama perangkat (huruf kecil) -> list[tugas]
_waiters = {}  # id tugas -> concurrent.futures.Future berisi jawaban


def _file():
    return os.path.join(config.DATA_DIR, "devices.json")


def _target_file():
    return os.path.join(config.DATA_DIR, "device_target.json")


def _load():
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_file(), "w", encoding="utf-8") as f:
        json.dump(data, f)


def _key(name):
    return (name or "").strip().lower()


# --- sisi Hub: daftar perangkat -------------------------------------------------------
def register(name, url):
    """Catat sebuah perangkat (dipanggil saat agent mendaftar atau poll).

    Kembalikan True kalau perangkat ini BARU (belum pernah terdaftar), supaya
    Hub bisa mengabari owner.
    """
    name = (name or "perangkat").strip()[:60]
    with _lock:
        devices = _load()
        key = _key(name)
        is_new = key not in devices
        devices[key] = {"name": name, "url": url, "last_seen": time.time()}
        _save(devices)
    return is_new


def listing():
    now = time.time()
    items = []
    for d in _load().values():
        items.append({
            "name": d.get("name", "?"),
            "url": d.get("url", ""),
            "online": (now - float(d.get("last_seen", 0))) < ONLINE_WINDOW,
            "last_seen": float(d.get("last_seen", 0)),
        })
    return sorted(items, key=lambda x: (x["name"] or "").lower())


def find(name):
    """Perangkat dengan nama itu (tak peduli huruf; awalan yang unik juga boleh)."""
    key = _key(name)
    if not key:
        return None
    items = listing()
    exact = [d for d in items if d["name"].lower() == key]
    if exact:
        return exact[0]
    prefix = [d for d in items if d["name"].lower().startswith(key)]
    return prefix[0] if len(prefix) == 1 else None


def forget(name):
    with _lock:
        devices = _load()
        if devices.pop(_key(name), None) is None:
            return False
        _save(devices)
        _queues.pop(_key(name), None)
    if _key(get_target()) == _key(name):
        set_target(None)
    return True


# --- sisi Hub: perangkat tujuan pesan Telegram ----------------------------------------
def get_target():
    """Nama perangkat tujuan pesan pemilik, atau None (= komputer ini)."""
    try:
        with open(_target_file(), encoding="utf-8") as f:
            return json.load(f).get("name") or None
    except (OSError, ValueError, AttributeError):
        return None


def set_target(name):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_target_file(), "w", encoding="utf-8") as f:
        json.dump({"name": name or None}, f)


# --- sisi Hub: antrean tugas ----------------------------------------------------------
def enqueue(name, text):
    """Titipkan pesan untuk perangkat. Kembalikan (id, future jawaban)."""
    task = {"id": uuid.uuid4().hex[:12], "text": text}
    future = concurrent.futures.Future()
    with _lock:
        _waiters[task["id"]] = future
        _queues.setdefault(_key(name), []).append(task)
        _lock.notify_all()
    return task["id"], future


def stop_waiting(task_id):
    """Berhenti menunggu jawaban (mis. habis waktu). Tugasnya tetap dikerjakan;
    jawaban yang datang belakangan dikirim langsung ke pemilik."""
    with _lock:
        _waiters.pop(task_id, None)


def take(name, wait=POLL_WAIT):
    """Ambil tugas berikutnya untuk perangkat; tunggu paling lama `wait` detik."""
    key = _key(name)
    deadline = time.monotonic() + max(0.0, wait)
    with _lock:
        while True:
            queue = _queues.get(key)
            if queue:
                return queue.pop(0)
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            _lock.wait(left)


def resolve(task_id, text):
    """Serahkan jawaban perangkat ke yang menunggu. False kalau tak ada yang menunggu."""
    with _lock:
        future = _waiters.pop(task_id, None)
    if future is None or future.done():
        return False
    future.set_result(text)
    return True


def sync_bundle(app):
    """Hal yang harus sama di semua perangkat, beserta sidik jarinya."""
    from . import access, brain, keys, node

    bundle = {
        "memory": app.memory.text(),
        "brain": brain.current_backend(),
        "full_access": access.full(),
        # Isi .env yang ditanam saat /pasang: kalau Hub mengganti token bot, pemilik,
        # kata sandi, atau daftar tamu, perangkat menyusul sendiri — tidak perlu
        # dipasang ulang. Nilai khusus komputer (folder, alamat) tidak ikut.
        "env": node.invite_env(keys.ENV_FILE),
    }
    raw = json.dumps(bundle, sort_keys=True).encode("utf-8")
    bundle["version"] = hashlib.sha1(raw).hexdigest()[:12]
    return bundle


# --- sisi Perangkat (agent) -----------------------------------------------------------
def _hub_post(path, payload, timeout=15):
    """Kirim ke Hub; kembalikan isi JSON-nya, atau None kalau gagal."""
    if not (config.HUB_URL and config.HUB_SECRET):
        return None
    body = {"name": config.DEVICE_NAME, "secret": config.HUB_SECRET, **payload}
    try:
        r = httpx.post(
            config.HUB_URL + path,
            json=body,
            headers={"X-Itsme-Dashboard": "1"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def register_to_hub():
    """Daftar/heartbeat ke Hub. Kembalikan True kalau berhasil."""
    return _hub_post("/api/devices/register", {"port": config.DASHBOARD_PORT}, timeout=10) is not None


def poll_hub():
    """Tunggu tugas dari Hub (sekaligus heartbeat). None kalau Hub tak terjangkau."""
    return _hub_post(
        "/api/devices/poll", {"port": config.DASHBOARD_PORT, "wait": POLL_WAIT}, timeout=POLL_WAIT + 15
    )


def send_result(task_id, text, tries=3):
    for _ in range(tries):
        if _hub_post("/api/devices/result", {"id": task_id, "text": text}) is not None:
            return True
        time.sleep(2)
    return False


def send_message(text):
    return _hub_post("/api/devices/message", {"text": text}) is not None


def ask_confirm(text):
    """Minta tombol Ya/Batal ke pemilik lewat Hub. Gagal/tak terjangkau = batal."""
    data = _hub_post("/api/devices/confirm", {"text": text}, timeout=360)
    return bool(data and data.get("yes"))


def fetch_sync():
    return _hub_post("/api/devices/sync", {})


class HubMemory(Memory):
    """Memori perangkat: salinan memori Hub. Catatan baru juga dikirim ke Hub,
    jadi semua komputer berbagi satu memori jangka panjang."""

    def add(self, note):
        result = super().add(note)
        if (note or "").strip():
            _hub_post("/api/devices/memory", {"action": "add", "text": note})
        return result

    def clear(self):
        super().clear()
        _hub_post("/api/devices/memory", {"action": "clear"})

    def set(self, text):
        result = super().set(text)
        _hub_post("/api/devices/memory", {"action": "set", "text": text})
        return result

    def replace_from_hub(self, text):
        """Timpa salinan lokal dengan isi Hub (tanpa mengirim balik)."""
        Memory.set(self, text)
