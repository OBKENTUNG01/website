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
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

from . import config, hp

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


def wake(name, timeout=10):
    """Minta jaringan membangunkan komputer bernama `name` lewat Wake-on-LAN.

    Coba dulu node di komputer ini (yang otomatis juga menanyai tetangga LAN-nya
    sendiri - lihat itsme-node server.py /wake). Kalau tak ada yang pernah melihat
    MAC-nya langsung di sana, coba tiap komputer JEMBATAN yang sedang online - salah
    satunya mungkin satu lokasi fisik dengan `name` (lihat bridge.py, hp.py). Kembalikan
    dict {"ok":..,...}, atau None kalau node di komputer ini sendiri tidak terpasang."""
    if not installed():
        return None
    body = json.dumps({"name": name}).encode()
    req = urllib.request.Request(config.NODE_URL + "/wake", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except (OSError, ValueError):
        res = {"ok": False, "error": "node di komputer ini tidak menjawab"}
    if res.get("ok"):
        return res
    for p in hp.listing():
        if p.get("kind") != "computer" or not p.get("online"):
            continue
        if (p.get("name") or "").strip().lower() == name.strip().lower():
            continue  # jangan minta komputer itu sendiri membangunkan dirinya
        via_bridge = hp.command(p["name"], "wol", {"target": name})
        if via_bridge and via_bridge.get("ok"):
            return via_bridge
    return res


# Komputer yang sudah bergabung SEBELUM perbaikan "seed" di invite()
# tidak punya titik awal gosip sama sekali - diam-diam gagal-pindah-pemimpin,
# sinkron memori/otak/akses, dan pembagian riset tidak pernah jalan di antara
# mereka. Titipkan alamat LAN node ini lewat jembatan yang SUDAH tersambung
# (hp.py) supaya ikut diperbaiki otomatis, tanpa harus /pasang ulang manual
# (lihat itsme-node bridge.py "seed"). nama->sudah pernah dititipi, per proses
# (aman dititipi ulang; ini cuma supaya tidak berulang-ulang tiap kali dipanggil).
_seeded_bridges = set()


def push_seed_to_bridges():
    """Titipkan alamat LAN node ini ke tiap komputer jembatan yang online dan
    belum pernah dititipi di proses ini. Aman dipanggil berkali-kali/berkala."""
    if not installed():
        return
    addr = (status() or {}).get("addr")
    if not addr:
        return
    for p in hp.listing():
        if p.get("kind") != "computer" or not p.get("online"):
            continue
        key = (p.get("name") or "").strip().lower()
        if not key or key in _seeded_bridges:
            continue
        _seeded_bridges.add(key)
        hp.command(p["name"], "seed", {"addr": addr}, wait=0)


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
# /pasang_tetap: satu tautan dipakai ulang untuk beberapa komputer (reusable=True
# di create_invite), tapi tetap dibatasi waktu - bukan rahasia abadi (cocokkan
# dengan MAX_REUSABLE_TTL di itsme-node/inode/invites.py).
REUSABLE_INVITE_TTL = 30 * 24 * 60 * 60
# Isi .env yang khusus komputer ini (folder, alamat, perangkat keras): tidak ikut
# disalin ke komputer baru.
_LOCAL_ONLY = {
    "ITSME_DATA_DIR", "ITSME_DEVICE_NAME", "ITSME_DRIVE_CREDENTIALS", "ITSME_DRIVE_DOWNLOAD_DIR",
    "ITSME_FOLLOW_NODE", "ITSME_HUB_URL", "ITSME_HUB_SECRET", "ITSME_HUB_HEARTBEAT",
    "ITSME_LOG_FILE", "ITSME_MEMORY_FILE", "ITSME_NODE_URL", "ITSME_OLLAMA_MODEL",
    "ITSME_OLLAMA_NUM_CTX", "ITSME_OLLAMA_NUM_GPU", "OLLAMA_URL", "WEBSITE_REPO_DIR",
}
# Rahasia yang HANYA hidup di komputer yang memegang bot (Hub), atas permintaan pemilik: key NusaGate/
# DeepSeek tidak disalin ke komputer baru dan tidak diselaraskan ke perangkat. Perangkat yang sudah
# terlanjur menyimpannya dikosongkan lewat penyelarasan (managed_keys menyebutnya tanpa nilai).
# Akibatnya: komputer lain yang menjadi pemimpin (failover) harus dipasangi key sendiri lewat /kunci.
HUB_ONLY_SECRETS = {"DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS"}
_CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # tanpa huruf/angka yang mirip


def new_invite_code():
    import secrets

    raw = "".join(secrets.choice(_CODE_CHARS) for _ in range(12))
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


def invite_env(env_file):
    """Isi .env bot untuk komputer baru (token bot, pemilik, kunci otak, ...). Tanpa nilai khusus komputer
    (_LOCAL_ONLY) dan tanpa rahasia khusus-Hub (HUB_ONLY_SECRETS: key NusaGate/DeepSeek)."""
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(env_file).items()
            if v and k not in _LOCAL_ONLY and k not in HUB_ONLY_SECRETS and "\n" not in v}


