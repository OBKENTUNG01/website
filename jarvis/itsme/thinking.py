"""Memahami -> menjawab -> memeriksa: cara otak lokal bersama menjaga jawaban tetap NYAMBUNG.

Model lokal kecil gampang melenceng: menjawab hal lain, menulis penalarannya (bahasa Inggris) sebagai
jawaban, atau memanggil alat yang tak perlu. Tiga langkah ini dikerjakan komputer yang berbeda kalau ada
(think_pool memilih yang lowong):

 1. MEMAHAMI  - model cepat meringkas maksud pesan terakhir menjadi "kartu maksud" (tujuan, jenis, perlu
                alat atau tidak, bahasa). Pesan yang benar-benar tidak jelas dijawab dengan pertanyaan balik,
                bukan tebakan yang melenceng.
 2. MENJAWAB  - model terbaik menjawab dengan kartu itu terpasang di system prompt. Alat HANYA ditawarkan
                kalau memang perlu (model kecil yang disodori 20 alat untuk "halo" mulai bertindak aneh).
 3. MEMERIKSA - pemeriksaan murah tanpa model (kosong? penalaran bocor? bahasa beda?), lalu komputer lain
                menilai "apakah ini menjawab maksudnya?". Kalau tidak, dijawab ulang SEKALI di komputer lain
                (dengan alat bila tadi tanpa alat). Tidak pernah berputar lebih dari itu.
"""

import json
import re
from collections import namedtuple

from . import config, think_pool
from .agentutil import text_only

Verdict = namedtuple("Verdict", "ok reason local")

KINDS = ("obrolan", "tanya", "perintah", "tidak_jelas")
LANG_NAMES = {"id": "Indonesia", "en": "Inggris", "ms": "Melayu", "jv": "Jawa", "su": "Sunda"}

UNDERSTAND_PROMPT = """Kamu penganalisis pesan. Tugasmu HANYA memahami maksud PESAN TERAKHIR pengguna dalam \
percakapan di bawah — BUKAN menjawabnya.
Balas dengan SATU objek JSON saja, tanpa teks lain, berisi kunci:
- "tujuan": satu kalimat bahasa Indonesia tentang apa yang sebenarnya diminta pengguna.
- "jenis": "obrolan" (sapaan atau ngobrol santai), "tanya" (minta informasi atau penjelasan), \
"perintah" (minta sesuatu dikerjakan: cari, buka, baca, buat, kirim, ubah, jalankan), atau "tidak_jelas" \
(benar-benar tak bisa ditebak dari percakapan).
- "butuh_alat": true kalau menjawabnya perlu melakukan sesuatu di komputer atau internet (cari web, \
baca/tulis berkas, jalankan perintah); selain itu false.
- "bahasa": kode bahasa pesan, mis. "id" atau "en".
- "pertanyaan_balik": kalau jenis "tidak_jelas", satu pertanyaan singkat untuk memperjelas; selain itu "".
Jangan menambah hal yang tidak diminta."""

VERIFY_PROMPT = """Kamu pemeriksa jawaban. Kamu diberi PERMINTAAN pengguna, MAKSUD yang sudah dipahami, dan \
JAWABAN asisten.
Nilai: apakah JAWABAN langsung menjawab MAKSUD itu, dalam bahasa yang sama dengan permintaan, tanpa \
membahas hal lain yang tidak diminta, dan tanpa menulis proses berpikir?
Balas SATU objek JSON saja: {"menjawab": true atau false, "alasan": "satu kalimat"}.
Kalau ragu, "menjawab": true."""

RULES = """ATURAN JAWABAN:
- Jawab LANGSUNG hal yang diminta di atas, dalam bahasa yang sama dengan pesannya. Jangan membahas hal lain.
- Jangan menulis proses berpikirmu, jangan mengulang pertanyaan, jangan membuka dengan "Okay" atau "Baik, saya akan".
- Kalau kurang yakin, katakan terus terang apa yang belum kamu tahu — jangan mengarang dan jangan melantur."""

# Cap waktu yang dipasang bot di depan pesan pemilik (main._stamped): "[Senin, 05-01-2026 09.30] ".
_STAMP = re.compile(r"^\s*\[[A-Za-z]+, \d{2}-\d{2}-\d{4} \d{2}[.:]\d{2}\]\s*")
_TRIVIAL = re.compile(
    r"^\s*(h+a+l+o+|h[ae]+i+|hi+|hello|hey|p+|pagi|siang|sore|malam|selamat\s+(pagi|siang|sore|malam)|"
    r"assalamu\W*alaikum|(terima\s+kasih|makasih|thanks?|thx)(\s+\w+){0,2}|ok(e|ay|ey)?|siap|baik|ya|iya|y|"
    r"tidak|nggak|gak|sip|mantap|test|tes)\s*[!.?,]*\s*$", re.IGNORECASE)
