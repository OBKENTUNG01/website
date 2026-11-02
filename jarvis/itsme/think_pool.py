"""Kumpulan komputer yang berpikir untuk bot ("otak lokal bersama").

Bot bicara dengan It's Me Node di komputernya sendiri (config.NODE_URL); node itulah yang meneruskan
permintaan ke komputer lain (rute /think, bertanda tangan, lihat itsme-node). Modul ini memutuskan
KOMPUTER MANA yang berpikir untuk tiap permintaan:

- semua komputer yang punya model lokal dipakai: yang cepat & lowong dulu, beban tersebar karena
  permintaan yang sedang berjalan ikut dihitung sebelum kabar gosip (2 detik) sempat datang;
- yang gagal / sibuk / sedang memilih pemimpin diistirahatkan sebentar dan permintaan yang SAMA
  langsung dicoba di komputer lain; kalau semuanya sibuk, ditunggu (sabar, bukan menyerah);
- komputer yang modelnya SELALU berpikir (penalaran Inggris bocor ke jawaban) hanya dipakai kalau
  tak ada pilihan lain, dan dipanggil dengan mode berpikir resmi supaya penalarannya terpisah.

Pemimpin mati -> semua komputer berhenti berpikir sampai pemimpin baru terdengar (rancangan node);
selama itu `ask` menunggu, lalu lanjut sendiri begitu mereka pulih.
"""

import logging
import math
import re
import threading
import time
from collections import namedtuple

import httpx

from . import config, node

log = logging.getLogger("itsme")

STATUS_TTL = 2.0   # detik status node disimpan, supaya banyak permintaan tak membanjiri node
# Istirahat (detik) komputer yang baru gagal, menurut jenis kegagalan (lihat itsme-node thinker.py).
COOLDOWN = {"busy": 6.0, "paused": 5.0, "timeout": 60.0, "unreachable": 45.0, "unavailable": 45.0,
            "crashed": 120.0, "gone": 30.0, "bad": 20.0, "no_model": 600.0, "disabled": 600.0, "error": 45.0}
_WHY = {
    "busy": "sedang sibuk", "paused": "sedang memilih pemimpin", "timeout": "terlalu lama menjawab",
    "unreachable": "tidak terjangkau", "unavailable": "Ollama-nya mati", "crashed": "modelnya terus berhenti mendadak",
    "gone": "sudah tidak ada di jaringan", "bad": "menolak permintaan", "no_model": "belum punya model",
    "disabled": "berpikir dimatikan", "error": "error",
}

Reply = namedtuple("Reply", "message text thinking node node_id model ms tps")


class ClusterUnavailable(Exception):
    """Tidak ada komputer yang bisa berpikir sekarang; pesannya siap dibaca pemilik."""


_lock = threading.RLock()
_clock = time.monotonic
_sleep = time.sleep
_local = threading.local()   # anggaran waktu per thread (lihat budget)


class budget:
    """Batas waktu TOTAL untuk semua ask() di thread ini selama blok `with`. Dipakai jawaban untuk tamu: yang
    menunggu cuma beberapa menit tidak boleh menyita komputer lebih lama dari itu. Waktu habis -> ask()
    melempar ClusterUnavailable dan panggilan yang sedang berjalan dibatasi sisa waktunya (Ollama berhenti
    menghasilkan begitu koneksinya putus)."""

    def __init__(self, seconds):
        self.seconds = seconds

    def __enter__(self):
        self.previous = getattr(_local, "deadline", None)
        end = _clock() + self.seconds
        _local.deadline = end if self.previous is None else min(self.previous, end)
        return self

    def __exit__(self, *exc):
        _local.deadline = self.previous
        return False


def _remaining():
    end = getattr(_local, "deadline", None)
    return None if end is None else end - _clock()
_snap = {"at": -1e9, "rows": [], "reachable": False, "phase": None, "leader": None}
_cool = {}       # id komputer -> (monotonik sampai kapan diistirahatkan)
_reserved = {}   # id komputer -> jumlah permintaan bot yang sedang berjalan ke sana
_last_error = {}  # id komputer -> teks kegagalan terakhir (untuk /bersama_status)


def reset():
    """Kosongkan ingatan (uji)."""
    with _lock:
        _snap.update(at=-1e9, rows=[], reachable=False, phase=None, leader=None)
        _cool.clear()
        _reserved.clear()
        _last_error.clear()


