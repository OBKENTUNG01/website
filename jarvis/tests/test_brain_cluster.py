"""Uji brain_cluster.py + jalur otak "kumpulan" di brain.py: loop agen di atas komputer-komputer yang berpikir.
Kumpulan diganti tiruan (think_pool.ask): ruang lingkupnya adalah bagaimana jawaban dijaga tetap nyambung."""

import json
import unittest
from unittest import mock

from itsme import brain, brain_cluster, config, node, think_pool, thinking
from itsme.tools import FUNCTION_TOOLS

CARD = {"tujuan": "Menanyakan ibu kota Jawa Barat", "jenis": "tanya", "butuh_alat": False, "bahasa": "id",
        "pertanyaan_balik": ""}


def reply(content="", node_name="PC-A", node_id="a", **extra):
    message = {"role": "assistant", "content": content}
    message.update(extra)
    return think_pool.Reply(message=message, text=content, thinking=message.get("thinking", ""), node=node_name,
                            node_id=node_id, model="m", ms=10, tps=5.0)


class FakePool:
    """Pengganti think_pool.ask. Langkah cepat (memahami/memeriksa) dijawab dari `card`/`verdicts`; giliran jawaban
    dari antrean `answers` (Reply, atau Exception)."""

    def __init__(self, card=None, answers=(), verdicts=()):
        self.card = CARD if card is None else card
        self.answers = list(answers)
        self.verdicts = list(verdicts)
        self.calls = []

    def __call__(self, messages, **kw):
        self.calls.append((messages, kw))
        if kw["role"] == "fast":
            head = messages[0]["content"]
            if head.startswith("Kamu penganalisis"):
                return reply(json.dumps(self.card))
            verdict = self.verdicts.pop(0) if self.verdicts else {"menjawab": True, "alasan": ""}
            return reply(json.dumps(verdict))
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def answer_calls(self):
        return [(m, kw) for m, kw in self.calls if kw["role"] == "answer"]

    def fast_calls(self):
        return [(m, kw) for m, kw in self.calls if kw["role"] == "fast"]


class Memory:
    def text(self):
        return "Pemilik suka kopi."


def ctx():
    return {"memory": Memory(), "chat_id": 7}


def user(text):
    return {"role": "user", "content": text}


class Base(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(config, "CLUSTER_UNDERSTAND", True), mock.patch.object(config, "CLUSTER_VERIFY", True),
                  mock.patch.object(config, "CLUSTER_THINK", "auto"), mock.patch.object(config, "CLUSTER_MAX_STEPS", 6),
                  mock.patch.object(config, "CLUSTER_HISTORY", 60), mock.patch.object(config, "CLUSTER_MAX_TOKENS", 4096)):
            p.start()
            self.addCleanup(p.stop)

    def run_agent(self, pool, messages, **kw):
        with mock.patch.object(think_pool, "ask", pool):
            return brain_cluster.run_agent(messages, ctx(), **kw)


