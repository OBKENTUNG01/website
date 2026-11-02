"""Otak lokal bersama: semua komputer yang sudah bergabung ikut berpikir untuk bot.

Dipakai kalau pemilik memilih /bersama. Loop agennya sama dengan otak lokal biasa (brain_ollama.py:
alat, riwayat, jawaban akhir), tetapi setiap giliran berpikir dikerjakan komputer terbaik yang lowong
(think_pool.py) dan dijaga alur memahami -> menjawab -> memeriksa (thinking.py) supaya jawaban tetap
nyambung dengan maksud pesan, bukan melenceng.

Beda dengan otak lokal biasa, anggarannya lebih besar tetapi tetap berbatas: maksimal 64 langkah alat
(bukan 16), riwayat 60 pesan (bukan 24), tiap panggilan model sampai 30 menit, dan waktu menunggu
komputer lowong/pemimpin baru sampai 10 menit (lihat config.CLUSTER_*). Permintaan yang antre tetap
dibatasi per node; yang penuh memakai komputer lain atau gagal dengan pesan yang bisa dibaca.
"""

import logging

from . import config, persona, think_pool, thinking
from .agentutil import clean_reply, parse_arguments, trim_history
from .think_pool import ClusterUnavailable
from .tools import FUNCTION_TOOLS, handle_tool, validate_input

log = logging.getLogger("itsme")

CLARIFY_FAIL = ("Maaf, aku belum bisa memahami maksudmu dengan baik. Bisa ditulis ulang dengan kalimat lain "
                "atau lebih rinci?")

__all__ = ["ClusterUnavailable", "run_agent", "guest_chat", "available"]


def available():
    return think_pool.ready()


def _options():
    opts = {"temperature": 0.3}
    if config.CLUSTER_MAX_TOKENS > 0:
        opts["num_predict"] = config.CLUSTER_MAX_TOKENS
    return opts


def _tool_calls(message):
    return [c for c in (message.get("tool_calls") or []) if isinstance(c, dict)]


def _answer_ok(card, request, text, node_id):
    """(ok, Verdict). Jawaban yang kosong/bocor/melenceng gagal; jawaban tanpa pemeriksaan dianggap lolos."""
    verdict = thinking.verify(card, request, text, exclude=(node_id,) if node_id else ())
    return verdict.ok, verdict


def _clean_note(messages, note):
    """Buang catatan 'jawab ulang' sementara dari riwayat (isinya hanya untuk satu giliran)."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i] is note:
            del messages[i]
            return


def run_agent(messages, ctx, raise_errors=False):
    """Loop agen di atas komputer-komputer yang berpikir. Mengubah `messages` di tempat dan mengembalikan
    jawaban akhir. Kalau tak ada komputer yang bisa berpikir: pesan siap baca (atau ClusterUnavailable
    kalau raise_errors=True, supaya brain.py bisa memakai otak cadangan)."""
    try:
        return _run(messages, ctx)
    except ClusterUnavailable as exc:
        if raise_errors:
            raise
        return f"⚠️ {exc}"


def _run(messages, ctx):
    system = persona.system_prompt(ctx["memory"].text(), "cari_web", "baca_halaman")
    request = thinking.last_user_text(messages)
    card = thinking.understand(messages)
    if card["jenis"] == "tidak_jelas" and card["pertanyaan_balik"]:
        # Lebih baik bertanya daripada menebak lalu melenceng.
        messages.append({"role": "assistant", "content": card["pertanyaan_balik"]})
        return card["pertanyaan_balik"]
    system += thinking.anchor(card)
    tools = FUNCTION_TOOLS if thinking.needs_tools(card) else None
    deep = thinking.think_mode(card)

    retried, note, banned = False, None, ()
    for _ in range(config.CLUSTER_MAX_STEPS):
        trim_history(messages, config.CLUSTER_HISTORY)
        reply = think_pool.ask([{"role": "system", "content": system}] + list(messages), role="answer",
                               think=True if deep else "auto", options=_options(), tools=tools, exclude=banned)
        message = reply.message
        content = clean_reply(message.get("content"))
        calls = _tool_calls(message)

        # Giliran asisten disimpan seperti diterima (termasuk panggilan alat), seperti otak lokal biasa.
        assistant = {"role": "assistant", "content": content}
        if message.get("thinking"):
            assistant["thinking"] = message["thinking"]
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)

        if not calls:
            ok, verdict = True, None
            if not retried and (thinking.local_check(card, content) or card["jenis"] != "obrolan"):
                ok, verdict = _answer_ok(card, request, content, reply.node_id)
            if not ok and not retried:
                # Jawaban melenceng: buang, lalu jawab ulang SEKALI di komputer lain, kali ini dengan alat.
                log.info("otak bersama: jawaban dari %s dinilai melenceng (%s); dijawab ulang", reply.node, verdict.reason)
                retried = True
                messages.pop()
                note = {"role": "user", "content": thinking.retry_note(card, verdict)}
                messages.append(note)
                if card["jenis"] != "obrolan":
                    tools = FUNCTION_TOOLS   # tadi mungkin salah kira "tanpa alat": kali ini alat ditawarkan
                banned = (reply.node_id,)
                continue
            if note is not None:
                _clean_note(messages, note)
            if not ok or thinking.local_check(card, content):
                # Sudah dicoba ulang dan tetap tidak tepat: lebih jujur minta diulang daripada mengirim lantur.
                if messages and messages[-1] is assistant:
                    messages.pop()
                messages.append({"role": "assistant", "content": CLARIFY_FAIL})
                return CLARIFY_FAIL
            return content or "(selesai)"

        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args, problem = parse_arguments(fn.get("arguments"))
            if problem is None:
                problem = validate_input(name, args)
            out = problem or handle_tool(name, args, ctx)
            result = {"role": "tool", "tool_name": name, "content": str(out)}
            if call.get("id"):
                result["tool_call_id"] = call["id"]
            messages.append(result)

    if note is not None:
        _clean_note(messages, note)
    return "Langkahnya terlalu banyak, aku berhenti dulu. Lanjut?"


def guest_chat(system_text, messages):
    """Jawaban tanya-jawab untuk TAMU (tanpa alat, tanpa memori pemilik; lihat brain_guest.py).
    Melempar ClusterUnavailable kalau tak ada komputer yang bisa berpikir."""
    request = thinking.last_user_text(messages)
    card = thinking.understand(messages)
    if card["jenis"] == "tidak_jelas" and card["pertanyaan_balik"]:
        return card["pertanyaan_balik"]
    system = system_text + thinking.anchor(card)
    banned, verdict = (), None
    for attempt in range(2):
        convo = list(messages)
        if attempt:
            convo.append({"role": "user", "content": thinking.retry_note(card, verdict)})
        reply = think_pool.ask([{"role": "system", "content": system}] + convo, role="answer",
                               think=thinking.think_mode(card) or "auto", options=_options(), exclude=banned)
        text = clean_reply(reply.message.get("content"))
        ok, verdict = True, None
        if thinking.local_check(card, text) or card["jenis"] != "obrolan":
            ok, verdict = _answer_ok(card, request, text, reply.node_id)
        if ok and not thinking.local_check(card, text):
            return text
        banned = (reply.node_id,)
    return CLARIFY_FAIL