def size_b(model):
    """Ukuran model dalam miliar parameter dari namanya ('qwen3:4b-instruct...' -> 4, 'qwen3:1.7b' -> 1.7)."""
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)b(?![a-z])", (model or "").split(":", 1)[-1].lower())
    return float(m.group(1)) if m else 1.0


def installed():
    return node.installed()


def snapshot(force=False, ttl=STATUS_TTL):
    """Daftar komputer + kesiapan berpikirnya menurut node di komputer ini (disimpan `ttl` detik)."""
    now = _clock()
    with _lock:
        if not force and now - _snap["at"] < ttl:
            return list(_snap["rows"])
    st = node.status(timeout=4)
    rows = []
    for n in (st or {}).get("nodes") or []:
        t = n.get("think") or {}
        model = t.get("model") or ""
        rows.append({
            "id": n.get("id"), "name": n.get("name") or "?", "leader": bool(n.get("leader")),
            "has_think": bool(n.get("think")), "model": model, "ok": bool(t.get("ok")) and bool(model),
            "up": bool(t.get("up")), "loaded": bool(t.get("loaded")), "ctl": t.get("ctl"),
            "tps": float(t.get("tps") or 0.0), "inflight": int(t.get("inflight") or 0),
            "waiting": int(t.get("waiting") or 0), "slots": int(t.get("slots") or 1),
            "served": int(t.get("served") or 0), "restarts": int(t.get("restarts") or 0),
            "cpu": bool(t.get("cpu")), "ctx": int(t.get("ctx") or 0), "size_b": size_b(model),
        })
    with _lock:
        _snap.update(at=now, rows=rows, reachable=st is not None, phase=(st or {}).get("phase"),
                     leader=(st or {}).get("leader"))
    return list(rows)


def _load(row):
    with _lock:
        mine = _reserved.get(row["id"], 0)
    return row["inflight"] + row["waiting"] + mine


def score(row, role):
    """Makin besar makin diutamakan. 'fast' (memahami/memeriksa): yang tercepat & lowong.
    'answer': kualitas model (akar ukuran) x kecepatan (akar), dibagi beban."""
    speed = max(row["tps"], 0.5)
    load = _load(row)
    if role == "fast":
        base = speed / (1 + 2 * load)
    else:
        base = math.sqrt(row["size_b"]) * math.sqrt(speed) / (1 + 1.5 * load)
    if row["ctl"] is None:
        base *= 0.6   # 'think:false' belum teruji: boleh dipakai, tapi yang sudah teruji didahulukan
    return base


def candidates(role="answer", exclude=(), rows=None):
    """Komputer yang boleh dipakai sekarang, terbaik dulu. `rows` = hasil snapshot() yang sudah diambil."""
    now = _clock()
    with _lock:
        cool = dict(_cool)
    rows = [r for r in (snapshot() if rows is None else rows)
            if r["ok"] and r["id"] not in exclude and cool.get(r["id"], 0.0) <= now]
    # Komputer yang modelnya SELALU berpikir (ctl False) hanya dipakai kalau tak ada pilihan lain: penalarannya
    # lambat dan gampang melenceng; yang bisa menjawab langsung didahulukan untuk peran apa pun.
    direct = [r for r in rows if r["ctl"] is not False]
    rows = direct or rows
    return sorted(rows, key=lambda r: -score(r, role))


def _claim(role, tried, rows):
    """Pilih komputer terbaik DAN langsung memesannya (beban +1) dalam satu langkah tak terbagi, supaya dua
    permintaan serentak tidak menumpuk di komputer yang sama sebelum kabar gosip sempat berubah."""
    with _lock:
        picked = candidates(role, tried, rows=rows)
        if not picked:
            return None
        _reserved[picked[0]["id"]] = _reserved.get(picked[0]["id"], 0) + 1
        return picked[0]


def ready():
    """Ada komputer yang siap berpikir sekarang (di luar istirahat)?"""
    return bool(candidates("answer"))


def _think_flag(row, wanted):
    if row["ctl"] is False:
        return True   # model selalu berpikir: minta berpikir resmi supaya penalarannya terpisah dari jawaban
    if wanted == "auto":
        return False
    return bool(wanted)