class PlainAnswerTest(Base):
    def test_jawaban_lurus(self):
        pool = FakePool(answers=[reply("Ibu kota Jawa Barat adalah Bandung.")])
        msgs = [user("[Senin, 05-01-2026 09.30] apa ibu kota Jawa Barat?")]
        out = self.run_agent(pool, msgs)
        self.assertEqual(out, "Ibu kota Jawa Barat adalah Bandung.")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])    # riwayat bersih
        [(sent, kw)] = pool.answer_calls()
        system = sent[0]["content"]
        self.assertIn("Pemilik suka kopi.", system)                           # persona + memori pemilik
        self.assertIn("MAKSUD PESAN TERAKHIR", system)                        # kartu maksud terpasang
        self.assertIn("Yang diminta: Menanyakan ibu kota Jawa Barat", system)
        self.assertIsNone(kw["tools"])                                         # pertanyaan biasa: tanpa alat
        self.assertEqual(kw["think"], "auto")
        self.assertEqual(kw["options"], {"temperature": 0.3, "num_predict": 4096})
        self.assertEqual(len(pool.fast_calls()), 2)                           # memahami + memeriksa

    def test_sapaan_ringan_satu_panggilan_saja(self):
        pool = FakePool(answers=[reply("Halo juga! Ada yang bisa kubantu?")])
        out = self.run_agent(pool, [user("halo")])
        self.assertEqual(out, "Halo juga! Ada yang bisa kubantu?")
        self.assertEqual(len(pool.calls), 1)
        self.assertIsNone(pool.calls[0][1]["tools"])

    def test_pagar_token_bisa_dimatikan(self):
        pool = FakePool(answers=[reply("Bandung.")])
        with mock.patch.object(config, "CLUSTER_MAX_TOKENS", 0):
            self.run_agent(pool, [user("apa ibu kota Jawa Barat?")])
        self.assertEqual(pool.answer_calls()[0][1]["options"], {"temperature": 0.3})

    def test_riwayat_dipangkas_sesuai_cluster_history(self):
        msgs = []
        for i in range(6):
            msgs += [user(f"pesan {i}"), {"role": "assistant", "content": f"jawab {i}"}]
        msgs.append(user("halo"))
        pool = FakePool(answers=[reply("Halo!")])
        with mock.patch.object(config, "CLUSTER_HISTORY", 4):
            self.run_agent(pool, msgs)
        sent = pool.answer_calls()[0][0]
        self.assertLessEqual(len(sent) - 1, 4)
        self.assertEqual(sent[-1]["content"], "halo")

    def test_mode_berpikir_dalam_untuk_perintah(self):
        pool = FakePool(card=dict(CARD, jenis="perintah", butuh_alat=True), answers=[reply("Sudah.")])
        self.run_agent(pool, [user("cari harga laptop gaming terbaru")])
        [(_, kw)] = pool.answer_calls()
        self.assertIs(kw["think"], True)
        self.assertEqual(kw["tools"], FUNCTION_TOOLS)


class ClarifyTest(Base):
    def test_pesan_tidak_jelas_dijawab_pertanyaan_balik(self):
        pool = FakePool(card=dict(CARD, jenis="tidak_jelas", pertanyaan_balik="Maksudnya yang mana?"))
        msgs = [user("yang itu")]
        out = self.run_agent(pool, msgs)
        self.assertEqual(out, "Maksudnya yang mana?")
        self.assertEqual(pool.answer_calls(), [])                              # tidak menebak lalu melenceng
        self.assertEqual(msgs[-1], {"role": "assistant", "content": "Maksudnya yang mana?"})


class ToolLoopTest(Base):
    def test_alat_dijalankan_lalu_jawaban_akhir(self):
        call = {"function": {"name": "cari_web", "arguments": {"kata_kunci": "cuaca Samarinda"}}, "id": "c1"}
        pool = FakePool(card=dict(CARD, jenis="perintah", butuh_alat=True, tujuan="Mencari cuaca Samarinda"),
                        answers=[reply("", tool_calls=[call]), reply("Cuaca Samarinda hari ini cerah, 32 derajat.")])
        msgs = [user("cek cuaca Samarinda")]
        with mock.patch.object(brain_cluster, "handle_tool", return_value="cerah 32C") as tool:
            out = self.run_agent(pool, msgs)
        tool.assert_called_once()
        self.assertEqual(tool.call_args[0][:2], ("cari_web", {"kata_kunci": "cuaca Samarinda"}))
        self.assertEqual(out, "Cuaca Samarinda hari ini cerah, 32 derajat.")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(msgs[2], {"role": "tool", "tool_name": "cari_web", "content": "cerah 32C", "tool_call_id": "c1"})
        self.assertEqual(len(pool.answer_calls()), 2)

    def test_argumen_alat_rusak_dilaporkan_ke_model(self):
        call = {"function": {"name": "cari_web", "arguments": "{bukan json"}}
        pool = FakePool(card=dict(CARD, jenis="perintah", butuh_alat=True), answers=[reply("", tool_calls=[call]), reply("Maaf, ulang.")])
        msgs = [user("cari sesuatu")]
        with mock.patch.object(brain_cluster, "handle_tool") as tool:
            self.run_agent(pool, msgs)
        tool.assert_not_called()
        self.assertEqual((msgs[2]["role"], msgs[2]["content"]), ("tool", "Argumen alat bukan JSON yang valid."))

    def test_langkah_alat_tak_berujung_dihentikan(self):
        call = {"function": {"name": "cari_web", "arguments": {"kata_kunci": "x"}}}
        pool = FakePool(card=dict(CARD, jenis="perintah", butuh_alat=True), answers=[reply("", tool_calls=[call])] * 10)
        with mock.patch.object(brain_cluster, "handle_tool", return_value="ok"):
            out = self.run_agent(pool, [user("cari terus")])
        self.assertIn("Langkahnya terlalu banyak", out)
        self.assertEqual(len(pool.answer_calls()), 6)                         # CLUSTER_MAX_STEPS


