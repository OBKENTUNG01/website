"""Otak DeepSeek: loop agen lewat API DeepSeek (kompatibel OpenAI).

Dipakai kalau ITSME_BACKEND=deepseek atau setelah perintah /deepseek. DeepSeek
murah dan pintar; memakai format chat & pemanggilan alat yang sama dengan
OpenRouter, jadi modul ini jauh lebih ringkas (tanpa kuota gratis, tanpa model
cadangan). Model bawaan deepseek-chat mendukung alat.

Alamat API (DEEPSEEK_URL) bisa diganti ke gateway lain yang kompatibel OpenAI —
berguna kalau key-mu bukan dari api.deepseek.com langsung (mis. diawali nsk-).

Kalau DeepSeek tidak bisa dipakai (saldo habis, key ditolak, internet putus),
fungsi ini melempar CloudUnavailable dan pemilih otak (brain.py) menyerahkan
pertanyaannya ke otak lokal — sama seperti OpenRouter.
"""

import logging
import time

import httpx

from . import config
from .agentutil import clean_reply, parse_arguments, trim_history
from .brain_openrouter import CloudUnavailable  # satu jenis error untuk semua otak cloud
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

log = logging.getLogger("itsme")
_http = httpx.Client(timeout=httpx.Timeout(config.DEEPSEEK_TIMEOUT, connect=10.0))

# Jeda sebelum mencoba lagi kesalahan sesaat (detik).
RETRY_WAITS = (2, 6)
RETRY_CAP = 20


def _headers():
    return {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}


def _request(messages, memory_text):
    body = {
        "model": config.deepseek_model(),
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


def _retry_after(resp):
    value = resp.headers.get("retry-after") if resp is not None else None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _explain(status, message):
    """Ubah error DeepSeek jadi (kalimat jelas untuk pemilik, layak_dicoba_lagi)."""
    detail = f" ({message})" if message else ""
    if status == 401:
        return (
            "API key DeepSeek ditolak (salah, dicabut, atau alamat API-nya beda). "
            "Kalau key-mu dari gateway lain, isi DEEPSEEK_URL di .env.",
            False,
        )
    if status == 402:
        where = "nusagate.com" if "nusagate" in config.deepseek_url() else "platform.deepseek.com"
        return f"Saldo penyedia DeepSeek-mu habis. Isi ulang di {where}.", False
    if status == 429:
        return "DeepSeek sedang membatasi permintaan (terlalu sering atau sibuk). Coba lagi sebentar lagi.", True
    if status in (400, 404, 422):
        return f"Model '{config.deepseek_model()}' atau permintaannya tidak bisa dipakai{detail}.", False
    if status == 408 or (status and status >= 500):
        return "Server DeepSeek sedang bermasalah atau sibuk. Coba lagi sebentar lagi.", True
    return f"DeepSeek mengembalikan error {status}{detail}.", False


def _call_once(body):
    """Kirim satu permintaan; kembalikan choices[0] atau lempar CloudUnavailable."""
    if not config.DEEPSEEK_API_KEY:
        raise CloudUnavailable("Belum ada API key DeepSeek.")
    try:
        resp = _http.post(
            f"{config.deepseek_url()}/chat/completions", json=body, headers=_headers()
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
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message") or "")[:200]
            if status == 200:
                status = error.get("code") if isinstance(error.get("code"), int) else 502
        text, transient = _explain(status, message)
        raise CloudUnavailable(text, transient=transient, retry_after=_retry_after(resp), status=status)
    if choice is None:
        raise CloudUnavailable("Jawaban DeepSeek kosong.", transient=True)
    return choice


def _call(body):
    """Seperti _call_once, tapi kesalahan sesaat dicoba lagi dengan jeda."""
    retries = 0
    while True:
        try:
            return _call_once(body)
        except CloudUnavailable as exc:
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
