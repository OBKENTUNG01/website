"""Dashboard web It's Me (nomor 4).

Halaman web untuk melihat status bot dan mengaturnya: ganti otak, mode akses,
lihat/edit memori, dan lihat log. Jalan di dalam proses bot (thread terpisah),
jadi perubahan langsung berlaku pada It's Me yang sedang jalan.

Keamanan:
- Bawaan hanya di 127.0.0.1 — cuma bisa dibuka dari komputer ini.
- Kalau dibuka ke jaringan (ITSME_DASHBOARD_HOST=0.0.0.0, mis. untuk diakses
  dari HP), wajib token. Token dibuat otomatis dan disimpan di
  data/dashboard_token.txt; buka halaman dengan ...?token=ISI_TOKEN sekali,
  lalu token disimpan di cookie.
- Permintaan yang mengubah sesuatu (POST) wajib membawa header khusus, jadi
  halaman web lain tidak bisa diam-diam menyuruh dashboard lewat browsermu.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from . import access, brain, config, hp, tools, updater

log = logging.getLogger("itsme")


# --- jembatan ke It's Me Node (layar/berkas komputer lain, lewat node di sini) ---
def _node_get(path, query):
    """GET ke node di komputer ini. (kode, content-type, bytes, content-disposition)."""
    url = config.NODE_URL + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return (r.status, r.headers.get("Content-Type", "application/json"),
                    r.read(), r.headers.get("Content-Disposition", ""))
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "application/json"), e.read(), ""
    except OSError as e:
        msg = json.dumps({"error": "It's Me Node tidak terjawab di komputer ini (%s)" % type(e).__name__})
        return 502, "application/json; charset=utf-8", msg.encode(), ""


def _bridge_name(node_id):
    """Nama komputer kalau node_id adalah komputer via jembatan ('bridge:NAMA'), else None."""
    return node_id[len("bridge:"):] if node_id and node_id.startswith("bridge:") else None


def _node_post(path, body, client_ip=""):
    data = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json"}
    if client_ip:
        # Node menganggap semua panggilan dari 127.0.0.1 sebagai "lokal", jadi tanpa
        # ini penjaga /enroll di node tidak pernah tahu siapa pemanggil sebenarnya.
        headers["X-Itsme-Client-IP"] = client_ip
    req = urllib.request.Request(config.NODE_URL + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        return 502, json.dumps({"error": "It's Me Node tidak terjawab (%s)" % type(e).__name__}).encode()

# Pekerjaan chat yang sedang berjalan: id -> concurrent.futures.Future.
# Chat bisa makan waktu (otak lokal/konfirmasi), jadi dimulai lalu di-poll.
_JOBS = {}

_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}
# Token & token bot yang mungkin terlihat di log tidak boleh ikut ditampilkan.
_REDACT = re.compile(r"(sk-[a-z]+-[A-Za-z0-9_-]{6,}|\d{6,}:[A-Za-z0-9_-]{20,})")
# Header yang hanya bisa dipasang oleh JavaScript dashboard sendiri (bukan oleh
# form dari situs lain): penjaga sederhana terhadap CSRF.
_GUARD_HEADER = "x-itsme-dashboard"
# Jalur dari aplikasi HP (long-poll): auth pakai token HP, tanpa header/sesi dashboard.
_HP_INGRESS = {"/api/hp/poll", "/api/hp/result", "/api/hp/frame"}
# Jalur komputer baru bergabung TANPA Tailscale: menukar kode undangan / sidik jari
# di dashboard publik ini, yang meneruskannya ke It's Me Node di komputer ini. Auth-nya
# adalah kode undangan sekali pakai itu sendiri (divalidasi node), bukan sesi dashboard.
_NODE_INGRESS = {"/api/node/invite", "/api/node/enroll"}
# Jalur yang dipakai perangkat lain untuk berbicara dengan Hub (lihat devices.py).
_DEVICE_PATHS = {
    f"/api/devices/{p}" for p in ("register", "poll", "result", "message", "confirm", "memory", "sync")
}


def _token_file():
    return os.path.join(config.DATA_DIR, "dashboard_token.txt")


def _load_or_make_token():
    try:
        with open(_token_file(), encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_token_file(), "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


# --- data untuk halaman -----------------------------------------------------------
def _brain_entry(name, ready):
    return {"ready": ready, "desc": brain.describe(name) if ready else None}


def status(app):
    return {
        "name": config.NAME,
        "brain": brain.current_backend(),
        "brain_desc": brain.describe(),
        "brains": {
            "ollama": {"label": "Lokal (Ollama)", **_brain_entry("ollama", True)},
            "openrouter": {"label": "Cloud (OpenRouter)", **_brain_entry("openrouter", bool(config.OPENROUTER_API_KEY))},
            "deepseek": {"label": "DeepSeek", **_brain_entry("deepseek", bool(config.DEEPSEEK_API_KEY))},
            "claude": {"label": "Claude (API key)", **_brain_entry("claude", bool(config.ANTHROPIC_API_KEY))},
            "claude_code": {"label": "Claude di komputer", **_brain_entry("claude_code", brain.claude_code_ready())},
        },
        "access": "full" if access.full() else "safe",
        "version": (updater.installed_version() or "")[:7],
        "memory_chars": len(app.memory.text()),
        "uptime": max(0, (datetime.now() - app.started).total_seconds()) if getattr(app, "started", None) else 0,
        "login": bool(config.DASHBOARD_PASSWORD),
    }


def _ollama_up():
    try:
        return httpx.get(f"{config.OLLAMA_URL}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def info():
    """Bagian status yang perlu jaringan (dipanggil sesekali, bukan tiap detik)."""
    out = {"ollama_up": _ollama_up(), "quota": None}
    if config.OPENROUTER_API_KEY and config.OPENROUTER_MODEL.endswith(":free"):
        try:
            from .brain_openrouter import free_quota

            out["quota"] = free_quota()
        except Exception:  # noqa: BLE001 - info tambahan, jangan sampai menggagalkan halaman
            out["quota"] = None
    return out


def _logs(limit=120):
    try:
        with open(config.LOG_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return "(log belum ada)"
    return _REDACT.sub("***", "".join(lines)).strip() or "(log kosong)"


# --- aksi --------------------------------------------------------------------------
def _switch_brain(app, name):
    if name not in brain.BACKENDS:
        return False, f"Otak tidak dikenal: {name}"
    try:
        brain.set_backend(name)
    except ValueError as exc:
        return False, str(exc)
    # Riwayat obrolan direset (format tiap otak beda). Lewat event loop kalau ada.
    if app.loop:
        app.loop.call_soon_threadsafe(app.histories.clear)
    else:
        app.histories.clear()
    return True, f"Otak sekarang: {brain.describe()}"


def _set_access(full):
    access.set_full(full)
    return True, "Akses penuh aktif." if full else "Akses aman aktif."


def _memory(app, action, text):
    if action == "clear":
        app.memory.clear()
    elif action == "set":
        app.memory.set(text)
    elif action == "add":
        app.memory.add(text)
    else:
        return False, "Aksi memori tidak dikenal."
    return True, app.memory.text()


# --- berkas & perintah komputer ----------------------------------------------------
# Dashboard sudah owner-only (localhost/token), jadi menjelajah & melihat berkas
# non-rahasia tidak perlu konfirmasi ulang. Yang MENGUBAH sesuatu (buka aplikasi,
# jalankan perintah, zip/ekstrak) tetap lewat aturan akses: tombol di Telegram
# saat akses aman, langsung + kabar saat akses penuh. Berkas rahasia selalu dijaga.
_MAX_VIEW_BYTES = 256 * 1024
_MAX_ENTRIES = 3000


def _list_dir(path):
    path = os.path.normpath(path or os.path.expanduser("~"))
    if not os.path.isdir(path):
        return None, "Folder tidak ditemukan."
    try:
        names = os.listdir(path)
    except OSError as exc:
        return None, f"Tidak bisa membuka folder: {exc}"
    entries = []
    for name in names[:_MAX_ENTRIES]:
        full = os.path.join(path, name)
        is_dir = os.path.isdir(full)
        try:
            size = 0 if is_dir else os.path.getsize(full)
        except OSError:
            size = 0
        entries.append({"name": name, "full": full, "dir": is_dir, "size": size, "secret": tools.is_secret_path(full)})
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None, "entries": entries}, None


def _read_file(path):
    path = os.path.normpath(path or "")
    if not os.path.isfile(path):
        return None, "Berkas tidak ditemukan."
    if tools.is_secret_path(path):
        return None, "Ditolak: berkas ini biasanya berisi rahasia (token, password, kunci)."
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, str(exc)
    if size > _MAX_VIEW_BYTES:
        return None, f"Berkas terlalu besar untuk ditampilkan ({tools._human(size)}). Pakai zip untuk mengunduhnya."
    try:
        with open(path, encoding="utf-8") as f:
            return {"path": path, "text": f.read()}, None
    except (UnicodeDecodeError, OSError):
        return None, "Berkas ini bukan teks (biner), jadi tidak bisa ditampilkan di sini."


def _open_path(app, path):
    path = os.path.normpath(path or "")
    if not os.path.exists(path):
        return False, "Tidak ditemukan."
    if not tools.approve(app.owner_ctx(), f"Buka di aplikasi bawaan?\n\n{path}"):
        return False, "Dibatalkan oleh pemilik."
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - buka dengan aplikasi bawaan Windows
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
    except OSError as exc:
        return False, f"Gagal membuka: {exc}"
    return True, f"Dibuka: {os.path.basename(path) or path}"


def _run_command(app, command):
    command = (command or "").strip()
    if not command:
        return False, "Perintah kosong."
    if not tools.is_read_only(command):
        always = tools.mentions_secret(command) or tools.is_archive_command(command)
        if not tools.approve(app.owner_ctx(), f"Jalankan perintah ini?\n\n{command}", always_ask=always):
            return False, "Dibatalkan oleh pemilik."
    return True, tools._run(command, ctx_cwd(app))[1]


def ctx_cwd(app):
    return config.WEBSITE_REPO_DIR or None


def _zip(app, sumber, tujuan):
    sources = [os.path.normpath(s.strip()) for s in (sumber or "").splitlines() if s.strip()]
    if not sources:
        return "Tidak ada folder/berkas yang disebutkan."
    return tools._make_zip(sources, (tujuan or "").strip(), app.owner_ctx())


def _extract(app, arsip, tujuan):
    return tools._extract((arsip or "").strip(), (tujuan or "").strip(), app.owner_ctx())


# --- Google Drive (panel, berfungsi untuk otak apa pun) ----------------------------
def _drive_items(items):
    return [{"id": i["id"], "name": i["name"], "folder": i["folder"], "size": i["size"]} for i in items]


def drive_list(folder_id):
    from . import drive
    try:
        return {"items": _drive_items(drive.list_folder(folder_id or "root"))}, None
    except drive.DriveError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"Google Drive error: {type(exc).__name__}: {exc}"


def drive_search(q):
    from . import drive
    try:
        return {"items": _drive_items(drive.search(q or ""))}, None
    except drive.DriveError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"Google Drive error: {type(exc).__name__}: {exc}"


def drive_action(app, name, inp):
    # Pakai jalur alat yang sama dengan chat: persetujuan lewat Telegram pemilik,
    # berkas rahasia dijaga, pesan hasil siap tampil.
    return tools.handle_drive_tool(name, inp, app.owner_ctx())


# --- server ------------------------------------------------------------------------
# --- perangkat (Hub: terima pendaftaran, daftar) -----------------------------------
def _notify_owner(app, text):
    tg = getattr(app, "tg", None)
    loop = getattr(app, "loop", None)
    if tg and config.OWNER_ID and loop:
        try:
            asyncio.run_coroutine_threadsafe(tg.send(config.OWNER_ID, text), loop)
        except Exception:  # noqa: BLE001 - kabar gagal tidak boleh mengganggu
            pass


def _secret_ok(data):
    return bool(config.HUB_SECRET) and secrets.compare_digest(str(data.get("secret") or ""), config.HUB_SECRET)


def register_device(app, client_ip, data):
    """Hub menerima pendaftaran dari sebuah perangkat. Diamankan secret bersama."""
    from . import devices

    if not _secret_ok(data):
        return {"ok": False, "error": "secret salah"}, 403
    try:
        port = int(data.get("port") or config.DASHBOARD_PORT)
    except (TypeError, ValueError):
        port = config.DASHBOARD_PORT
    name = (data.get("name") or client_ip)
    url = f"http://{client_ip}:{port}"
    is_new = devices.register(name, url)
    if is_new:
        _notify_owner(app, f"🔌 Perangkat baru online: {name} ({url})\nKirim /pakai {name} untuk menyuruhnya bekerja.")
    return {"ok": True, "new": is_new}, 200


def _ask_owner(app, text):
    """Tampilkan tombol Ya/Batal di Telegram pemilik dari thread dashboard."""
    ask = getattr(app, "_ask_confirm", None)
    if not (ask and getattr(app, "tg", None) and app.loop and config.OWNER_ID):
        return False
    try:
        return bool(asyncio.run_coroutine_threadsafe(ask(config.OWNER_ID, text), app.loop).result(timeout=330))
    except Exception:  # noqa: BLE001 - gagal/habis waktu = batal
        return False


def device_api(app, path, client_ip, data):
    """Permintaan dari perangkat ke Hub (/api/devices/...). Diamankan secret bersama,
    bukan login dashboard. Kembalikan (isi JSON, kode HTTP)."""
    from . import devices

    if config.AGENT_MODE:
        return {"ok": False, "error": "komputer ini perangkat, bukan Hub"}, 404
    if path == "/api/devices/register":
        return register_device(app, client_ip, data)
    if not _secret_ok(data):
        return {"ok": False, "error": "secret salah"}, 403
    name = (data.get("name") or client_ip).strip()[:60]
    if path == "/api/devices/poll":
        register_device(app, client_ip, data)  # sekaligus heartbeat
        try:
            wait = min(float(data.get("wait") or 0), devices.POLL_WAIT)
        except (TypeError, ValueError):
            wait = 0
        task = devices.take(name, wait)
        return {"ok": True, "task": task, "sync": devices.sync_bundle(app)["version"]}, 200
    if path == "/api/devices/result":
        text = str(data.get("text") or "")
        if not devices.resolve(str(data.get("id") or ""), text):
            _notify_owner(app, f"🖥️ {name}:\n{text}")  # tak ada yang menunggu (mis. Hub menyala ulang)
        return {"ok": True}, 200
    if path == "/api/devices/message":
        _notify_owner(app, f"🖥️ {name}: {data.get('text') or ''}")
        return {"ok": True}, 200
    if path == "/api/devices/confirm":
        return {"ok": True, "yes": _ask_owner(app, f"[🖥️ {name}] {data.get('text') or ''}")}, 200
    if path == "/api/devices/memory":
        action, text = data.get("action"), data.get("text") or ""
        if action == "add":
            app.memory.add(f"{text} (dari {name})")
        elif action == "set":
            app.memory.set(text)
        elif action == "clear":
            app.memory.clear()
        else:
            return {"ok": False, "error": "aksi memori tidak dikenal"}, 400
        return {"ok": True}, 200
    if path == "/api/devices/sync":
        return {"ok": True, **devices.sync_bundle(app)}, 200
    return {"ok": False, "error": "not found"}, 404


# --- login (dipakai kalau ITSME_DASHBOARD_PASSWORD diisi) --------------------------
_SESSIONS = {}  # session_id -> waktu kedaluwarsa (epoch)
_SESSION_TTL = 7 * 24 * 3600


def _new_session():
    now = time.time()
    for k in [k for k, exp in list(_SESSIONS.items()) if exp < now]:
        _SESSIONS.pop(k, None)
    sid = secrets.token_urlsafe(24)
    _SESSIONS[sid] = now + _SESSION_TTL
    return sid


def _valid_session(sid):
    exp = _SESSIONS.get(sid or "")
    return bool(exp) and exp > time.time()


def _check_login(user, password):
    return (
        bool(config.DASHBOARD_PASSWORD)
        and secrets.compare_digest(user or "", config.DASHBOARD_USER)
        and secrets.compare_digest(password or "", config.DASHBOARD_PASSWORD)
    )


# --- login dengan berkas kunci (berkas tetap yang wajib diunggah) ---
def _key_file():
    return os.path.join(config.DATA_DIR, "dashboard.key")


def load_or_make_key():
    """Kunci dashboard yang TETAP (tidak berubah). Pengguna menyimpan salinan
    berkasnya dan mengunggahnya untuk masuk."""
    try:
        with open(_key_file(), encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(48)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_key_file(), "w", encoding="utf-8") as f:
        f.write(key + "\n")
    try:
        os.chmod(_key_file(), 0o600)
    except OSError:
        pass
    return key


def _check_key(given, key):
    # Terima isi berkas apa adanya; cocokkan baris pertama non-kosong juga.
    given = (given or "").strip()
    if not given or not key:
        return False
    if secrets.compare_digest(given, key):
        return True
    first = next((ln.strip() for ln in given.splitlines() if ln.strip()), "")
    return bool(first) and secrets.compare_digest(first, key)


# Anti tebak-coba: kegagalan login per-IP -> jeda yang makin lama.
_LOGIN_FAILS = {}  # ip -> [jumlah_gagal, diblokir_sampai]
_LOCK_AFTER = 5     # mulai memblokir setelah sekian gagal
_LOCK_MAX = 3600    # blokir paling lama 1 jam


_LOCAL_PEERS = {"127.0.0.1", "::1", "localhost"}


def _proxy_peer_ok(peer):
    """Header proxy hanya dipercaya kalau koneksi datang dari tunnel di komputer ini.

    Tanpa cek ini, penyerang cukup mengirim CF-Connecting-IP berbeda tiap percobaan
    untuk lolos dari jeda anti brute-force (_LOGIN_FAILS).
    """
    return (peer or "").split("%")[0].strip().lower() in _LOCAL_PEERS


def _login_blocked(ip):
    rec = _LOGIN_FAILS.get(ip)
    return bool(rec and rec[1] > time.time())


def _login_fail(ip):
    rec = _LOGIN_FAILS.get(ip) or [0, 0]
    rec[0] += 1
    if rec[0] >= _LOCK_AFTER:
        rec[1] = time.time() + min(_LOCK_MAX, 30 * (2 ** (rec[0] - _LOCK_AFTER)))
    _LOGIN_FAILS[ip] = rec
    # Jangan tumbuh tanpa batas.
    if len(_LOGIN_FAILS) > 5000:
        for k in [k for k, v in list(_LOGIN_FAILS.items()) if v[1] < time.time()][:2000]:
            _LOGIN_FAILS.pop(k, None)


def _login_ok(ip):
    _LOGIN_FAILS.pop(ip, None)


def _make_handler(app, token):
    public = config.DASHBOARD_PUBLIC
    key_login = config.DASHBOARD_KEY_LOGIN or public
    key = load_or_make_key() if key_login else ""
    require_token = bool(token) and not key_login
    # Mode publik / berkas kunci: WAJIB login (tidak ada jalan pintas localhost/token).
    login_required = key_login or bool(config.DASHBOARD_PASSWORD)
    allow_domains = {d.strip().lower() for d in config.DASHBOARD_DOMAINS.split(",") if d.strip()}
    # Header keamanan di tiap respons. Inline dipakai halaman lama, jadi script/style
    # 'unsafe-inline' tetap diizinkan, tapi sumber luar & framing ditutup.
    _CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'")
    sec_headers = {
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
        "Content-Security-Policy": _CSP,
    }
    if public:
        sec_headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # jangan penuhi log bot dengan setiap permintaan

        # -- util --
        def _query(self):
            return parse_qs(urlparse(self.path).query)

        def _cookie(self, name):
            for part in (self.headers.get("Cookie") or "").split(";"):
                if part.strip().startswith(name + "="):
                    return part.split("=", 1)[1].strip()
            return ""

        def _client_ip(self):
            # Header proxy hanya dipercaya dari tunnel lokal, bukan dari klien internet.
            if config.DASHBOARD_TRUST_PROXY and _proxy_peer_ok(self.client_address[0]):
                cf = self.headers.get("CF-Connecting-IP")
                if cf:
                    return cf.strip()
                xff = self.headers.get("X-Forwarded-For")
                if xff:
                    return xff.split(",")[0].strip()
            return self.client_address[0]

        def _host_ok(self):
            if not allow_domains:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
            return host in allow_domains

        def _authed(self):
            if login_required:
                return _valid_session(self._cookie("itsme_session"))
            if not require_token:
                return True
            given = (
                self.headers.get("X-Itsme-Token")
                or (self._query().get("token") or [""])[0]
                or self._cookie("itsme_token")
            )
            return bool(given) and secrets.compare_digest(given, token)

        def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=None, headers=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in sec_headers.items():
                self.send_header(k, v)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200, cookie=None):
            self._send(code, json.dumps(obj), cookie=cookie)

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                return {}

        # -- GET --
        def do_GET(self):
            path = urlparse(self.path).path
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, "<h2>Domain tidak diizinkan</h2>", "text/html; charset=utf-8")
            if not self._authed():
                if login_required and path in ("/", "/index.html", "/login"):
                    return self._send(HTTPStatus.OK, _login_page(key_login, bool(config.DASHBOARD_PASSWORD)), "text/html; charset=utf-8")
                if not login_required and path in ("/", "/index.html"):
                    return self._send(HTTPStatus.UNAUTHORIZED, _NEED_TOKEN_HTML, "text/html; charset=utf-8")
                return self._json({"error": "unauthorized"}, 401)
            if path in ("/", "/index.html", "/login"):
                cookie = None
                if require_token and not login_required:
                    cookie = f"itsme_token={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
                return self._send(HTTPStatus.OK, _PAGE, "text/html; charset=utf-8", cookie=cookie)
            if path == "/api/keyfile":
                if not key_login:
                    return self._json({"error": "login berkas kunci tidak aktif"}, 404)
                return self._send(HTTPStatus.OK, key + "\n", "text/plain; charset=utf-8",
                                  headers={"Content-Disposition": "attachment; filename=\"dashboard.key\""})
            if path == "/api/status":
                return self._json(status(app))
            if path == "/api/memory":
                return self._json({"text": app.memory.text()})
            if path == "/api/logs":
                return self._json({"text": _logs()})
            if path == "/api/info":
                return self._json(info())
            if path == "/api/chat":  # polling jawaban chat
                return self._chat_poll()
            if path == "/api/fs/list":
                data, err = _list_dir((self._query().get("path") or [""])[0])
                return self._json(data or {"error": err}, 200 if data else 400)
            if path == "/api/fs/read":
                data, err = _read_file((self._query().get("path") or [""])[0])
                return self._json(data or {"error": err}, 200 if data else 400)
            if path == "/api/drive/status":
                from . import drive
                return self._json({"ready": drive.ready()})
            if path == "/api/drive/list":
                data, err = drive_list((self._query().get("folder") or [""])[0])
                return self._json(data or {"error": err}, 200 if data else 400)
            if path == "/api/drive/search":
                data, err = drive_search((self._query().get("q") or [""])[0])
                return self._json(data or {"error": err}, 200 if data else 400)
            if path == "/api/devices":
                from . import devices
                # Dua jalur koneksi, satu tampilan: perangkat It's Me (poll) dan komputer
                # yang tersambung lewat It's Me Node / jembatan. Dulu tab ini hanya
                # membaca devices.json, jadi komputer yang sudah terhubung tetap tampil
                # sebagai "belum ada komputer yang terhubung".
                code, _ct, body, _cd = _node_get("/status", {})
                try:
                    st = json.loads(body) if code == 200 else {}
                except ValueError:
                    st = {}
                comps = [{"name": n.get("name"), "id": n.get("id"), "via": "node"}
                         for n in st.get("nodes") or [] if n.get("name")]
                comps += [{"name": d.get("name"), "id": "bridge:" + (d.get("name") or ""), "via": "bridge"}
                          for d in hp.listing() if d.get("kind") == "computer" and d.get("name")]
                return self._json({"devices": devices.listing(), "computers": comps,
                                   "is_hub": not config.AGENT_MODE, "target": devices.get_target()})
            # -- komputer lain lewat It's Me Node (Tailscale) ATAU jembatan (tanpa Tailscale) --
            if path == "/api/node/status":
                code, ctype, body, _ = _node_get("/status", {})
                try:
                    st = json.loads(body)
                except ValueError:
                    st = {"nodes": [], "phase": "?", "epoch": 0}
                names = {n.get("name") for n in st.get("nodes", [])}
                for d in hp.listing():
                    if d.get("kind") == "computer" and d["online"] and d["name"] not in names:
                        st.setdefault("nodes", []).append({
                            "id": "bridge:" + d["name"], "name": d["name"], "addr": "jembatan",
                            "working": False, "model": "", "score": 0, "stats": {}, "bot": False,
                            "leader": False, "via": "bridge"})
                return self._json(st)
            if path == "/api/node/screen":
                q = self._query()
                bn = _bridge_name((q.get("node") or [""])[0])
                if bn is not None:
                    data = hp.command(bn, "screen", {"w": (q.get("w") or ["1280"])[0]})
                    if isinstance(data, (bytes, bytearray)) and data:
                        ct = "image/png" if bytes(data[:8]) == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                        return self._send(HTTPStatus.OK, bytes(data), ct)
                    return self._json({"error": "komputer (jembatan) tidak menjawab"}, 504)
                code, ctype, body, _ = _node_get(
                    "/screen", {"node": (q.get("node") or [""])[0], "w": (q.get("w") or ["1280"])[0]})
                return self._send(code, body, ctype)
            if path in ("/api/node/fs/list", "/api/node/fs/read"):
                q = self._query()
                bn = _bridge_name((q.get("node") or [""])[0])
                if bn is not None:
                    ctype_cmd = "fs_list" if path.endswith("list") else "fs_read"
                    data = hp.command(bn, ctype_cmd, {"path": (q.get("path") or [""])[0]})
                    if isinstance(data, dict):
                        return self._json(data, 200 if not data.get("error") else 400)
                    return self._json({"error": "komputer (jembatan) tidak menjawab"}, 504)
                code, ctype, body, _ = _node_get(
                    path[len("/api/node"):], {"node": (q.get("node") or [""])[0], "path": (q.get("path") or [""])[0]})
                return self._send(code, body, ctype)
            if path == "/api/node/fs/download":
                q = self._query()
                code, ctype, body, disp = _node_get(
                    "/fs/download", {"node": (q.get("node") or [""])[0], "path": (q.get("path") or [""])[0]})
                return self._send(code, body, ctype, headers={"Content-Disposition": disp} if disp else None)
            # -- HP (aplikasi It's Me HP) --
            if path == "/api/hp":
                hp.forget_stale()
                return self._json({"phones": hp.listing(), "token": hp.token(),
                                   "code": hp.pair_code()})
            if path == "/api/hp/build":  # Pembuat Aplikasi: status build APK
                from . import hpbuild
                return self._json(hpbuild.status())
            if path == "/api/hp/screen":
                q = self._query()
                data = hp.command((q.get("name") or [""])[0], "screen", {"w": (q.get("w") or ["1080"])[0]})
                if isinstance(data, (bytes, bytearray)) and data:
                    ctype = "image/png" if bytes(data[:8]) == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                    return self._send(HTTPStatus.OK, bytes(data), ctype)
                return self._json({"error": "HP tidak menjawab (offline, atau izin layar belum diberikan)"}, 504)
            if path in ("/api/hp/fs/list", "/api/hp/fs/read"):
                q = self._query()
                ctype = "fs_list" if path.endswith("list") else "fs_read"
                data = hp.command((q.get("name") or [""])[0], ctype, {"path": (q.get("path") or [""])[0]})
                if isinstance(data, dict):
                    return self._json(data, 200 if not data.get("error") else 400)
                return self._json({"error": "HP tidak menjawab"}, 504)
            return self._json({"error": "not found"}, 404)

        # -- aplikasi HP (long-poll, auth pakai token HP, bukan sesi dashboard) --
        def _hp_ingress(self, path):
            if not self._host_ok():
                return self._json({"error": "domain tidak diizinkan"}, 403)
            if path == "/api/hp/frame":  # bingkai layar = bytes mentah (JPEG)
                q = self._query()
                if not hp.check_token((q.get("token") or [""])[0], self._client_ip()):
                    return self._json({"error": "token HP salah"}, 401)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n > 0 else b""
                hp.result((q.get("id") or [""])[0], body)
                hp.seen((q.get("name") or [""])[0])
                return self._json({"ok": True})
            data = self._body()
            if not hp.check_token(data.get("token", ""), self._client_ip()):
                return self._json({"error": "token HP salah"}, 401)
            if path == "/api/hp/poll":
                cmd = hp.poll(data.get("name", "HP"), data.get("caps") or [], kind=data.get("kind", "hp"))
                return self._json(cmd or {})
            if path == "/api/hp/result":
                hp.result(data.get("id", ""), data.get("value"))
                hp.seen(data.get("name", ""))
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)

        # -- komputer baru bergabung TANPA Tailscale: tukar undangan / sidik jari --
        # Diteruskan ke It's Me Node di komputer ini (127.0.0.1), yang memvalidasi
        # kodenya; token jembatan ditambahkan supaya komputer baru bisa menyambung balik.
        def _node_join_ingress(self, path):
            if not self._host_ok():
                return self._json({"error": "domain tidak diizinkan"}, 403)
            if config.AGENT_MODE:
                return self._json({"error": "komputer ini perangkat, bukan Hub"}, 404)
            data = self._body()
            name = str(data.get("name") or "")[:60]
            if path == "/api/node/invite":
                code = str(data.get("code") or "").strip()
                if not code:
                    return self._json({"error": "kode undangan kosong"}, 400)
                status, body = _node_post("/invite", {"code": code, "name": name},
                                          self._client_ip())
            else:  # /api/node/enroll: gabung-ulang pakai sidik jari perangkat keras
                fp = str(data.get("fingerprint") or "").strip()
                if not fp:
                    return self._json({"error": "sidik jari kosong"}, 400)
                status, body = _node_post("/enroll", {"fingerprint": fp, "name": name},
                                          self._client_ip())
            if status != 200:
                return self._send(status, body)
            try:
                obj = json.loads(body)
            except ValueError:
                obj = {}
            obj["bridge_token"] = hp.token()  # untuk menyambung KELUAR ke dashboard ini
            return self._json(obj)

        # -- POST --
        def do_POST(self):
            path = urlparse(self.path).path
            if path in _HP_INGRESS:
                return self._hp_ingress(path)
            if path in _NODE_INGRESS:
                return self._node_join_ingress(path)
            if self.headers.get(_GUARD_HEADER) != "1":
                return self._json({"error": "permintaan ditolak (tanpa header dashboard)"}, 403)
            if not self._host_ok():
                return self._json({"error": "domain tidak diizinkan"}, 403)
            data = self._body()
            if path in _DEVICE_PATHS:  # dari perangkat ke Hub (secret, bukan login)
                obj, code = device_api(app, path, self.client_address[0], data)
                return self._json(obj, code)
            if path == "/login":
                ip = self._client_ip()
                if _login_blocked(ip):
                    return self._json({"ok": False, "error": "Terlalu banyak percobaan. Coba lagi nanti."}, 429)
                ok = (key_login and _check_key(data.get("key", ""), key)) or _check_login(data.get("user", ""), data.get("password", ""))
                if ok:
                    _login_ok(ip)
                    cookie = f"itsme_session={_new_session()}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_SESSION_TTL}"
                    return self._json({"ok": True}, cookie=cookie)
                _login_fail(ip)
                err = "Berkas kunci salah." if key_login else "User atau password salah."
                return self._json({"ok": False, "error": err}, 401)
            if path == "/logout":
                _SESSIONS.pop(self._cookie("itsme_session"), None)
                return self._json({"ok": True}, cookie="itsme_session=; Path=/; Max-Age=0")
            if not self._authed():
                return self._json({"error": "unauthorized"}, 401)
            if path == "/api/brain":
                ok, msg = _switch_brain(app, data.get("backend", ""))
            elif path == "/api/access":
                ok, msg = _set_access(data.get("full"))
            elif path == "/api/memory":
                ok, result = _memory(app, data.get("action", ""), data.get("text", ""))
                return self._json({"ok": ok, "text": result} if ok else {"ok": False, "error": result}, 200 if ok else 400)
            elif path == "/api/chat":
                return self._chat_start(app, data)
            elif path == "/api/fs/open":
                ok, msg = _open_path(app, data.get("path", ""))
            elif path == "/api/fs/run":
                ok, msg = _run_command(app, data.get("command", ""))
            elif path == "/api/fs/zip":
                return self._json({"ok": True, "message": _zip(app, data.get("sumber", ""), data.get("tujuan", ""))})
            elif path == "/api/fs/extract":
                return self._json({"ok": True, "message": _extract(app, data.get("arsip", ""), data.get("tujuan", ""))})
            elif path == "/api/drive/upload":
                return self._json({"message": drive_action(app, "drive_unggah", {"path_lokal": data.get("path_lokal", ""), "folder_id": data.get("folder_id", ""), "nama": data.get("nama", "")})})
            elif path == "/api/drive/download":
                return self._json({"message": drive_action(app, "drive_unduh", {"file_id": data.get("file_id", ""), "path_lokal": data.get("path_lokal", "")})})
            elif path == "/api/drive/manage":
                return self._json({"message": drive_action(app, "drive_kelola", {k: data.get(k, "") for k in ("aksi", "file_id", "folder_id", "nama")})})
            elif path == "/api/node/fs/write":
                bn = _bridge_name(data.get("node", ""))
                if bn is not None:
                    return self._json({"error": "komputer lewat jembatan belum mendukung unggah"}, 400)
                code, body = _node_post("/fs/write",
                                        {"path": data.get("path", ""), "b64": data.get("b64", "")})
                return self._send(code, body)
            elif path == "/api/node/input":
                bn = _bridge_name(data.get("node", ""))
                if bn is not None:
                    res = hp.command(bn, "input", {"events": data.get("events") or []}, wait=8)
                    return self._json(res if isinstance(res, dict) else {"error": "tidak menjawab"},
                                      200 if isinstance(res, dict) else 504)
                code, body = _node_post("/input", data)
                return self._send(code, body)
            elif path == "/api/hp/input":
                res = hp.command(data.get("name", ""), "input", {"events": data.get("events") or []}, wait=8)
                if isinstance(res, dict):
                    return self._json(res)
                return self._json({"error": "HP tidak menjawab"}, 504)
            elif path in ("/api/hp/uitree", "/api/hp/shell", "/api/hp/devowner"):
                ctype = path.rsplit("/", 1)[-1]
                args = {"cmd": data.get("cmd", "")} if ctype == "shell" else None
                res = hp.command(data.get("name", ""), ctype, args, wait=20)
                if isinstance(res, dict):
                    return self._json(res)
                return self._json({"error": "HP tidak menjawab"}, 504)
            elif path == "/api/hp/build":  # Pembuat Aplikasi: picu build APK
                from . import hpbuild
                try:
                    hpbuild.trigger(data)
                except hpbuild.BuildError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 400)
                return self._json({"ok": True})
            elif path == "/api/devices/forget":
                from . import devices
                return self._json({"ok": devices.forget(data.get("name", ""))})
            elif path == "/api/devices/target":
                from . import devices
                dev = devices.find(data.get("name") or "") if data.get("name") else None
                if data.get("name") and not dev:
                    return self._json({"ok": False, "error": "perangkat tidak ditemukan"}, 400)
                devices.set_target(dev["name"] if dev else None)
                return self._json({"ok": True, "target": devices.get_target()})
            else:
                return self._json({"error": "not found"}, 404)
            return self._json({"ok": ok, "message": msg, "status": status(app)}, 200 if ok else 400)

        # -- chat (mulai + polling) --
        def _chat_start(self, app, data):
            text = (data.get("text") or "").strip()
            if not text:
                return self._json({"error": "pesan kosong"}, 400)
            if not app.loop:
                return self._json({"error": "bot belum siap"}, 503)
            for jid in [k for k, f in list(_JOBS.items()) if f.done()][20:]:
                _JOBS.pop(jid, None)  # buang job lama yang sudah selesai & tak diambil
            job = secrets.token_hex(8)
            _JOBS[job] = asyncio.run_coroutine_threadsafe(app.ask(text), app.loop)
            return self._json({"job": job})

        def _chat_poll(self):
            job = (self._query().get("id") or [""])[0]
            fut = _JOBS.get(job)
            if fut is None:
                return self._json({"error": "job tidak ada"}, 404)
            if not fut.done():
                return self._json({"done": False})
            _JOBS.pop(job, None)
            try:
                reply = fut.result()
            except Exception as exc:  # noqa: BLE001
                reply = f"⚠️ Error: {type(exc).__name__}: {exc}"
            return self._json({"done": True, "reply": reply})

    return Handler


def start(app):
    """Nyalakan dashboard di thread terpisah. Kembalikan server (atau None)."""
    if not config.DASHBOARD_ENABLED:
        return None
    host = config.DASHBOARD_HOST
    on_lan = host not in _LOOPBACK
    key_login = config.DASHBOARD_KEY_LOGIN or config.DASHBOARD_PUBLIC
    # Login (password/berkas kunci) aktif -> token tidak dipakai.
    token = _load_or_make_token() if (on_lan and not config.DASHBOARD_PASSWORD and not key_login) else ""
    try:
        server = ThreadingHTTPServer((host, config.DASHBOARD_PORT), _make_handler(app, token))
    except OSError as exc:
        log.warning("Dashboard tidak bisa dinyalakan di %s:%s (%s)", host, config.DASHBOARD_PORT, exc)
        return None
    threading.Thread(target=server.serve_forever, name="itsme-dashboard", daemon=True).start()
    shown = "localhost" if not on_lan else host
    log.info("Dashboard: http://%s:%s", shown, config.DASHBOARD_PORT)
    if key_login:
        load_or_make_key()
        log.info("Dashboard%s minta BERKAS KUNCI untuk masuk: %s",
                 " (mode publik)" if config.DASHBOARD_PUBLIC else "", _key_file())
    elif config.DASHBOARD_PASSWORD:
        log.info("Dashboard pakai login (user: %s).", config.DASHBOARD_USER)
    elif on_lan:
        log.info("Dashboard token (buka ...?token=... sekali): %s", token)
    return server


_NEED_TOKEN_HTML = (
    "<!doctype html><meta charset=utf-8><body style='font-family:system-ui;padding:2rem'>"
    "<h2>Token diperlukan</h2><p>Buka halaman ini dengan menambahkan <code>?token=TOKEN</code> "
    "di alamatnya. Token ada di berkas <code>data/dashboard_token.txt</code> di komputer, "
    "dan ditampilkan di log It's Me saat menyala.</p></body>"
)

_LOGIN_PAGE = r"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Masuk · It's Me</title>
<style>
:root{--bg:#eef1f6;--card:#fff;--text:#13161d;--muted:#616b7a;--border:#e2e6ee;--accent:#6366f1;--accent2:#8b5cf6;--accent-text:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#0b0d12;--card:#161922;--text:#e9ecf3;--muted:#9aa3b2;--border:#262b36;--accent:#818cf8;--accent2:#a78bfa;--accent-text:#0b0d12}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  background:radial-gradient(900px 500px at 100% -10%,color-mix(in srgb,var(--accent) 16%,transparent),transparent),
             radial-gradient(800px 460px at -10% 0,color-mix(in srgb,var(--accent2) 16%,transparent),transparent),var(--bg)}
.box{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:28px;width:min(360px,92vw);box-shadow:0 20px 50px rgba(20,25,40,.14)}
h1{margin:0 0 4px;font-size:1.3rem}
h1 b{background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
p{margin:0 0 18px;color:var(--muted);font-size:.9rem}
label{display:block;font-size:.8rem;color:var(--muted);margin:12px 0 5px}
input{width:100%;background:transparent;color:var(--text);border:1px solid var(--border);border-radius:11px;padding:11px;font:inherit}
input:focus{outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent)}
button{width:100%;margin-top:20px;border:0;border-radius:11px;padding:12px;cursor:pointer;font:inherit;font-weight:700;
  color:var(--accent-text);background:linear-gradient(90deg,var(--accent),var(--accent2))}
.err{color:#e11d48;font-size:.85rem;margin-top:12px;min-height:1em}
.file{border-style:dashed;cursor:pointer;padding:14px}
</style></head><body>
__BODY__
</body></html>"""

