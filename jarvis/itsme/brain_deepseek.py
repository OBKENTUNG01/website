"""Otak DeepSeek / NusaGate: loop agen lewat API kompatibel OpenAI, dengan KUMPULAN key.

Dipakai kalau ITSME_BACKEND=deepseek atau setelah perintah /deepseek. DeepSeek murah dan pintar;
memakai format chat & pemanggilan alat yang sama dengan OpenRouter, jadi modul ini jauh lebih
ringkas (tanpa kuota gratis). Model bawaan deepseek-chat (atau deepseek-v4.1-flash di NusaGate)
mendukung alat.

Banyak key (keypool.py): tiap permintaan memakai key yang paling lama menganggur, jadi semua key
bekerja merata dan NusaGate (60 permintaan/menit PER KEY) tidak menahan satu key saja. Key yang
sedang dibatasi, ditolak, atau kehabisan saldo ditinggalkan sebentar dan permintaan yang sama
langsung dicoba dengan key lain - pemilik tidak melihat kegagalan selama masih ada satu key sehat.
Model juga bisa diputar bergantian (ITSME_NUSAGATE_MODELS): satu model per jawaban.

Alamat API (DEEPSEEK_URL) bisa diganti ke gateway lain yang kompatibel OpenAI.

Kalau semua key tidak bisa dipakai (saldo habis, ditolak, internet putus), fungsi ini melempar
CloudUnavailable dan pemilih otak (brain.py) menyerahkan pertanyaannya ke otak lokal - sama
seperti OpenRouter.
"""

import logging
import time

import httpx

from . import config, keypool, keys
from .agentutil import clean_reply, parse_arguments, trim_history
from .brain_openrouter import CloudUnavailable  # satu jenis error untuk semua otak cloud
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

log = logging.getLogger("itsme")
_http = httpx.Client(timeout=httpx.Timeout(config.DEEPSEEK_TIMEOUT, connect=10.0))

# Jeda sebelum mencoba lagi kesalahan sesaat (detik).
RETRY_WAITS = (2, 6)
RETRY_CAP = 20
# Jawaban tamu dibatasi panjangnya (hemat biaya, dan tamu hanya tanya-jawab).
GUEST_MAX_TOKENS = 500


def _headers(key):
    return {"Authorization": f"Bearer {key}"}


def _request(messages, memory_text, model):
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt(memory_text, "cari_web", "baca_halaman"),
            }
        ]
        + messages,
        "tools": FUNCTION_TOOLS,
    }
    return body


def _retry_after(resp, data=None):
    """Detik tunggu dari header Retry-After, atau dari retry_after di isi galat NusaGate."""
    candidates = [resp.headers.get("retry-after") if resp is not None else None]
    if isinstance(data, dict):
        candidates.append(data.get("retry_after"))
        if isinstance(data.get("error"), dict):
            candidates.append(data["error"].get("retry_after"))
    for value in candidates:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return None


def _explain(status, message, code=""):
    """Ubah error DeepSeek jadi (kalimat jelas untuk pemilik, layak_dicoba_lagi)."""
    detail = f" ({message})" if message else ""
    if status in (401, 403):
        return (
            "API key DeepSeek ditolak (salah, dicabut, atau alamat API-nya beda). "
            "Kalau key-mu dari gateway lain, isi DEEPSEEK_URL di .env.",
            False,
        )
    if status == 402:
        where = "nusagate.com" if "nusagate" in config.deepseek_url() else "platform.deepseek.com"
        return f"Saldo penyedia DeepSeek-mu habis. Isi ulang di {where}.", False
    if status == 429:
        if "cap" in code or "window" in code:
            return "Batas paket (jendela waktu) NusaGate untuk key ini tercapai.", True
        return "DeepSeek sedang membatasi permintaan (terlalu sering atau sibuk). Coba lagi sebentar lagi.", True
    if "context_length" in code:  # NusaGate: context_length_exceeded
        return ("Percakapan sudah terlalu panjang untuk model ini. Kirim /reset untuk memulai percakapan "
                "baru (memori tetap)."), False
    if status in (400, 404, 422):
        return f"Model '{config.deepseek_model()}' atau permintaannya tidak bisa dipakai{detail}.", False
    if status == 408 or (status and status >= 500):
        return "Server DeepSeek sedang bermasalah atau sibuk. Coba lagi sebentar lagi.", True
    return f"DeepSeek mengembalikan error {status}{detail}.", False


