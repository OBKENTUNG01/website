"""Pemilih otak It's Me.

  ollama     → model lokal di komputermu lewat Ollama (gratis, privat).
  openrouter → model cloud lewat OpenRouter (jauh lebih pintar). Kalau
               OpenRouter tidak bisa dipakai, otak lokal otomatis mengambil
               alih (bisa dimatikan dengan ITSME_FALLBACK_LOCAL=0).
  deepseek   → model DeepSeek, murah & pintar. Jatuh ke otak lokal juga.
  claude     → Claude lewat Anthropic API.
  claude_code → Claude di komputer (Claude Code) dengan akun Claude-mu.

Otak awal diambil dari ITSME_BACKEND, lalu bisa diganti saat bot berjalan
lewat perintah Telegram /lokal, /cloud, /deepseek, /claude, dan /claudepc.
Modul tiap otak di-import saat dipakai, jadi pemakai satu otak tidak butuh
perlengkapan otak lainnya.
"""

import json
import os

from . import config, keypool, keys
from .agentutil import text_only

BACKENDS = ("ollama", "openrouter", "deepseek", "claude", "claude_code")

_state = {"backend": config.BACKEND}


def current_backend():
    return _state["backend"]


def _choice_file():
    return os.path.join(config.DATA_DIR, "brain.json")


def restore():
    """Pakai lagi otak pilihan terakhir pemilik, supaya tetap sama setelah
    It's Me menyala ulang (mis. sesudah pembaruan otomatis)."""
    try:
        with open(_choice_file(), encoding="utf-8") as f:
            name = json.load(f).get("backend")
    except (OSError, ValueError, AttributeError):
        return
    try:
        set_backend(name, remember=False)
    except ValueError:
        pass  # mis. key/token-nya sudah dihapus: tetap otak bawaan


def set_backend(name, remember=True):
    """Ganti otak. Lempar ValueError (pesan siap dibaca) kalau belum bisa."""
    if name not in BACKENDS:
        raise ValueError(f"Otak '{name}' tidak dikenal.")
    if name == "openrouter" and not config.OPENROUTER_API_KEY:
        raise ValueError(
            "Belum ada API key OpenRouter. Kirim /kunci <key OpenRouter> "
            "(pesannya langsung kuhapus), atau jalankan perintah setup lagi."
        )
    if name == "deepseek" and not keypool.has_keys():
        raise ValueError(
            "Belum ada API key DeepSeek. Kirim /deepseek <key> (pesannya langsung "
            "kuhapus). Key dari NusaGate diawali nsk_; key DeepSeek resmi diawali sk-. "
            "Boleh banyak key sekaligus (semuanya dipakai bergiliran)."
        )
    if name == "claude" and not config.ANTHROPIC_API_KEY:
        raise ValueError(
            "Belum ada ANTHROPIC_API_KEY. Kirim /kunci <key Claude> (pesannya "
            "langsung kuhapus), atau isi di .env lalu nyalakan ulang It's Me."
        )
    if name == "claude_code" and not claude_code_ready():
        from .brain_claude_code import LOGIN_HINT

        raise ValueError(LOGIN_HINT)
    _state["backend"] = name
    if remember:
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(_choice_file(), "w", encoding="utf-8") as f:
                json.dump({"backend": name}, f)
        except OSError:
            pass  # tetap berganti sekarang; hanya tidak diingat setelah menyala ulang


def claude_code_ready():
    """Token akun Claude ada? Dibaca ulang dari .env kalau perlu, karena
    `python -m itsme.claude_login` menyimpannya dari proses lain."""
    if not config.CLAUDE_CODE_OAUTH_TOKEN:
        keys.refresh_from_env("CLAUDE_CODE_OAUTH_TOKEN")
    return bool(config.CLAUDE_CODE_OAUTH_TOKEN)


def describe(name=None):
    name = name or current_backend()
    if name == "openrouter":
        from .brain_openrouter import fallback_models

        fallbacks = fallback_models()
        extra = f", cadangan {', '.join(fallbacks)}" if fallbacks else ""
        return f"cloud {config.OPENROUTER_MODEL} (OpenRouter{extra})"
    if name == "deepseek":
        first = (config.deepseek_keys() or [None])[0]
        prov = "NusaGate" if "nusagate" in config.deepseek_url(first) else "DeepSeek resmi"
        count = len(config.deepseek_keys())
        extra = f", {count} key dipakai bergiliran" if count > 1 else ""
        models = config.NUSAGATE_MODELS
        shown = f"{len(models)} model bergantian" if len(models) > 1 else config.deepseek_model(first)
        return f"DeepSeek {shown} ({prov}{extra})"
    if name == "claude":
        return f"Claude {config.MODEL} (effort {config.EFFORT})"
    if name == "claude_code":
        model = config.CLAUDE_CODE_MODEL or "model bawaan akunmu"
        return f"Claude di komputer ({model}, akun Claude-mu, tanpa API key)"
    return f"lokal {config.OLLAMA_MODEL} (Ollama)"


def run_agent(messages, ctx):
    if ctx.get("guest"):
        # Tamu (kontak WhatsApp, tamu Telegram) TIDAK PERNAH memakai otak pilihan pemilik: otak
        # "Claude di komputer" punya alat sendiri (shell, berkas) yang tidak dijaga ctx["guest"].
        from .brain_guest import reply

        return reply(messages, ctx)
    backend = current_backend()
    if backend == "claude":
        from .brain_claude import run_agent as run

        return run(messages, ctx)

    if backend == "claude_code":
        from .brain_claude_code import run_agent as run

        return run(messages, ctx)

    if backend in ("openrouter", "deepseek"):
        from .brain_openrouter import CloudUnavailable

        if backend == "deepseek":
            from .brain_deepseek import run_agent as cloud
        else:
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
