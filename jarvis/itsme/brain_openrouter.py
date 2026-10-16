"""Otak cloud: loop agen lewat OpenRouter (opsional, jauh lebih pintar).

Dipakai kalau ITSME_BACKEND=openrouter atau setelah perintah /cloud.
OpenRouter memberi akses ke ratusan model (gratis maupun berbayar) dengan satu
API key, memakai format chat yang sama dengan otak lokal.

Kesalahan sesaat (model penuh, penyedia bermasalah) dicoba lagi dengan jeda,
dan untuk model gratis OpenRouter langsung mencoba model cadangan
(openrouter/free) dalam permintaan yang sama. Kalau tetap tidak bisa dipakai
(saldo habis, kuota harian, internet, key ditolak), fungsi ini melempar
CloudUnavailable dan pemilih otak (brain.py) menyerahkan pertanyaannya ke
otak lokal.
"""

import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config
from .agentutil import clean_reply, parse_arguments, trim_history
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

_http = httpx.Client(timeout=httpx.Timeout(config.OPENROUTER_TIMEOUT, connect=10.0))

# Jeda sebelum mencoba lagi kesalahan sesaat (detik). Retry-After dari server
# dipakai kalau ada, dibatasi RETRY_CAP.
RETRY_WAITS = (2, 6)
RETRY_CAP = 20
# Model cadangan dimatikan untuk sesi ini kalau OpenRouter menolaknya.
_state = {"fallbacks": True}


class CloudUnavailable(Exception):
    """Otak cloud tidak bisa dipakai sekarang; pesannya siap dibaca pemilik."""

    def __init__(self, message, transient=False, retry_after=None, status=None):
        super().__init__(message)
        self.transient = transient  # True = layak dicoba lagi sebentar lagi
        self.retry_after = retry_after
        self.status = status


def _headers():
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        # Atribusi aplikasi (opsional) sesuai dokumentasi OpenRouter.
        "HTTP-Referer": "https://github.com/OBKENTUNG01/website",
        "X-OpenRouter-Title": config.NAME,
    }


def _is_free(model=None):
    model = model or config.OPENROUTER_MODEL
    return model.endswith(":free") or model == "openrouter/free"


def fallback_models():
    return list(config.OPENROUTER_FALLBACKS) if _state["fallbacks"] else []


def _request(messages, memory_text):
    body = {
        "model": config.OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt(memory_text, "cari_web", "baca_halaman"),
            }
        ]
        + messages,
        "tools": FUNCTION_TOOLS,
    }
    fallbacks = fallback_models()
    if fallbacks:
        # Kalau model utama penuh atau bermasalah, OpenRouter langsung mencoba
        # model berikutnya dalam permintaan yang sama.
        body["models"] = [config.OPENROUTER_MODEL] + fallbacks
    if config.OPENROUTER_REASONING not in ("", "default"):
        body["reasoning"] = {"effort": config.OPENROUTER_REASONING}
    return body


# --- kuota & batas ----------------------------------------------------------
def free_quota(timeout=8):
    """Kuota harian model gratis akun ini: dict used/limit/remaining, atau None."""
    if not config.OPENROUTER_API_KEY:
        return None
    try:
        resp = httpx.get(f"{config.OPENROUTER_URL}/key", headers=_headers(), timeout=timeout)
        quota = (resp.json().get("data") or {}).get("free_model_daily_requests")
        return {k: int(quota[k]) for k in ("used", "limit", "remaining")}
    except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError):
        return None


def reset_time_text(reset=None):
    """Jam (waktu komputer ini) saat kuota harian pulih. Kuota dihitung per hari UTC."""
    when = None
    try:
        value = float(reset)
        when = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    if when is None:
        now = datetime.now(timezone.utc)
        when = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return when.astimezone().strftime("%H.%M")


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _retry_after(resp):
    value = resp.headers.get("retry-after") if resp is not None else None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _rate_limited(resp, message, meta):
    """Jelaskan 429. OpenRouter memakai kode yang sama untuk kuota harian
    habis, terlalu banyak permintaan per menit, dan model yang sedang penuh
    di penyedianya. Kembalikan (pesan, layak_dicoba_lagi)."""
    text = message.lower()
    limit = resp.headers.get("x-ratelimit-limit")
    remaining = resp.headers.get("x-ratelimit-remaining")
    reset = resp.headers.get("x-ratelimit-reset")
    if "per-day" in text or "per day" in text or "daily" in text:
        kind = "daily"
    elif "per-min" in text or "per min" in text:
        kind = "minute"
    elif meta.get("provider_code") or meta.get("provider_name") or "upstream" in text or "provider" in text:
        kind = "busy"
    elif remaining is not None and _int(remaining) == 0:
        # Batas platform OpenRouter selalu disertai header X-RateLimit-*.
        kind = "minute" if (_int(limit) or 0) <= 20 else "daily"
    else:
        kind = "unknown"
    if kind == "unknown" and _is_free():
        quota = free_quota()
        if quota is not None:
            kind = "daily" if quota["remaining"] <= 0 else "busy"
            limit = limit or quota["limit"]

    if kind == "daily":
        return (
            f"Kuota harian model gratis OpenRouter sudah habis ({_int(limit) or 50} permintaan/hari). "
            f"Kuota baru mulai pukul {reset_time_text(reset)}. Supaya jadi 1.000/hari, isi saldo "
            "minimal $10 sekali di openrouter.ai/settings/credits.",
            False,
        )
    if kind == "minute":
        return "Terlalu banyak permintaan dalam semenit ke model gratis OpenRouter (maks. 20/menit).", True
    if kind == "busy":
        if _is_free():
            return (
                "Model gratis di OpenRouter sedang penuh dipakai banyak orang (dibatasi "
                "penyedianya), bukan kuotamu yang habis. Coba lagi sebentar lagi.",
                True,
            )
        return "Penyedia model di OpenRouter sedang sibuk. Coba lagi sebentar lagi.", True
    return (
        "OpenRouter sedang membatasi permintaan (model penuh atau kuota harian habis; "
        "cek sisa kuota dengan /otak).",
        True,
    )