class OffTopicTest(Base):
    def test_penalaran_bocor_dijawab_ulang_di_komputer_lain(self):
        pool = FakePool(answers=[reply("Okay, so the user is asking about the capital of West Java. Let me think...",
                                       node_name="PC-A", node_id="a"),
                                 reply("Ibu kota Jawa Barat adalah Bandung.", node_name="PC-B", node_id="b")])
        msgs = [user("apa ibu kota Jawa Barat?")]
        out = self.run_agent(pool, msgs)
        self.assertEqual(out, "Ibu kota Jawa Barat adalah Bandung.")
        first, second = pool.answer_calls()
        self.assertEqual(first[1]["exclude"], ())
        self.assertEqual(second[1]["exclude"], ("a",))                        # komputer yang melenceng dilewati
        self.assertIsNone(first[1]["tools"])
        self.assertEqual(second[1]["tools"], FUNCTION_TOOLS)                  # tadi mungkin salah kira "tanpa alat"
        self.assertIn("Jawab ulang SEKARANG", second[0][-1]["content"])      # catatan sementara terlihat model
        # Riwayat akhir bersih: tidak ada jawaban yang melenceng, tidak ada catatan sementara.
        self.assertEqual(msgs, [user("apa ibu kota Jawa Barat?"),
                                {"role": "assistant", "content": "Ibu kota Jawa Barat adalah Bandung."}])

    def test_model_pemeriksa_menilai_melenceng_lalu_dijawab_ulang(self):
        pool = FakePool(answers=[reply("Jawa Barat punya banyak gunung dan teh.", node_id="a"),
                                 reply("Bandung.", node_id="b")],
                        verdicts=[{"menjawab": False, "alasan": "tidak menyebut kota"}])
        out = self.run_agent(pool, [user("apa ibu kota Jawa Barat?")])
        self.assertEqual(out, "Bandung.")
        self.assertEqual(len(pool.answer_calls()), 2)
        self.assertIn("tidak menyebut kota", pool.answer_calls()[1][0][-1]["content"])

    def test_obrolan_tidak_diperiksa_model_tapi_bocor_tetap_ditangkap(self):
        pool = FakePool(card=dict(CARD, jenis="obrolan"),
                        answers=[reply("Hmm, the user said hello. Let me think.", node_id="a"), reply("Halo juga!", node_id="b")])
        out = self.run_agent(pool, [user("selamat datang di grup kami semua")])
        self.assertEqual(out, "Halo juga!")
        self.assertEqual([c for _, c in pool.fast_calls() if c["fmt"] == "json"][1:], [])   # tak ada panggilan memeriksa
        self.assertIsNone(pool.answer_calls()[1][1]["tools"])                # obrolan tidak pernah diberi alat

    def test_tetap_melenceng_setelah_dicoba_ulang_minta_diulang(self):
        pool = FakePool(answers=[reply("Okay, so the user wants the capital.", node_id="a"),
                                 reply("Let me think about West Java first.", node_id="b")])
        msgs = [user("apa ibu kota Jawa Barat?")]
        out = self.run_agent(pool, msgs)
        self.assertEqual(out, brain_cluster.CLARIFY_FAIL)
        self.assertEqual(msgs, [user("apa ibu kota Jawa Barat?"), {"role": "assistant", "content": brain_cluster.CLARIFY_FAIL}])
        self.assertEqual(len(pool.answer_calls()), 2)                        # tepat SATU percobaan ulang, tidak berputar

    def test_jawaban_ulang_yang_baik_tidak_diperiksa_lagi(self):
        pool = FakePool(answers=[reply("Okay, so the user wants the capital.", node_id="a"),
                                 reply("Bandung.", node_id="b")],
                        verdicts=[])
        self.run_agent(pool, [user("apa ibu kota Jawa Barat?")])
        verifies = [m for m, kw in pool.fast_calls() if m[0]["content"].startswith("Kamu pemeriksa")]
        self.assertEqual(verifies, [])      # jawaban pertama gugur lewat pemeriksaan lokal; yang kedua tidak dinilai ulang


