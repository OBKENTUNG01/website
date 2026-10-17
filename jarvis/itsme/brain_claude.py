"""Otak Claude: loop agen dengan Claude (Opus 5.5, effort max).

Dipakai kalau ITSME_BACKEND=claude. Memakai SDK Anthropic resmi. Model dikunci
dari config dan tidak diganti ke model lain. Alat server (web_search/web_fetch)
dan alat klien dipadukan dalam satu loop.
"""

from anthropic import Anthropic

from . import config
from .persona import system_prompt
from .tools import CLAUDE_TOOLS, handle_tool, validate_input

_client = None  # (key, klien); dibuat ulang kalau key diganti lewat /kunci


def _anthropic():
    global _client
    if _client is None or _client[0] != config.ANTHROPIC_API_KEY:
        _client = (config.ANTHROPIC_API_KEY, Anthropic(api_key=config.ANTHROPIC_API_KEY))
    return _client[1]


def _build_request(messages, memory_text):
    return {
        "model": config.MODEL,
        "max_tokens": config.MAX_TOKENS,
        "system": system_prompt(memory_text, "web_search", "web_fetch"),
        "messages": messages,
        "tools": CLAUDE_TOOLS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": config.EFFORT},
    }


def run_agent(messages, ctx):
    """Jalankan loop agen sampai selesai. Mengubah `messages` di tempat.

    Kembalikan teks jawaban akhir untuk dikirim ke pemilik.
    """
    memory_text = ctx["memory"].text()

    for _ in range(config.MAX_STEPS):
        try:
            with _anthropic().messages.stream(
                **_build_request(messages, memory_text)
            ) as stream:
                msg = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            return f"Maaf, ada error saat memanggil otak: {exc}"

        # Simpan giliran asisten apa adanya (termasuk blok thinking & hasil
        # alat server) — penting agar riwayat tetap valid di Opus 5.5.
        messages.append({"role": "assistant", "content": msg.content})

        if msg.stop_reason == "refusal":
            return (
                "Maaf, bagian itu tidak bisa aku lakukan. "
                "Tapi sisanya tetap bisa — beri tahu aku langkah berikutnya."
            )

        if msg.stop_reason == "pause_turn":
            # Alat server masih jalan — kirim ulang untuk melanjutkan.
            continue

        if msg.stop_reason == "tool_use":
            results = []
            for block in msg.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool_input = block.input if isinstance(block.input, dict) else {}
                problem = validate_input(block.name, tool_input)
                if problem:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": problem,
                            "is_error": True,
                        }
                    )
                    continue
                out = handle_tool(block.name, tool_input, ctx)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(out),
                    }
                )
            if results:
                messages.append({"role": "user", "content": results})
                continue
            # tidak ada alat klien yang perlu dijalankan — berhenti.
            return _text_of(msg)

        return _text_of(msg)

    return "Langkahnya terlalu banyak, aku berhenti dulu. Lanjut?"


def _text_of(msg):
    parts = [
        b.text
        for b in msg.content
        if getattr(b, "type", None) == "text" and getattr(b, "text", "")
    ]
    return "\n".join(parts).strip() or "(selesai)"