def _explain(status, message, meta=None, resp=None):
    """Ubah error OpenRouter jadi (kalimat jelas untuk pemilik, layak_dicoba_lagi)."""
    meta = meta or {}
    detail = f" ({message})" if message else ""
    if status == 401:
        return "API key OpenRouter ditolak. Buat key baru lalu jalankan setup lagi.", False
    if status == 402:
        if meta.get("limit_source") == "openrouter_in_flight_budget":
            return "OpenRouter sedang menahan permintaan sampai pemakaian sebelumnya tercatat.", True
        return "Saldo OpenRouter habis atau batas kredit key sudah tercapai.", False
    if status == 429 and resp is not None:
        return _rate_limited(resp, message, meta)
    if status == 403:
        return f"Permintaan ditolak OpenRouter{detail}.", False
    if status in (400, 404):
        return f"Model '{config.OPENROUTER_MODEL}' tidak bisa dipakai{detail}.", False
    if status == 408 or (status and status >= 500):
        return "Penyedia model di OpenRouter sedang bermasalah.", True
    return f"OpenRouter mengembalikan error {status}{detail}.", False


# --- permintaan ---------------------------------------------------------------
def _call_once(body):
    """Kirim satu permintaan; kembalikan choices[0] atau lempar CloudUnavailable."""
    if not config.OPENROUTER_API_KEY:
        raise CloudUnavailable("Belum ada API key OpenRouter.")
    try:
        resp = _http.post(
            f"{config.OPENROUTER_URL}/chat/completions", json=body, headers=_headers()
        )
    except httpx.TimeoutException as exc:
        raise CloudUnavailable("Otak cloud terlalu lama menjawab.") from exc
    except httpx.HTTPError as exc:
        raise CloudUnavailable(
            f"Tidak bisa menghubungi OpenRouter ({type(exc).__name__})."
        ) from exc

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    error = data.get("error")
    choices = data.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else None
    # Penyedia bisa gagal setelah OpenRouter menjawab 200; kesalahannya lalu
    # ada di choices[0] dan isi jawabannya tidak utuh.
    if not error and choice and (choice.get("finish_reason") == "error" or choice.get("error")):
        error = choice.get("error") or {"code": 502, "message": "penyedia model terputus"}
    if resp.status_code != 200 or error:
        status = resp.status_code
        message, meta = "", {}
        if isinstance(error, dict):
            message = str(error.get("message") or "")[:200]
            if isinstance(error.get("metadata"), dict):
                meta = error["metadata"]
            if status == 200:
                status = error.get("code") if isinstance(error.get("code"), int) else 502
        text, transient = _explain(status, message, meta, resp)
        raise CloudUnavailable(text, transient=transient, retry_after=_retry_after(resp), status=status)
    if choice is None:
        raise CloudUnavailable("Jawaban OpenRouter kosong.", transient=True)
    model = str(data.get("model") or "")
    if "models" in body and model and not model.startswith(config.OPENROUTER_MODEL.split(":")[0]):
        print(f"[itsme] otak cloud: dijawab model cadangan {model}", flush=True)
    return choice


def _call(body):
    """Seperti _call_once, tapi kesalahan sesaat dicoba lagi dengan jeda."""
    retries = 0
    while True:
        try:
            return _call_once(body)
        except CloudUnavailable as exc:
            if exc.status in (400, 404) and "models" in body:
                # Mungkin model cadangannya yang ditolak: coba tanpa cadangan.
                # Kalau itu berhasil, cadangan dimatikan untuk sesi ini.
                choice = _call({k: v for k, v in body.items() if k != "models"})
                _state["fallbacks"] = False
                print("[itsme] model cadangan OpenRouter ditolak; dipakai tanpa cadangan.", flush=True)
                return choice
            if not exc.transient or retries >= len(RETRY_WAITS):
                raise
            wait = exc.retry_after if exc.retry_after is not None else RETRY_WAITS[retries]
            retries += 1
            time.sleep(min(wait, RETRY_CAP))


def run_agent(messages, ctx):
    """Jalankan loop agen sampai selesai. Mengubah `messages` di tempat.

    Kembalikan teks jawaban akhir, atau lempar CloudUnavailable.
    """
    memory_text = ctx["memory"].text()

    for _ in range(config.MAX_STEPS):
        trim_history(messages, config.HISTORY_MESSAGES)
        choice = _call(_request(messages, memory_text))
        message = choice.get("message") or {}

        content = clean_reply(message.get("content"))
        tool_calls = [
            c for c in (message.get("tool_calls") or []) if isinstance(c, dict)
        ]

        # Simpan giliran asisten apa adanya. reasoning_details harus dikirim
        # balik tanpa diubah saat ada panggilan alat (anjuran OpenRouter).
        assistant = {"role": "assistant", "content": content}
        if message.get("reasoning_details"):
            assistant["reasoning_details"] = message["reasoning_details"]
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)

        if not tool_calls:
            if choice.get("finish_reason") == "length" and content:
                content += "\n\n(jawaban terpotong karena terlalu panjang)"
            return content or "(selesai)"

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args, problem = parse_arguments(fn.get("arguments"))
            if problem is None:
                problem = validate_input(name, args)
            out = problem or handle_tool(name, args, ctx)
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id") or "", "content": str(out)}
            )

    return "Langkahnya terlalu banyak, aku berhenti dulu. Lanjut?"