_LOGIN_KEY_BLOCK = (
    '<label>Berkas kunci (dashboard.key)</label>'
    '<input id="keyfile" class="file" type="file" accept=".key,text/plain">'
    '<p style="margin:8px 0 0;font-size:.8rem">Pilih berkas <b>dashboard.key</b> milikmu. '
    'Berkas ini tetap dan jadi satu-satunya cara masuk.</p>'
)
_LOGIN_PW_BLOCK = (
    '<label>User</label><input id="user" value="admin" autocomplete="username">'
    '<label>Password</label><input id="pw" type="password" autocomplete="current-password">'
)
_LOGIN_JS = r"""<script>
async function masuk(e){
  e.preventDefault();
  const body = {};
  const fe = document.getElementById('keyfile');
  if(fe && fe.files && fe.files[0]){ try{ body.key = await fe.files[0].text(); }catch(_){ } }
  const u = document.getElementById('user'), p = document.getElementById('pw');
  if(u && p && p.value){ body.user = u.value; body.password = p.value; }
  if(!body.key && !body.password){ document.getElementById('err').textContent = 'Pilih berkas kunci (atau isi password).'; return false; }
  const r = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json','X-Itsme-Dashboard':'1'}, body:JSON.stringify(body)});
  const d = await r.json().catch(()=>({}));
  if(r.ok && d.ok) location.href = '/';
  else document.getElementById('err').textContent = (d.error || 'Gagal masuk.');
  return false;
}
</script>"""


