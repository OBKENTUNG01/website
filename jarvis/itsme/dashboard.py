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
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from . import access, brain, config, updater

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


# --- server ------------------------------------------------------------------------
def _make_handler(app, token):
    require_token = bool(token)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # jangan penuhi log bot dengan setiap permintaan

        # -- util --
        def _query(self):
            return parse_qs(urlparse(self.path).query)

        def _cookie_token(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                if part.strip().startswith("itsme_token="):
                    return part.split("=", 1)[1].strip()
            return ""

        def _authed(self):
            if not require_token:
                return True
            given = (
                self.headers.get("X-Itsme-Token")
                or (self._query().get("token") or [""])[0]
                or self._cookie_token()
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

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                return {}

        # -- GET --
        def do_GET(self):
            if not self._authed():
                return self._send(HTTPStatus.UNAUTHORIZED, _NEED_TOKEN_HTML, "text/html; charset=utf-8")
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                cookie = None
                if require_token:
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
            return self._json({"error": "not found"}, 404)

        # -- POST --
        def do_POST(self):
            if not self._authed():
                return self._json({"error": "token salah"}, 401)
            if self.headers.get(_GUARD_HEADER) != "1":
                return self._json({"error": "permintaan ditolak (tanpa header dashboard)"}, 403)
            path = urlparse(self.path).path
            data = self._body()
            if path == "/api/brain":
                ok, msg = _switch_brain(app, data.get("backend", ""))
            elif path == "/api/access":
                ok, msg = _set_access(bool(data.get("full")))
            elif path == "/api/memory":
                ok, result = _memory(app, data.get("action", ""), data.get("text", ""))
                return self._json({"ok": ok, "text": result} if ok else {"ok": False, "error": result}, 200 if ok else 400)
            elif path == "/api/chat":
                return self._chat_start(app, data)
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
    token = _load_or_make_token() if on_lan else ""
    try:
        server = ThreadingHTTPServer((host, config.DASHBOARD_PORT), _make_handler(app, token))
    except OSError as exc:
        log.warning("Dashboard tidak bisa dinyalakan di %s:%s (%s)", host, config.DASHBOARD_PORT, exc)
        return None
    threading.Thread(target=server.serve_forever, name="itsme-dashboard", daemon=True).start()
    shown = "localhost" if not on_lan else host
    log.info("Dashboard: http://%s:%s", shown, config.DASHBOARD_PORT)
    if on_lan:
        log.info("Dashboard token (buka ...?token=... sekali): %s", token)
    return server


_NEED_TOKEN_HTML = (
    "<!doctype html><meta charset=utf-8><body style='font-family:system-ui;padding:2rem'>"
    "<h2>Token diperlukan</h2><p>Buka halaman ini dengan menambahkan <code>?token=TOKEN</code> "
    "di alamatnya. Token ada di berkas <code>data/dashboard_token.txt</code> di komputer, "
    "dan ditampilkan di log It's Me saat menyala.</p></body>"
)

_PAGE = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>It's Me</title>
<style>
:root{
  --bg:#f5f6f8; --card:#ffffff; --text:#14171f; --muted:#5b6472; --border:#e4e7ec;
  --accent:#2563eb; --accent-text:#fff; --ok:#16a34a; --danger:#dc2626; --warnbg:#fef3c7; --warntext:#92400e;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0f1115; --card:#191c22; --text:#e8eaef; --muted:#99a1ad; --border:#2a2f3a;
  --accent:#3b82f6; --accent-text:#fff; --ok:#22c55e; --danger:#f87171; --warnbg:#3a2f12; --warntext:#fcd34d;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5}
.wrap{max-width:720px;margin:0 auto;padding:16px}
header{display:flex;align-items:center;gap:10px;margin:8px 0 18px}
header h1{font-size:1.25rem;margin:0;font-weight:650}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:14px}
.card h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 12px;font-weight:600}
.big{font-size:1.05rem;font-weight:600}
.muted{color:var(--muted)}
.row{display:flex;flex-wrap:wrap;gap:8px}
button{font:inherit;cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--text);
  padding:9px 14px;border-radius:10px;transition:.12s}
