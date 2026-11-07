"""Unggahan berkas / folder aplikasi untuk REA (dashboard > Pengaturan > REA).

Kenapa ada: kartu REA dulu hanya menerima PATH di komputer server. Pemilik yang membuka dashboard dari HP atau dari
komputer lain tidak bisa "memasukkan" aplikasinya. Modul ini menerima berkas (atau seluruh folder aplikasi) dari
browser, menyimpannya di <DATA_DIR>/rea-uploads/<id>/, lalu rea.py menganalisisnya dari sana. Berkas unggahan TIDAK
PERNAH dijalankan - hanya dibaca REA (alat analisis statis).

Rancangan, dan dari mana asalnya (hasil riset, bukan tebakan):
  - POTONGAN, bukan satu permintaan raksasa. Cloudflare membatasi badan permintaan 100 MB (paket Free/Pro, 200 MB
    Business; developers.cloudflare.com/workers/platform/limits) dan jaringan HP sering putus. Potongan bawaan 8 MiB.
  - Bisa DILANJUTKAN, meniru protokol tus.io: offset potongan harus sama dengan jumlah byte yang sudah diterima,
    kalau beda -> 409 + posisi sebenarnya. Sumber kebenaran = ukuran berkas di DISK, bukan ingatan proses, jadi
    unggahan selamat dari It's Me menyala ulang.
  - Tiap potongan SEMUA-ATAU-TIDAK-SAMA-SEKALI: dibaca penuh ke memori (<= ukuran potongan), diperiksa checksum
    SHA-256-nya kalau browser mengirimnya (tus: extension checksum), baru ditulis. Klien putus di tengah potongan
    tidak meninggalkan setengah potongan di disk.
  - OWASP File Upload Cheat Sheet: hanya pemilik yang sudah login (dicek di dashboard) + header anti-CSRF, batas
    ukuran, nama berkas dibersihkan (karakter terlarang, ':' = alur data NTFS, nama perangkat Windows seperti
    CON/NUL.txt, titik/spasi di ujung, karakter kontrol & pembalik arah teks), setiap unggahan di folder acak
    sendiri sehingga nama tidak bisa menimpa apa pun, tidak pernah disajikan balik ke browser, dan folder tidak
    boleh keluar dari akarnya ('..', path mutlak, tautan).
  - Penyimpanan dibatasi: paling banyak N unggahan, total ukuran, umur (dipangkas otomatis); unggahan yang dipakai
    job REA yang sedang berjalan tidak pernah dihapus.

API: UploadError, limits, clean_name, create, status, get, list_uploads, write_chunk, finish, resolve, touch,
delete, prune.
"""

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import stat
import sys
import threading
import time
import unicodedata

from . import config, rea

log = logging.getLogger("itsme")

IS_WIN = sys.platform == "win32"
_MB = 1 << 20
_ID_RE = re.compile(r"[0-9a-f]{12}")
_SHA_RE = re.compile(r"[0-9a-fA-F]{64}")

MAX_ACTIVE = 4                 # unggahan yang belum selesai sekaligus
PARTIAL_TTL = 24 * 3600        # unggahan setengah jalan yang tak disentuh selama ini dibuang
DISK_MARGIN = 512 * _MB        # sisa ruang disk yang harus tetap ada setelah unggahan selesai
MAX_NAME = 120                 # panjang nama berkas (setelah dibersihkan)
MAX_COMPONENT = 150            # panjang satu nama folder/berkas di dalam folder yang diunggah
MAX_DEPTH = 32                 # kedalaman folder
MAX_WIN_PATH = 240             # path penuh di Windows (batas klasik 260 dikurangi cadangan)
CANDIDATES = 30                # berkas penting yang ditawarkan dari dalam folder unggahan


