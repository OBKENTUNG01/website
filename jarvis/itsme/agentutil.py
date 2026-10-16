"""Bantuan kecil yang dipakai bersama oleh otak lokal (Ollama) dan cloud (OpenRouter).

Keduanya memakai format pesan "chat" yang sama: user / assistant (+ tool_calls)
/ tool.
"""

import json
import re

# Sebagian model menulis proses berpikirnya di dalam teks jawaban.
_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def clean_reply(content):
    """Teks jawaban yang siap dikirim: tanpa blok <think>, tanpa spasi berlebih."""
    if isinstance(content, list):  # beberapa penyedia mengirim potongan teks
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return _THINK_TAGS.sub("", content or "").strip()


def parse_arguments(raw):
    """Argumen alat → (dict, None) atau (None, pesan_error).

    Ollama mengirim argumen sebagai objek JSON, OpenRouter sebagai teks JSON.
    """
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


def trim_history(messages, limit):
    """Buang riwayat lama (di tempat) supaya muat di konteks dan hemat biaya.

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


def text_only(messages):
    """Salinan riwayat berisi teks user & asisten saja (tanpa alat).

    Dipakai saat otak cadangan mengambil alih: format panggilan alat tiap otak
    berbeda, tapi teks obrolan bisa dibaca semuanya.
    """
    out = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    return out