def _login_page(key_login, password_login):
    fields = ""
    if key_login:
        fields += _LOGIN_KEY_BLOCK
    if password_login:
        fields += _LOGIN_PW_BLOCK
    if not fields:  # seharusnya tak terjadi; jaga-jaga
        fields = _LOGIN_PW_BLOCK
    body = (
        '<form class="box" onsubmit="return masuk(event)">'
        '<h1>🫡 <b>It\'s Me</b></h1>'
        '<p>Masuk untuk membuka dashboard.</p>'
        + fields +
        '<button type="submit">Masuk</button>'
        '<div class="err" id="err"></div></form>' + _LOGIN_JS
    )
    return _LOGIN_PAGE.replace("__BODY__", body)

_PAGE = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>It's Me</title>
<style>
:root{
  --bg:#f5f6f8; --bg2:#eceef2; --card:#ffffff; --text:#101319; --muted:#5b6472; --border:#e6e8ee;
  --accent:#2f6df6; --accent2:#2f6df6; --accent-soft:rgba(47,109,246,.12); --accent-text:#ffffff;
  --ok:#1a9e5b; --danger:#e0384e; --warnbg:#fff4d6; --warntext:#8a5a00;
  --shadow:0 1px 2px rgba(16,19,25,.04),0 8px 24px rgba(16,19,25,.06); --radius:14px; --nav:232px;
}
:root[data-theme="dark"]{
  --bg:#0d0f14; --bg2:#141821; --card:#151924; --text:#e8ebf1; --muted:#949db0; --border:#242a37;
  --accent:#5b8dff; --accent2:#5b8dff; --accent-soft:rgba(91,141,255,.16); --accent-text:#0b0e14;
  --ok:#36c784; --danger:#ff6274; --warnbg:#2f2710; --warntext:#f3c969;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 30px rgba(0,0,0,.4);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0d0f14; --bg2:#141821; --card:#151924; --text:#e8ebf1; --muted:#949db0; --border:#242a37;
  --accent:#5b8dff; --accent2:#5b8dff; --accent-soft:rgba(91,141,255,.16); --accent-text:#0b0e14;
  --ok:#36c784; --danger:#ff6274; --warnbg:#2f2710; --warntext:#f3c969;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 30px rgba(0,0,0,.4);
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;color:var(--text);background:var(--bg);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,Inter,sans-serif;line-height:1.55;font-size:15px;-webkit-font-smoothing:antialiased}
.topbar{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:12px;height:56px;padding:0 16px;
  background:color-mix(in srgb,var(--card) 88%,transparent);backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:9px;font-size:1.02rem;font-weight:750;letter-spacing:-.01em}