def _num_env(name, default, lo, hi):
    """Angka dari variabel lingkungan, dijepit ke [lo, hi]; isian aneh (kosong, 'inf', 'nan', teks) -> default."""
    raw = (os.environ.get(name) or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return int(min(max(value, lo), hi))


def max_bytes():
    """Ukuran total terbesar satu unggahan (byte)."""
    return _num_env("ITSME_REA_UPLOAD_MAX_MB", 2048, 1, 102400) * _MB


def keep_bytes():
    """Total ukuran unggahan SELESAI yang disimpan (byte); yang tertua dipangkas."""
    return _num_env("ITSME_REA_UPLOAD_KEEP_MB", 4096, 1, 512000) * _MB


def keep_count():
    return _num_env("ITSME_REA_UPLOAD_KEEP", 10, 1, 200)


def ttl_seconds():
    return _num_env("ITSME_REA_UPLOAD_DAYS", 7, 1, 365) * 86400


def max_files():
    return _num_env("ITSME_REA_UPLOAD_FILES", 5000, 1, 50000)


def chunk_bytes():
    """Ukuran potongan yang diminta dari browser (juga batas atas tiap permintaan potongan)."""
    return _num_env("ITSME_REA_CHUNK_MB", 8, 1, 32) * _MB


def limits():
    """Batas yang berlaku, untuk ditampilkan di halaman dan dipakai browser."""
    return {"max_bytes": max_bytes(), "chunk_bytes": chunk_bytes(), "max_files": max_files(),
            "keep": keep_count(), "keep_bytes": keep_bytes(), "days": ttl_seconds() // 86400,
            "max_active": MAX_ACTIVE}


class UploadError(Exception):
    """Unggahan ditolak atau gagal; pesannya siap ditampilkan ke pemilik. `status` = kode HTTP yang pas,
    `extra` = isian tambahan untuk balasan JSON (mis. received saat 409)."""

    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


# --- membersihkan nama ---------------------------------------------------------------------------
# Karakter terlarang di Windows (< > : " / \ | ? *), karakter kontrol, serta karakter tak terlihat/pembalik arah
# teks (U+202E membuat "invoice‮fdp.exe" tampak seperti PDF padahal EXE).
_BAD = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*​-‏ -‮⁠-⁩﻿]')
# Nama perangkat Windows (Microsoft Learn "Naming Files, Paths, and Namespaces"): CON, PRN, AUX, NUL, COM1-9,
# LPT1-9 (juga dengan pangkat ¹²³), dengan atau tanpa ekstensi ("NUL.txt" sama dengan NUL).
_RESERVED = re.compile(r"(con|prn|aux|nul|conin\$|conout\$|clock\$|com[0-9¹²³]|"
                       r"lpt[0-9¹²³])", re.IGNORECASE)


def _is_device_name(text):
    """True kalau Windows menganggap nama ini sebuah perangkat. Yang dibandingkan hanya bagian sebelum titik
    pertama, tanpa spasi di ujungnya: 'NUL.txt', 'NUL.tar.gz' dan juga 'NUL .txt' sama-sama berarti NUL."""
    return bool(_RESERVED.fullmatch(text.split(".", 1)[0].rstrip(" ")))


def _show(text, limit=60):
    """Nama yang aman dikutip di pesan galat (tanpa karakter kontrol, dipotong)."""
    return re.sub(r"[\x00-\x1f\x7f -‮]+", " ", str(text)).strip()[:limit]


def clean_name(raw):
    """Nama berkas aman dari teks bebas (nama dari browser): tanpa folder, karakter terlarang diganti '_', titik
    dan spasi di ujung dibuang, nama perangkat Windows diberi awalan '_', dipotong <= MAX_NAME dengan ekstensi
    dipertahankan. Tidak pernah kosong dan tidak pernah berisi pemisah path."""
    text = unicodedata.normalize("NFC", str(raw if raw is not None else ""))
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = _BAD.sub("_", text).strip(" ").rstrip(". ")
    if text in ("", ".", ".."):
        return "berkas"
    if _is_device_name(text):
        text = "_" + text
    if len(text) > MAX_NAME:
        stem, ext = os.path.splitext(text)
        ext = ext[:16]
        text = (stem[:MAX_NAME - len(ext)] + ext).rstrip(". ") or "berkas"
    return text


