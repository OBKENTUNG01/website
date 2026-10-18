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
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from . import access, brain, config, tools, updater

log = logging.getLogger("itsme")

# Pekerjaan chat yang sedang berjalan: id -> concurrent.futures.Future.
# Chat bisa makan waktu (otak lokal/konfirmasi), jadi dimulai lalu di-poll.
_JOBS = {}

_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}
# Token & token bot yang mungkin terlihat di log tidak boleh ikut ditampilkan.
_REDACT = re.compile(r"(sk-[a-z]+-[A-Za-z0-9_-]{6,}|\d{6,}:[A-Za-z0-9_-]{20,})")
# Header yang hanya bisa dipasang oleh JavaScript dashboard sendiri (bukan oleh
# form dari situs lain): penjaga sederhana terhadap CSRF.
_GUARD_HEADER = "x-itsme-dashboard"
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


def _make_handler(app, token):
    require_token = bool(token)
    login_required = bool(config.DASHBOARD_PASSWORD)

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

        def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
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
            if not self._authed():
                if login_required and path in ("/", "/index.html", "/login"):
                    return self._send(HTTPStatus.OK, _LOGIN_PAGE, "text/html; charset=utf-8")
                if not login_required and path in ("/", "/index.html"):
                    return self._send(HTTPStatus.UNAUTHORIZED, _NEED_TOKEN_HTML, "text/html; charset=utf-8")
                return self._json({"error": "unauthorized"}, 401)
            if path in ("/", "/index.html", "/login"):
                cookie = None
                if require_token and not login_required:
                    cookie = f"itsme_token={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
                return self._send(HTTPStatus.OK, _PAGE, "text/html; charset=utf-8", cookie=cookie)
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
                return self._json({"devices": devices.listing(), "is_hub": not config.AGENT_MODE, "target": devices.get_target()})
            return self._json({"error": "not found"}, 404)

        # -- POST --
        def do_POST(self):
            if self.headers.get(_GUARD_HEADER) != "1":
                return self._json({"error": "permintaan ditolak (tanpa header dashboard)"}, 403)
            path = urlparse(self.path).path
            data = self._body()
            if path in _DEVICE_PATHS:  # dari perangkat ke Hub (secret, bukan login)
                obj, code = device_api(app, path, self.client_address[0], data)
                return self._json(obj, code)
            if path == "/login":
                if _check_login(data.get("user", ""), data.get("password", "")):
                    cookie = f"itsme_session={_new_session()}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_SESSION_TTL}"
                    return self._json({"ok": True}, cookie=cookie)
                return self._json({"ok": False, "error": "User atau password salah."}, 401)
            if path == "/logout":
                _SESSIONS.pop(self._cookie("itsme_session"), None)
                return self._json({"ok": True}, cookie="itsme_session=; Path=/; Max-Age=0")
            if not self._authed():
                return self._json({"error": "unauthorized"}, 401)
            if path == "/api/brain":
                ok, msg = _switch_brain(app, data.get("backend", ""))
            elif path == "/api/access":
                ok, msg = _set_access(bool(data.get("full")))
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
    # Kalau login diaktifkan, token tidak dipakai.
    token = _load_or_make_token() if (on_lan and not config.DASHBOARD_PASSWORD) else ""
    try:
        server = ThreadingHTTPServer((host, config.DASHBOARD_PORT), _make_handler(app, token))
    except OSError as exc:
        log.warning("Dashboard tidak bisa dinyalakan di %s:%s (%s)", host, config.DASHBOARD_PORT, exc)
        return None
    threading.Thread(target=server.serve_forever, name="itsme-dashboard", daemon=True).start()
    shown = "localhost" if not on_lan else host
    log.info("Dashboard: http://%s:%s", shown, config.DASHBOARD_PORT)
    if config.DASHBOARD_PASSWORD:
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
</style></head><body>
<form class="box" onsubmit="return masuk(event)">
  <h1>🫡 <b>It's Me</b></h1>
  <p>Masuk untuk membuka dashboard.</p>
  <label>User</label><input id="user" value="admin" autocomplete="username">
  <label>Password</label><input id="pw" type="password" autocomplete="current-password" autofocus>
  <button type="submit">Masuk</button>
  <div class="err" id="err"></div>
