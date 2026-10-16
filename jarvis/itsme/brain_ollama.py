"""Otak lokal: loop agen dengan model yang berjalan di Ollama di komputermu.

Dipakai kalau ITSME_BACKEND=ollama (bawaan), dan sebagai cadangan saat otak
cloud tidak bisa dipakai. Berbicara langsung ke REST API Ollama
(POST /api/chat) dengan httpx — tidak butuh API key, tidak ada data yang
keluar ke penyedia AI.
"""

import httpx

from . import config
from .agentutil import clean_reply, parse_arguments, trim_history
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

_http = httpx.Client(timeout=httpx.Timeout(config.OLLAMA_TIMEOUT, connect=10.0))


class LocalUnavailable(Exception):
    """Otak lokal tidak bisa menjawab sekarang; pesannya siap dibaca pemilik."""


def _request(messages, memory_text):
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt(memory_text, "cari_web", "baca_halaman"),
            }
        ]
        + messages,
        "tools": FUNCTION_TOOLS,
        "stream": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": {"num_ctx": config.OLLAMA_NUM_CTX},
    }
    if config.OLLAMA_THINK is not None:
        body["think"] = config.OLLAMA_THINK
    return body


def _call_ollama(body):
    """Kembalikan (message, error). Tepat satu dari keduanya terisi."""
    try:
        resp = _http.post(f"{config.OLLAMA_URL}/api/chat", json=body)
    except httpx.ConnectError:
        return None, (
            f"Otak lokal belum menyala — Ollama tidak bisa dihubungi di "
            f"{config.OLLAMA_URL}. Pastikan aplikasi Ollama sedang berjalan."
        )
    except httpx.TimeoutException:
        return None, (
            "Otak lokal terlalu lama berpikir dan dihentikan. Coba pertanyaan "
            "yang lebih pendek, atau pakai model yang lebih kecil."
        )
    except httpx.HTTPError as exc:
        return None, f"Gagal menghubungi Ollama: {exc}"

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        detail = data.get("error") or resp.text[:300]
        if resp.status_code == 404 and "not found" in str(detail).lower():
            return None, (
                f"Model '{config.OLLAMA_MODEL}' belum ada di komputer. Kalau setup "
                "sedang mengunduhnya, tunggu sampai selesai; kalau tidak, jalankan "
                "perintah setup lagi."
            )
        if "does not support thinking" in str(detail):
            return None, (
                f"Model '{config.OLLAMA_MODEL}' tidak punya mode berpikir. "
                "Ubah ITSME_OLLAMA_THINK=false di .env."
            )
        return None, f"Ollama menolak permintaan ({resp.status_code}): {detail}"
    message = data.get("message")
    if not isinstance(message, dict):
        return None, "Jawaban Ollama tidak berisi pesan."
    return message, None


def run_agent(messages, ctx, raise_errors=False):
    """Jalankan loop agen sampai selesai. Mengubah `messages` di tempat.

    Kembalikan teks jawaban akhir untuk dikirim ke pemilik. Kalau Ollama
    tidak bisa dipakai, pesan kesalahannya dikembalikan sebagai jawaban, atau
    dilempar sebagai LocalUnavailable kalau raise_errors=True.
    """
    memory_text = ctx["memory"].text()

    for _ in range(config.MAX_STEPS):
        trim_history(messages, config.HISTORY_MESSAGES)
        message, error = _call_ollama(_request(messages, memory_text))
        if error:
            if raise_errors:
                raise LocalUnavailable(error)
            return error

        content = clean_reply(message.get("content"))
        tool_calls = [
            c for c in (message.get("tool_calls") or []) if isinstance(c, dict)
        ]

        # Simpan giliran asisten seperti diterima (termasuk proses berpikir
        # dan panggilan alat), sesuai anjuran dokumentasi Ollama.
        assistant = {"role": "assistant", "content": content}
        if message.get("thinking"):
            assistant["thinking"] = message["thinking"]
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)

        if not tool_calls:
            return content or "(selesai)"

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args, problem = parse_arguments(fn.get("arguments"))
            if problem is None:
                problem = validate_input(name, args)
            out = problem or handle_tool(name, args, ctx)
            result = {"role": "tool", "tool_name": name, "content": str(out)}
            if call.get("id"):  # Ollama >= 0.12.10 memberi id per panggilan
                result["tool_call_id"] = call["id"]
            messages.append(result)

    return "Langkahnya terlalu banyak, aku berhenti dulu. Lanjut?"