def clean_component(raw):
    """Satu nama folder/berkas di DALAM folder yang diunggah. Ketat: tidak diubah diam-diam (struktur aplikasi
    harus persis), kalau tidak aman ditolak dengan alasan."""
    text = unicodedata.normalize("NFC", str(raw if raw is not None else ""))
    if text in ("", ".", ".."):
        raise UploadError("Folder berisi nama kosong atau '.'/'..' - ditolak (bisa keluar dari folder unggahan).")
    if _BAD.search(text):
        raise UploadError(f"Nama '{_show(text)}' mengandung karakter terlarang (< > : \" / \\ | ? * atau "
                          "karakter kontrol/tak terlihat) - ditolak.")
    if text != text.strip(" ") or text.endswith("."):
        raise UploadError(f"Nama '{_show(text)}' diawali/diakhiri spasi atau diakhiri titik (tidak valid di "
                          "Windows) - ditolak.")
    if _is_device_name(text):
        raise UploadError(f"Nama '{_show(text)}' adalah nama perangkat Windows (CON, NUL, COM1, ...) - ditolak.")
    if len(text) > MAX_COMPONENT:
        raise UploadError(f"Nama '{_show(text)}...' terlalu panjang (maks. {MAX_COMPONENT} karakter).")
    return text


def _split_rel(path):
    """Komponen path relatif yang sudah diperiksa, atau UploadError (path mutlak, '..', nama terlarang)."""
    text = str(path if path is not None else "").replace("\\", "/")
    parts = text.split("/")
    if not 1 <= len(parts) <= MAX_DEPTH:
        raise UploadError(f"Folder terlalu dalam (maks. {MAX_DEPTH} tingkat) atau path kosong.")
    return [clean_component(p) for p in parts]


# --- penyimpanan --------------------------------------------------------------------------------
_GLOBAL = threading.RLock()
_LOCKS = {}          # id -> Lock penulis (satu potongan sekaligus per unggahan)
_META_CACHE = {}     # id -> (mtime_ns, size, meta): meta.json folder besar tak perlu diurai tiap potongan


def _root():
    return os.path.join(config.DATA_DIR, "rea-uploads")


def _norm(upload_id):
    uid = str(upload_id or "").strip().lower()
    return uid if _ID_RE.fullmatch(uid) else None


def _udir(uid):
    return os.path.join(_root(), uid)


def _content(uid):
    return os.path.join(_udir(uid), "content")


def _part_path(uid, index):
    return os.path.join(_udir(uid), "parts", f"{int(index)}.part")


def _final_path(uid, entry):
    return os.path.join(_content(uid), *entry["path"].split("/"))


