"""Otak It's Me: loop agen dengan Claude (Opus 5.5, effort max).

Memakai SDK Anthropic resmi. Model dikunci dari config dan tidak diganti ke
model lain. Alat server (web_search/web_fetch) dan alat klien dipadukan dalam
satu loop.
"""

from anthropic import Anthropic

from . import config
from .tools import ALL_TOOLS, handle_tool

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _system_prompt(memory_text):
    return f"""Kamu adalah {config.NAME}, asisten pribadi milik satu orang
(pemilik). Kamu berbicara bahasa Indonesia yang santai tapi sopan, memanggil
pemilik dengan "Tuan" sesekali seperti JARVIS di Iron Man.

GAYA:
- Ringkas. Dalam obrolan, jawab seperlunya — beberapa kalimat cukup.
- Boleh panjang hanya saat membacakan data/hasil yang memang diminta.
- Jujur. Kalau tidak yakin, cari dulu lewat web_search, jangan mengarang.

KEMAMPUAN:
- Cari & baca info terbaru dari internet dengan web_search dan web_fetch.
  Pengetahuan bawaanmu ada batas waktunya, jadi untuk hal terkini selalu cek
  internet dulu.
- Jalankan perintah, baca/tulis berkas, dan deploy website lewat Git.
- Simpan hal penting dengan simpan_memori supaya diingat lain kali.
- Kerjakan beberapa tugas sekaligus: untuk tugas yang bisa jalan paralel,
  pakai tugas_latar supaya pemilik tetap bisa mengobrol sementara kamu bekerja.

ATURAN:
- Aksi berisiko (menulis berkas, perintah berbahaya, deploy) otomatis minta
  konfirmasi ke pemilik lewat tombol. Kamu tidak perlu bertanya lagi di teks —
  cukup panggil alatnya; sistem yang menampilkan tombolnya.
- Setelah pemilik menekan "Ya", kerjakan tanpa ragu.

MEMORI SAAT INI:
{memory_text or "(masih kosong)"}
"""


def _build_request(messages, memory_text):
    return {
        "model": config.MODEL,
        "max_tokens": config.MAX_TOKENS,
        "system": _system_prompt(memory_text),
        "messages": messages,
        "tools": ALL_TOOLS,
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
            with _client.messages.stream(
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
                if getattr(block, "type", None) == "tool_use":
                    out = handle_tool(block.name, block.input or {}, ctx)
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
