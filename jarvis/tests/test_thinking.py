"""Uji thinking.py: memahami -> menjawab -> memeriksa. Kumpulan komputer diganti tiruan (think_pool.ask);
yang diuji adalah keputusan pipa ini: kapan bertanya balik, kapan alat ditawarkan, apa yang dianggap
melenceng, dan bahwa kegagalan langkah bantu tidak pernah menahan jawaban."""

import json
import unittest
from unittest import mock

from itsme import config, think_pool, thinking


def reply(text, node="PC-B", node_id="b"):
    return think_pool.Reply(message={"role": "assistant", "content": text}, text=text, thinking="", node=node,
                            node_id=node_id, model="m", ms=10, tps=5.0)


def card_json(**over):
    card = {"tujuan": "Menanyakan ibu kota Jawa Barat", "jenis": "tanya", "butuh_alat": False, "bahasa": "id",
            "pertanyaan_balik": ""}
    card.update(over)
    return json.dumps(card)


class FakeAsk:
    """Pengganti think_pool.ask: mencatat panggilan dan menjawab sesuai antrean."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, messages, **kw):
        self.calls.append((messages, kw))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, think_pool.Reply) else reply(item)


def user(text):
    return {"role": "user", "content": text}


class TextHelpersTest(unittest.TestCase):
    def test_last_user_text_tanpa_cap_waktu(self):
        msgs = [user("[Senin, 05-01-2026 09.30] halo"), {"role": "assistant", "content": "hai"},
                user("[Selasa, 06-01-2026 10.00] apa kabar?"), {"role": "assistant", "content": "baik"}]
        self.assertEqual(thinking.last_user_text(msgs), "apa kabar?")
        self.assertEqual(thinking.last_user_text([]), "")
        self.assertEqual(thinking.last_user_text([{"role": "assistant", "content": "x"}]), "")
        self.assertEqual(thinking.last_user_text([user("[Senin] tidak dibuang karena bukan cap waktu")]),
                         "[Senin] tidak dibuang karena bukan cap waktu")

    def test_is_trivial(self):
        for text in ("halo", "Haloo!", "hai", "Hi", "pagi", "selamat malam", "makasih ya", "terima kasih banyak", "ok", "oke",
                     "siap", "iya", "tes", "p", "Assalamualaikum"):
            self.assertTrue(thinking.is_trivial(text), text)
        for text in ("apa ibu kota Jawa Barat?", "halo, tolong cari info cuaca", "", "buatkan ringkasan berkas ini",
                     "ok" * 30):
            self.assertFalse(thinking.is_trivial(text), text)

    def test_parse_json_object(self):
        self.assertEqual(thinking.parse_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(thinking.parse_json_object('Tentu! ```json\n{"a": 1}\n``` semoga membantu'), {"a": 1})
        for bad in ("", "tidak ada json", "{rusak", "[1, 2]", '{"a": 1'):
            self.assertIsNone(thinking.parse_json_object(bad), bad)

    def test_penalaran_bocor_dikenali(self):
        for text in ("Okay, so the user is asking about West Java", "Let me think about this", "Hmm, the capital",
                     "<think>x</think>", "The user wants a summary", "I need to figure out the capital"):
            self.assertTrue(thinking.looks_like_reasoning(text), text)
        for text in ("Ibu kota Jawa Barat adalah Bandung.", "Ok, siap!", "Okay", "Ya.", ""):
            self.assertFalse(thinking.looks_like_reasoning(text), text)


class UnderstandTest(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(config, "CLUSTER_UNDERSTAND", True), mock.patch.object(config, "CLUSTER_VERIFY", True),
                  mock.patch.object(config, "CLUSTER_THINK", "auto")):
            p.start()
            self.addCleanup(p.stop)

    def run_understand(self, messages, *items):
        fake = FakeAsk(*items)
        with mock.patch.object(think_pool, "ask", fake):
            return thinking.understand(messages), fake

    def test_kartu_lengkap(self):
        card, fake = self.run_understand([user("apa ibu kota Jawa Barat?")], card_json())
        self.assertEqual(card, {"tujuan": "Menanyakan ibu kota Jawa Barat", "jenis": "tanya", "butuh_alat": False,
                                "bahasa": "id", "pertanyaan_balik": "", "dipahami": True})
        _, kw = fake.calls[0]
        self.assertEqual((kw["role"], kw["think"], kw["fmt"]), ("fast", False, "json"))
        self.assertEqual(kw["options"]["temperature"], 0)

    def test_percakapan_dikirim_sebagai_teks_saja(self):
        msgs = [user("a"), {"role": "assistant", "content": "b", "tool_calls": [{"x": 1}]},
                {"role": "tool", "tool_name": "x", "content": "hasil"}, user("apa kabar hari ini?")]
        _, fake = self.run_understand(msgs, card_json())
        sent = fake.calls[0][0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual([m["role"] for m in sent[1:]], ["user", "assistant", "user"])
        self.assertNotIn("tool_calls", sent[2])

    def test_jenis_tak_dikenal_jadi_tanya_dan_bahasa_tidak_dipaksakan(self):
        card, _ = self.run_understand([user("tolong jelaskan pajak")], card_json(jenis="aneh", bahasa="INDONESIA!!"))
        self.assertEqual((card["jenis"], card["bahasa"]), ("tanya", "auto"))
        card, _ = self.run_understand([user("explain tax")], card_json(bahasa="EN"))
        self.assertEqual(card["bahasa"], "en")

    def test_butuh_alat_hanya_kalau_benar_true(self):
        card, _ = self.run_understand([user("cari berita hari ini")], card_json(jenis="perintah", butuh_alat="ya"))
        self.assertFalse(card["butuh_alat"])
        card, _ = self.run_understand([user("cari berita hari ini")], card_json(jenis="perintah", butuh_alat=True))
        self.assertTrue(card["butuh_alat"])

    def test_tidak_jelas_hanya_dipercaya_untuk_pesan_pendek_tanpa_konteks(self):
        card, _ = self.run_understand([user("yang itu")], card_json(jenis="tidak_jelas", pertanyaan_balik="Maksudnya yang mana?"))
        self.assertEqual((card["jenis"], card["pertanyaan_balik"]), ("tidak_jelas", "Maksudnya yang mana?"))
        long_msg = "tolong bandingkan harga laptop gaming untuk kuliah dan kerja kantor bulan ini"
        card, _ = self.run_understand([user(long_msg)], card_json(jenis="tidak_jelas", pertanyaan_balik="Maksudnya?"))
        self.assertEqual((card["jenis"], card["pertanyaan_balik"]), ("tanya", ""))   # pesan panjang tetap dicoba dijawab
        history = [user("a"), {"role": "assistant", "content": "b"}, user("c"), {"role": "assistant", "content": "d"},
                   user("yang itu")]
        card, _ = self.run_understand(history, card_json(jenis="tidak_jelas", pertanyaan_balik="Maksudnya?"))
        self.assertEqual(card["jenis"], "tanya")                                      # ada konteks percakapan
        card, _ = self.run_understand([user("yang itu")], card_json(jenis="tidak_jelas", pertanyaan_balik=""))
        self.assertEqual(card["jenis"], "tanya")                                      # tanpa pertanyaan balik tak bisa bertanya

    def test_gagal_memahami_tidak_pernah_melempar(self):
        for item in (think_pool.ClusterUnavailable("kosong"), "bukan json sama sekali", "[1,2]"):
            card, _ = self.run_understand([user("apa itu inflasi?")], item)
            self.assertFalse(card["dipahami"], item)
            self.assertEqual((card["jenis"], card["tujuan"]), ("tanya", "apa itu inflasi?"))

    def test_kegagalan_memahami_tidak_memaksa_bahasa_indonesia(self):
        card, _ = self.run_understand(
            [user("How does photosynthesis work?")], think_pool.ClusterUnavailable("offline"))
        self.assertEqual(card["bahasa"], "auto")
        prompt = thinking.anchor(card)
        self.assertNotIn("Bahasa jawaban: Indonesia", prompt)
        self.assertIn("bahasa yang sama dengan pesannya", prompt)
        retry = thinking.retry_note(card, None)
        self.assertIn("bahasa yang sama dengan permintaan", retry)
        self.assertNotIn("Bahasa auto", retry)

    def test_sapaan_ringan_tanpa_memanggil_model(self):
        card, fake = self.run_understand([user("halo")], )
        self.assertEqual(fake.calls, [])
        self.assertEqual((card["jenis"], card["dipahami"]), ("obrolan", True))

    def test_dimatikan_lewat_config(self):
        with mock.patch.object(config, "CLUSTER_UNDERSTAND", False):
            card, fake = self.run_understand([user("apa itu inflasi?")])
        self.assertEqual(fake.calls, [])
        self.assertFalse(card["dipahami"])

    def test_pesan_kosong(self):
        card, fake = self.run_understand([])
        self.assertEqual((card["tujuan"], card["dipahami"], fake.calls), ("", False, []))


class PolicyTest(unittest.TestCase):
    def card(self, **over):
        card = {"tujuan": "x", "jenis": "tanya", "butuh_alat": False, "bahasa": "id", "pertanyaan_balik": "",
                "dipahami": True}
        card.update(over)
        return card

    def test_alat_hanya_kalau_perlu(self):
        self.assertFalse(thinking.needs_tools(self.card()))
        self.assertFalse(thinking.needs_tools(self.card(jenis="obrolan")))
        self.assertTrue(thinking.needs_tools(self.card(butuh_alat=True)))
        self.assertTrue(thinking.needs_tools(self.card(jenis="perintah")))
        self.assertTrue(thinking.needs_tools(self.card(dipahami=False)))   # gagal paham: jangan memotong kemampuan

    def test_mode_berpikir(self):
        with mock.patch.object(config, "CLUSTER_THINK", "auto"):
            self.assertFalse(thinking.think_mode(self.card()))
            self.assertTrue(thinking.think_mode(self.card(jenis="perintah")))
            self.assertTrue(thinking.think_mode(self.card(butuh_alat=True)))
            self.assertFalse(thinking.think_mode(self.card(jenis="obrolan", butuh_alat=True)))
        with mock.patch.object(config, "CLUSTER_THINK", "deep"):
            self.assertTrue(thinking.think_mode(self.card()))
            self.assertFalse(thinking.think_mode(self.card(jenis="obrolan")))
        with mock.patch.object(config, "CLUSTER_THINK", "off"):
            self.assertFalse(thinking.think_mode(self.card(jenis="perintah")))

    def test_anchor_memuat_maksud_dan_aturan(self):
        text = thinking.anchor(self.card(tujuan="Menanyakan ibu kota", bahasa="id"))
        self.assertIn("Yang diminta: Menanyakan ibu kota", text)
        self.assertIn("Bahasa jawaban: Indonesia", text)
        self.assertIn("Jangan menulis proses berpikirmu", text)
        self.assertEqual(thinking.anchor(self.card(tujuan="")), "")

    def test_pesan_ulang(self):
        note = thinking.retry_note(self.card(tujuan="ibu kota", bahasa="en"), thinking.Verdict(False, "melantur", False))
        self.assertIn("melantur", note)
        self.assertIn('"ibu kota"', note)
        self.assertIn("Inggris", note)
        self.assertIn("tidak nyambung", thinking.retry_note(self.card(), None))


class LocalCheckTest(unittest.TestCase):
    card = {"bahasa": "id"}

    def test_lolos(self):
        for text in ("Ibu kota Jawa Barat adalah Bandung.", "Ok, siap!", "Bandung",
                     "Python adalah bahasa pemrograman yang dipakai untuk web, data, dan otomasi."):
            self.assertIsNone(thinking.local_check(self.card, text), text)

    def test_kosong(self):
        self.assertIn("kosong", thinking.local_check(self.card, "   "))

    def test_penalaran_bocor(self):
        self.assertIn("proses berpikir", thinking.local_check(self.card, "Okay, so the user is asking about the capital."))

    def test_bahasa_inggris_untuk_pesan_indonesia(self):
        english = "The capital of West Java is Bandung and it is known for the cool weather that you can enjoy there."
        self.assertIn("bahasa", thinking.local_check(self.card, english))
        self.assertIsNone(thinking.local_check({"bahasa": "en"}, english))     # pesannya memang Inggris
        self.assertIsNone(thinking.local_check(self.card, "OK"))               # terlalu pendek untuk dinilai
        mixed = "Ibu kota Jawa Barat adalah Bandung dan the capital itu terkenal untuk wisata di Indonesia."
        self.assertIsNone(thinking.local_check(self.card, mixed))              # campuran wajar tidak ditolak


class VerifyTest(unittest.TestCase):
    card = {"tujuan": "Menanyakan ibu kota", "jenis": "tanya", "butuh_alat": False, "bahasa": "id",
            "pertanyaan_balik": "", "dipahami": True}

    def setUp(self):
        for p in (mock.patch.object(config, "CLUSTER_VERIFY", True),):
            p.start()
            self.addCleanup(p.stop)

    def run_verify(self, answer, *items, card=None, exclude=("a",)):
        fake = FakeAsk(*items)
        with mock.patch.object(think_pool, "ask", fake):
            return thinking.verify(card or self.card, "apa ibu kota Jawa Barat?", answer, exclude=exclude), fake

    def test_pemeriksaan_lokal_menggagalkan_tanpa_memanggil_model(self):
        verdict, fake = self.run_verify("Okay, so the user wants the capital")
        self.assertEqual((verdict.ok, verdict.local), (False, True))
        self.assertEqual(fake.calls, [])

    def test_obrolan_tidak_diperiksa_model(self):
        verdict, fake = self.run_verify("Halo juga!", card=dict(self.card, jenis="obrolan"))
        self.assertTrue(verdict.ok)
        self.assertEqual(fake.calls, [])

    def test_dimatikan(self):
        with mock.patch.object(config, "CLUSTER_VERIFY", False):
            verdict, fake = self.run_verify("Bandung.")
        self.assertTrue(verdict.ok)
        self.assertEqual(fake.calls, [])

    def test_model_menilai_tidak_menjawab(self):
        verdict, fake = self.run_verify("Jawa Barat punya banyak gunung.", json.dumps({"menjawab": False, "alasan": "tidak menyebut kota"}))
        self.assertEqual((verdict.ok, verdict.reason, verdict.local), (False, "tidak menyebut kota", False))
        messages, kw = fake.calls[0]
        self.assertEqual((kw["role"], kw["fmt"], kw["exclude"]), ("fast", "json", ("a",)))   # komputer lain yang menilai
        self.assertIn("MAKSUD YANG SUDAH DIPAHAMI: Menanyakan ibu kota", messages[1]["content"])
        self.assertIn("Jawa Barat punya banyak gunung.", messages[1]["content"])

    def test_model_menilai_menjawab(self):
        verdict, _ = self.run_verify("Bandung.", json.dumps({"menjawab": True, "alasan": ""}))
        self.assertTrue(verdict.ok)

    def test_ragu_atau_tak_terbaca_dianggap_lolos(self):
        for item in ("bukan json", json.dumps({"alasan": "tanpa kunci menjawab"}), json.dumps({"menjawab": "ya"})):
            verdict, _ = self.run_verify("Bandung.", item)
            self.assertTrue(verdict.ok, item)

    def test_tak_ada_komputer_untuk_memeriksa_jawaban_tetap_dikirim(self):
        verdict, fake = self.run_verify("Bandung.", think_pool.ClusterUnavailable("a"), think_pool.ClusterUnavailable("b"))
        self.assertTrue(verdict.ok)
        self.assertEqual(len(fake.calls), 2)

    def test_komputer_lain_tak_ada_pakai_komputer_yang_sama(self):
        verdict, fake = self.run_verify("Jawa Barat punya gunung.", think_pool.ClusterUnavailable("tak ada lain"),
                                        json.dumps({"menjawab": False, "alasan": "melenceng"}))
        self.assertFalse(verdict.ok)
        self.assertEqual([kw["exclude"] for _, kw in fake.calls], [("a",), ()])


if __name__ == "__main__":
    unittest.main()
