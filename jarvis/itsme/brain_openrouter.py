"""Otak cloud: loop agen lewat OpenRouter (opsional, jauh lebih pintar).

Dipakai kalau ITSME_BACKEND=openrouter atau setelah perintah /cloud.
OpenRouter memberi akses ke ratusan model (gratis maupun berbayar) dengan satu
API key, memakai format chat yang sama dengan otak lokal.

Kalau OpenRouter tidak bisa dipakai (saldo habis, batas harian, internet,
key ditolak), fungsi ini melempar CloudUnavailable dan pemilih otak (brain.py)
menyerahkan pertanyaannya ke otak lokal.
"""

import httpx

from . import config
from .agentutil import clean_reply, parse_arguments, trim_history
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

_http = httpx.Client(timeout=httpx.Timeout(config.OPENROUTER_TIMEOUT, connect=10.0))


class CloudUnavailable(Exception):
    """Otak cloud tidak bisa dipakai sekarang; pesannya siap dibaca pemilik."""


def _headers():
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        # Atribusi aplikasi (opsional) sesuai dokumentasi OpenRouter.
        "HTTP-Referer": "https://github.com/OBKENTUNG01/website",
        "X-OpenRouter-Title": config.NAME,
    }


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
    if config.OPENROUTER_REASONING not in ("", "default"):
        body["reasoning"] = {"effort": config.OPENROUTER_REASONING}
    return body


def _explain(status, message):
    """Ubah kode error OpenRouter jadi kalimat yang jelas untuk pemilik."""
    detail = f" ({message})" if message else ""
    if status == 401:
        return "API key OpenRouter ditolak. Buat key baru lalu jalankan setup lagi."
    if status == 402:
        return "Saldo OpenRouter habis atau batas kredit key sudah tercapai."
    if status == 429:
        return (
            "Batas pemakaian OpenRouter tercapai (model gratis: 50 permintaan/hari). "
            "Coba lagi nanti."
        )
    if status == 403:
        return f"Permintaan ditolak OpenRouter{detail}."
    if status in (400, 404):
        return f"Model '{config.OPENROUTER_MODEL}' tidak bisa dipakai{detail}."
    if status and status >= 500:
        return "Penyedia model di OpenRouter sedang bermasalah."
    return f"OpenRouter mengembalikan error {status}{detail}."


def _call(body):
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
    error = data.get("error") if isinstance(data, dict) else None
    if resp.status_code != 200 or error:
        status = resp.status_code
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message") or "")[:200]
            if status == 200 and isinstance(error.get("code"), int):
                status = error["code"]
        raise CloudUnavailable(_explain(status, message))
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise CloudUnavailable("Jawaban OpenRouter kosong.")
    return choices[0]


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