class UnavailableTest(Base):
    def test_tak_ada_komputer_pesan_siap_baca(self):
        pool = FakePool(answers=[think_pool.ClusterUnavailable("Belum ada komputer yang siap berpikir.")])
        out = self.run_agent(pool, [user("halo")])
        self.assertEqual(out, "⚠️ Belum ada komputer yang siap berpikir.")

    def test_raise_errors_untuk_otak_cadangan(self):
        pool = FakePool(answers=[think_pool.ClusterUnavailable("x")])
        with self.assertRaises(brain_cluster.ClusterUnavailable):
            self.run_agent(pool, [user("halo")], raise_errors=True)


class GuestChatTest(Base):
    def run_guest(self, pool, messages):
        with mock.patch.object(think_pool, "ask", pool):
            return brain_cluster.guest_chat("Kamu asisten tamu.", messages)

    def test_jawaban_tamu_tanpa_alat(self):
        pool = FakePool(answers=[reply("Ibu kota Jawa Barat adalah Bandung.")])
        out = self.run_guest(pool, [user("apa ibu kota Jawa Barat?")])
        self.assertEqual(out, "Ibu kota Jawa Barat adalah Bandung.")
        [(sent, kw)] = pool.answer_calls()
        self.assertTrue(sent[0]["content"].startswith("Kamu asisten tamu."))
        self.assertIn("MAKSUD PESAN TERAKHIR", sent[0]["content"])
        self.assertNotIn("tools", kw)                                          # tamu tidak pernah diberi alat

    def test_tamu_pertanyaan_balik(self):
        pool = FakePool(card=dict(CARD, jenis="tidak_jelas", pertanyaan_balik="Maksud kakak yang mana?"))
        self.assertEqual(self.run_guest(pool, [user("yang itu")]), "Maksud kakak yang mana?")

    def test_tamu_bocor_dijawab_ulang_lalu_minta_diulang(self):
        pool = FakePool(answers=[reply("Okay, so the user asks.", node_id="a"), reply("Bandung.", node_id="b")])
        self.assertEqual(self.run_guest(pool, [user("apa ibu kota Jawa Barat?")]), "Bandung.")
        self.assertEqual(pool.answer_calls()[1][1]["exclude"], ("a",))
        pool = FakePool(answers=[reply("Okay, so the user asks.", node_id="a"), reply("Let me think again.", node_id="b")])
        self.assertEqual(self.run_guest(pool, [user("apa ibu kota Jawa Barat?")]), brain_cluster.CLARIFY_FAIL)

    def test_tamu_tak_ada_komputer(self):
        pool = FakePool(answers=[think_pool.ClusterUnavailable("x")])
        with self.assertRaises(brain_cluster.ClusterUnavailable):
            self.run_guest(pool, [user("halo")])

    def test_riwayat_tamu_tidak_diubah(self):
        pool = FakePool(answers=[reply("Bandung.")])
        msgs = [user("apa ibu kota Jawa Barat?")]
        self.run_guest(pool, msgs)
        self.assertEqual(msgs, [user("apa ibu kota Jawa Barat?")])


