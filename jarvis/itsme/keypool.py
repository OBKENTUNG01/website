"""Kumpulan API key NusaGate/DeepSeek: SEMUA key dipakai, bergiliran dan merata.

NusaGate membatasi 60 permintaan/menit PER KEY (galat 429 "rate_limit"), dan saldo atau paketnya
juga bisa habis (402, atau 429 "pass_window_cap"). Dengan banyak key, beban dibagi rata; key yang
sedang dibatasi ditinggalkan sebentar tanpa pemilik merasakan kegagalan, sementara key lain
tetap bekerja, dan key yang sudah pulih otomatis ikut lagi.

Aturan memilih (acquire): dari key yang siap, ambil yang SEDANG DIPAKAI paling sedikit, lalu yang
PALING LAMA tidak dipakai. Hasilnya giliran berputar rata, dan permintaan serentak menyebar.
- 401 (ditolak/dicabut): dikeluarkan sejam (atau sampai diperiksa ulang lewat /kunci_tes), lalu dicoba lagi.
- 402 (saldo habis): dikeluarkan 30 menit. 429 batas paket: 20 menit. 429 batas per menit: sesuai
  retry_after dari penyedia. Lalu dicoba lagi sendiri.
- 5xx / timeout / jaringan: hanya diturunkan prioritasnya 20 detik.
- Model: bila ITSME_NUSAGATE_MODELS berisi beberapa model, tiap jawaban memakai model yang paling
  lama menganggur (dan yang ditolak penyedia ditinggalkan 30 menit), jadi tidak ada model yang
  menganggur dan tidak ada yang kebanjiran.

Key tidak pernah dicatat di log atau ditampilkan utuh: keadaan disimpan dengan id hash. Hitungan
pemakaian disimpan di data/keypool.json (id hash saja) supaya tetap ada setelah menyala ulang.
"""

import hashlib
import itertools
import json
import logging
import os
import threading
import time
from collections import Counter

from . import config

log = logging.getLogger("itsme")

_WINDOW = 60.0  # NusaGate: batas per menit
_COOLDOWN = {"balance": 1800.0, "cap": 1200.0}  # detik
_INVALID_COOLDOWN = 3600.0  # key ditolak: dicoba lagi sejam kemudian (galat 401 sesaat tidak mematikannya selamanya)
_LIMIT_DEFAULT, _LIMIT_MIN, _LIMIT_MAX = 30.0, 5.0, 300.0
_PENALTY = 20.0
_MODEL_COOLDOWN = 1800.0
_SAVE_EVERY = 20.0

_lock = threading.RLock()
_clock = time.monotonic  # diganti uji
_wall = time.time
_states = {}
_models = {}
_alert = None  # callback(kind, key_tertutup): dikabarkan sekali saat key jadi tidak bisa dipakai
_loaded = False
_last_save = 0.0
_dirty = False
_since = None
# Urutan giliran: penghitung naik, BUKAN jam. Jam monotonik Windows hanya beresolusi ~15 ms, jadi dua
# permintaan beruntun bisa bercap waktu sama dan key pertama menang terus.
_seq = itertools.count(1)


class PoolExhausted(Exception):
    """Tidak ada key yang bisa dipakai sekarang. `wait` = detik sampai ada yang pulih (atau None)."""

    def __init__(self, message, wait=None, reasons=None):
        super().__init__(message)
        self.wait = wait
        self.reasons = reasons or {}


class Lease:
    __slots__ = ("key", "id")

    def __init__(self, key):
        self.key, self.id = key, key_id(key)


class _KeyState:
    def __init__(self):
        self.uses = 0
        self.errors = 0
        self.inflight = 0
        self.order = 0  # nomor giliran terakhir dipakai (0 = belum pernah): untuk memilih
        self.last_wall = 0.0  # jam dinding: untuk ditampilkan
        self.stamps = []  # waktu tiap percobaan dalam semenit terakhir
        self.until = 0.0  # dikeluarkan sampai (monotonik)
        self.why = ""  # "" | limit | cap | balance | invalid
        self.penalty = 0.0


class _ModelState:
    def __init__(self):
        self.uses = 0
        self.order = 0
        self.until = 0.0


def key_id(key):
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]


def mask(key):
    from . import keys  # impor di sini: keys.py memakai modul ini

    return keys.mask(key)


def all_keys():
    return config.deepseek_keys()


