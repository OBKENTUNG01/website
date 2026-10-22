"""Ikut pemimpin It's Me Node: bot Telegram hanya aktif di komputer pemimpin.

Kalau It's Me Node (https://github.com/OBKENTUNG01/itsme-node) terpasang, It's Me
dipasang di beberapa komputer dengan token Telegram yang sama, tapi hanya komputer
yang sedang menjadi PEMIMPIN jaringan node yang mengambil pesan Telegram. Komputer
lain siaga. Kalau pemimpin mati, node memilih pemimpin baru dan It's Me di komputer
itu otomatis aktif, jadi bot tidak bergantung pada satu komputer inti. Memori, otak
yang dipilih, dan mode akses disalin node dari pemimpin ke komputer lain.

Tanpa node (atau ITSME_FOLLOW_NODE=0), It's Me jalan seperti biasa.
"""

import json
import os
import time
import urllib.parse
import urllib.request

from . import config

# Halaman layar & berkas semua komputer (disajikan node, hanya dari komputer ini).
VIEWER_URL = config.NODE_URL + "/"

ACTIVE, STANDBY = "aktif", "siaga"
# Node terpasang tapi tidak menjawab selama ini (detik): anggap tidak ada, supaya
# bot tidak ikut mati kalau node rusak atau dicopot.
GRACE = 60.0
# Bot aktif baru mundur kalau node menyatakan "bukan pemimpin" selama ini (detik),
# supaya jeda sesaat tidak membuat bot berpindah-pindah.
STEP_DOWN_AFTER = 12.0
# Node bisa baru dipasang saat It's Me sedang jalan: periksa lagi sekian detik sekali.
RECHECK = 60.0


def _node_config():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ItsMeNode", "data", "config.json")


def installed():
    return os.path.exists(_node_config())