def _post(payload, timeout):
    """POST ke node di komputer ini. Dipisah agar mudah diganti di uji."""
    r = httpx.post(config.NODE_URL + "/think", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _rest(node_id, kind):
    with _lock:
        _cool[node_id] = _clock() + COOLDOWN.get(kind, 45.0)


def _try(row, messages, think, options, tools, fmt, timeout):
    """Satu percobaan di satu komputer yang SUDAH dipesan (_claim); pesanan dilepas di sini.
    Kembalikan Reply, atau jenis kegagalan (teks)."""
    payload = {"node": row["id"], "messages": messages, "timeout": timeout}
    flag = _think_flag(row, think)
    payload["think"] = flag
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
    if fmt:
        payload["format"] = fmt
    started = _clock()
    try:
        remaining = _remaining()
        res = _post(payload, min(timeout + 30, remaining) if remaining is not None else timeout + 30)
    except httpx.HTTPStatusError as exc:
        kind = "paused" if exc.response.status_code == 503 else "error"
        _last_error[row["id"]] = f"HTTP {exc.response.status_code}"
        _rest(row["id"], kind)
        return kind
    except (httpx.HTTPError, ValueError) as exc:
        _last_error[row["id"]] = type(exc).__name__
        _rest(row["id"], "unreachable")
        snapshot(force=True)
        return "node_down"
    finally:
        with _lock:
            _reserved[row["id"]] = max(0, _reserved.get(row["id"], 1) - 1)
    if isinstance(res, dict) and res.get("ok") and isinstance(res.get("message"), dict):
        message = res["message"]
        return Reply(message=message, text=message.get("content") or "", thinking=message.get("thinking") or "",
                     node=res.get("name") or row["name"], node_id=row["id"], model=res.get("model") or row["model"],
                     ms=int(res.get("ms") or (_clock() - started) * 1000), tps=float(res.get("tps") or 0.0))
    kind = (res or {}).get("kind") or "error"
    _last_error[row["id"]] = (res or {}).get("error") or kind
    _rest(row["id"], kind)
    return kind


def ask(messages, *, role="answer", think="auto", options=None, tools=None, fmt=None, timeout=None,
        exclude=(), wait=None):
    """Minta satu giliran berpikir ke komputer terbaik yang lowong. Kembalikan Reply.

    Gagal / sibuk / sedang memilih pemimpin -> komputer lain; kalau semuanya sibuk, menunggu sampai `wait`
    detik (bawaan CLUSTER_WAIT). Batas `wait` hanya untuk mencari komputer/pemimpin yang siap; setelah ada
    komputer terpilih, waktu panggilan model diatur terpisah oleh `timeout`. Melempar ClusterUnavailable
    kalau benar-benar tak ada yang bisa."""
    if not node.installed():
        raise ClusterUnavailable("It's Me Node belum terpasang di komputer ini, jadi belum ada komputer lain "
                                 "yang bisa diajak berpikir. Pasang lewat /pasang.")
    if timeout is None:
        timeout = config.CLUSTER_FAST_TIMEOUT if role == "fast" else config.CLUSTER_TIMEOUT
    deadline = _clock() + (config.CLUSTER_WAIT if wait is None else wait)
    tried, failed = set(exclude), []
    while True:
        left = _remaining()
        if left is not None:
            if left <= 1.0:
                raise ClusterUnavailable("Waktu menunggu jawaban dari komputer-komputer sudah habis.")
            deadline = min(deadline, _clock() + left)
        if _clock() >= deadline:
            electing = _snap["reachable"] and _snap["phase"] not in (None, "running")
            raise ClusterUnavailable(_explain(_snap["rows"], [], electing,
                                              wait_s=config.CLUSTER_WAIT if wait is None else wait))
        snap = snapshot()
        # Pemimpin mati dan pemilihan baru berjalan: SEMUA komputer berhenti berpikir (rancangan node), jadi
        # tidak ada gunanya mencoba - tunggu sampai pemimpin baru terdengar.
        electing = _snap["reachable"] and _snap["phase"] not in (None, "running")
        row = None if electing else _claim(role, tried, snap)
        if row is not None:
            outcome = _try(row, messages, think, options, tools, fmt, timeout if left is None else min(timeout, left))
            if isinstance(outcome, Reply):
                return outcome
            failed.append(f"{row['name']} {_WHY.get(outcome, outcome)}")
            if outcome == "node_down":
                raise ClusterUnavailable("It's Me Node di komputer ini tidak menjawab, jadi daftar komputer "
                                         "yang berpikir tidak bisa dibaca.")
            if outcome not in ("busy", "paused"):
                tried.add(row["id"])   # busy/paused sementara: nanti dicoba lagi
            if _clock() >= deadline:
                raise ClusterUnavailable(_explain(snap, failed, electing, wait_s=config.CLUSTER_WAIT if wait is None else wait))
            if outcome in ("busy", "paused"):
                _sleep(min(2.0, max(0.0, deadline - _clock())))
            continue
        # Tidak ada yang bisa dipakai sekarang. Ada yang hanya sedang beristirahat/sibuk/memilih pemimpin?
        alive = [r for r in snap if r["ok"] and r["id"] not in tried]
        if (alive or electing) and _clock() < deadline:
            _sleep(2.0)
            snapshot(force=True)
            continue
        raise ClusterUnavailable(_explain(snap, failed, electing, wait_s=config.CLUSTER_WAIT if wait is None else wait))


def _explain(rows, failed, electing, wait_s):
    if not _snap["reachable"]:
        return "It's Me Node di komputer ini tidak menjawab, jadi tidak ada komputer yang bisa diajak berpikir."
    if electing:
        return ("Jaringan komputer sedang memilih pemimpin baru, jadi semua komputer berhenti berpikir sebentar. "
                f"Sudah kutunggu {int(wait_s)} detik tanpa hasil; coba lagi sebentar.")
    if failed:
        return "Semua komputer yang kucoba gagal: " + "; ".join(failed[-4:]) + "."
    why = []
    for r in rows:
        if not r["has_think"]:
            why.append(f"{r['name']} belum diperbarui")
        elif not r["model"]:
            why.append(f"{r['name']} belum punya model")
        elif not r["ok"]:
            why.append(f"{r['name']} Ollama-nya belum menyala")
    return "Belum ada komputer yang siap berpikir" + (": " + "; ".join(why) if why else "") + "."


# --- tampilan --------------------------------------------------------------------------------
def describe():
    """Satu baris ringkas. Dibaca dashboard tiap detik, jadi memakai status yang boleh berumur 30 detik."""
    rows = [r for r in snapshot(ttl=30.0) if r["ok"]]
    if not rows:
        return "lokal bersama (belum ada komputer yang siap)"
    parts = ", ".join(f"{r['name']} {r['model']}" for r in sorted(rows, key=lambda r: -r["size_b"])[:4])
    return f"lokal bersama: {len(rows)} komputer berpikir ({parts})"


def report():
    """Baris-baris status untuk pemilik (/bersama_status)."""
    rows = snapshot(force=True)
    if not _snap["reachable"]:
        return ["It's Me Node di komputer ini tidak menjawab (belum terpasang atau sedang menyala ulang)."]
    phase = _snap["phase"]
    lines = []
    if phase != "running":
        lines.append("⏸ Jaringan sedang memilih pemimpin baru: semua komputer berhenti berpikir sebentar, "
                     "lalu lanjut sendiri.")
    ready_n = len([r for r in rows if r["ok"]])
    lines.append(f"{ready_n} dari {len(rows)} komputer siap berpikir.")
    now = _clock()
    for r in sorted(rows, key=lambda r: (not r["ok"], -r["size_b"], r["name"])):
        tag = " ⭐ pemimpin" if r["leader"] else ""
        if r["ok"]:
            bits = [r["model"], f"{r['tps']:.1f} tok/dtk"]
            if r["cpu"]:
                bits.append("CPU saja")
            if r["ctl"] is False:
                bits.append("model selalu berpikir")
            busy = r["inflight"] + r["waiting"]
            bits.append(f"sedang mengerjakan {busy}" if busy else "lowong")
            bits.append(f"sudah melayani {r['served']}x")
            if r["restarts"]:
                bits.append(f"Ollama dinyalakan ulang {r['restarts']}x")
            rest = _cool.get(r["id"], 0.0) - now
            if rest > 0:
                bits.append(f"istirahat {int(rest)} dtk ({_last_error.get(r['id'], '?')})")
            lines.append(f"✅ {r['name']}{tag} — " + " · ".join(bits))
        elif not r["has_think"]:
            lines.append(f"⚠️ {r['name']}{tag} — belum memperbarui It's Me Node (otomatis dalam ≤30 menit)")
        elif not r["model"]:
            lines.append(f"⏸ {r['name']}{tag} — belum punya model (RAM kecil atau Ollama belum terpasang)")
        else:
            lines.append(f"🔧 {r['name']}{tag} — {r['model']}: Ollama belum menyala, sedang dinyalakan lagi")
    return lines