.brand b{font-weight:750}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 22%,transparent)}
.spacer{flex:1}
.icon{display:inline-flex;align-items:center;justify-content:center;min-width:38px;height:38px;border:1px solid var(--border);
  background:var(--card);color:var(--text);border-radius:10px;padding:0 11px;cursor:pointer;font:inherit;transition:.15s}
.icon:hover{border-color:var(--accent);color:var(--accent)}
.badges{display:none;gap:7px}
@media(min-width:720px){.badges{display:flex}}
.badge{font-size:.76rem;padding:4px 11px;border-radius:999px;background:var(--bg2);border:1px solid var(--border);color:var(--muted);white-space:nowrap}
.badge.ok{color:var(--ok)} .badge.bad{color:var(--danger)}
.tabs{position:sticky;top:56px;z-index:20;display:flex;gap:6px;overflow-x:auto;padding:9px 12px;scrollbar-width:none;
  background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
.tabs::-webkit-scrollbar{display:none}
.tabs button{flex:0 0 auto;display:flex;align-items:center;gap:8px;border:0;background:transparent;color:var(--muted);
  padding:9px 14px;border-radius:10px;cursor:pointer;font:inherit;font-weight:600;font-size:.92rem;transition:.14s;white-space:nowrap}
.tabs button:hover{color:var(--text);background:var(--bg2)}
.tabs button.active{color:var(--accent);background:var(--accent-soft)}
@media(min-width:900px){
  .tabs{position:fixed;top:56px;left:0;bottom:0;width:var(--nav);flex-direction:column;gap:3px;overflow-y:auto;
    padding:14px 12px;border-right:1px solid var(--border);border-bottom:0;background:var(--card)}
  .tabs button{width:100%;font-size:.95rem;padding:11px 13px}
  .tabs button.active{box-shadow:inset 3px 0 0 var(--accent)}
}
.wrap{max-width:820px;margin:0 auto;padding:20px 16px 40px}
@media(min-width:900px){.wrap{margin-left:var(--nav);max-width:880px;padding:26px 30px 52px}}
.panel{display:none;animation:fade .22s ease}
.panel.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px;box-shadow:var(--shadow)}
.card h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 14px;font-weight:700}
.big{font-size:1.05rem;font-weight:650}
.muted{color:var(--muted)}
.row{display:flex;flex-wrap:wrap;gap:8px}
button{font:inherit;cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--text);padding:10px 15px;border-radius:10px;font-weight:600;transition:.14s}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.5;cursor:not-allowed}
button.active,button.primary{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
button.active:hover:not(:disabled),button.primary:hover:not(:disabled){filter:brightness(1.07);color:var(--accent-text)}
button.danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 38%,var(--border))}
button.danger:hover:not(:disabled){border-color:var(--danger);color:var(--danger)}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.82rem;font-weight:700}
.pill.safe{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}
.pill.full{background:var(--warnbg);color:var(--warntext)}
textarea{width:100%;min-height:150px;resize:vertical;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:12px;font:inherit}
pre{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:12px;overflow:auto;max-height:320px;font-size:.8rem;margin:0;white-space:pre-wrap;word-break:break-word}
.sub{font-size:.85rem;color:var(--muted);margin-top:6px}
input,select{background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font:inherit}
input:focus,textarea:focus,select:focus{outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent);outline-offset:1px;border-color:var(--accent)}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(10px);background:var(--text);color:var(--bg);
  padding:12px 18px;border-radius:12px;opacity:0;transition:.25s;pointer-events:none;max-width:90%;box-shadow:var(--shadow);z-index:50;font-weight:600}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.spread{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.chat{display:flex;flex-direction:column;gap:10px;max-height:56vh;min-height:240px;overflow:auto;padding:4px}
.chat:empty::before{content:"Belum ada pesan. Sapa It's Me di bawah.";color:var(--muted);font-size:.9rem;margin:auto}
.msg{max-width:84%;padding:11px 14px;border-radius:16px;white-space:pre-wrap;word-break:break-word;font-size:.95rem}
.msg.user{align-self:flex-end;background:var(--accent);color:var(--accent-text);border-bottom-right-radius:5px}
.msg.bot{align-self:flex-start;background:var(--bg2);border:1px solid var(--border);border-bottom-left-radius:5px}
.msg.thinking{opacity:.7;font-style:italic}
.chatbar{display:flex;gap:8px;margin-top:14px}
.chatbar input{flex:1}
.fslist{max-height:300px;overflow:auto;border:1px solid var(--border);border-radius:12px;margin-top:10px;background:var(--bg2)}
.fsrow{display:flex;align-items:center;gap:10px;padding:10px 13px;cursor:pointer;border-bottom:1px solid var(--border);font-size:.9rem}
.fsrow:last-child{border-bottom:0}
.fsrow:hover{background:var(--accent-soft)}
.fsrow.sel{background:var(--accent-soft)}
.fsrow .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fsrow .sz{color:var(--muted);font-size:.78rem}
.fsrow.secret{opacity:.55}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand"><span>🫡</span><b id="name">It's Me</b><span class="dot" title="sedang jalan"></span></div>
  <div class="badges" id="info">
    <span id="bUptime" class="badge"></span>
    <span id="bOllama" class="badge">Ollama…</span>
    <span id="bQuota" class="badge" hidden></span>
  </div>
  <div class="spacer"></div>
  <button class="icon" id="themeBtn" onclick="toggleTheme()" title="Ganti tema">🌙</button>
  <button class="icon" id="logoutBtn" onclick="logout()" hidden>Keluar</button>
</div>
<nav class="tabs" id="tabs">
  <button data-tab="chat" class="active">💬 Ngobrol</button>
  <button data-tab="settings">⚙️ Pengaturan</button>
  <button data-tab="files">📁 Berkas</button>
  <button data-tab="komputer">🖥️ Komputer</button>
  <button data-tab="hp">📱 HP</button>
  <button data-tab="drive">☁️ Drive</button>
  <button data-tab="devices">🖥️ Perangkat</button>
  <button data-tab="memori">🧠 Memori</button>
  <button data-tab="log">📜 Log</button>
</nav>
<main class="wrap">
  <section data-panel="chat" class="panel active">
  <div class="card">
    <h2>Ngobrol dengan It's Me</h2>
    <div id="chat" class="chat"></div>
    <div class="chatbar">
      <input id="chatInput" placeholder="Ketik pesan untuk It's Me…" autocomplete="off">
      <button class="primary" onclick="sendChat()">Kirim</button>
    </div>
    <div class="sub">Berbagi konteks dengan Telegram. Saat akses aman, konfirmasi aksi muncul di Telegram.</div>
  </div>

  </section>
  <section data-panel="settings" class="panel">
  <div class="card">
    <h2>Otak</h2>
    <div class="big" id="brainDesc">…</div>
    <div class="row" id="brains" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2>Akses</h2>
    <div class="spread">
      <div><span id="accessPill" class="pill">…</span><div class="sub" id="accessSub"></div></div>
      <div class="row">
        <button id="btnSafe" onclick="setAccess(false)">Aman</button>
        <button id="btnFull" onclick="setAccess(true)">Penuh</button>
      </div>
    </div>
  </div>

  </section>
  <section data-panel="files" class="panel">
  <div class="card">
    <h2>Berkas komputer</h2>
    <div class="row" style="gap:6px">
      <button onclick="fsUp()">↑ Naik</button>
      <input id="fsPath" style="flex:1" placeholder="path folder…" autocomplete="off">
      <button onclick="fsGo()">Buka</button>
    </div>
    <div id="fsList" class="fslist"></div>
    <div class="row" style="margin-top:10px">
      <button id="fsViewBtn" onclick="fsView()" disabled>Lihat</button>
      <button class="primary" id="fsOpenBtn" onclick="fsOpen()" disabled>Buka di aplikasi</button>
      <button onclick="fsZip()">Zip folder ini</button>
    </div>
    <pre id="fsFile" hidden></pre>
    <div class="sub" style="margin-top:8px">Ekstrak arsip:
      <input id="fsArc" placeholder="path .zip / .tar" style="width:52%">
      <button onclick="fsExtract()">Ekstrak</button></div>
  </div>

  <div class="card">
    <h2>Perintah</h2>
    <div class="row">
      <input id="cmd" style="flex:1" placeholder="mis. Start-Process notepad" autocomplete="off">
      <button class="primary" onclick="runCmd()">Jalankan</button>
    </div>
    <pre id="cmdOut" hidden></pre>
    <div class="sub">Perintah yang mengubah sesuatu minta persetujuan di Telegram saat akses aman; berkas rahasia dijaga.</div>
  </div>

  </section>
  <section data-panel="komputer" class="panel">
  <div class="card">
    <h2 class="spread">Komputer <button onclick="kompLoad()" style="padding:5px 11px;font-size:.8rem">Muat ulang</button></h2>
    <div id="kompPcs" class="row" style="gap:6px;flex-wrap:wrap"></div>
    <div class="row" style="gap:6px;margin-top:10px">
      <button id="kompTabScreen" class="primary" onclick="kompShow('screen')">🖥️ Layar</button>
      <button id="kompTabFiles" onclick="kompShow('files')">📁 Berkas</button>
    </div>
    <div id="kompScreen">
      <div class="row" style="gap:10px;flex-wrap:wrap;margin:8px 0;align-items:center">
        <label><input type="checkbox" id="kompCtrl"> Kendalikan (sentuh / mouse)</label>
        <label>Lebar <select id="kompW"><option>800</option><option selected>1280</option><option>1600</option><option>1920</option></select></label>
        <label>Jeda <select id="kompRate"><option value="250">cepat</option><option value="500" selected>sedang</option><option value="1000">hemat</option></select></label>
        <button onclick="kompSnap()">Ambil sekali</button>
        <span class="sub" id="kompInfo"></span>
      </div>
      <div id="kompWrap" tabindex="0" style="position:relative;background:#000;border:1px solid var(--line,#2a2f3a);border-radius:10px;overflow:hidden;outline:none">
        <img id="kompImg" alt="layar" style="display:block;max-width:100%;height:auto;user-select:none;-webkit-user-select:none">
      </div>
      <div id="kompKbd" class="row" style="gap:6px;margin-top:8px;flex-wrap:wrap">
        <input id="kompText" placeholder="ketik teks lalu Kirim / Enter" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" style="flex:1;min-width:150px">
        <button onclick="kompTypeSend()">Kirim</button>
      </div>
      <div class="row" id="kompKeys" style="gap:5px;margin-top:6px;flex-wrap:wrap">
        <button onclick="kompKey('escape')">Esc</button>
        <button onclick="kompKey('tab')">Tab</button>
        <button onclick="kompKey('enter')">⏎</button>
        <button onclick="kompKey('backspace')">⌫</button>
        <button onclick="kompKey('delete')">Del</button>
        <button onclick="kompKey('up')">↑</button>
        <button onclick="kompKey('down')">↓</button>
        <button onclick="kompKey('left')">←</button>
        <button onclick="kompKey('right')">→</button>
        <button onclick="kompKey('c',{ctrl:1})">Ctrl+C</button>
        <button onclick="kompKey('v',{ctrl:1})">Ctrl+V</button>
        <button onclick="kompKey('tab',{alt:1})">Alt+Tab</button>
        <button onclick="kompKey('escape',{ctrl:1})">⊞ Start</button>
      </div>
      <div class="sub" id="kompSelfHint" hidden style="margin-top:8px"></div>
      <div class="sub" style="margin-top:8px">
        <b>Di HP (sentuh):</b> ketuk = klik, ketuk 2× = klik ganda, tahan = klik kanan, seret = seret, dua jari = gulir.
        Ketik lewat kotak di atas. <b>Di komputer:</b> mouse &amp; keyboard biasa. Gambar diperbarui berkala (ada sedikit jeda).
      </div>
    </div>
    <div id="kompFiles" hidden>
      <div class="row" style="gap:6px;margin:8px 0">
        <button onclick="kompUp()">↑ Naik</button>
        <input type="file" id="kompUpFile" hidden onchange="kompUpload(this)">
        <input id="kompPath" style="flex:1" placeholder="C:\ atau /home/…" autocomplete="off">
        <button onclick="kompGo()">Buka</button>
        <button onclick="document.getElementById('kompUpFile').click()">⬆ Unggah</button>
      </div>
      <div id="kompList" class="fslist"></div>
      <pre id="kompPrev" hidden></pre>
    </div>
  </div>

  </section>
  <section data-panel="hp" class="panel">
  <div class="card">
    <h2 class="spread">Pembuat Aplikasi HP <button onclick="hpbLoad()" style="padding:5px 11px;font-size:.8rem">Muat ulang</button></h2>
    <div class="sub">Ubah nama, warna ikon, dan ID aplikasi It's Me HP, lalu buat APK-nya langsung dari sini (dibangun otomatis di GitHub).</div>
    <div id="hpbSetup" class="sub" style="margin-top:8px"></div>
    <div id="hpbForm" hidden style="margin-top:10px">
      <div class="row" style="gap:10px;flex-wrap:wrap">
        <label style="flex:1;min-width:150px">Nama aplikasi<br><input id="hpbName" placeholder="It's Me HP" autocomplete="off" style="width:100%"></label>
        <label>Warna ikon<br><input id="hpbAccent" type="color" value="#4F8CFF"></label>
      </div>
      <div class="row" style="gap:10px;flex-wrap:wrap;margin-top:8px">
        <label style="flex:1;min-width:150px">Application ID (opsional)<br><input id="hpbId" placeholder="com.itsme.hp" autocomplete="off" style="width:100%"></label>
        <label style="width:110px">Versi (opsional)<br><input id="hpbVer" placeholder="0.2" autocomplete="off" style="width:100%"></label>
      </div>
      <div class="row" style="gap:10px;margin-top:12px;align-items:center;flex-wrap:wrap">
        <button class="primary" id="hpbGo" onclick="hpbBuild()">⚙️ Buat APK</button>
        <a id="hpbDl" href="#" target="_blank" rel="noopener" hidden>⬇️ Unduh APK terbaru</a>
        <span class="sub" id="hpbMsg"></span>
      </div>
      <div class="sub" style="margin-top:8px">Build di GitHub ±3-6 menit. Kalau diminta token: kirim <code>/kunci &lt;token GitHub&gt;</code> ke bot (butuh izin Actions). Setelah selesai, tautan unduh muncul; pasang di HP seperti biasa. Untuk APK bertanda tangan rilis (lebih tepercaya &amp; update mulus), jalankan workflow <b>make-keystore</b> sekali di repo itsme-hp lalu tambahkan secret-nya (lihat README).</div>
    </div>
  </div>
  <div class="card">
    <h2 class="spread">HP (aplikasi It's Me HP) <button onclick="hpLoad()" style="padding:5px 11px;font-size:.8rem">Muat ulang</button></h2>
    <div id="hpPcs" class="row" style="gap:6px;flex-wrap:wrap"></div>
    <div id="hpSetup" class="sub" style="margin-top:8px"></div>
    <div id="hpBody" hidden>
      <div class="row" style="gap:6px;margin-top:10px">
        <button id="hpTabScreen" class="primary" onclick="hpShow('screen')">📺 Layar</button>
        <button id="hpTabFiles" onclick="hpShow('files')">📁 Berkas</button>
      </div>
      <div id="hpScreen">
        <div class="row" style="gap:10px;flex-wrap:wrap;margin:8px 0">
          <label><input type="checkbox" id="hpCtrl"> Kendalikan (sentuh &amp; ketik)</label>
          <label>Jeda <select id="hpRate"><option value="500">cepat</option><option value="1000" selected>sedang</option><option value="2000">hemat</option></select></label>
          <button onclick="hpSnap()">Ambil sekali</button>
          <span class="sub" id="hpInfo"></span>
        </div>
        <div id="hpWrap" tabindex="0" style="max-width:420px;background:#000;border:1px solid var(--border);border-radius:14px;overflow:hidden;outline:none">
          <img id="hpImg" alt="layar HP" style="display:block;max-width:100%;height:auto;touch-action:none">
        </div>
        <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:8px">
          <button onclick="hpKey('back')">◀ Back</button>
          <button onclick="hpKey('home')">● Home</button>
          <button onclick="hpKey('recents')">▣ Recents</button>
          <button onclick="hpKey('notifications')">🔔</button>
          <button onclick="hpKey('backspace')">⌫</button>
          <button onclick="hpKey('enter')">⏎</button>
        </div>
        <div class="row" style="gap:6px;margin-top:6px">
          <input id="hpText" style="flex:1" placeholder="ketik teks lalu Kirim…" autocomplete="off">
          <button onclick="hpTypeSend()">Kirim</button>
        </div>
        <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:8px">
          <button onclick="hpUi()">🔎 Lihat isi layar</button>
          <span class="sub" id="hpCaps"></span>
        </div>
        <pre id="hpUiOut" hidden style="max-height:240px;overflow:auto"></pre>
        <div class="row" style="gap:6px;margin-top:6px">
          <input id="hpShell" style="flex:1" placeholder="perintah shell lewat Shizuku (mis. pm list packages | grep wa)" autocomplete="off">
          <button onclick="hpShellSend()">Jalankan</button>
        </div>
        <pre id="hpShellOut" hidden style="max-height:200px;overflow:auto"></pre>
        <div class="sub" style="margin-top:8px">Perlu izin “rekam layar” (untuk melihat) dan “Accessibility” (untuk kontrol) di HP. Centang “Kendalikan”, lalu klik/seret di layar, atau pakai tombol &amp; ketik. Seret = geser/scroll.</div>
      </div>
      <div id="hpFiles" hidden>
        <div class="row" style="gap:6px;margin:8px 0">
          <button onclick="hpUp()">↑ Naik</button>
          <input id="hpPath" style="flex:1" placeholder="/sdcard/…" autocomplete="off">
          <button onclick="hpGo()">Buka</button>
        </div>
        <div id="hpList" class="fslist"></div>
        <pre id="hpPrev" hidden></pre>
      </div>
    </div>
  </div>

  </section>
  <section data-panel="drive" class="panel">
  <div class="card">
    <h2>Google Drive</h2>
    <div id="driveOff" class="sub" hidden></div>
    <button id="driveCheck" onclick="drvStatus()" hidden>Cek lagi</button>
    <div id="driveOn" hidden>
      <div class="row" style="gap:6px">
        <button onclick="drvHome()">🏠 My Drive</button>
        <input id="drvSearch" placeholder="cari di Drive…" style="flex:1" autocomplete="off">
        <button onclick="drvFind()">Cari</button>
      </div>
      <div id="drvList" class="fslist"></div>
      <div class="row" style="margin-top:10px">
        <button id="drvDl" onclick="drvDownload()" disabled>Unduh</button>
        <button id="drvRn" onclick="drvRename()" disabled>Ganti nama</button>
        <button class="danger" id="drvDel" onclick="drvDelete()" disabled>Hapus</button>
        <button onclick="drvNewFolder()">+ Folder</button>
      </div>
      <div class="sub" style="margin-top:8px">Unggah berkas:
        <input id="drvUp" placeholder="path berkas di komputer" style="width:50%">
        <button onclick="drvUpload()">Unggah ke sini</button></div>
    </div>
  </div>

  </section>
  <section data-panel="devices" class="panel">
  <div class="card">
    <h2 class="spread">Perangkat <button onclick="loadDevices()" style="padding:5px 11px;font-size:.8rem">Muat ulang</button></h2>
    <div id="devRole" class="sub" style="margin-bottom:6px"></div>
    <div id="devList" class="fslist"></div>
    <div class="sub" id="devHint" style="margin-top:10px"></div>
  </div>

  </section>
  <section data-panel="memori" class="panel">
  <div class="card">
    <h2>Memori</h2>
    <textarea id="memory" placeholder="(memori kosong)"></textarea>
    <div class="row" style="margin-top:10px">
      <button class="primary" onclick="saveMemory()">Simpan</button>
      <button onclick="loadMemory()">Muat ulang</button>
      <button class="danger" onclick="clearMemory()">Kosongkan</button>
    </div>
  </div>

  </section>
  <section data-panel="log" class="panel">
  <div class="card">
    <h2 class="spread">Log <button onclick="loadLogs()" style="padding:5px 11px;font-size:.8rem">Muat ulang</button></h2>
    <pre id="logs" style="max-height:60vh">…</pre>
  </div>
  </section>

  <p class="muted" style="text-align:center;font-size:.8rem;margin:18px 0 6px">It's Me · dashboard di komputermu</p>
</main>
<div id="toast"></div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({'X-Itsme-Dashboard':'1','Content-Type':'application/json'},
                               TOKEN ? {'X-Itsme-Token':TOKEN} : {}, opts.headers||{});
  return fetch(path, opts).then(async r => {
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.error || ('HTTP '+r.status));
    return d;
  });
}
let toastTimer;
function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(()=>t.classList.remove('show'), 2600);
}
// --- tab, tema, logout ---
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.dataset.panel === b.dataset.tab));
  if(b.dataset.tab === 'devices') loadDevices();
  if(b.dataset.tab === 'komputer') kompInit(); else kompStop();
  if(b.dataset.tab === 'hp') hpInit(); else hpStop();
});
function curTheme(){ const a = document.documentElement.getAttribute('data-theme'); return a || (matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light'); }
function applyTheme(t){ if(t) document.documentElement.setAttribute('data-theme', t); document.getElementById('themeBtn').textContent = curTheme()==='dark' ? '☀️' : '🌙'; }
function toggleTheme(){ const t = curTheme()==='dark' ? 'light' : 'dark'; try{ localStorage.setItem('itsme_theme', t); }catch(e){} applyTheme(t); }
function logout(){ api('/logout', {method:'POST'}).then(()=>location.reload()).catch(()=>location.reload()); }
try{ applyTheme(localStorage.getItem('itsme_theme') || null); }catch(e){ applyTheme(null); }

function renderStatus(s){
  document.getElementById('name').textContent = s.name;
  document.title = s.name;
  if('login' in s) document.getElementById('logoutBtn').hidden = !s.login;
  document.getElementById('brainDesc').textContent = s.brain_desc;
  const box = document.getElementById('brains'); box.innerHTML = '';
  for(const [key, b] of Object.entries(s.brains)){
    const btn = document.createElement('button');
    btn.textContent = b.label;
    if(key === s.brain) btn.classList.add('active');
    if(!b.ready){ btn.disabled = true; btn.title = 'belum siap'; }
    btn.onclick = () => switchBrain(key);
    box.appendChild(btn);
  }
  const full = s.access === 'full';
  const pill = document.getElementById('accessPill');
  pill.textContent = full ? 'Penuh' : 'Aman';
  pill.className = 'pill ' + (full ? 'full' : 'safe');
  document.getElementById('accessSub').textContent = full
    ? 'Langsung bertindak; berkas rahasia tetap dijaga.'
    : 'Aksi yang mengubah sesuatu minta tombol di Telegram.';
  document.getElementById('btnSafe').classList.toggle('active', !full);
  document.getElementById('btnFull').classList.toggle('active', full);
  document.getElementById('bUptime').textContent = fmtUptime(s.uptime);
}
function loadStatus(){ api('/api/status').then(renderStatus).catch(e=>toast(e.message)); }
function switchBrain(name){
  api('/api/brain', {method:'POST', body:JSON.stringify({backend:name})})
    .then(d => { renderStatus(d.status); toast(d.message); })
    .catch(e => toast(e.message));
}
function setAccess(full){
  if(full && !confirm('Aktifkan AKSES PENUH?\n\nBot langsung bertindak tanpa tombol (berkas rahasia tetap dijaga). '
    + 'Siapa pun yang bisa membuka dashboard/Telegram-mu bisa mengendalikan komputer ini.')) return;
  api('/api/access', {method:'POST', body:JSON.stringify({full})})
    .then(d => { renderStatus(d.status); toast(d.message); })
    .catch(e => toast(e.message));
}
function loadMemory(){ api('/api/memory').then(d => document.getElementById('memory').value = d.text).catch(e=>toast(e.message)); }
function saveMemory(){
  api('/api/memory', {method:'POST', body:JSON.stringify({action:'set', text:document.getElementById('memory').value})})
    .then(d => { document.getElementById('memory').value = d.text; toast('Memori disimpan.'); })
    .catch(e => toast(e.message));
}
function clearMemory(){
  if(!confirm('Kosongkan seluruh memori jangka panjang?')) return;
  api('/api/memory', {method:'POST', body:JSON.stringify({action:'clear'})})
    .then(d => { document.getElementById('memory').value = d.text; toast('Memori dikosongkan.'); })
    .catch(e => toast(e.message));
}
function loadLogs(){ api('/api/logs').then(d => { document.getElementById('logs').textContent = d.text; }).catch(e=>toast(e.message)); }
function fmtUptime(sec){
  sec = Math.floor(sec||0); const h = Math.floor(sec/3600), m = Math.floor(sec%3600/60);
  return 'nyala ' + (h ? h+'j '+m+'m' : m+'m');
}
function loadInfo(){
  api('/api/info').then(d => {
    const o = document.getElementById('bOllama');
    o.textContent = 'Ollama ' + (d.ollama_up ? 'nyala' : 'mati');
    o.className = 'badge ' + (d.ollama_up ? 'ok' : 'bad');
    const q = document.getElementById('bQuota');
    if(d.quota){ q.hidden = false; q.textContent = 'Kuota gratis: ' + d.quota.remaining + '/' + d.quota.limit; }
    else q.hidden = true;
  }).catch(()=>{});
}
function addMsg(who, text){
  const c = document.getElementById('chat');
  const d = document.createElement('div'); d.className = 'msg ' + who; d.textContent = text;
  c.appendChild(d); c.scrollTop = c.scrollHeight; return d;
}
function pollChat(job){
  return new Promise((resolve, reject) => {
    const tick = () => api('/api/chat?id=' + job)
      .then(d => d.done ? resolve(d.reply) : setTimeout(tick, 1200))
      .catch(reject);
    setTimeout(tick, 600);
  });
}
async function sendChat(){
  const inp = document.getElementById('chatInput');
  const text = inp.value.trim(); if(!text) return;
  inp.value = ''; addMsg('user', text);
  const bubble = addMsg('bot', "It's Me sedang berpikir…"); bubble.classList.add('thinking');
  try{
    const r = await api('/api/chat', {method:'POST', body:JSON.stringify({text})});
    bubble.textContent = await pollChat(r.job);
  }catch(e){ bubble.textContent = '⚠️ ' + e.message; }
  bubble.classList.remove('thinking');
  document.getElementById('chat').scrollTop = 1e9;
}
document.getElementById('chatInput').addEventListener('keydown', e => {
  if(e.key === 'Enter'){ e.preventDefault(); sendChat(); }
});

// --- Berkas komputer ---
let fsCur = '', fsParent = null, fsSel = null;
function fmtBytes(n){ const u=['B','KB','MB','GB']; let i=0, s=n; while(s>=1024 && i<3){ s/=1024; i++; } return (i? s.toFixed(1): s) + ' ' + u[i]; }
function fsGo(p){
  const path = (p !== undefined && p !== null) ? p : document.getElementById('fsPath').value;
  api('/api/fs/list?path=' + encodeURIComponent(path || '')).then(fsRender).catch(e => toast(e.message));
}
function fsUp(){ if(fsParent !== null) fsGo(fsParent); }
function fsRender(d){
  fsCur = d.path; fsParent = d.parent; fsSel = null;
  document.getElementById('fsPath').value = d.path;
  document.getElementById('fsFile').hidden = true;
  document.getElementById('fsViewBtn').disabled = true;
  document.getElementById('fsOpenBtn').disabled = true;
  const box = document.getElementById('fsList'); box.innerHTML = '';
  for(const e of d.entries){
    const row = document.createElement('div');
    row.className = 'fsrow' + (e.secret ? ' secret' : '');
    row.innerHTML = '<span>' + (e.dir ? '📁' : '📄') + '</span><span class="nm"></span>'
      + (e.dir ? '' : '<span class="sz">' + fmtBytes(e.size) + '</span>')
      + (e.secret ? ' <span title="rahasia">🔒</span>' : '');
    row.querySelector('.nm').textContent = e.name;
    row.onclick = () => e.dir ? fsGo(e.full) : fsPick(row, e.full, e.secret);
    box.appendChild(row);
  }
}
function fsPick(row, full, secret){
  document.querySelectorAll('.fsrow.sel').forEach(r => r.classList.remove('sel'));
  row.classList.add('sel'); fsSel = full;
  document.getElementById('fsOpenBtn').disabled = false;
  document.getElementById('fsViewBtn').disabled = secret;
}
function fsView(){
  if(!fsSel) return;
  api('/api/fs/read?path=' + encodeURIComponent(fsSel)).then(d => {
    const pre = document.getElementById('fsFile'); pre.hidden = false; pre.textContent = d.text;
  }).catch(e => toast(e.message));
}
function fsOpen(){ if(fsSel) api('/api/fs/open', {method:'POST', body:JSON.stringify({path:fsSel})}).then(d=>toast(d.message)).catch(e=>toast(e.message)); }
function fsZip(){ if(fsCur) api('/api/fs/zip', {method:'POST', body:JSON.stringify({sumber:fsCur})}).then(d=>toast(d.message)).catch(e=>toast(e.message)); }
function fsExtract(){
  const a = document.getElementById('fsArc').value.trim(); if(!a) return;
  api('/api/fs/extract', {method:'POST', body:JSON.stringify({arsip:a})}).then(d=>toast(d.message)).catch(e=>toast(e.message));
}
function runCmd(){
  const c = document.getElementById('cmd').value.trim(); if(!c) return;
  const out = document.getElementById('cmdOut'); out.hidden = false; out.textContent = 'Menjalankan…';
  api('/api/fs/run', {method:'POST', body:JSON.stringify({command:c})})
    .then(d => out.textContent = d.message || '(selesai)')
    .catch(e => out.textContent = '⚠️ ' + e.message);
}
document.getElementById('fsPath').addEventListener('keydown', e => { if(e.key==='Enter'){ e.preventDefault(); fsGo(); } });
document.getElementById('cmd').addEventListener('keydown', e => { if(e.key==='Enter'){ e.preventDefault(); runCmd(); } });

// --- Google Drive ---
let drvFolder = 'root', drvSel = null, drvSelName = '', drvSelFolder = false;
function drvStatus(){
  api('/api/drive/status').then(d => {
    document.getElementById('driveOn').hidden = !d.ready;
    document.getElementById('driveOff').hidden = d.ready;
    document.getElementById('driveCheck').hidden = d.ready;
    if(d.ready) drvHome();
    else document.getElementById('driveOff').textContent =
      'Belum terhubung. Jalankan di komputer: .venv\\Scripts\\python -m itsme.drive_login (lihat README).';
  }).catch(()=>{});
}
function drvHome(){ drvFolder = 'root'; drvLoad(); }
function drvLoad(){ api('/api/drive/list?folder=' + encodeURIComponent(drvFolder)).then(drvRender).catch(e=>toast(e.message)); }
function drvFind(){
  const q = document.getElementById('drvSearch').value.trim();
  if(!q) return drvHome();
  api('/api/drive/search?q=' + encodeURIComponent(q)).then(drvRender).catch(e=>toast(e.message));
}
function drvDisable(){ ['drvDl','drvRn','drvDel'].forEach(id => document.getElementById(id).disabled = true); }
function drvRender(d){
  drvSel = null; drvDisable();
  const box = document.getElementById('drvList'); box.innerHTML = '';
  if(!d.items.length){ box.innerHTML = '<div class="fsrow">(kosong)</div>'; return; }
  for(const it of d.items){
    const row = document.createElement('div'); row.className = 'fsrow';
    row.innerHTML = '<span>' + (it.folder?'📁':'📄') + '</span><span class="nm"></span>'
      + (it.folder?'':'<span class="sz">' + fmtBytes(it.size) + '</span>');
    row.querySelector('.nm').textContent = it.name;
    row.onclick = () => it.folder ? (drvFolder = it.id, drvLoad()) : drvPick(row, it);
    box.appendChild(row);
  }
}
function drvPick(row, it){
  document.querySelectorAll('#drvList .fsrow.sel').forEach(r => r.classList.remove('sel'));
  row.classList.add('sel'); drvSel = it.id; drvSelName = it.name; drvSelFolder = it.folder;
  document.getElementById('drvDl').disabled = it.folder;
  document.getElementById('drvRn').disabled = false;
  document.getElementById('drvDel').disabled = false;
}
function drvPost(path, data){ return api('/api/drive/' + path, {method:'POST', body:JSON.stringify(data)}).then(d => toast(d.message)); }
function drvDownload(){ if(drvSel) drvPost('download', {file_id: drvSel}); }
function drvRename(){ if(!drvSel) return; const n = prompt('Nama baru:', drvSelName); if(n) drvPost('manage', {aksi:'ganti_nama', file_id:drvSel, nama:n}).then(drvLoad); }
function drvDelete(){ if(drvSel && confirm('Buang "' + drvSelName + '" ke sampah Drive?')) drvPost('manage', {aksi:'hapus', file_id:drvSel}).then(drvLoad); }
function drvNewFolder(){ const n = prompt('Nama folder baru:'); if(n) drvPost('manage', {aksi:'buat_folder', nama:n, folder_id:(drvFolder==='root'?'':drvFolder)}).then(drvLoad); }
function drvUpload(){
  const p = document.getElementById('drvUp').value.trim(); if(!p) return;
  drvPost('upload', {path_lokal:p, folder_id:(drvFolder==='root'?'':drvFolder)}).then(()=>{ document.getElementById('drvUp').value=''; drvLoad(); });
}
document.getElementById('drvSearch').addEventListener('keydown', e => { if(e.key==='Enter'){ e.preventDefault(); drvFind(); } });

// --- Perangkat (banyak komputer, satu bot) ---
function fmtSeen(ts){
  const d = Math.max(0, Date.now()/1000 - (ts||0));
  if(!ts) return '';
  if(d < 60) return 'baru saja';
  if(d < 3600) return Math.floor(d/60) + ' menit lalu';
  if(d < 86400) return Math.floor(d/3600) + ' jam lalu';
  return Math.floor(d/86400) + ' hari lalu';
}
function devForget(name){
  if(!confirm('Lupakan perangkat "' + name + '" dari daftar?')) return;
  api('/api/devices/forget', {method:'POST', body:JSON.stringify({name})}).then(loadDevices).catch(e=>toast(e.message));
}
function loadDevices(){
  api('/api/devices').then(d => {
    const role = document.getElementById('devRole'), hint = document.getElementById('devHint');
    if(d.is_hub){
      role.textContent = 'Komputer ini Hub (memakai bot Telegram). Perangkat lain mendaftar ke sini.';
      hint.innerHTML = 'Tambah perangkat: di komputer lain, pasang It\'s Me lalu isi di berkas .env-nya '
        + '<code>ITSME_HUB_URL</code> (alamat dashboard Hub ini, mis. lewat Tailscale) dan '
        + '<code>ITSME_HUB_SECRET</code> (sama persis dengan Hub). Perangkat itu akan muncul di sini. '
        + 'Satu bot, satu token, satu id — tidak perlu bikin bot baru. Di Telegram: /perangkat untuk daftar, '
        + '/pakai &lt;nama&gt; untuk menyuruh perangkat itu bekerja, /pakai saja untuk kembali ke komputer ini.';
    } else {
      role.textContent = 'Komputer ini perangkat (agent) yang terhubung ke Hub. Daftar lengkap perangkat ada di Hub.';
      hint.textContent = '';
    }
    const box = document.getElementById('devList'); box.innerHTML = '';
    // Perangkat (poll) + komputer lewat It's Me Node/jembatan, tanpa duplikat nama.
    const seenNames = new Set(d.devices.map(x => (x.name||'').toLowerCase()));
    const all = d.devices.concat((d.computers||[]).filter(c => c.name && !seenNames.has(c.name.toLowerCase())).map(c => ({name:c.name, online:true, url:'/#komputer', via:c.via})));
    if(!all.length){ box.innerHTML = '<div class="fsrow"><span class="nm">(belum ada komputer yang terhubung — tambah lewat /pasang di Telegram)</span></div>'; return; }
    for(const dev of all){
      const row = document.createElement('div'); row.className = 'fsrow';
      row.innerHTML = '<span>' + (dev.online ? '🟢' : '⚪') + '</span>'
        + '<span class="nm"></span>'
        + '<span class="sz">' + (dev.online ? 'online' : fmtSeen(dev.last_seen)) + '</span>';
      row.querySelector('.nm').textContent = dev.name + (d.target === dev.name ? '  🎯 tujuan Telegram' : '');
      if(d.is_hub){
        const on = d.target === dev.name, use = document.createElement('button');
        use.textContent = on ? 'Berhenti' : 'Pakai'; use.style.cssText = 'padding:4px 10px;font-size:.8rem';
        use.title = 'Pesan Telegram berikutnya dikerjakan perangkat ini';
        use.onclick = () => api('/api/devices/target', {method:'POST', body:JSON.stringify({name: on ? '' : dev.name})}).then(loadDevices).catch(e=>toast(e.message));
        row.appendChild(use);
      }
      const open = document.createElement('button');
      open.textContent = 'Buka'; open.style.cssText = 'padding:4px 10px;font-size:.8rem';
      open.onclick = () => window.open(dev.url, '_blank');
      const forget = document.createElement('button');
      forget.textContent = 'Lupakan'; forget.className = 'danger'; forget.style.cssText = 'padding:4px 10px;font-size:.8rem';
      forget.onclick = () => devForget(dev.name);
      row.appendChild(open); row.appendChild(forget);
      box.appendChild(row);
    }
  }).catch(e=>toast(e.message));
}

// --- Komputer: layar + kendali + berkas komputer lain (lewat It's Me Node) ---
let kompNodes=[], kompSel=null, kompMe=null, kompView='screen', kompActive=false, kompTimer=null, kompLoading=false, kompLastMove=0;
function tok(){ return TOKEN ? '&token='+encodeURIComponent(TOKEN) : ''; }
function kompHeaders(){ return Object.assign({'X-Itsme-Dashboard':'1','Content-Type':'application/json'}, TOKEN?{'X-Itsme-Token':TOKEN}:{}); }
function kompInit(){ kompActive=true; if(!kompNodes.length) kompLoad(); else if(kompView==='screen') kompSnap(); }
function kompStop(){ kompActive=false; clearTimeout(kompTimer); }
async function kompLoad(){
  try{ const s = await api('/api/node/status'); kompNodes = s.nodes||[]; kompMe = s.me;
    if(!kompSel || !kompNodes.find(n=>n.id===kompSel)) kompSel = (kompNodes.find(n=>n.id===s.me)||kompNodes[0]||{}).id || null;
    const box=document.getElementById('kompPcs'); box.innerHTML='';
    kompNodes.forEach(n=>{ const btn=document.createElement('button');
      btn.className = n.id===kompSel ? 'primary' : '';
      btn.textContent = (n.leader?'👑 ':'')+n.name + (n.id===kompMe?' (ini)':'');
      btn.onclick=()=>{ kompSel=n.id; kompLoad(); if(kompView==='screen') kompSnap(); else kompBrowse(''); };
      box.appendChild(btn); });
    const self = kompSel===kompMe;
    const h=document.getElementById('kompSelfHint'); h.hidden=!self;
    if(self) h.textContent='Ini komputer yang sedang kamu pakai. Mengendalikan komputer yang sama cenderung bentrok dengan mouse/keyboard aslimu — kendali paling berguna untuk komputer LAIN. Lihat & berkas tetap jalan.';
    if(kompActive && kompView==='screen') kompSnap();
  }catch(e){ document.getElementById('kompPcs').innerHTML='<span class="sub">It\'s Me Node belum menjawab di komputer ini ('+e.message+'). Perlu It\'s Me Node terpasang & versi 0.4.</span>'; }
}
function kompShow(v){ kompView=v;
  document.getElementById('kompScreen').hidden = v!=='screen';
  document.getElementById('kompFiles').hidden = v!=='files';
  document.getElementById('kompTabScreen').className = v==='screen'?'primary':'';
  document.getElementById('kompTabFiles').className = v==='files'?'primary':'';
  if(v==='screen') kompSnap(); else { clearTimeout(kompTimer); if(!document.getElementById('kompList').children.length) kompBrowse(''); }
}
function kompNext(){ if(!kompActive||kompView!=='screen') return; clearTimeout(kompTimer); kompTimer=setTimeout(kompSnap, +document.getElementById('kompRate').value); }
function kompSnap(){
  if(!kompActive||kompView!=='screen'||!kompSel||kompLoading) return;
  kompLoading=true; const t0=performance.now();
  const u='/api/node/screen?node='+encodeURIComponent(kompSel)+'&w='+document.getElementById('kompW').value+'&t='+Date.now()+tok();
  const probe=new Image();
  probe.onload=()=>{ document.getElementById('kompImg').src=probe.src; kompLoading=false;
    document.getElementById('kompInfo').textContent=probe.naturalWidth+'×'+probe.naturalHeight+' · '+Math.round(performance.now()-t0)+' ms';
    kompNext(); };
  probe.onerror=()=>{ kompLoading=false; document.getElementById('kompInfo').textContent='layar tidak bisa diambil (komputer mati / bukan Windows / tidak ada sesi layar)'; };
  probe.src=u;
}
function kompSend(events){ if(!kompSel) return;
  fetch('/api/node/input',{method:'POST',headers:kompHeaders(),body:JSON.stringify({node:kompSel,events})}).catch(()=>{}); }
function kompCtrlOn(){ return document.getElementById('kompCtrl').checked; }
function kompKey(key,mods){ mods=mods||{}; kompSend([{t:'key',key:key,ctrl:!!mods.ctrl,alt:!!mods.alt,shift:!!mods.shift,win:!!mods.win}]); }
function kompTypeSend(){ const t=document.getElementById('kompText'); if(t.value){ kompSend([{t:'text',text:t.value}]); t.value=''; } }
document.getElementById('kompText').addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); kompTypeSend(); } });
function kompRel(ev){ const r=document.getElementById('kompImg').getBoundingClientRect();
  return {x:Math.min(1,Math.max(0,(ev.clientX-r.left)/r.width)), y:Math.min(1,Math.max(0,(ev.clientY-r.top)/r.height))}; }