# Awal teks yang menandakan model menulis proses berpikirnya (Inggris) sebagai jawaban; sengaja sempit
# ("Ok, siap" atau "Okay" saja adalah jawaban Indonesia yang wajar). Sama dengan itsme-node llm.py.
_REASONING = re.compile(
    r"^\s*(<think>"
    r"|okay,? (so|let|the user)\b|alright,? (so|let)\b"
    r"|let me (think|see|start|figure|check|recall)\b|let's (see|think|start|break)\b"
    r"|hmm[,.…]|so,? the user\b|the user (is|wants|asks|asked)\b"
    r"|first,? i (need|should|'ll)\b|i need to (figure|find|determine|confirm|check)\b|wait,)",
    re.IGNORECASE)
_EN = frozenset("the and is are of to that this with for in it you was be as on at by an have has not but or from "
                "which what can will would my your they their there been were".split())
_ID = frozenset("yang dan di ini itu untuk dengan adalah dari pada ke akan juga tidak saya kamu anda aku bisa atau "
                "karena sudah ada dalam bukan lebih sebuah para oleh".split())


# --- bantuan teks -----------------------------------------------------------------------------
def last_user_text(messages):
    """Isi pesan pengguna yang terakhir, tanpa cap waktu '[Senin, 05-01-2026 09.30]' di depannya."""
    for m in reversed(messages):
        content = m.get("content")
        if m.get("role") == "user" and isinstance(content, str) and content.strip():
            return _STAMP.sub("", content, count=1).strip()
    return ""


def is_trivial(text):
    text = (text or "").strip()
    return len(text) <= 40 and bool(_TRIVIAL.match(text))


def looks_like_reasoning(text):
    text = (text or "").strip()
    return bool(_REASONING.match(text)) or "<think>" in text.lower()


