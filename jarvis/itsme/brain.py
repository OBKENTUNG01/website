"""Pemilih otak It's Me.

  ollama     → model lokal di komputermu lewat Ollama (gratis, privat).
  openrouter → model cloud lewat OpenRouter (jauh lebih pintar). Kalau
               OpenRouter tidak bisa dipakai, otak lokal otomatis mengambil
               alih (bisa dimatikan dengan ITSME_FALLBACK_LOCAL=0).
  claude     → Claude lewat Anthropic API.

Otak awal diambil dari ITSME_BACKEND, lalu bisa diganti saat bot berjalan
lewat perintah Telegram /lokal, /cloud, dan /claude. Modul tiap otak di-import saat
dipakai, jadi pemakai satu otak tidak butuh perlengkapan otak lainnya.
"""

from . import config
from .agentutil import text_only

BACKENDS = ("ollama", "openrouter", "claude")

_state = {"backend": config.BACKEND}


def current_backend():
    return _state["backend"]


def set_backend(name):
    """Ganti otak. Lempar ValueError (pesan siap dibaca) kalau belum bisa."""
    if name not in BACKENDS:
        raise ValueError(f"Otak '{name}' tidak dikenal.")
    if name == "openrouter" and not config.OPENROUTER_API_KEY:
        raise ValueError(
            "Belum ada API key OpenRouter. Jalankan perintah setup lagi dan "
            "tempel key-nya saat diminta."
        )
    if name == "claude" and not config.ANTHROPIC_API_KEY:
        raise ValueError(
            "Belum ada ANTHROPIC_API_KEY di .env. Isi dulu di komputer (jangan "
            "kirim key lewat chat), lalu nyalakan ulang It's Me."
        )
    _state["backend"] = name


def describe(name=None):
    name = name or current_backend()
    if name == "openrouter":
        from .brain_openrouter import fallback_models

        fallbacks = fallback_models()
        extra = f", cadangan {', '.join(fallbacks)}" if fallbacks else ""
        return f"cloud {config.OPENROUTER_MODEL} (OpenRouter{extra})"
    if name == "claude":
        return f"Claude {config.MODEL} (effort {config.EFFORT})"
    return f"lokal {config.OLLAMA_MODEL} (Ollama)"


def run_agent(messages, ctx):
    backend = current_backend()
    if backend == "claude":
        from .brain_claude import run_agent as run

        return run(messages, ctx)

    if backend == "openrouter":
        from .brain_openrouter import CloudUnavailable
        from .brain_openrouter import run_agent as cloud

        try:
            return cloud(messages, ctx)
        except CloudUnavailable as exc:
            if not config.FALLBACK_LOCAL:
                return f"⚠️ {exc}"
            from .brain_ollama import LocalUnavailable
            from .brain_ollama import run_agent as local

            # Otak lokal menjawab dari salinan teks obrolan (tanpa jejak alat
            # format cloud), lalu hanya jawabannya yang masuk ke riwayat.
            try:
                reply = local(text_only(messages), ctx, raise_errors=True)
            except LocalUnavailable as local_exc:
                return f"⚠️ {exc}\n\nOtak lokal (cadangan) juga belum bisa menjawab: {local_exc}"
            messages.append({"role": "assistant", "content": reply})
            return f"⚠️ {exc} Kujawab pakai otak lokal.\n\n{reply}"

    from .brain_ollama import run_agent as run

    return run(messages, ctx)