(function(){
  const img=document.getElementById('kompImg'), wrap=document.getElementById('kompWrap');
  // Kursor: simpan posisi terakhir, kirim sekali per frame. Sebelumnya dibatasi
  // 1 per 60 ms sehingga gerak mouse terasa patah-patah dibanding remote desktop.
  let pendingMove=null, moveQueued=false;
  function flushMove(){ moveQueued=false; if(!pendingMove)return; const p=pendingMove; pendingMove=null;
    kompSend([{t:'move',x:p.x,y:p.y}]); }
  img.addEventListener('mousemove',ev=>{ if(!kompCtrlOn())return; pendingMove=kompRel(ev);
    if(!moveQueued){ moveQueued=true; requestAnimationFrame(flushMove); } });
  img.addEventListener('mousedown',ev=>{ if(!kompCtrlOn())return; ev.preventDefault(); wrap.focus(); const b=['left','middle','right'][ev.button]||'left'; kompSend([{t:'down',button:b,...kompRel(ev)}]); });
  img.addEventListener('mouseup',ev=>{ if(!kompCtrlOn())return; ev.preventDefault(); const b=['left','middle','right'][ev.button]||'left'; kompSend([{t:'up',button:b,...kompRel(ev)}]); });
  img.addEventListener('dblclick',ev=>{ if(!kompCtrlOn())return; ev.preventDefault(); kompSend([{t:'dblclick',...kompRel(ev)}]); });
  img.addEventListener('contextmenu',ev=>{ if(kompCtrlOn()) ev.preventDefault(); });
  wrap.addEventListener('wheel',ev=>{ if(!kompCtrlOn())return; ev.preventDefault(); kompSend([{t:'scroll',dy:ev.deltaY<0?120:-120}]); },{passive:false});
  // Keyboard ditangkap di tingkat dokumen supaya jalan walau fokus tidak di gambar,
  // tapi hanya saat tab Komputer + Kendalikan aktif dan fokus bukan di kotak isian.
  const MODS={Control:'ctrl',Shift:'shift',Alt:'alt',Meta:'win',AltGraph:'alt'};
  document.addEventListener('keydown',ev=>{
    if(!(kompActive && kompView==='screen' && kompCtrlOn())) return;
    const tg=ev.target, tag=tg&&tg.tagName;
    if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;
    ev.preventDefault();
    const k=ev.key;
    if(MODS[k]){ kompSend([{t:'key',key:MODS[k],down:true}]); return; }
    if(k.length===1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey){ kompSend([{t:'text',text:k}]); return; }
    const map={Enter:'enter',Backspace:'backspace',Tab:'tab',Escape:'escape',Delete:'delete',' ':'space',
      ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Home:'home',End:'end',PageUp:'pageup',PageDown:'pagedown'};
    let key=map[k]|| (k.length===1?k:k.toLowerCase());
    kompSend([{t:'key',key,ctrl:ev.ctrlKey,alt:ev.altKey,shift:ev.shiftKey,win:ev.metaKey}]);
  });
  document.addEventListener('keyup',ev=>{
    if(!(kompActive && kompView==='screen' && kompCtrlOn())) return;
    const tg=ev.target, tag=tg&&tg.tagName;
    if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;
    if(MODS[ev.key]){ ev.preventDefault(); kompSend([{t:'key',key:MODS[ev.key],up:true}]); }
  });
  // Tempel dari clipboard komputer ini: teks dikirim sebagai ketikan ke komputer tujuan.
  wrap.addEventListener('paste',ev=>{
    if(!kompCtrlOn()) return;
    const t=(ev.clipboardData||window.clipboardData)?.getData('text');
    if(t){ ev.preventDefault(); kompSend([{t:'text',text:t.slice(0,2000)}]); }
  });
  document.getElementById('kompCtrl').addEventListener('change',()=>{
    if(kompCtrlOn()){ wrap.focus(); img.style.touchAction='none'; } else { img.style.touchAction='auto'; }
  });
  // --- Sentuh (HP): ketuk=klik, seret=seret, tahan=klik kanan, dua jari=gulir ---
  let tS=null, tLast=null, tMoved=false, tDrag=false, tLong=null, tTwo=false, tLastY=0;
  function tPt(t){ const r=img.getBoundingClientRect();
    return {x:Math.min(1,Math.max(0,(t.clientX-r.left)/r.width)), y:Math.min(1,Math.max(0,(t.clientY-r.top)/r.height))}; }
  img.addEventListener('touchstart',ev=>{ if(!kompCtrlOn())return; ev.preventDefault(); wrap.focus();
    if(ev.touches.length>=2){ tTwo=true; tS=null; clearTimeout(tLong); tLastY=ev.touches[0].clientY; return; }
    tTwo=false; tMoved=false; tDrag=false; const p=tPt(ev.touches[0]); tS={x:p.x,y:p.y,t:Date.now()}; tLast=p;
    kompSend([{t:'move',x:p.x,y:p.y}]);
    tLong=setTimeout(()=>{ if(tS&&!tMoved){ kompSend([{t:'down',button:'right',x:tS.x,y:tS.y},{t:'up',button:'right',x:tS.x,y:tS.y}]); tS=null; } },550);
  },{passive:false});
  img.addEventListener('touchmove',ev=>{ if(!kompCtrlOn())return; ev.preventDefault();
    if(tTwo && ev.touches.length>=2){ const y=ev.touches[0].clientY, dy=y-tLastY;
      if(Math.abs(dy)>3){ tLastY=y; kompSend([{t:'scroll',dy: dy>0?-120:120}]); } return; }
    if(!tS) return; const p=tPt(ev.touches[0]); tLast=p;
    if(!tMoved && Math.hypot(p.x-tS.x,p.y-tS.y)>0.02){ tMoved=true; clearTimeout(tLong);
      kompSend([{t:'down',button:'left',x:tS.x,y:tS.y}]); tDrag=true; }
    if(tDrag) kompSend([{t:'move',x:p.x,y:p.y}]);
  },{passive:false});
  img.addEventListener('touchend',ev=>{ if(!kompCtrlOn())return; clearTimeout(tLong);
    if(tTwo){ if(ev.touches.length===0) tTwo=false; return; }
    if(!tS) return; const dt=Date.now()-tS.t, e=tLast||tS;
    if(tDrag){ kompSend([{t:'up',button:'left',x:e.x,y:e.y}]); }
    else if(!tMoved && dt<500){ kompSend([{t:'move',x:tS.x,y:tS.y},{t:'down',button:'left',x:tS.x,y:tS.y},{t:'up',button:'left',x:tS.x,y:tS.y}]); }
    tS=null; tMoved=false; tDrag=false;
  },{passive:false});
})();
async function kompBrowse(path){
  try{ const d=await api('/api/node/fs/list?node='+encodeURIComponent(kompSel||'')+'&path='+encodeURIComponent(path||''));
    document.getElementById('kompPath').value=d.path||''; document.getElementById('kompPrev').hidden=true;
    const L=document.getElementById('kompList'); L.dataset.parent=(d.parent==null?'':d.parent); L.innerHTML='';
    (d.entries||[]).forEach(e=>{ const row=document.createElement('div'); row.className='fsrow';
      row.innerHTML='<span>'+(e.dir?'📁':'📄')+'</span><span class="nm"></span>'+(e.secret?' <span class="sub">🔒 rahasia</span>':'')+'<span class="sub" style="margin-left:auto">'+(e.dir?'':kompSize(e.size))+'</span>';
      row.querySelector('.nm').textContent=e.name;
      row.style.cursor='pointer';
      row.onclick=()=>{ if(e.dir) kompBrowse(e.path); else if(!e.secret) kompPrev(e); };
      if(!e.dir && !e.secret){ const a=document.createElement('a'); a.textContent='⬇'; a.title='Unduh'; a.style.marginLeft='8px';
        a.href='/api/node/fs/download?node='+encodeURIComponent(kompSel||'')+'&path='+encodeURIComponent(e.path)+tok(); a.target='_blank';
        a.onclick=ev=>ev.stopPropagation(); row.appendChild(a); }
      L.appendChild(row); });
    if(!L.children.length) L.innerHTML='<div class="sub">(kosong)</div>';
  }catch(e){ document.getElementById('kompList').innerHTML='<div class="sub" style="color:#ff6b6b">'+e.message+'</div>'; }
}
function kompSize(n){ if(!n) return ''; const u=['B','KB','MB','GB']; let i=0; while(n>=1024&&i<3){n/=1024;i++;} return n.toFixed(i?1:0)+u[i]; }
async function kompPrev(e){ try{ const d=await api('/api/node/fs/read?node='+encodeURIComponent(kompSel||'')+'&path='+encodeURIComponent(e.path));
    const p=document.getElementById('kompPrev'); p.hidden=false; p.textContent=(d.text||'')+(d.truncated?'\n…(dipotong)':''); }
  catch(err){ const p=document.getElementById('kompPrev'); p.hidden=false; p.textContent='('+err.message+')'; } }