</form>
<script>
async function masuk(e){
  e.preventDefault();
  const r = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json','X-Itsme-Dashboard':'1'},
    body:JSON.stringify({user:document.getElementById('user').value, password:document.getElementById('pw').value})});
  const d = await r.json().catch(()=>({}));
  if(r.ok && d.ok) location.href = '/';
  else document.getElementById('err').textContent = (d.error || 'Gagal masuk.');
  return false;
}
</script></body></html>"""

_PAGE = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>It's Me</title>
<style>
:root{
  --bg:#eef1f6; --bg2:#e8ecf4; --card:#ffffff; --text:#13161d; --muted:#616b7a; --border:#e2e6ee;
  --accent:#6366f1; --accent2:#8b5cf6; --accent-text:#ffffff; --ok:#16a34a; --danger:#e11d48;
  --warnbg:#fef3c7; --warntext:#92400e; --shadow:0 6px 24px rgba(20,25,40,.07); --radius:16px;
}
:root[data-theme="dark"]{
  --bg:#0b0d12; --bg2:#10131a; --card:#161922; --text:#e9ecf3; --muted:#9aa3b2; --border:#262b36;
  --accent:#818cf8; --accent2:#a78bfa; --accent-text:#0b0d12; --ok:#34d399; --danger:#fb7185;
  --warnbg:#3a2f12; --warntext:#fcd34d; --shadow:0 10px 32px rgba(0,0,0,.45);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0b0d12; --bg2:#10131a; --card:#161922; --text:#e9ecf3; --muted:#9aa3b2; --border:#262b36;
  --accent:#818cf8; --accent2:#a78bfa; --accent-text:#0b0d12; --ok:#34d399; --danger:#fb7185;
  --warnbg:#3a2f12; --warntext:#fcd34d; --shadow:0 10px 32px rgba(0,0,0,.45);
}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5;
  background:radial-gradient(1100px 560px at 100% -10%,color-mix(in srgb,var(--accent) 13%,transparent),transparent),
             radial-gradient(900px 500px at -10% 0,color-mix(in srgb,var(--accent2) 13%,transparent),transparent),var(--bg)}
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;padding:11px 16px;
  background:color-mix(in srgb,var(--card) 70%,transparent);backdrop-filter:blur(14px) saturate(1.4);
  -webkit-backdrop-filter:blur(14px) saturate(1.4);border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:8px;font-size:1.05rem;font-weight:800}
.brand b{background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.spacer{flex:1}
.icon{border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:10px;padding:7px 11px;cursor:pointer;font:inherit}
.icon:hover{border-color:var(--accent)}
.badges{display:none;gap:6px}
@media(min-width:660px){.badges{display:flex}}
.badge{font-size:.76rem;padding:3px 10px;border-radius:999px;background:var(--card);border:1px solid var(--border);color:var(--muted);white-space:nowrap}
.badge.ok{color:var(--ok)} .badge.bad{color:var(--danger)}
.tabs{position:sticky;top:54px;z-index:19;display:flex;gap:6px;overflow-x:auto;padding:10px 16px;scrollbar-width:none;
  background:color-mix(in srgb,var(--bg) 82%,transparent);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}
.tabs::-webkit-scrollbar{display:none}
.tabs button{flex:0 0 auto;border:1px solid transparent;background:transparent;color:var(--muted);padding:8px 15px;
  border-radius:999px;cursor:pointer;font:inherit;font-weight:700;transition:.15s}
.tabs button:hover{color:var(--text);background:var(--card)}
.tabs button.active{color:var(--accent-text);background:linear-gradient(90deg,var(--accent),var(--accent2));box-shadow:var(--shadow)}
.wrap{max-width:760px;margin:0 auto;padding:16px}
.panel{display:none;animation:fade .25s ease}
.panel.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px;margin-bottom:14px;box-shadow:var(--shadow)}
.card h2{font-size:.76rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 12px;font-weight:700}
.big{font-size:1.05rem;font-weight:650}
.muted{color:var(--muted)}
.row{display:flex;flex-wrap:wrap;gap:8px}
button{font:inherit;cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--text);padding:9px 14px;border-radius:11px;transition:.15s}
button:hover:not(:disabled){border-color:var(--accent);transform:translateY(-1px)}
button:disabled{opacity:.45;cursor:not-allowed}
button.active{background:linear-gradient(90deg,var(--accent),var(--accent2));color:var(--accent-text);border-color:transparent}
button.primary{background:linear-gradient(90deg,var(--accent),var(--accent2));color:var(--accent-text);border-color:transparent}
button.danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--border))}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.82rem;font-weight:700}
.pill.safe{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.pill.full{background:var(--warnbg);color:var(--warntext)}
textarea{width:100%;min-height:150px;resize:vertical;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:11px;padding:11px;font:inherit}
pre{background:var(--bg2);border:1px solid var(--border);border-radius:11px;padding:11px;overflow:auto;max-height:320px;font-size:.8rem;margin:0;white-space:pre-wrap;word-break:break-word}
.sub{font-size:.85rem;color:var(--muted);margin-top:6px}
input{background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:11px;padding:10px;font:inherit}
input:focus,textarea:focus{outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent);outline-offset:1px}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(10px);background:var(--text);color:var(--bg);
  padding:11px 18px;border-radius:12px;opacity:0;transition:.25s;pointer-events:none;max-width:90%;box-shadow:var(--shadow);z-index:50}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.spread{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.chat{display:flex;flex-direction:column;gap:9px;max-height:52vh;min-height:220px;overflow:auto;padding:4px}
.chat:empty::before{content:"Belum ada pesan. Sapa It's Me di bawah.";color:var(--muted);font-size:.9rem;margin:auto}
.msg{max-width:85%;padding:10px 13px;border-radius:16px;white-space:pre-wrap;word-break:break-word;font-size:.95rem;box-shadow:var(--shadow)}
.msg.user{align-self:flex-end;background:linear-gradient(90deg,var(--accent),var(--accent2));color:var(--accent-text);border-bottom-right-radius:5px}
.msg.bot{align-self:flex-start;background:var(--bg2);border:1px solid var(--border);border-bottom-left-radius:5px}
.msg.thinking{opacity:.7;font-style:italic}
.chatbar{display:flex;gap:8px;margin-top:12px}
.chatbar input{flex:1}
.fslist{max-height:300px;overflow:auto;border:1px solid var(--border);border-radius:12px;margin-top:10px;background:var(--bg2)}
.fsrow{display:flex;align-items:center;gap:9px;padding:9px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:.9rem}
.fsrow:last-child{border-bottom:0}
.fsrow:hover{background:color-mix(in srgb,var(--accent) 12%,transparent)}
.fsrow.sel{background:color-mix(in srgb,var(--accent) 22%,transparent)}
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
    if(!d.devices.length){ box.innerHTML = '<div class="fsrow"><span class="nm">(belum ada perangkat lain)</span></div>'; return; }
    for(const dev of d.devices){
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

loadStatus(); loadMemory(); loadLogs(); loadInfo(); fsGo(''); drvStatus(); loadDevices();
setInterval(loadStatus, 5000);
setInterval(loadInfo, 30000);
</script>
</body>
</html>"""
