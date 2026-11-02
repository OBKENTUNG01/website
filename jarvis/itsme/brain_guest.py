"""Jawaban untuk TAMU: orang selain pemilik (kontak WhatsApp di mode terbuka, tamu Telegram).

Tamu TIDAK PERNAH sampai ke otak yang punya alat (Claude di komputer, shell, berkas), tidak
membawa memori pemilik, dan hanya tanya-jawab dengan jawaban singkat. Dulu tamu memakai otak yang
sedang dipilih pemilik; dengan "Claude di komputer" itu berarti alat-alatnya terbuka untuk orang
asing (penjaga `guest` hanya ada di tools.py, sedangkan Claude Code memakai alatnya sendiri).

Urutan otak untuk tamu (yang pertama siap dipakai):
  1. Kumpulan key NusaGate/DeepSeek (hemat, dan beban tersebar merata di semua key; keypool.py)
  2. Otak lokal BERSAMA: semua komputer yang bergabung ikut berpikir (brain_cluster.py), lengkap dengan
     alur memahami -> menjawab -> memeriksa. Kalau pemilik memilih /bersama, ini yang PERTAMA.
     Dimatikan dengan ITSME_GUEST_CLUSTER=0.
  3. OpenRouter
  4. Otak lokal satu komputer (Ollama) - HANYA kalau pemilik menyalakan ITSME_WA_GUEST_LOCAL=1: lambat dan
     bisa macet, jadi bawaannya orang asing tidak diizinkan menyita komputer pemilik dengan model lokal.
Kalau semuanya gagal, tamu mendapat permintaan maaf singkat - tidak pernah isi galat teknis.
"""

import logging
import os

from . import config, keypool, persona, think_pool
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


def cluster_enabled():
    """Otak lokal bersama ikut menjawab tamu? (diizinkan, dan It's Me Node terpasang)"""
    return config.GUEST_CLUSTER and think_pool.installed()


def wait_cap(base):
    """Paling lama tamu menunggu satu jawaban (detik): `base` untuk cloud, lebih lega bila otak lokal bersama ikut."""
    return max(base, config.CLUSTER_GUEST_TIMEOUT) if cluster_enabled() else base


def _cluster_reply(system, chat):
    from . import brain_cluster

    try:
        # Anggaran waktu: tamu yang sudah tak menunggu tidak boleh terus menyita komputer-komputer.
        with think_pool.budget(max(10.0, config.CLUSTER_GUEST_TIMEOUT - 10.0)):
            return brain_cluster.guest_chat(system, chat)
    except brain_cluster.ClusterUnavailable as exc:
        log.warning("tamu: otak lokal bersama tidak bisa menjawab (%s)", exc)
        return None


def reply(messages, ctx):
    """Jawab tamu. `messages` = riwayat chat tamu itu (diubah di tempat: jawaban ikut dicatat)."""
    from . import brain
    from .brain_openrouter import CloudUnavailable

    trim_history(messages, GUEST_HISTORY)
    system = persona.guest_prompt(read_info())
    chat = text_only(messages)

    def done(text):
        messages.append({"role": "assistant", "content": text})
        return text

    use_cluster = cluster_enabled()
    cluster_first = use_cluster and brain.current_backend() == "kumpulan"
    if cluster_first:
        text = _cluster_reply(system, chat)
        if text:
            return done(text)
    if keypool.has_keys():
        from . import brain_deepseek

        try:
            return done(brain_deepseek.guest_chat(system, chat))
        except CloudUnavailable as exc:
            log.warning("tamu: kumpulan key tidak bisa menjawab (%s)", exc)
    if use_cluster and not cluster_first:
        text = _cluster_reply(system, chat)
        if text:
            return done(text)
    if config.OPENROUTER_API_KEY:
        from . import brain_openrouter

        try:
            return done(brain_openrouter.guest_chat(system, chat))
        except CloudUnavailable as exc:
            log.warning("tamu: OpenRouter tidak bisa menjawab (%s)", exc)
    if config.WA_GUEST_LOCAL:
        from . import brain_ollama

        try:
            return done(brain_ollama.guest_chat(system, chat))
        except brain_ollama.LocalUnavailable as exc:
            log.warning("tamu: otak lokal tidak bisa menjawab (%s)", exc)
    return SORRY