function kompUp(){ kompBrowse(document.getElementById('kompList').dataset.parent||''); }
// Unggah: berkas dibaca di browser ini, dikirim sebagai base64 ke komputer tujuan.
function kompUpload(input){
  const f = input.files && input.files[0]; if(!f) return;
  const dir = document.getElementById('kompPath').value || '';
  const sep = (dir && !/[\\/]$/.test(dir)) ? (dir.includes('\\') ? '\\' : '/') : '';
  const toast2 = m => { const t=document.getElementById('kompInfo'); if(t) t.textContent = m; };
  const r = new FileReader();
  r.onload = async () => {
    const b64 = String(r.result).split(',')[1] || '';
    toast2('mengunggah ' + f.name + ' (' + Math.round(f.size/1024) + ' KB)…');
    try{
      const d = await api('/api/node/fs/write', {method:'POST', body: JSON.stringify(
        {node: kompSel, path: dir + sep + f.name, b64})});
      toast2(d.error ? ('gagal: ' + d.error) : ('terunggah: ' + (d.path||f.name)));
    }catch(e){ toast2('gagal: ' + e.message); }
    input.value = '';
  };
  r.readAsDataURL(f);
}
function kompGo(){ kompBrowse(document.getElementById('kompPath').value); }
document.getElementById('kompPath').addEventListener('keydown',e=>{ if(e.key==='Enter') kompGo(); });