def managed_keys(env_file):
    """Nama kunci .env yang dikelola Hub (termasuk yang dikosongkan), untuk penyelarasan ke perangkat: perangkat
    mengosongkan kunci yang disebut di sini tapi tidak ikut dikirim nilainya.

    - Nilai khusus komputer (_LOCAL_ONLY) TIDAK disebut: perangkat tidak boleh mengosongkan miliknya sendiri
      (nama komputer, alamat Hub, ...) hanya karena Hub kebetulan punya baris dengan nama yang sama.
    - Rahasia khusus-Hub (HUB_ONLY_SECRETS) SELALU disebut, walau Hub tak punya barisnya: salinan lama di
      perangkat (dari penyalinan seluruh .env di masa lalu) ikut dibersihkan."""
    from dotenv import dotenv_values

    names = {k for k, v in dotenv_values(env_file).items() if v is not None and k not in _LOCAL_ONLY}
    return sorted(names | HUB_ONLY_SECRETS)


def create_invite(code, env, ttl=INVITE_TTL, timeout=5, reusable=False):
    """Titipkan undangan ke node di komputer ini. True kalau diterima.

    reusable=True: kode TIDAK hangus dipakai (bisa dipakai beberapa komputer
    dengan tautan yang sama - /pasang_tetap), tapi tetap kedaluwarsa sendiri."""
    body = json.dumps({"code": code, "ttl": ttl, "env": env, "reusable": bool(reusable)}).encode()
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


# --- Ollama per komputer: status, pasang, copot lewat node (GET/POST /ollama) ----------
# Node di komputer ini meneruskan permintaan ke komputer tujuan (?node=<id>). Pasang/copot
# berjalan sebagai pekerjaan LATAR di komputer tujuan (bisa makan beberapa menit, sedangkan
# node hanya menunggu jawaban komputer lain ~25 detik): bot memulainya dengan ollama_start,
# lalu memantaunya dengan ollama_wait. Semua fungsi di bawah TIDAK melempar karena jaringan.
OLLAMA_ACTIONS = ("install", "uninstall")
OLLAMA_WAIT = 150.0  # detik: batas bawaan ollama_wait menunggu satu pekerjaan
_NO_ROUTE = "tidak ada"  # jawaban 404 node yang belum punya rute /ollama (versi lama)
_OLD_NODE = ("It's Me Node-nya versi lama dan belum bisa mengontrol Ollama - perbarui It's Me Node "
             "(di komputer ini dan di komputer tujuan)")


