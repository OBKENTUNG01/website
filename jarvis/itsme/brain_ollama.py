"""Otak lokal: loop agen dengan model yang berjalan di Ollama di komputermu.

Dipakai kalau ITSME_BACKEND=ollama (bawaan), dan sebagai cadangan saat otak
cloud tidak bisa dipakai. Berbicara langsung ke REST API Ollama
(POST /api/chat) dengan httpx — tidak butuh API key, tidak ada data yang
keluar ke penyedia AI.
"""

import json
import logging
import os
import sys

import httpx

from . import config
from .agentutil import clean_reply, parse_arguments, trim_history
from .persona import system_prompt
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

log = logging.getLogger("itsme")
_http = httpx.Client(timeout=httpx.Timeout(config.OLLAMA_TIMEOUT, connect=10.0))


class LocalUnavailable(Exception):
    """Otak lokal tidak bisa menjawab sekarang; pesannya siap dibaca pemilik."""


# Kalau proses model di Ollama (llama-server) mati mendadak, biasanya karena
# driver atau memori GPU, It's Me mencoba lagi dengan pengaturan yang lebih
# aman. Pengaturan yang akhirnya jalan disimpan (config.OLLAMA_STATE_FILE),
# supaya It's Me tidak crash lagi setiap kali dinyalakan ulang. Hapus berkas
# itu (mis. setelah memperbarui driver GPU) untuk mencoba GPU lagi.
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
    "CPU saja (lebih lambat, tapi stabil). Pengaturan ini diingat.",
)
# Tanda di log Ollama bahwa yang mati adalah GPU-nya (mis. kartu grafis lama
# lewat Vulkan: "device lost"). Kalau begitu, langsung pakai CPU saja.
_GPU_CRASH_SIGNS = ("device lost", "vk_error_device_lost", "cuda error", "cudaerror", "hiperror")


def _load_level():
    try:
        with open(config.OLLAMA_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        level = int(data.get("level", 0))
        if data.get("model") == config.OLLAMA_MODEL and 0 <= level < len(_SAFE):
            return level
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return 0


def _save_level(level):
    try:
        os.makedirs(os.path.dirname(config.OLLAMA_STATE_FILE), exist_ok=True)
        with open(config.OLLAMA_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"model": config.OLLAMA_MODEL, "level": level}, f)
    except OSError:
        pass


def _ollama_log_path():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return os.path.join(base, "Ollama", "server.log") if base else None
    if sys.platform == "darwin":
        return os.path.expanduser("~/.ollama/logs/server.log")
    return None  # Linux: log ada di journalctl, tidak dibaca dari sini


def _gpu_crashed():
    """Lihat ujung log Ollama: apakah crash terakhir terjadi di GPU?"""
    path = _ollama_log_path()
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 32768))
            tail = f.read().decode("utf-8", "replace").lower()
    except OSError:
        return False
    return any(sign in tail for sign in _GPU_CRASH_SIGNS)


_level = _load_level()
# Catatan di jawaban hanya muncul saat pengaturan baru saja diturunkan.
_state = {"level": _level, "noted": _level}
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
        # GPU yang mati tidak tertolong oleh konteks lebih kecil: langsung CPU.
        _state["level"] = len(_SAFE) - 1 if _gpu_crashed() else _state["level"] + 1
        _save_level(_state["level"])
        log.warning("otak lokal berhenti mendadak; dicoba lagi dengan %s", _SAFE_NAMES[_state["level"]])
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