// --- HP: aplikasi It's Me HP (layar & berkas HP lewat long-poll) ---
let hpSel=null, hpView='screen', hpActive=false, hpTimer=null, hpLoading=false, hpToken='';
function hpInit(){ hpActive=true; hpLoad(); hpbLoad(); }
function hpStop(){ hpActive=false; clearTimeout(hpTimer); clearTimeout(hpbTimer); }

// --- Pembuat Aplikasi HP (generate APK lewat GitHub Actions) ---
let hpbTimer=null;
async function hpbLoad(){
  const setup=document.getElementById('hpbSetup'), form=document.getElementById('hpbForm');
  try{
    const s = await api('/api/hp/build');
    if(!s.configured){
      form.hidden = true;
      setup.innerHTML = 'Untuk membuat APK, kirim token GitHub ke bot: <code style="user-select:all">/kunci &lt;token GitHub&gt;</code> '+
        '(token dengan izin Actions pada repo <b>'+(s.repo||'aplikasi HP')+'</b>), lalu Muat ulang.';
      return;
    }
    setup.textContent=''; form.hidden = false; hpbRender(s);
  }catch(e){ setup.textContent='Gagal memuat: '+e.message; }
}
function hpbRender(s){
  const dl=document.getElementById('hpbDl'), msg=document.getElementById('hpbMsg'), go=document.getElementById('hpbGo');
  const run=s.run||{}; const building = run.status==='queued' || run.status==='in_progress';
  let txt='';
  if(s.error){ txt='⚠️ '+s.error; }
  else if(building){ txt='⏳ Sedang membangun APK…'; }
  else if(run.conclusion==='success'){ txt='✅ Build terakhir berhasil.'; }
  else if(run.conclusion){ txt='Build terakhir: '+run.conclusion; }
  if(run.url) txt += (txt?' ':'')+'<a href="'+run.url+'" target="_blank" rel="noopener">log</a>';
  msg.innerHTML = txt; go.disabled = building;
  if(s.apk){ dl.hidden=false; dl.href=s.apk; } else { dl.hidden=true; }
  clearTimeout(hpbTimer);
  if(building) hpbTimer=setTimeout(hpbLoad, 15000);
}
async function hpbBuild(){
  const go=document.getElementById('hpbGo'), msg=document.getElementById('hpbMsg');
  const body={ app_name:document.getElementById('hpbName').value.trim(),
    accent:document.getElementById('hpbAccent').value,
    app_id:document.getElementById('hpbId').value.trim(),
    version_name:document.getElementById('hpbVer').value.trim() };
  go.disabled=true; msg.textContent='Memulai build…';
  try{ await api('/api/hp/build',{method:'POST',body:JSON.stringify(body)});
    toast('Build APK dimulai di GitHub.'); setTimeout(hpbLoad, 3000);
  }catch(e){ msg.textContent='❌ '+e.message; go.disabled=false; }
}
async function hpLoad(){
  try{ const s = await api('/api/hp'); hpToken = s.code||s.token||''; const phones = s.phones||[];
    const online = phones.filter(p=>p.online);
    if(!hpSel || !phones.find(p=>p.name===hpSel && p.online)) hpSel = (online[0]||{}).name || null;
    const box=document.getElementById('hpPcs'); box.innerHTML='';
    phones.forEach(p=>{ const btn=document.createElement('button');
      btn.className = p.name===hpSel ? 'primary' : ''; btn.disabled = !p.online;
      btn.textContent = (p.online?'🟢 ':'⚪ ')+p.name;
      btn.onclick=()=>{ hpSel=p.name; hpLoad(); if(hpView==='screen') hpSnap(); else hpBrowse('/sdcard'); };
      box.appendChild(btn); });
    document.getElementById('hpBody').hidden = !hpSel;
    const setup=document.getElementById('hpSetup');
    if(!phones.length){
      setup.innerHTML = 'Belum ada HP tersambung. Pasang aplikasi <b>It\'s Me HP</b> (APK dari repo itsme-hp), buka, '+
        'lalu isi alamat dashboard ini dan kode pasang 6 karakter ini:<br><code style="user-select:all;font-size:1.4em;letter-spacing:.15em">'+ (hpToken||'(kode)') +'</code>';
    } else { setup.innerHTML = 'Kode pasang HP lain: <code style="user-select:all">'+(hpToken||'')+'</code>'; }
    const sel = phones.find(p => (p.name||'').toLowerCase() === (hpSel||'').toLowerCase());
    const caps = (sel && sel.caps) || [];
    document.getElementById('hpCaps').textContent = caps.includes('shell')
      ? '🔓 Shizuku aktif (hak adb)' : 'ℹ️ Shizuku belum aktif — hak adb belum tersedia';
    if(hpActive && hpView==='screen' && hpSel) hpSnap();
  }catch(e){ document.getElementById('hpSetup').textContent = 'Gagal memuat: '+e.message; }
}
function hpShow(v){ hpView=v;
  document.getElementById('hpScreen').hidden = v!=='screen';
  document.getElementById('hpFiles').hidden = v!=='files';
  document.getElementById('hpTabScreen').className = v==='screen'?'primary':'';
  document.getElementById('hpTabFiles').className = v==='files'?'primary':'';
  if(v==='screen') hpSnap(); else { clearTimeout(hpTimer); if(!document.getElementById('hpList').children.length) hpBrowse('/sdcard'); }
}
function hpNext(){ if(!hpActive||hpView!=='screen') return; clearTimeout(hpTimer); hpTimer=setTimeout(hpSnap, +document.getElementById('hpRate').value); }
function hpSnap(){
  if(!hpActive||hpView!=='screen'||!hpSel||hpLoading) return;
  hpLoading=true; const t0=performance.now();
  const probe=new Image();
  probe.onload=()=>{ document.getElementById('hpImg').src=probe.src; hpLoading=false;
    document.getElementById('hpInfo').textContent=probe.naturalWidth+'×'+probe.naturalHeight+' · '+Math.round(performance.now()-t0)+' ms'; hpNext(); };
  probe.onerror=()=>{ hpLoading=false; document.getElementById('hpInfo').textContent='HP tidak menjawab (offline / izin layar belum diberi)'; hpNext(); };
  probe.src='/api/hp/screen?name='+encodeURIComponent(hpSel)+'&t='+Date.now()+tok();
}
function hpCtrlOn(){ const c=document.getElementById('hpCtrl'); return c && c.checked; }
function hpSend(events){ if(!hpSel) return;
  api('/api/hp/input',{method:'POST',body:JSON.stringify({name:hpSel,events})}).catch(()=>{}); }