def has_keys():
    return bool(config.deepseek_keys())


def set_alert(callback):
    global _alert
    _alert = callback


def _state(key):
    kid = key_id(key)
    st = _states.get(kid)
    if st is None:
        st = _states[kid] = _KeyState()
        _load_once()
        saved = _saved.get("keys", {}).get(kid)
        if isinstance(saved, dict):
            st.uses = int(saved.get("uses") or 0)
            st.errors = int(saved.get("errors") or 0)
    return st


# --- penyimpanan hitungan ---------------------------------------------------------
_saved = {}


def _file():
    return os.path.join(config.DATA_DIR, "keypool.json")


def _load_once():
    global _loaded, _saved, _since
    if _loaded:
        return
    _loaded = True
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _saved = data
            _since = data.get("since")
    except (OSError, ValueError):
        pass
    if _since is None:
        _since = _wall()


def _save(force=False):
    global _last_save, _dirty
    now = _clock()
    if not force and (not _dirty or now - _last_save < _SAVE_EVERY):
        return
    _last_save, _dirty = now, False
    data = {
        "since": _since,
        "keys": {kid: {"uses": st.uses, "errors": st.errors} for kid, st in _states.items()},
        "models": {name: ms.uses for name, ms in _models.items()},
    }
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = _file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _file())
    except OSError:
        pass


def flush():
    """Simpan hitungan sekarang juga (dipanggil saat berhenti dan di uji)."""
    with _lock:
        _save(force=True)


def reset_all():
    """Kosongkan keadaan di memori (uji)."""
    global _loaded, _saved, _since, _dirty
    with _lock:
        _states.clear()
        _models.clear()
        _loaded, _saved, _since, _dirty = False, {}, None, False


# --- memilih key --------------------------------------------------------------------
_LABELS = {
    "limit": "dibatasi penyedia (60/menit per key)",
    "cap": "batas paket tercapai",
    "balance": "saldo habis",
    "invalid": "ditolak/dicabut",
    "penuh": "sudah penuh menit ini",
}


def _describe(reasons):
    if not reasons:
        return "Belum ada API key DeepSeek/NusaGate."
    parts = [f"{n} key {_LABELS.get(why, why)}" for why, n in reasons.most_common()]
    return "Semua key sedang tidak bisa dipakai: " + ", ".join(parts) + "."


def acquire(exclude=()):
    """Pinjam satu key (Lease). Wajib dipasangkan dengan release(). Melempar PoolExhausted."""
    now = _clock()
    with _lock:
        pool = all_keys()
        if not pool:
            raise PoolExhausted("Belum ada API key DeepSeek/NusaGate. Kirim /kunci <key> lewat Telegram.")
        best, best_rank = None, None
        reasons, waits = Counter(), []
        for key in pool:
            if key_id(key) in exclude:
                continue
            st = _state(key)
            st.stamps = [t for t in st.stamps if now - t < _WINDOW]
            if st.until > now:
                reasons[st.why or "limit"] += 1
                waits.append(st.until - now)
                continue
            if len(st.stamps) >= config.POOL_RPM:
                reasons["penuh"] += 1
                waits.append(max(0.5, _WINDOW - (now - st.stamps[0])))
                continue
            rank = (st.penalty > now, st.inflight, st.order)
            if best is None or rank < best_rank:
                best, best_rank = key, rank
        if best is None:
            raise PoolExhausted(_describe(reasons) if reasons else "Semua key sudah dicoba untuk permintaan ini.",
                                min(waits) if waits else None, reasons)
        st = _state(best)
        st.inflight += 1
        st.stamps.append(now)
        st.order = next(_seq)
        return Lease(best)