def _replace(src, dst):
    """os.replace dengan beberapa kali coba ulang: di Windows antivirus/pengindeks kadang memegang berkas
    sesaat setelah ditulis (PermissionError sementara)."""
    for attempt in range(8):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not IS_WIN or attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _save_meta(meta):
    path = os.path.join(_udir(meta["id"]), "meta.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    _replace(tmp, path)
    _META_CACHE.pop(meta["id"], None)


def _valid_meta(meta, uid):
    """Bentuk meta.json yang boleh dipercaya (berkas bisa rusak atau diubah orang lain di disk)."""
    try:
        if not isinstance(meta, dict) or meta.get("id") != uid or meta.get("kind") not in ("file", "dir"):
            return False
        if meta.get("state") not in ("uploading", "ready") or not isinstance(meta.get("name"), str):
            return False
        files = meta.get("files")
        if not isinstance(files, list) or not files:
            return False
        for f in files:
            if not isinstance(f, dict) or isinstance(f.get("size"), bool) or not isinstance(f.get("size"), int):
                return False
            if f["size"] < 0 or not isinstance(f.get("path"), str):
                return False
            parts = f["path"].split("/")
            if meta["kind"] == "file":
                if len(parts) != 1 or clean_name(parts[0]) != parts[0]:
                    return False
            else:
                _split_rel(f["path"])
        if meta["kind"] == "dir" and (not isinstance(meta.get("root"), str) or any(
                not f["path"].startswith(meta["root"] + "/") for f in files)):
            return False
        return True
    except (UploadError, TypeError, KeyError, AttributeError):
        return False


def _read_meta(uid):
    path = os.path.join(_udir(uid), "meta.json")
    try:
        st = os.stat(path)
    except OSError:
        return None
    cached = _META_CACHE.get(uid)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    if not _valid_meta(meta, uid):
        return None
    _META_CACHE[uid] = (st.st_mtime_ns, st.st_size, meta)
    return meta


def _need(upload_id):
    uid = _norm(upload_id)
    meta = _read_meta(uid) if uid else None
    if meta is None:
        raise UploadError("Unggahan itu tidak ada (mungkin sudah dihapus atau kedaluwarsa).", 404)
    return meta


def _lock_for(uid):
    with _GLOBAL:
        return _LOCKS.setdefault(uid, threading.Lock())


def _file_state(meta, index):
    """(byte diterima, lengkap?) untuk berkas ke-`index`, dibaca dari DISK: itulah satu-satunya sumber kebenaran."""
    uid = meta["id"]
    entry = meta["files"][index]
    size = entry["size"]
    try:
        st = os.stat(_final_path(uid, entry))
        if stat.S_ISREG(st.st_mode) and st.st_size == size:
            return size, True
    except OSError:
        pass
    try:
        have = os.stat(_part_path(uid, index)).st_size
    except OSError:
        have = 0
    return min(have, size), False


def _progress(meta):
    """(berkas selesai, byte diterima total, {'file','offset'} berikutnya atau None)."""
    done = received = 0
    nxt = None
    for i in range(len(meta["files"])):
        have, complete = _file_state(meta, i)
        received += have
        if complete:
            done += 1
        elif nxt is None:
            nxt = {"file": i, "offset": have}
    return done, received, nxt


def _candidates(meta):
    """Berkas penting (exe/dll/apk/asar/jar/...) di dalam folder unggahan, terbesar dulu - untuk tombol
    'analisis berkas ini'."""
    if meta["kind"] != "dir":
        return []
    cut = len(meta["root"]) + 1
    found = []
    for i, f in enumerate(meta["files"]):
        if os.path.splitext(f["path"])[1].lower() in rea._AUTO_EXT:
            found.append((f["size"], i))
    found.sort(reverse=True)
    return [{"index": i, "path": meta["files"][i]["path"][cut:], "size": size} for size, i in found[:CANDIDATES]]


def view(meta):
    """Bentuk publik sebuah unggahan (dikirim ke halaman). Unggahan yang sudah selesai tidak diperiksa berkas
    demi berkas (daftar ini sering diminta; utuh-tidaknya dicek saat dipakai, lihat resolve)."""
    if meta["state"] == "ready":
        done, received, nxt = len(meta["files"]), meta["size"], None
    else:
        done, received, nxt = _progress(meta)
    out = {
        "id": meta["id"], "kind": meta["kind"], "name": meta["name"], "state": meta["state"],
        "size": meta["size"], "received": received, "files_total": len(meta["files"]), "files_done": done,
        "next": nxt, "created": meta.get("created"), "updated": meta.get("updated"),
        "last_used": meta.get("last_used"), "sha256": meta.get("sha256"),
    }
    out["candidates"] = _candidates(meta) if meta["state"] == "ready" else []
    return out


# --- membuat unggahan -----------------------------------------------------------------------------
def _free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _manifest_file(name, size):
    if not isinstance(name, str) or not name.strip():
        raise UploadError("Nama berkas kosong.")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise UploadError("Ukuran berkas tidak valid.")
    safe = clean_name(name)
    return "file", safe, None, [{"path": safe, "size": size}]


def _manifest_dir(files):
    if not isinstance(files, list) or not files:
        raise UploadError("Folder kosong - tidak ada berkas untuk diunggah.")
    if len(files) > max_files():
        raise UploadError(f"Folder berisi {len(files)} berkas; batasnya {max_files()} "
                          "(ITSME_REA_UPLOAD_FILES). Unggah satu berkas utamanya saja, atau kirim lewat path.", 413)
    out, seen, dirs = [], set(), set()
    root = None
    for item in files:
        if not isinstance(item, dict):
            raise UploadError("Daftar berkas tidak valid.")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise UploadError("Ukuran berkas tidak valid.")
        parts = _split_rel(item.get("path"))
        if len(parts) < 2:
            raise UploadError("Berkas harus berada di dalam satu folder induk.")
        if root is None:
            root = parts[0]
        elif parts[0].casefold() != root.casefold():
            raise UploadError("Semua berkas harus berada di satu folder induk yang sama.")
        parts[0] = root
        key = "/".join(parts).casefold()
        if key in seen:
            raise UploadError(f"Ada dua berkas dengan nama sama (beda huruf besar/kecil): '{_show('/'.join(parts))}'.")
        seen.add(key)
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]).casefold())
        out.append({"path": "/".join(parts), "size": size})
    clash = next((f["path"] for f in out if f["path"].casefold() in dirs), None)
    if clash:
        raise UploadError(f"'{_show(clash)}' tercatat sebagai berkas sekaligus folder.")
    return "dir", root, root, out