async function hpUi(){ const out=document.getElementById('hpUiOut');
  try{ const d=await api('/api/hp/uitree',{method:'POST',body:JSON.stringify({name:hpSel})});
    out.hidden=false;
    if(d.error){ out.textContent=d.error; return; }
    out.textContent=(d.nodes||[]).map(n=>('['+n.cls+'] '+(n.text||n.desc||'')+(n.tap?' (bisa diketuk)':'')+' @'+n.x+','+n.y)).join('\n')||'(layar kosong / tidak terbaca)';
  }catch(e){ out.hidden=false; out.textContent='Gagal: '+e.message; } }
async function hpShellSend(){ const i=document.getElementById('hpShell'), out=document.getElementById('hpShellOut');
  if(!i.value.trim()) return; out.hidden=false; out.textContent='menjalankan…';
  try{ const d=await api('/api/hp/shell',{method:'POST',body:JSON.stringify({name:hpSel,cmd:i.value})});
    out.textContent=(d.error? 'Gagal: '+d.error : (d.out||'(tanpa keluaran)'));
  }catch(e){ out.textContent='Gagal: '+e.message; } }
function hpKey(k){ if(hpSel) hpSend([{t:'key',key:k}]); }
function hpTypeSend(){ const t=document.getElementById('hpText'); if(t.value){ hpSend([{t:'text',text:t.value}]); t.value=''; } }
function hpRel(ev){ const r=document.getElementById('hpImg').getBoundingClientRect();
  return {x:Math.min(1,Math.max(0,(ev.clientX-r.left)/r.width)), y:Math.min(1,Math.max(0,(ev.clientY-r.top)/r.height))}; }
(function(){
  const img=document.getElementById('hpImg'), wrap=document.getElementById('hpWrap');
  let down=null, downT=0;
  img.addEventListener('mousedown',ev=>{ if(!hpCtrlOn())return; ev.preventDefault(); wrap.focus(); down=hpRel(ev); downT=Date.now(); });
  img.addEventListener('mouseup',ev=>{ if(!hpCtrlOn()||!down)return; ev.preventDefault();
    const up=hpRel(ev); const dx=up.x-down.x, dy=up.y-down.y; const dist=Math.hypot(dx,dy); const dt=Date.now()-downT;
    if(dist<0.03){ if(dt>500) hpSend([{t:'long',...down}]); else hpSend([{t:'tap',...down}]); }
    else hpSend([{t:'swipe',x:down.x,y:down.y,x2:up.x,y2:up.y,ms:Math.min(600,Math.max(80,dt))}]);
    down=null; });
  img.addEventListener('dblclick',ev=>{ if(!hpCtrlOn())return; ev.preventDefault(); hpSend([{t:'double',...hpRel(ev)}]); });
  document.getElementById('hpCtrl').addEventListener('change',()=>{ if(hpCtrlOn()) wrap.focus(); });
  document.addEventListener('keydown',ev=>{
    if(!(hpActive && hpView==='screen' && hpCtrlOn())) return;
    const tg=ev.target, tag=tg&&tg.tagName;
    if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;
    const k=ev.key;
    if(k==='Enter'){ ev.preventDefault(); hpKey('enter'); return; }
    if(k==='Backspace'){ ev.preventDefault(); hpKey('backspace'); return; }
    if(k.length===1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey){ ev.preventDefault(); hpSend([{t:'text',text:k}]); }
  });
})();
async function hpBrowse(path){
  try{ const d=await api('/api/hp/fs/list?name='+encodeURIComponent(hpSel||'')+'&path='+encodeURIComponent(path||''));
    if(d.error) throw new Error(d.error);
    document.getElementById('hpPath').value=d.path||''; document.getElementById('hpPrev').hidden=true;
    const L=document.getElementById('hpList'); L.dataset.parent=(d.parent==null?'':d.parent); L.innerHTML='';
    (d.entries||[]).forEach(e=>{ const row=document.createElement('div'); row.className='fsrow';
      row.innerHTML='<span>'+(e.dir?'📁':'📄')+'</span><span class="nm"></span><span class="sub" style="margin-left:auto">'+(e.dir?'':kompSize(e.size))+'</span>';
      row.querySelector('.nm').textContent=e.name; row.style.cursor='pointer';
      row.onclick=()=>{ if(e.dir) hpBrowse(e.path); else hpPrev(e); };
      L.appendChild(row); });
    if(!L.children.length) L.innerHTML='<div class="sub">(kosong)</div>';
  }catch(e){ document.getElementById('hpList').innerHTML='<div class="sub" style="color:#ff6b6b">'+e.message+'</div>'; }
}
async function hpPrev(e){ try{ const d=await api('/api/hp/fs/read?name='+encodeURIComponent(hpSel||'')+'&path='+encodeURIComponent(e.path));
    const p=document.getElementById('hpPrev'); p.hidden=false; p.textContent=d.error?('('+d.error+')'):((d.text||'')+(d.truncated?'\n…(dipotong)':'')); }
  catch(err){ const p=document.getElementById('hpPrev'); p.hidden=false; p.textContent='('+err.message+')'; } }
function hpUp(){ hpBrowse(document.getElementById('hpList').dataset.parent||'/sdcard'); }
function hpGo(){ hpBrowse(document.getElementById('hpPath').value); }
document.getElementById('hpPath').addEventListener('keydown',e=>{ if(e.key==='Enter') hpGo(); });

loadStatus(); loadMemory(); loadLogs(); loadInfo(); fsGo(''); drvStatus(); loadDevices();
setInterval(loadStatus, 5000);
setInterval(loadInfo, 30000);
</script>
</body>
</html>"""