def release(lease, outcome, retry_after=None):
    """Lapor hasil permintaan: ok | limit | cap | balance | invalid | busy | client."""
    global _dirty
    now = _clock()
    announce = None
    with _lock:
        st = _state(lease.key)
        st.inflight = max(0, st.inflight - 1)
        before = st.why
        if outcome == "ok":
            st.uses += 1
            st.last_wall = _wall()
            st.penalty = 0.0
            st.until, st.why = 0.0, ""
        elif outcome == "limit":
            wait = retry_after if retry_after else _LIMIT_DEFAULT
            st.until = now + min(max(float(wait), _LIMIT_MIN), _LIMIT_MAX)
            st.why = "limit"
            st.errors += 1
        elif outcome in ("cap", "balance"):
            st.until = now + _COOLDOWN[outcome]
            st.why = outcome
            st.errors += 1
        elif outcome == "invalid":
            st.why, st.until = "invalid", now + _INVALID_COOLDOWN
            st.errors += 1
        elif outcome == "busy":
            st.penalty = now + _PENALTY
            st.errors += 1
        if outcome in ("balance", "cap", "invalid") and before != outcome:
            announce = (outcome, lease.key)
        _dirty = True
        _save()
    if announce and _alert:
        try:
            _alert(announce[0], mask(announce[1]))
        except Exception:  # noqa: BLE001 - kabar gagal tidak boleh merusak permintaan
            log.exception("gagal mengabari pemilik soal key")


def mark_checked(key, ok, full=True):
    """Hasil pemeriksaan /kunci_tes. ok=False: ditolak penyedia, dikeluarkan sementara.
    ok=True, full=True: key terbukti MENJAWAB, semua penanda dibersihkan. ok=True, full=False: penyedia
    mengenali key ini (daftar model terbuka) tapi belum terbukti bisa menjawab - saldo atau batas paket
    bisa saja tetap habis - jadi hanya penanda 'ditolak' yang dicabut."""
    with _lock:
        st = _state(key)
        if not ok:
            st.why, st.until = "invalid", _clock() + _INVALID_COOLDOWN
        elif full:
            st.why, st.until, st.penalty = "", 0.0, 0.0
        elif st.why == "invalid":
            st.why, st.until = "", 0.0


def forget(key):
    with _lock:
        _states.pop(key_id(key), None)


# --- model ------------------------------------------------------------------------------
def models():
    return list(config.NUSAGATE_MODELS)


def next_model(exclude=()):
    """Model berikutnya untuk satu jawaban: yang paling lama menganggur. None = tidak diatur
    (pakai model bawaan key)."""
    names = models()
    if not names:
        return None
    now = _clock()
    with _lock:
        best, best_at = None, None
        for name in names:
            if name in exclude:
                continue
            ms = _models.setdefault(name, _ModelState())
            if ms.until > now:
                continue
            if best is None or ms.order < best_at:
                best, best_at = name, ms.order
        if best is not None:
            _models[best].order = next(_seq)
        return best


def model_ok(name):
    global _dirty
    if not name:
        return
    with _lock:
        _models.setdefault(name, _ModelState()).uses += 1
        _dirty = True
        _save()


def model_bad(name):
    """Penyedia menolak model ini (tidak ada / tak mendukung permintaan): tinggalkan 30 menit."""
    if not name:
        return
    with _lock:
        _models.setdefault(name, _ModelState()).until = _clock() + _MODEL_COOLDOWN


def is_gateway(key):
    return str(key).startswith("nsk") or bool(config.DEEPSEEK_URL)


def model_for(key, run_model=None):
    """Model yang dipakai untuk `key`: model putaran ini (kalau key dari gateway), selain itu bawaan key."""
    if run_model and is_gateway(key):
        return run_model
    return config.deepseek_model(key)


# --- tampilan --------------------------------------------------------------------------------
def snapshot():
    """Daftar key untuk ditampilkan (sudah ditutup, bukan key asli)."""
    now, wall = _clock(), _wall()
    rows = []
    with _lock:
        for number, key in enumerate(all_keys(), 1):
            st = _state(key)
            recent = [t for t in st.stamps if now - t < _WINDOW]
            if st.until > now:
                status, until = st.why or "limit", wall + (st.until - now)
            elif len(recent) >= config.POOL_RPM:
                status, until = "penuh", wall + (_WINDOW - (now - recent[0]))
            elif st.penalty > now:
                status, until = "sibuk", wall + (st.penalty - now)
            else:
                status, until = "siap", None
            rows.append({
                "no": number, "id": key_id(key), "mask": mask(key), "status": status, "until": until,
                "uses": st.uses, "errors": st.errors, "last": st.last_wall or None,
                "per_menit": len(recent), "inflight": st.inflight,
            })
    return rows


def model_rows():
    now = _clock()
    with _lock:
        return [{"name": n, "uses": _models.get(n, _ModelState()).uses,
                 "cooling": _models.get(n, _ModelState()).until > now} for n in models()]


def since():
    with _lock:
        _load_once()
        return _since