button:hover:not(:disabled){border-color:var(--accent)}
button:disabled{opacity:.45;cursor:not-allowed}
button.active{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
button.primary{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
button.danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--border))}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.82rem;font-weight:600}
.pill.safe{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.pill.full{background:var(--warnbg);color:var(--warntext)}
textarea{width:100%;min-height:150px;resize:vertical;background:var(--bg);color:var(--text);
  border:1px solid var(--border);border-radius:10px;padding:10px;font:inherit}
pre{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:10px;
  overflow:auto;max-height:300px;font-size:.8rem;margin:0;white-space:pre-wrap;word-break:break-word}
.sub{font-size:.85rem;color:var(--muted);margin-top:4px}
#toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:var(--text);color:var(--bg);
  padding:10px 16px;border-radius:10px;opacity:0;transition:.2s;pointer-events:none;max-width:90%}
#toast.show{opacity:1}
.spread{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.info{display:flex;flex-wrap:wrap;gap:6px;margin:-6px 0 16px}
.badge{font-size:.78rem;padding:3px 10px;border-radius:999px;background:var(--card);border:1px solid var(--border);color:var(--muted)}
.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 35%,var(--border))}
.badge.bad{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 35%,var(--border))}
.chat{display:flex;flex-direction:column;gap:8px;max-height:330px;overflow:auto;padding:2px}
.chat:empty::before{content:"Belum ada pesan. Sapa It's Me di bawah.";color:var(--muted);font-size:.9rem}
.msg{max-width:88%;padding:9px 12px;border-radius:14px;white-space:pre-wrap;word-break:break-word;font-size:.95rem}
.msg.user{align-self:flex-end;background:var(--accent);color:var(--accent-text);border-bottom-right-radius:4px}
.msg.bot{align-self:flex-start;background:var(--bg);border:1px solid var(--border);border-bottom-left-radius:4px}
.msg.thinking{opacity:.65;font-style:italic}
.chatbar{display:flex;gap:8px;margin-top:10px}
.chatbar input{flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:11px;font:inherit}
</style>
</head>
<body>
<div class="wrap">
  <header><span class="dot" title="sedang jalan"></span><h1>🫡 <span id="name">It's Me</span></h1></header>

  <div id="info" class="info">
    <span id="bUptime" class="badge"></span>
    <span id="bOllama" class="badge">Ollama…</span>
    <span id="bQuota" class="badge" hidden></span>
  </div>

  <div class="card">
    <h2>Ngobrol</h2>
    <div id="chat" class="chat"></div>
    <div class="chatbar">
      <input id="chatInput" placeholder="Ketik pesan untuk It's Me…" autocomplete="off">
      <button class="primary" onclick="sendChat()">Kirim</button>
    </div>
    <div class="sub">Berbagi konteks dengan Telegram. Saat akses aman, konfirmasi aksi muncul di Telegram.</div>
  </div>

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

  <div class="card">
    <h2>Memori</h2>
    <textarea id="memory" placeholder="(memori kosong)"></textarea>
    <div class="row" style="margin-top:10px">
      <button class="primary" onclick="saveMemory()">Simpan</button>
      <button onclick="loadMemory()">Muat ulang</button>
      <button class="danger" onclick="clearMemory()">Kosongkan</button>
    </div>
  </div>

  <div class="card">
    <h2 class="spread">Log <button onclick="loadLogs()" style="padding:4px 10px;font-size:.8rem">Muat ulang</button></h2>
    <pre id="logs">…</pre>
  </div>

  <p class="muted" style="text-align:center;font-size:.8rem">It's Me · dashboard di komputermu</p>
</div>
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
function renderStatus(s){
  document.getElementById('name').textContent = s.name;
  document.title = s.name;
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
loadStatus(); loadMemory(); loadLogs(); loadInfo();
setInterval(loadStatus, 5000);
setInterval(loadInfo, 30000);
</script>
</body>
</html>"""