def _longest(files, uid):
    base = len(os.path.join(_content(uid), "x")) if uid else len(os.path.join(_root(), "x" * 12, "content", "x"))
    return max(base + len(f["path"]) for f in files)


def _active_count():
    n = 0
    root = _root()
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if _ID_RE.fullmatch(name):
            meta = _read_meta(name)
            if meta and meta["state"] == "uploading":
                n += 1
    return n


def create(kind, name=None, size=None, files=None, fingerprint=None):
    """Mulai (atau lanjutkan) sebuah unggahan -> (view, reused).

    kind='file': name + size. kind='dir': files=[{'path': 'Folder/sub/x.dll', 'size': n}, ...] dengan satu folder
    induk. `fingerprint` (nama|ukuran|waktu ubah dari browser) dipakai untuk mengenali berkas yang SAMA:
    unggahan setengah jalan dilanjutkan dari byte terakhir, dan yang sudah selesai dipakai ulang (reused=True)."""
    if kind == "file":
        kind, label, root, manifest = _manifest_file(name, size)
    elif kind == "dir":
        kind, label, root, manifest = _manifest_dir(files)
    else:
        raise UploadError("Jenis unggahan harus 'file' atau 'dir'.")
    total = sum(f["size"] for f in manifest)
    if total > max_bytes():
        raise UploadError(f"Terlalu besar: {_fmt(total)}; batasnya {_fmt(max_bytes())} per unggahan "
                          "(naikkan lewat ITSME_REA_UPLOAD_MAX_MB).", 413)
    if IS_WIN and _longest(manifest, None) > MAX_WIN_PATH:
        raise UploadError("Ada path yang terlalu panjang untuk Windows (lebih dari 240 karakter di folder data). "
                          "Pendekkan nama folder/berkas, atau kirim sebagai satu berkas.", 413)
    fingerprint = str(fingerprint)[:300] if fingerprint else None
    with _GLOBAL:
        os.makedirs(_root(), exist_ok=True)
        prune()
        if fingerprint:
            found = _find_same(fingerprint, kind, total)
            if found is not None:
                meta, reused = found
                meta = dict(meta, updated=time.time(), last_used=time.time())   # dipakai lagi: jangan keburu dipangkas
                _save_meta(meta)
                return view(meta), reused
        if _active_count() >= MAX_ACTIVE:
            raise UploadError(f"Sudah ada {MAX_ACTIVE} unggahan yang belum selesai. Selesaikan atau hapus salah "
                              "satunya dulu (daftar 'Belum selesai' di bawah).", 429)
        free = _free_bytes(_root())
        if free is not None and free < total + DISK_MARGIN:
            raise UploadError(f"Ruang disk tidak cukup: perlu {_fmt(total)} (+ cadangan {_fmt(DISK_MARGIN)}), "
                              f"tersisa {_fmt(free)}.", 507)
        uid = secrets.token_hex(6)
        while os.path.exists(_udir(uid)):
            uid = secrets.token_hex(6)
        if IS_WIN and _longest(manifest, uid) > MAX_WIN_PATH:
            raise UploadError("Ada path yang terlalu panjang untuk Windows. Pendekkan nama folder/berkas.", 413)
        now = time.time()
        meta = {"id": uid, "kind": kind, "name": label, "root": root, "files": manifest, "size": total,
                "state": "uploading", "created": now, "updated": now, "last_used": now, "sha256": None,
                "fingerprint": fingerprint}
        os.makedirs(os.path.join(_udir(uid), "parts"))
        os.makedirs(_content(uid))
        try:
            for entry in manifest:
                if entry["size"] == 0:   # berkas kosong tak punya potongan: langsung lengkap
                    final = _final_path(uid, entry)
                    os.makedirs(os.path.dirname(final), exist_ok=True)
                    open(final, "wb").close()
            _save_meta(meta)
        except OSError as exc:
            shutil.rmtree(_udir(uid), ignore_errors=True)
            raise UploadError(f"Tidak bisa menyiapkan folder unggahan: {exc}", 500) from exc
        return view(meta), False


