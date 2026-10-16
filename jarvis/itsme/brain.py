"""Pemilih otak It's Me.

ITSME_BACKEND=ollama (bawaan) → model lokal di komputermu lewat Ollama.
ITSME_BACKEND=claude          → Claude lewat Anthropic API (butuh API key).

Modul otak di-import saat dipakai, jadi pemakai Ollama tidak butuh API key
dan pemakai Claude tidak butuh Ollama.
"""

from . import config


def run_agent(messages, ctx):
    if config.BACKEND == "claude":
        from .brain_claude import run_agent as run
    else:
        from .brain_ollama import run_agent as run
    return run(messages, ctx)
