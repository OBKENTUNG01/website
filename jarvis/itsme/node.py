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
import urllib.request

from . import config

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