def parse_json_object(text):
    """Objek JSON pertama dalam teks (model kecil suka membungkusnya dengan ```json atau kalimat pembuka)."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


# --- 1. memahami ------------------------------------------------------------------------------
def default_card(request, understood=False):
    return {"tujuan": request.strip()[:300], "jenis": "obrolan" if is_trivial(request) else "tanya",
            "butuh_alat": False, "bahasa": "auto", "pertanyaan_balik": "", "dipahami": understood}


def _coerce(obj, request, history_len):
    card = default_card(request, understood=True)
    tujuan = obj.get("tujuan")
    if isinstance(tujuan, str) and tujuan.strip():
        card["tujuan"] = tujuan.strip()[:300]
    jenis = str(obj.get("jenis") or "").strip().lower()
    card["jenis"] = jenis if jenis in KINDS else "tanya"
    card["butuh_alat"] = obj.get("butuh_alat") is True
    lang = str(obj.get("bahasa") or "").strip().lower()[:5]
    card["bahasa"] = lang if re.fullmatch(r"[a-z]{2,3}(-[a-z]{2})?", lang) else "auto"
    back = obj.get("pertanyaan_balik")
    card["pertanyaan_balik"] = back.strip()[:300] if isinstance(back, str) else ""
    if card["jenis"] == "tidak_jelas":
        # Model kecil terlalu gampang menyerah dengan "tidak jelas": hanya dipercaya untuk pesan yang pendek
        # dan nyaris tanpa konteks percakapan; selebihnya tetap dicoba dijawab.
        short = len(request.split()) <= 6 and history_len <= 2
        if not (short and card["pertanyaan_balik"]):
            card["jenis"] = "tanya"
            card["pertanyaan_balik"] = ""
    return card


def understand(messages):
    """Kartu maksud pesan terakhir. Tidak pernah melempar: gagal memahami = kartu bawaan (tanya, perlu alat
    belum diketahui), supaya langkah menjawab yang menentukan."""
    request = last_user_text(messages)
    if not request:
        return default_card("", understood=False)
    if not config.CLUSTER_UNDERSTAND or is_trivial(request):
        return default_card(request, understood=is_trivial(request))
    history = text_only(messages)[-6:]
    try:
        reply = think_pool.ask([{"role": "system", "content": UNDERSTAND_PROMPT}] + history, role="fast",
                               think=False, fmt="json", options={"temperature": 0, "num_predict": 400}, wait=30)
    except think_pool.ClusterUnavailable:
        return default_card(request, understood=False)
    obj = parse_json_object(reply.text)
    if not obj:
        return default_card(request, understood=False)
    return _coerce(obj, request, len(history))


def needs_tools(card):
    """Tawarkan alat? Ya kalau maksudnya butuh alat/perintah, atau kalau tadi gagal dipahami (jangan ambil risiko
    memotong kemampuan)."""
    return card["butuh_alat"] or card["jenis"] == "perintah" or not card["dipahami"]


def think_mode(card):
    """Mode berpikir yang diminta untuk menjawab: True/False (lihat config.CLUSTER_THINK)."""
    if config.CLUSTER_THINK == "off" or card["jenis"] == "obrolan":
        return False
    if config.CLUSTER_THINK == "deep":
        return True
    return card["jenis"] == "perintah" or card["butuh_alat"]


def anchor(card):
    """Tambahan system prompt: maksud pesan terakhir yang sudah dipahami + aturan agar tidak melenceng."""
    if not card.get("tujuan"):
        return ""
    lang = LANG_NAMES.get(card["bahasa"])
    language_rule = f"- Bahasa jawaban: {lang}\n" if lang else ""
    return ("\n\nMAKSUD PESAN TERAKHIR (sudah dipahami, patuhi):\n"
            f"- Yang diminta: {card['tujuan']}\n- Jenis: {card['jenis']}\n{language_rule}{RULES}\n")


# --- 3. memeriksa -----------------------------------------------------------------------------
def _english_heavy(text):
    words = re.findall(r"[a-zA-Z']+", text.lower())
    en, idn = sum(w in _EN for w in words), sum(w in _ID for w in words)
    return en >= 4 and en > 2 * idn


def local_check(card, answer):
    """Pemeriksaan murah tanpa model. Kembalikan alasan gagal, atau None kalau lolos."""
    text = (answer or "").strip()
    if not text:
        return "jawabannya kosong"
    if looks_like_reasoning(text):
        return "isinya proses berpikir (bahasa Inggris), bukan jawaban"
    if str(card.get("bahasa", "id")).startswith("id") and len(text) > 60 and _english_heavy(text):
        return "bahasa jawabannya bukan bahasa Indonesia seperti pesannya"
    return None


def verify(card, request, answer, exclude=()):
    """Apakah jawaban ini menjawab maksud pesan? Verdict(ok, alasan, local)."""
    problem = local_check(card, answer)
    if problem:
        return Verdict(False, problem, True)
    if not config.CLUSTER_VERIFY or card["jenis"] == "obrolan":
        return Verdict(True, "", False)
    user = (f"PERMINTAAN:\n{request[:1500]}\n\nMAKSUD YANG SUDAH DIPAHAMI: {card['tujuan']}\n\n"
            f"JAWABAN:\n{answer[:3000]}")
    msgs = [{"role": "system", "content": VERIFY_PROMPT}, {"role": "user", "content": user}]
    opts = {"temperature": 0, "num_predict": 200}
    reply = None
    for banned in (tuple(exclude), ()):   # pendapat kedua dari komputer lain; kalau cuma satu, dari komputer yang sama
        try:
            reply = think_pool.ask(msgs, role="fast", think=False, fmt="json", options=opts, exclude=banned, wait=30)
            break
        except think_pool.ClusterUnavailable:
            continue
    if reply is None:
        return Verdict(True, "", False)   # tak bisa memeriksa: jangan menahan jawaban
    obj = parse_json_object(reply.text)
    if not obj or not isinstance(obj.get("menjawab"), bool):
        return Verdict(True, "", False)
    return Verdict(obj["menjawab"], str(obj.get("alasan") or "")[:200], False)


def retry_note(card, verdict):
    lang = LANG_NAMES.get(card["bahasa"])
    why = (verdict.reason if verdict else "") or "tidak nyambung dengan maksud pesan"
    language_rule = f"Bahasa {lang}." if lang else "Gunakan bahasa yang sama dengan permintaan."
    return (f"Jawabanmu tadi dinilai belum tepat ({why}). Jawab ulang SEKARANG, langsung ke maksud: "
            f"\"{card['tujuan']}\". {language_rule} Tanpa menulis proses berpikir.")