def _node_call(method, path, payload=None, timeout=10):
    """Satu panggilan JSON ke node di komputer ini: (kode HTTP, dict).

    Node tak terjangkau / jawabannya rusak -> (0, {"error": ...}) atau (kode, {"error": ...}).
    Isi jawaban galat HTTP (mis. 409) ikut dibaca: di situlah node menaruh alasannya."""
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(config.NODE_URL + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
    except (OSError, HTTPException, ValueError) as exc:
        return 0, {"error": f"node di komputer ini tidak menjawab ({type(exc).__name__})"}
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = None
    return code, body if isinstance(body, dict) else {"error": "jawaban node tidak dikenali"}


def _ollama_path(node_id):
    return "/ollama" + ("?" + urllib.parse.urlencode({"node": node_id}) if node_id else "")


def _node_status(timeout=3):
    """/status node di komputer ini sebagai dict, atau None. Tidak pernah melempar (juga untuk
    jawaban yang terpotong - yang tidak ditangkap status())."""
    code, res = _node_call("GET", "/status", timeout=timeout)
    return res if code == 200 and "error" not in res else None


def _clean_name(value, limit=60):
    """Nama komputer dari gosip (tak tepercaya): selalu teks SATU baris dan dipotong - dipakai di kartu
    persetujuan ("Copot Ollama dari ..."), jadi tak boleh menyelipkan baris palsu atau melempar galat."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value if value is not None else "")).strip()
    return text[:limit]


def ollama_fleet(st=None):
    """Komputer yang dikenal node di sini beserta status Ollama-nya, dari /status (Ollama ikut
    gosip antar-node): [{"id","name","online","asleep","installed","version","job"}].

    installed: True/False, atau None kalau tidak diketahui (node komputer itu versi lama yang
    belum melapor, atau komputernya sedang tidur). Komputer yang pernah satu LAN dengan node ini
    ("known") tapi tidak ikut gosip sekarang ikut sebagai asleep (id None - tak bisa dikontrol).
    `st` = hasil /status yang sudah dibaca (opsional). [] kalau node di sini tidak terjangkau."""
    st = _node_status() if st is None else st
    if not isinstance(st, dict):
        return []
    nodes, known = st.get("nodes"), st.get("known")
    out, seen = [], set()
    for n in nodes if isinstance(nodes, list) else []:
        if not isinstance(n, dict):
            continue
        info = n.get("ollama") if isinstance(n.get("ollama"), dict) else {}
        asleep = bool(n.get("asleep"))
        reported = not asleep and info.get("installed") is not None  # komputer itu melaporkan Ollama-nya
        out.append({"id": n.get("id"), "name": _clean_name(n.get("name")) or "?",
                    "online": not asleep and n.get("id") is not None, "asleep": asleep,
                    "installed": bool(info["installed"]) if reported else None,
                    "version": (info.get("version") or None) if reported else None,
                    "job": info.get("job") if reported and isinstance(info.get("job"), dict) else None})
        seen.add(_clean_name(n.get("name")).lower())
    for k in known if isinstance(known, list) else []:
        name = _clean_name(k.get("name") if isinstance(k, dict) else None)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"id": None, "name": name, "online": False, "asleep": True,
                        "installed": None, "version": None, "job": None})
    return out


def ollama_overview(timeout=3):
    """(status node, ollama_fleet) dari satu pembacaan /status; (None, []) kalau node di komputer
    ini tidak terjangkau. Status itu dipakai alat untuk tahu id komputer ini (st["me"])."""
    st = _node_status(timeout)
    return st, (ollama_fleet(st) if st is not None else [])


def pick_computer(items, name):
    """Cari komputer bernama `name` di `items` (dict ber-"name"): nama persis (huruf besar-kecil
    tak dibedakan), lalu awalan nama yang unik, lalu potongan nama yang unik.

    Kembalikan (item, kandidat): item kalau cocok tepat satu; selain itu (None, kandidat) -
    kandidat kosong kalau tak ada yang cocok, berisi beberapa komputer kalau ambigu."""
    key = (name or "").strip().lower()
    if not key:
        return None, []
    for matches in (lambda n: n == key, lambda n: n.startswith(key), lambda n: key in n):
        hits = [c for c in items if matches((c.get("name") or "").lower())]
        if hits:
            return (hits[0] if len(hits) == 1 else None), hits
    return None, []


def ollama_status(node_id=None, timeout=30):
    """Status Ollama sebuah komputer (node_id; kosong = komputer ini):
    {"supported","installed","version","path","leftover","running","models_kept","job"}.

    Node di sini meneruskan pertanyaannya ke komputer itu, jadi jawabannya SEGAR (bukan gosip).
    None kalau node tak menjawab, komputernya tak ditemukan/mati, atau node di sana versi lama
    yang belum punya rute /ollama."""
    code, res = _node_call("GET", _ollama_path(node_id), timeout=timeout)
    return res if code == 200 and "error" not in res else None


def _ollama_refusal(node_id, code, res):
    """Penolakan mentah dari node -> {"ok": False, "error": <alasan yang bisa dibaca>[, "job"]}.

    Kalau komputer tujuan menolak (409: sedang ada pekerjaan / bukan Windows; 404: node lama),
    node di sini hanya meneruskannya sebagai 502 "tak terjawab" - alasan sebenarnya dicari lewat
    status komputer itu."""
    out = {"ok": False, "error": res.get("error") or f"ditolak node (HTTP {code})"}
    if isinstance(res.get("job"), dict):
        out["job"] = res["job"]
    if code == 404 and res.get("error") == _NO_ROUTE:
        out["error"] = _OLD_NODE
    elif code == 502:
        out["error"] = "komputer tujuan menolak atau tidak menjawab"
        code2, st = _node_call("GET", _ollama_path(node_id), timeout=30)
        job = st.get("job") if code2 == 200 else None
        if isinstance(job, dict) and job.get("state") == "running":
            out["error"] = f"masih ada pekerjaan '{job.get('action')}' yang berjalan di komputer itu"
            out["job"] = job
        elif code2 == 200 and st.get("supported") is False:
            out["error"] = "pasang/copot Ollama otomatis baru didukung di Windows (komputer itu bukan Windows)"
        elif code2 == 404 and st.get("error") == _NO_ROUTE:
            out["error"] = _OLD_NODE
        elif code2 != 200:
            out["error"] = "komputer tujuan tidak menjawab (mati atau terputus dari jaringan node)"
    return out


def ollama_start(node_id, action, timeout=30):
    """Mulai pekerjaan "install" / "uninstall" Ollama di komputer `node_id` (kosong = komputer ini).

    Kembalikan (kode HTTP, dict). dict["ok"] selalu ada: True -> {"ok": True, "job": {...}} dan
    pekerjaannya berjalan di latar (pantau dengan ollama_wait); False -> dict["error"] berisi
    alasan yang bisa dibaca (dan dict["job"] kalau sudah ada pekerjaan lain yang berjalan di sana).
    Kode 0 = node di komputer ini tak terjangkau."""
    if action not in OLLAMA_ACTIONS:
        return 0, {"ok": False, "error": f"aksi tidak dikenal: {action!r} (pilihan: install, uninstall)"}
    payload = {"action": action}
    if node_id:
        payload["node"] = node_id
    code, res = _node_call("POST", "/ollama", payload, timeout)
    if code == 200 and res.get("ok"):
        return code, res
    return code, _ollama_refusal(node_id, code, res)


def ollama_wait(node_id, timeout=OLLAMA_WAIT, every=2.0):
    """Tunggu pekerjaan pasang/copot di komputer `node_id` selesai (poll GET /ollama tiap `every`
    detik, paling lama `timeout` detik).

    Kembalikan pekerjaan terakhir yang dilaporkan komputer itu: {"action","state","step","started",
    "ended","message","log"} dengan state "done" / "failed" (selesai; hasilnya di "message") atau
    "running" (waktu habis, pekerjaannya masih berjalan). Kalau tak ada pekerjaan yang bisa dibaca
    (node tak menjawab, versi lama, atau komputer itu tak mencatat pekerjaan apa pun):
    {"state": "unknown", "message": <sebabnya>}. Gangguan sesaat (jaringan putus, komputer yang
    sibuk memasang sampai terlambat bergosip) tidak menghentikan pemantauan."""
    deadline = time.monotonic() + timeout
    last, why = None, "belum ada jawaban"
    while True:
        left = deadline - time.monotonic()
        code, res = _node_call("GET", _ollama_path(node_id), timeout=max(3.0, min(30.0, left)))
        if code == 200 and "error" not in res:
            job = res.get("job")
            if not isinstance(job, dict):
                return {"state": "unknown", "message": "komputer itu tidak melaporkan pekerjaan apa pun "
                        "(node-nya mungkin baru menyala ulang)"}
            last = job
            if job.get("state") != "running":
                return job
        elif res.get("error") == _NO_ROUTE or code in (401, 403):  # pasti bukan gangguan sesaat
            return {"state": "unknown",
                    "message": _OLD_NODE if res.get("error") == _NO_ROUTE else f"ditolak node (HTTP {code})"}
        else:
            why = res.get("error") or f"HTTP {code}"
        left = deadline - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(every, left))
    return last or {"state": "unknown", "message": f"pekerjaannya tidak bisa dibaca dari komputer itu: {why}"}
