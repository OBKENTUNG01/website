"""Otak lokal: loop agen dengan model yang berjalan di Ollama di komputermu.

Dipakai kalau ITSME_BACKEND=ollama (bawaan). Berbicara langsung ke REST API
Ollama (POST /api/chat) dengan httpx — tidak butuh API key, tidak ada data
yang keluar ke penyedia AI.
"""

import json
import re

import httpx

from . import config
from .persona import system_prompt
from .tools import OLLAMA_TOOLS, handle_tool, validate_input

# Sebagian model menulis proses berpikirnya di dalam teks jawaban.
_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_http = httpx.Client(timeout=httpx.Timeout(config.OLLAMA_TIMEOUT, connect=10.0))


def _trim(messages, limit):
    """Buang riwayat lama (di tempat) supaya muat di konteks model.

    Potongan selalu dimulai di pesan "user", supaya hasil alat tidak tertinggal
    tanpa pemanggilnya, dan pesan user giliran ini tidak pernah ikut terbuang.
    """
    if len(messages) <= limit:
        return
    user_idx = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idx:
        return
    start = len(messages) - limit
    while start < user_idx[-1] and messages[start].get("role") != "user":
        start += 1
    start = min(start, user_idx[-1])
    del messages[:start]


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
        "tools": OLLAMA_TOOLS,
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
                f"Model '{config.OLLAMA_MODEL}' belum diunduh. Jalankan di "
                f"komputer: ollama pull {config.OLLAMA_MODEL}"
            )
        return None, f"Ollama menolak permintaan ({resp.status_code}): {detail}"
    message = data.get("message")
    if not isinstance(message, dict):
        return None, "Jawaban Ollama tidak berisi pesan."
    return message, None


def _parse_arguments(raw):
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return None, "Argumen alat bukan JSON yang valid."
        if isinstance(parsed, dict):
            return parsed, None
    return None, "Argumen alat tidak valid."


def run_agent(messages, ctx):
    """Jalankan loop agen sampai selesai. Mengubah `messages` di tempat.

    Kembalikan teks jawaban akhir untuk dikirim ke pemilik.
    """
    memory_text = ctx["memory"].text()

    for _ in range(config.MAX_STEPS):
        _trim(messages, config.HISTORY_MESSAGES)
        message, error = _call_ollama(_request(messages, memory_text))
        if error:
            return error

        content = _THINK_TAGS.sub("", message.get("content") or "").strip()
        tool_calls = [
            c for c in (message.get("tool_calls") or []) if isinstance(c, dict)
        ]

        assistant = {"role": "assistant", "content": content}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)

        if not tool_calls:
            return content or "(selesai)"

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args, problem = _parse_arguments(fn.get("arguments"))
            if problem is None:
                problem = validate_input(name, args)
            out = problem or handle_tool(name, args, ctx)
            messages.append({"role": "tool", "tool_name": name, "content": str(out)})

    return "Langkahnya terlalu banyak, aku berhenti dulu. Lanjut?"