def _find_same(fingerprint, kind, total):
    """Unggahan yang sidik jarinya sama: yang setengah jalan boleh dilanjutkan; yang selesai dipakai ulang kalau
    isinya masih utuh di disk. -> (meta, reused) atau None."""
    best = None
    try:
        names = os.listdir(_root())
    except OSError:
        return None
    for name in names:
        if not _ID_RE.fullmatch(name):
            continue
        meta = _read_meta(name)
        if not meta or meta.get("fingerprint") != fingerprint or meta["kind"] != kind or meta["size"] != total:
            continue
        if meta["state"] == "ready" and not _intact(meta):
            continue
        if best is None or (meta.get("updated") or 0) > (best.get("updated") or 0):
            best = meta
    return (best, best["state"] == "ready") if best else None


def _intact(meta):
    return all(_file_state(meta, i)[1] for i in range(len(meta["files"])))


def fmt_size(n):
    """Ukuran manusiawi: '352.0 KB', '2.0 GB'."""
    return _fmt(n or 0)


def _fmt(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- menerima potongan ----------------------------------------------------------------------------
def write_chunk(upload_id, index, offset, data, sha256=None):
    """Tulis satu potongan di posisi `offset` berkas ke-`index`. -> {'file','received','size','done'}.

    409 (dengan 'received' = posisi sebenarnya) kalau offset tidak sama dengan byte yang sudah diterima;
    422 kalau checksum tidak cocok; 413 kalau melewati ukuran yang dinyatakan; 404 kalau unggahan tidak ada."""
    meta = _need(upload_id)
    uid = meta["id"]
    if meta["state"] != "uploading":
        raise UploadError("Unggahan ini sudah selesai - tidak menerima potongan lagi.", 409)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(meta["files"]):
        raise UploadError("Nomor berkas tidak valid.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise UploadError("Offset tidak valid.")
    if not data:
        raise UploadError("Potongan kosong.")
    if len(data) > chunk_bytes():
        raise UploadError(f"Potongan terlalu besar (maks. {_fmt(chunk_bytes())}).", 413)
    if sha256:
        if not _SHA_RE.fullmatch(str(sha256)):
            raise UploadError("Checksum potongan tidak valid.")
        if hashlib.sha256(data).hexdigest() != str(sha256).lower():
            raise UploadError("Potongan rusak di perjalanan (checksum tidak cocok) - kirim ulang.", 422)
    size = meta["files"][index]["size"]
    lock = _lock_for(uid)
    if not lock.acquire(blocking=False):
        raise UploadError("Potongan lain untuk unggahan ini sedang diproses - tunggu sebentar.", 409)
    try:
        if not os.path.isdir(_udir(uid)):   # dihapus saat potongan ini menunggu
            raise UploadError("Unggahan itu tidak ada (mungkin sudah dihapus atau kedaluwarsa).", 404)
        meta = _need(uid)                    # baca ulang SETELAH memegang kunci: finish() mungkin baru saja selesai
        if meta["state"] != "uploading":
            raise UploadError("Unggahan ini sudah selesai - tidak menerima potongan lagi.", 409)
        have, complete = _file_state(meta, index)
        if complete:
            raise UploadError("Berkas ini sudah lengkap.", 409, received=size)
        if offset != have:
            raise UploadError(f"Posisi tidak cocok: server sudah menerima {have} byte, potongan mulai di {offset}.",
                              409, received=have)
        if offset + len(data) > size:
            raise UploadError("Potongan melewati ukuran berkas yang dinyatakan di awal.", 413, received=have)
        part = _part_path(uid, index)
        try:
            with open(part, "ab") as f:
                f.write(data)
            end = offset + len(data)
            if end == size:
                final = _final_path(uid, meta["files"][index])
                os.makedirs(os.path.dirname(final), exist_ok=True)
                _replace(part, final)
        except OSError as exc:
            low = getattr(exc, "errno", None) in (28,) or "space" in str(exc).lower()
            raise UploadError("Ruang disk habis saat menulis." if low else f"Gagal menulis ke disk: {exc}",
                              507 if low else 500) from exc
        return {"file": index, "received": end, "size": size, "done": end == size}
    finally:
        lock.release()


def finish(upload_id):
    """Tandai unggahan selesai setelah semua berkas lengkap; berkas tunggal dihitung SHA-256-nya. Idempoten."""
    meta = _need(upload_id)
    uid = meta["id"]
    if meta["state"] == "ready":
        return view(meta)
    lock = _lock_for(uid)
    if not lock.acquire(blocking=False):
        raise UploadError("Masih ada potongan yang sedang diproses - coba lagi sebentar.", 409)
    try:
        missing = [i for i in range(len(meta["files"])) if not _file_state(meta, i)[1]]
        if missing:
            first = meta["files"][missing[0]]["path"]
            raise UploadError(f"{len(missing)} berkas belum lengkap atau hilang (mis. '{_show(first)}'). Kalau "
                              "semua sudah terkirim, kemungkinan antivirus (Windows Defender) menghapusnya karena "
                              "dianggap berbahaya - wajar untuk contoh malware; beri pengecualian untuk folder "
                              "data It's Me lalu unggah ulang.", 409, missing=len(missing))
        digest = None
        if meta["kind"] == "file":
            digest = _sha256_file(_final_path(uid, meta["files"][0]))
        meta = dict(meta, state="ready", sha256=digest, finished=time.time(), updated=time.time(),
                    last_used=time.time())
        shutil.rmtree(os.path.join(_udir(uid), "parts"), ignore_errors=True)
        _save_meta(meta)
    finally:
        lock.release()
    with _GLOBAL:
        prune()
    return view(meta)


def _sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError as exc:
        raise UploadError(f"Berkas hilang atau tak terbaca setelah diterima: {exc}. Kemungkinan antivirus "
                          "menghapusnya.", 409) from exc
    return h.hexdigest()


# --- membaca / memakai / menghapus -----------------------------------------------------------------
def get(upload_id):
    """view() sebuah unggahan, atau None."""
    uid = _norm(upload_id)
    meta = _read_meta(uid) if uid else None
    return view(meta) if meta else None


def status(upload_id):
    """view() untuk melanjutkan unggahan; UploadError 404 kalau tidak ada."""
    return view(_need(upload_id))


def list_uploads():
    """Semua unggahan (yang terbaru dulu), termasuk yang belum selesai."""
    out = []
    try:
        names = os.listdir(_root())
    except OSError:
        return out
    for name in names:
        if _ID_RE.fullmatch(name):
            meta = _read_meta(name)
            if meta:
                out.append(meta)
    out.sort(key=lambda m: m.get("updated") or m.get("created") or 0, reverse=True)
    return [view(m) for m in out]


def resolve(upload_id, index=None):
    """Path yang akan diperiksa REA + label tampilan + info unggahan, untuk unggahan yang SUDAH selesai.
    `index` (opsional) memilih satu berkas di dalam folder unggahan."""
    meta = _need(upload_id)
    uid = meta["id"]
    if meta["state"] != "ready":
        raise UploadError("Unggahan ini belum selesai - tunggu sampai 100% lalu coba lagi.", 409)
    rel_file = None
    if meta["kind"] == "file":
        target = _final_path(uid, meta["files"][0])
        label = meta["name"]
    elif index is None:
        target = os.path.join(_content(uid), meta["root"])
        label = meta["name"]
    else:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(meta["files"]):
            raise UploadError("Nomor berkas di dalam folder tidak valid.")
        target = _final_path(uid, meta["files"][index])
        rel_file = meta["files"][index]["path"][len(meta["root"]) + 1:]
        label = f"{meta['name']}/{rel_file}"
    ok = os.path.isdir(target) if (meta["kind"] == "dir" and index is None) else os.path.isfile(target)
    if not ok or not _intact(meta):
        raise UploadError("Berkas unggahan sudah tidak ada di disk (dihapus antivirus atau dipangkas). Unggah "
                          "ulang.", 410)
    base = os.path.realpath(_content(uid))
    real = os.path.realpath(target)
    try:
        inside = os.path.commonpath([real, base]) == base
    except ValueError:
        inside = False
    if not inside:   # tautan/junction yang menunjuk keluar - tidak boleh terjadi, tapi jangan percaya disk
        raise UploadError("Berkas unggahan menunjuk ke luar folder penyimpanan - ditolak.", 403)
    info = {"id": uid, "name": meta["name"], "kind": meta["kind"], "size": meta["size"],
            "sha256": meta.get("sha256"), "file": index, "rel": rel_file, "label": label}
    return {"path": target, "label": label, "upload": info}


_HELD = {}   # id -> jumlah penahan: unggahan yang sedang menunggu persetujuan pemilik / mau dipakai analisis


@contextlib.contextmanager
def holding(upload_id):
    """Tahan sebuah unggahan supaya tidak dipangkas/dihapus selama blok ini (mis. selagi pemilik belum menekan
    'Ya' di Telegram, yang bisa berlangsung menit-menitan sebelum job REA-nya sempat terdaftar)."""
    uid = _norm(upload_id)
    with _GLOBAL:
        if uid:
            _HELD[uid] = _HELD.get(uid, 0) + 1
    try:
        yield
    finally:
        with _GLOBAL:
            if uid:
                _HELD[uid] = _HELD.get(uid, 1) - 1
                if _HELD[uid] <= 0:
                    _HELD.pop(uid, None)


def _held():
    with _GLOBAL:
        return set(_HELD)


def touch(upload_id):
    """Catat bahwa unggahan baru dipakai (memperpanjang umurnya di penyimpanan)."""
    uid = _norm(upload_id)
    meta = _read_meta(uid) if uid else None
    if meta is None:
        return
    try:
        _save_meta(dict(meta, last_used=time.time()))
    except OSError:
        pass


def delete(upload_id, protected=None):
    """Hapus sebuah unggahan beserta isinya. UploadError 409 kalau sedang dipakai job REA yang berjalan."""
    uid = _norm(upload_id)
    if uid is None or not os.path.isdir(_udir(uid)):
        raise UploadError("Unggahan itu tidak ada (mungkin sudah dihapus atau kedaluwarsa).", 404)
    busy = set(protected or ()) | rea.active_upload_ids() | _held()
    if uid in busy:
        raise UploadError("Unggahan ini sedang dipakai analisis REA yang berjalan - batalkan analisisnya dulu.", 409)
    with _GLOBAL:
        lock = _lock_for(uid)
        if not lock.acquire(blocking=False):
            raise UploadError("Unggahan ini sedang menerima potongan - coba lagi sebentar.", 409)
        try:
            _remove(uid)
        finally:
            lock.release()
            _LOCKS.pop(uid, None)
    return True


def _remove(uid):
    _META_CACHE.pop(uid, None)
    shutil.rmtree(_udir(uid), ignore_errors=True)
    if os.path.exists(_udir(uid)):   # berkas masih dipegang proses lain (Windows)
        raise UploadError("Sebagian berkas masih dipakai program lain dan belum bisa dihapus. Coba lagi nanti.", 409)


def prune(protected=None):
    """Pangkas penyimpanan: buang unggahan setengah jalan yang lama tak disentuh, yang selesai tapi lama tak
    dipakai, lalu sisakan paling banyak keep_count() unggahan selesai dengan total <= keep_bytes() (yang paling
    lama tak dipakai dibuang dulu). Unggahan yang dipakai job REA berjalan tidak pernah dihapus."""
    busy = set(protected or ()) | rea.active_upload_ids() | _held()
    now = time.time()
    root = _root()
    try:
        names = [n for n in os.listdir(root) if _ID_RE.fullmatch(n) and os.path.isdir(os.path.join(root, n))]
    except OSError:
        return
    ready = []
    for name in names:
        if name in busy:
            continue
        meta = _read_meta(name)
        if meta is None:   # rusak / setengah dibuat: buang kalau sudah lama
            try:
                old = now - os.path.getmtime(os.path.join(root, name)) > PARTIAL_TTL
            except OSError:
                old = False
            if old:
                _drop(name)
            continue
        touched = float(meta.get("updated") or meta.get("created") or 0)
        if meta["state"] == "uploading":
            if now - touched > PARTIAL_TTL:
                _drop(name)
            continue
        used = float(meta.get("last_used") or touched)
        if now - used > ttl_seconds():
            _drop(name)
            continue
        ready.append((used, name, meta["size"]))
    ready.sort(reverse=True)
    total = 0
    for rank, (_used, name, size) in enumerate(ready):
        total += size
        if rank >= keep_count() or (total > keep_bytes() and rank > 0):
            _drop(name)
            total -= size


def _drop(name):
    with _GLOBAL:
        lock = _LOCKS.get(name)
        if lock is not None and not lock.acquire(blocking=False):
            return   # sedang menerima potongan
        try:
            try:
                _remove(name)
            except UploadError:
                pass
        finally:
            if lock is not None:
                lock.release()
            _LOCKS.pop(name, None)