class BrainRoutingTest(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.dict(brain._state, {"backend": "kumpulan"}), mock.patch.object(config, "FALLBACK_LOCAL", True)):
            p.start()
            self.addCleanup(p.stop)

    def test_kumpulan_dikenal_dan_butuh_node_terpasang(self):
        self.assertIn("kumpulan", brain.BACKENDS)
        with mock.patch.object(node, "installed", return_value=False):
            with self.assertRaises(ValueError) as cm:
                brain.set_backend("kumpulan", remember=False)
        self.assertIn("belum terpasang", str(cm.exception))
        with mock.patch.object(node, "installed", return_value=True):
            brain.set_backend("kumpulan", remember=False)
        self.assertEqual(brain.current_backend(), "kumpulan")

    def test_pilihan_tidak_hilang_hanya_karena_node_belum_menjawab(self):
        """Setelah menyala ulang, pilihan otak dipulihkan saat node masih menyala: yang diperiksa HANYA terpasang-nya."""
        with mock.patch.object(node, "installed", return_value=True), mock.patch.object(node, "status", return_value=None):
            brain.set_backend("kumpulan", remember=False)
        self.assertEqual(brain.current_backend(), "kumpulan")

    def test_describe(self):
        with mock.patch.object(think_pool, "describe", return_value="lokal bersama: 2 komputer berpikir (A x, B y)"):
            self.assertIn("lokal bersama", brain.describe("kumpulan"))

    def test_run_agent_memakai_otak_bersama(self):
        with mock.patch("itsme.brain_cluster.run_agent", return_value="dari kumpulan") as run:
            self.assertEqual(brain.run_agent([user("halo")], ctx()), "dari kumpulan")
        self.assertTrue(run.call_args.kwargs["raise_errors"])

    def test_jatuh_ke_ollama_di_komputer_ini_saat_tak_ada_yang_berpikir(self):
        err = brain_cluster.ClusterUnavailable("Belum ada komputer yang siap.")
        msgs = [user("halo")]
        with mock.patch("itsme.brain_cluster.run_agent", side_effect=err), \
                mock.patch("itsme.brain_ollama.run_agent", return_value="jawaban lokal") as local:
            out = brain.run_agent(msgs, ctx())
        local.assert_called_once()
        self.assertIn("Belum ada komputer yang siap.", out)
        self.assertIn("jawaban lokal", out)
        self.assertEqual(msgs[-1], {"role": "assistant", "content": "jawaban lokal"})

    def test_tanpa_cadangan_hanya_pesan(self):
        err = brain_cluster.ClusterUnavailable("Belum ada komputer yang siap.")
        with mock.patch("itsme.brain_cluster.run_agent", side_effect=err), mock.patch.object(config, "FALLBACK_LOCAL", False):
            self.assertEqual(brain.run_agent([user("halo")], ctx()), "⚠️ Belum ada komputer yang siap.")

    def test_cadangan_juga_gagal(self):
        from itsme.brain_ollama import LocalUnavailable

        err = brain_cluster.ClusterUnavailable("Belum ada komputer yang siap.")
        with mock.patch("itsme.brain_cluster.run_agent", side_effect=err), \
                mock.patch("itsme.brain_ollama.run_agent", side_effect=LocalUnavailable("Ollama mati")):
            out = brain.run_agent([user("halo")], ctx())
        self.assertIn("Otak lokal (cadangan) juga belum bisa menjawab: Ollama mati", out)

    def test_tamu_tidak_pernah_lewat_otak_pilihan_pemilik(self):
        with mock.patch("itsme.brain_guest.reply", return_value="jawaban tamu") as guest, \
                mock.patch("itsme.brain_cluster.run_agent") as run:
            self.assertEqual(brain.run_agent([user("halo")], dict(ctx(), guest=True)), "jawaban tamu")
        guest.assert_called_once()
        run.assert_not_called()


class StampTest(unittest.TestCase):
    def test_cap_waktu_asli_dikenali(self):
        from itsme import main

        stamped = main._stamped("apa kabar?")
        self.assertEqual(thinking.last_user_text([user(stamped)]), "apa kabar?")


if __name__ == "__main__":
    unittest.main()