def _outcome(status, code=""):
    """Hasil satu permintaan menurut keypool: invalid | balance | cap | limit | busy | client."""
    code = str(code or "").lower()
    if status in (401, 403) or code == "invalid_api_key":
        return "invalid"
    if status == 402 or code == "insufficient_balance":
        return "balance"
    if status == 429:
        return "cap" if ("cap" in code or "window" in code) else "limit"
    if status is None or status == 408 or status >= 500:
        return "busy"
    return "client"  # 400/404/422: salah model atau permintaan, BUKAN salah key


def _post(key, body):
    """Kirim satu permintaan dengan satu key; kembalikan choices[0] atau lempar CloudUnavailable
    (dengan .status, .retry_after, .code, .raw)."""
    try:
        resp = _http.post(
            f"{config.deepseek_url(key)}/chat/completions", json=body, headers=_headers(key)
        )
    except httpx.TimeoutException as exc:
        raise CloudUnavailable("DeepSeek terlalu lama menjawab.") from exc
    except httpx.HTTPError as exc:
        raise CloudUnavailable(
            f"Tidak bisa menghubungi DeepSeek ({type(exc).__name__})."
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
    if resp.status_code != 200 or error:
        status = resp.status_code
        message, code = "", ""
        if isinstance(error, dict):
            # Penyedia kadang mengulang key dalam pesan galatnya; pesan ini bisa sampai ke chat & log.
            message = keys.scrub(str(error.get("message") or "")[:200])
            if not isinstance(error.get("code"), int):
                code = str(error.get("code") or "")
            if status == 200:
                status = error.get("code") if isinstance(error.get("code"), int) else 502
        text, transient = _explain(status, message, code.lower())
        exc = CloudUnavailable(text, transient=transient, retry_after=_retry_after(resp, data), status=status)
        exc.code, exc.raw = code, message
        raise exc
    if choice is None:
        raise CloudUnavailable("Jawaban DeepSeek kosong.", transient=True)
    return choice


def _call_once(build, run_model=None):
    """Satu permintaan lewat kumpulan key: kalau satu key gagal (dibatasi, ditolak, saldo habis,
    server sibuk), langsung coba key lain. `build(model)` membuat isi permintaannya."""
    tried, last = set(), None
    while True:
        exhausted = None
        try:
            lease = keypool.acquire(exclude=tried)
        except keypool.PoolExhausted as exc:
            exhausted = exc
        if exhausted is not None:
            if last is not None:
                raise last  # semua key sudah dicoba untuk permintaan ini: galat terakhir dari penyedia
            wait = exhausted.wait
            # Semua key sedang dikeluarkan sementara. Tunggu hanya kalau pulihnya cepat.
            raise CloudUnavailable(
                str(exhausted), transient=wait is not None and wait <= RETRY_CAP, retry_after=wait
            ) from exhausted
        try:
            choice = _post(lease.key, build(keypool.model_for(lease.key, run_model)))
        except CloudUnavailable as exc:
            outcome = _outcome(exc.status, getattr(exc, "code", ""))
            keypool.release(lease, outcome, retry_after=exc.retry_after)
            if outcome == "client":
                raise
            tried.add(lease.id)
            last = exc
            continue
        except BaseException:
            keypool.release(lease, "busy")  # jangan meninggalkan key terkunci "sedang dipakai"
            raise
        keypool.release(lease, "ok")
        return choice


def _call(build, run_model=None):
    """Seperti _call_once, tapi kesalahan sesaat dicoba lagi dengan jeda."""
    retries = 0
    while True:
        try:
            return _call_once(build, run_model)
        except CloudUnavailable as exc:
            if not exc.transient or retries >= len(RETRY_WAITS):
                raise
            wait = exc.retry_after if exc.retry_after is not None else RETRY_WAITS[retries]
            retries += 1
            time.sleep(min(wait, RETRY_CAP))


def _model_rejected(exc):
    """Penyedia menolak MODELNYA (bukan key, bukan isi permintaan kita)?"""
    if exc.status == 404 or str(getattr(exc, "code", "")).lower() == "model_not_found":
        return True
    raw = str(getattr(exc, "raw", "")).lower()
    return exc.status in (400, 422) and any(w in raw for w in ("tool", "function", "not support", "unsupported"))


def _call_with_model(build, model):
    """_call dengan cadangan MODEL: model yang ditolak penyedia ditinggalkan dan diganti model lain
    dari daftar putaran. Kembalikan (choice, model_yang_dipakai)."""
    skipped = set()
    while True:
        try:
            choice = _call(build, model)
        except CloudUnavailable as exc:
            if model and keypool.models() and _model_rejected(exc):
                keypool.model_bad(model)
                skipped.add(model)
                replacement = keypool.next_model(exclude=skipped)
                if replacement:
                    log.warning("model %s ditolak penyedia; dipakai %s", model, replacement)
                    model = replacement
                    continue
            raise
        keypool.model_ok(model)
        return choice, model


def ping(key, model=None):
    """Satu permintaan chat sangat kecil ke SATU key tertentu (bukan lewat giliran), untuk /kunci_tes chat:
    membuktikan key itu benar-benar bisa MENJAWAB, bukan sekadar terdaftar. Biayanya beberapa token.
    Hasilnya juga dicatat ke kumpulan (key yang saldonya habis/ditolak langsung dikeluarkan sementara).
    Kembalikan (berhasil, keterangan, detik)."""
    model = model or keypool.model_for(key, keypool.next_model())
    body = {"model": model, "messages": [{"role": "user", "content": "Balas satu kata saja: siap"}],
            "max_tokens": 8}
    started = time.monotonic()
    try:
        _post(key, body)
    except CloudUnavailable as exc:
        outcome = _outcome(exc.status, getattr(exc, "code", ""))
        keypool.release(keypool.Lease(key), outcome, retry_after=exc.retry_after)
        return False, str(exc), time.monotonic() - started
    keypool.mark_checked(key, True)
    return True, model, time.monotonic() - started


def guest_chat(system_text, messages):
    """Jawaban tanya-jawab untuk TAMU: tanpa alat, tanpa memori pemilik, singkat. Lewat kumpulan key
    dan putaran model yang sama. Kembalikan teks, atau lempar CloudUnavailable."""
    def build(model):
        return {
            "model": model,
            "messages": [{"role": "system", "content": system_text}] + list(messages),
            "max_tokens": GUEST_MAX_TOKENS,
        }

    choice, _ = _call_with_model(build, keypool.next_model())
    return clean_reply((choice.get("message") or {}).get("content")) or "(tidak ada jawaban)"


def run_agent(messages, ctx):
    """Jalankan loop agen sampai selesai. Mengubah `messages` di tempat.

    Kembalikan teks jawaban akhir, atau lempar CloudUnavailable.
    """
    memory_text = ctx["memory"].text()
    model = keypool.next_model()  # satu model per jawaban (None = model bawaan tiap key)

    for _ in range(config.MAX_STEPS):
        trim_history(messages, config.HISTORY_MESSAGES)
        choice, model = _call_with_model(lambda m: _request(messages, memory_text, m), model)
        message = choice.get("message") or {}

        content = clean_reply(message.get("content"))
        tool_calls = [
            c for c in (message.get("tool_calls") or []) if isinstance(c, dict)
        ]

        assistant = {"role": "assistant", "content": content}
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