def status(timeout=3):
    """Status node di komputer ini, atau None kalau tidak terjangkau."""
    try:
        with urllib.request.urlopen(config.NODE_URL + "/status", timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def role_of(st):
    """Peran It's Me menurut status node: aktif hanya di komputer pemimpin."""
    if st.get("phase") != "running" or not st.get("leader"):
        return STANDBY  # pemimpin sedang dipilih
    return ACTIVE if st["leader"] == st.get("me") else STANDBY


def leader_name(st):
    for n in st.get("nodes") or []:
        if n.get("id") == st.get("leader"):
            return n.get("name")
    return None


# --- akses komputer lain lewat node di komputer ini (layar & berkas) ---
def computers():
    """Daftar komputer yang terhubung (dari node di komputer ini)."""
    return (status() or {}).get("nodes", [])


def resolve(name):
    """Komputer dengan nama itu (persis, lalu awalan unik), atau None."""
    name = (name or "").strip().lower()
    if not name:
        return None
    nodes = computers()
    exact = [n for n in nodes if (n.get("name") or "").lower() == name]
    if exact:
        return exact[0]
    pre = [n for n in nodes if (n.get("name") or "").lower().startswith(name)]
    return pre[0] if len(pre) == 1 else None


def screenshot(node_id=None, w=1280, timeout=30):
    """PNG layar sebuah komputer (lewat node di sini). None kalau gagal."""
    url = f"{config.NODE_URL}/screen?w={int(w)}"
    if node_id:
        url += "&node=" + urllib.parse.quote(node_id)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.headers.get("Content-Type", "").startswith("image/"):
                return r.read()
    except OSError:
        pass
    return None


class Follower:
    """Memutuskan apakah It's Me di komputer ini boleh memakai Telegram."""

    def __init__(self):
        self.enabled = config.FOLLOW_NODE and installed()
        self.leader = None  # nama komputer pemimpin terakhir yang diketahui
        self._checked = time.monotonic()
        self._unreachable_since = None
        self._standby_since = None

    def decide(self, current):
        """Peran berikutnya, mengingat peran sekarang (ACTIVE/STANDBY)."""
        now = time.monotonic()
        if not self.enabled:
            if not config.FOLLOW_NODE or now - self._checked < RECHECK:
                return ACTIVE
            self._checked = now
            self.enabled = installed()
            if not self.enabled:
                return ACTIVE
        st = status()
        if st is None:
            self._standby_since = None
            if self._unreachable_since is None:
                self._unreachable_since = now
            # Node sedang menyala ulang: tetap pada peran sekarang. Kalau terlalu
            # lama, anggap node tidak ada dan bot tetap jalan.
            if current == STANDBY and now - self._unreachable_since >= GRACE:
                return ACTIVE
            return current
        self._unreachable_since = None
        self.leader = leader_name(st) or self.leader
        role = role_of(st)
        if current == ACTIVE and (st.get("phase") != "running" or not st.get("leader")):
            # Pemilihan sedang berjalan (mis. node baru dipasang): tunggu hasilnya,
            # jangan berhenti hanya karena belum ada pemimpin.
            self._standby_since = None
            return ACTIVE
        if role == STANDBY and current == ACTIVE:
            if self._standby_since is None:
                self._standby_since = now
            return STANDBY if now - self._standby_since >= STEP_DOWN_AFTER else ACTIVE
        self._standby_since = None
        return role

    def first_role(self, wait=GRACE, every=2.0):
        """Peran saat It's Me baru menyala. Kalau node masih memilih pemimpin
        (mis. komputer baru menyala), tunggu hasilnya paling lama `wait` detik."""
        if not self.enabled:
            return ACTIVE
        deadline = time.monotonic() + wait
        while True:
            st = status()
            if st is not None:
                self.leader = leader_name(st) or self.leader
                if st.get("phase") == "running" and st.get("leader"):
                    return role_of(st)
            if time.monotonic() >= deadline:
                return ACTIVE
            time.sleep(every)


# --- /pasang: undangan sekali pakai untuk komputer baru ------------------------
INSTALL_URL = "https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1"
INVITE_TTL = 30 * 60
# Isi .env yang khusus komputer ini (folder, alamat, perangkat keras): tidak ikut
# disalin ke komputer baru.
_LOCAL_ONLY = {
    "ITSME_DATA_DIR", "ITSME_DEVICE_NAME", "ITSME_DRIVE_CREDENTIALS", "ITSME_DRIVE_DOWNLOAD_DIR",
    "ITSME_FOLLOW_NODE", "ITSME_HUB_URL", "ITSME_HUB_SECRET", "ITSME_HUB_HEARTBEAT",
    "ITSME_LOG_FILE", "ITSME_MEMORY_FILE", "ITSME_NODE_URL", "ITSME_OLLAMA_MODEL",
    "ITSME_OLLAMA_NUM_CTX", "ITSME_OLLAMA_NUM_GPU", "OLLAMA_URL", "WEBSITE_REPO_DIR",
}
_CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # tanpa huruf/angka yang mirip


def new_invite_code():
    import secrets

    raw = "".join(secrets.choice(_CODE_CHARS) for _ in range(12))
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


def invite_env(env_file):
    """Isi .env bot untuk komputer baru (token bot, pemilik, kunci otak, ...)."""
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(env_file).items()
            if v and k not in _LOCAL_ONLY and "\n" not in v}


def create_invite(code, env, ttl=INVITE_TTL, timeout=5):
    """Titipkan undangan ke node di komputer ini. True kalau diterima."""
    body = json.dumps({"code": code, "ttl": ttl, "env": env}).encode()
    req = urllib.request.Request(config.NODE_URL + "/invite-new", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(json.loads(r.read()).get("ok"))
    except (OSError, ValueError):
        return False


def hub_public_url():
    """Alamat publik dashboard Hub tempat komputer baru menukar undangan & menyambung
    (tanpa Tailscale). Dari ITSME_PUBLIC_URL, atau domain pertama ITSME_DASHBOARD_DOMAINS
    (https). Kosong = Hub belum punya alamat publik."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL.rstrip("/")
    first = next((d.strip() for d in config.DASHBOARD_DOMAINS.split(",") if d.strip()), "")
    return ("https://" + first).rstrip("/") if first else ""


def install_command(code, hub_url):
    """Satu baris pemasang: komputer baru menukar kode undangan di dashboard Hub
    (hub_url) lalu menyambung KELUAR ke sana (bridge.py), tanpa Tailscale."""
    return f"$env:ITSME_INVITE='{code}'; $env:ITSME_HUB='{hub_url}'; irm {INSTALL_URL} | iex"
