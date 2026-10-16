"""Otak lokal: loop agen dengan model yang berjalan di Ollama di komputermu.

Dipakai kalau ITSME_BACKEND=ollama (bawaan), dan sebagai cadangan saat otak
cloud tidak bisa dipakai. Berbicara langsung ke REST API Ollama
(POST /api/chat) dengan httpx — tidak butuh API key, tidak ada data yang
keluar ke penyedia AI.
"""

import os

import httpx

from . import config
from .agentutil import clean_reply, parse_arguments, trim_history
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

_http = httpx.Client(timeout=httpx.Timeout(config.OLLAMA_TIMEOUT, connect=10.0))


class LocalUnavailable(Exception):
    """Otak lokal tidak bisa menjawab sekarang; pesannya siap dibaca pemilik."""


# Kalau proses model di Ollama (llama-server) mati mendadak, biasanya karena
# driver atau memori GPU, It's Me mencoba lagi dengan pengaturan yang lebih
# aman dan memakainya sampai It's Me dinyalakan ulang.
_SAFE = (
    {},
    {"num_ctx": 8192},
    {"num_ctx": 8192, "num_gpu": 0},
)
_SAFE_NAMES = ("pengaturan biasa", "konteks lebih kecil (8192)", "CPU saja (konteks 8192)")
_SAFE_NOTES = (
    "",
    "Catatan: otak lokal sempat berhenti mendadak, jadi sekarang aku memakai konteks "
    "yang lebih kecil supaya stabil.",
    "Catatan: otak lokal sempat berhenti mendadak di GPU, jadi sekarang aku memakai "
    "CPU saja (lebih lambat, tapi stabil) sampai It's Me dinyalakan ulang.",
)
_state = {"level": 0, "noted": 0}
_CRASH_SIGNS = (
    "error was encountered while running the model",
    "runner has unexpectedly stopped",
    "process has terminated",
    "process no longer running",
    "out-of-memory",
    "out of memory",
    "requires more system memory",
    "error starting llama-server",
    "startup failed",
    "timed out waiting for llama-server",
)
_LOG_HINT = (
    r"%LOCALAPPDATA%\Ollama\server.log"
    if os.name == "nt"
    else "~/.ollama/logs/server.log di Mac, atau journalctl -u ollama di Linux"
)


def _options():
    options = {"num_ctx": config.OLLAMA_NUM_CTX}
    if config.OLLAMA_NUM_GPU is not None:
        options["num_gpu"] = config.OLLAMA_NUM_GPU
    safe = _SAFE[_state["level"]]
    if "num_ctx" in safe:
        options["num_ctx"] = min(options["num_ctx"], safe["num_ctx"])
    if "num_gpu" in safe:
        options["num_gpu"] = safe["num_gpu"]
    return options


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
        "options": _options(),
    }
    if config.OLLAMA_THINK is not None:
        body["think"] = config.OLLAMA_THINK
    return body


def _call_ollama(body):
    """Kembalikan (message, error, crashed). Tepat satu dari message/error terisi;
    crashed=True kalau proses model di Ollama mati mendadak."""
    try:
        resp = _http.post(f"{config.OLLAMA_URL}/api/chat", json=body)
    except httpx.ConnectError:
        return None, (
            f"Otak lokal belum menyala — Ollama tidak bisa dihubungi di "
            f"{config.OLLAMA_URL}. Pastikan aplikasi Ollama sedang berjalan."
        ), False
    except httpx.TimeoutException:
        return None, (
            "Otak lokal terlalu lama berpikir dan dihentikan. Coba pertanyaan "
            "yang lebih pendek, atau pakai model yang lebih kecil."
        ), False
    except httpx.HTTPError as exc:
        return None, f"Gagal menghubungi Ollama: {exc}", False

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
            ), False
        if "does not support thinking" in str(detail):
            return None, (
                f"Model '{config.OLLAMA_MODEL}' tidak punya mode berpikir. "
                "Ubah ITSME_OLLAMA_THINK=false di .env."
            ), False
        crashed = any(sign in str(detail).lower() for sign in _CRASH_SIGNS)
        return None, f"Ollama menolak permintaan ({resp.status_code}): {detail}", crashed
    message = data.get("message")
    if not isinstance(message, dict):
        return None, "Jawaban Ollama tidak berisi pesan.", False
    return message, None, False


def _ask(messages, memory_text):
    """Panggil Ollama. Kalau proses modelnya mati mendadak, ulangi dengan
    pengaturan yang lebih aman (konteks lebih kecil, lalu CPU saja)."""
    message, error, crashed = _call_ollama(_request(messages, memory_text))
    while crashed and _state["level"] < len(_SAFE) - 1:
        _state["level"] += 1
        print(
            f"[itsme] otak lokal berhenti mendadak; dicoba lagi dengan {_SAFE_NAMES[_state['level']]}",
            flush=True,
        )
        message, error, crashed = _call_ollama(_request(messages, memory_text))
    if crashed:
        error = (
            "Otak lokal berhenti mendadak setiap kali menjalankan model, juga dengan "
            f"CPU saja. Penyebabnya tercatat di log Ollama ({_LOG_HINT}). {error}"
        )
    return message, error


def run_agent(messages, ctx, raise_errors=False):
    """Jalankan loop agen sampai selesai. Mengubah `messages` di tempat.

    Kembalikan teks jawaban akhir untuk dikirim ke pemilik. Kalau Ollama
    tidak bisa dipakai, pesan kesalahannya dikembalikan sebagai jawaban, atau
    dilempar sebagai LocalUnavailable kalau raise_errors=True.
    """
    memory_text = ctx["memory"].text()

    for _ in range(config.MAX_STEPS):
        trim_history(messages, config.HISTORY_MESSAGES)
        message, error = _ask(messages, memory_text)
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
            reply = content or "(selesai)"
            if _state["noted"] < _state["level"]:
                _state["noted"] = _state["level"]
                reply += f"\n\n({_SAFE_NOTES[_state['level']]})"
            return reply

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
