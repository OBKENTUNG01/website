"""Jawaban untuk TAMU: orang selain pemilik (kontak WhatsApp di mode terbuka, tamu Telegram).

Tamu TIDAK PERNAH sampai ke otak yang punya alat (Claude di komputer, shell, berkas), tidak
membawa memori pemilik, dan hanya tanya-jawab dengan jawaban singkat. Dulu tamu memakai otak yang
sedang dipilih pemilik; dengan "Claude di komputer" itu berarti alat-alatnya terbuka untuk orang
asing (penjaga `guest` hanya ada di tools.py, sedangkan Claude Code memakai alatnya sendiri).

Urutan otak untuk tamu (yang pertama siap dipakai):
  1. Kumpulan key NusaGate/DeepSeek (hemat, dan beban tersebar merata di semua key; keypool.py)
  2. OpenRouter
  3. Otak lokal (Ollama)
Kalau semuanya gagal, tamu mendapat permintaan maaf singkat - tidak pernah isi galat teknis.
"""

import logging
import os

from . import config, keypool, persona
from .agentutil import text_only, trim_history

log = logging.getLogger("itsme")

GUEST_HISTORY = 12  # pesan terakhir yang dibawa ke tiap jawaban
SORRY = "Maaf, asisten AI sedang tidak bisa menjawab. Coba lagi nanti ya."


def info_file():
    return os.path.join(config.DATA_DIR, "wa_info.txt")


def read_info():
    """Info yang BOLEH dibagikan ke tamu, ditulis pemilik lewat /wa_info (kosong = tidak ada)."""
    try:
        with open(info_file(), encoding="utf-8") as f:
            return f.read().strip()[:2000]
    except OSError:
        return ""


def reply(messages, ctx):
    """Jawab tamu. `messages` = riwayat chat tamu itu (diubah di tempat: jawaban ikut dicatat)."""
    from .brain_openrouter import CloudUnavailable

    trim_history(messages, GUEST_HISTORY)
    system = persona.guest_prompt(read_info())
    chat = text_only(messages)

    def done(text):
        messages.append({"role": "assistant", "content": text})
        return text

    if keypool.has_keys():
        from . import brain_deepseek

        try:
            return done(brain_deepseek.guest_chat(system, chat))
        except CloudUnavailable as exc:
            log.warning("tamu: kumpulan key tidak bisa menjawab (%s)", exc)
    if config.OPENROUTER_API_KEY:
        from . import brain_openrouter

        try:
            return done(brain_openrouter.guest_chat(system, chat))
        except CloudUnavailable as exc:
            log.warning("tamu: OpenRouter tidak bisa menjawab (%s)", exc)
    from . import brain_ollama

    try:
        return done(brain_ollama.guest_chat(system, chat))
    except brain_ollama.LocalUnavailable as exc:
        log.warning("tamu: otak lokal tidak bisa menjawab (%s)", exc)
    return SORRY
