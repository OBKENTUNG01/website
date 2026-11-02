"""Perintah Telegram otak lokal bersama (/bersama, /bersama_status, /otak) dan urutan otak untuk TAMU.

Tanpa jaringan: kumpulan komputer diganti tiruan. Yang diuji adalah apa yang DIBACA pemilik dan
siapa yang menjawab tamu (dan bahwa tamu tidak pernah menyita komputer lebih lama dari anggarannya)."""

import asyncio
import logging
import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import brain, brain_cluster, brain_deepseek, brain_guest, brain_openrouter, config, keypool, main, node, think_pool
from itsme.brain_openrouter import CloudUnavailable
from test_main import FakeTelegram

STATUS_LINES = ["2 dari 3 komputer siap berpikir.", "✅ PC-A ⭐ pemimpin — qwen3:4b · 7.1 tok/dtk · lowong",
                "⏸ PC-C — belum punya model (RAM kecil atau Ollama belum terpasang)"]


class CommandBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        keypool.reset_all()
        self.addCleanup(keypool.reset_all)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "FOLLOW_NODE", False),
            mock.patch.object(config, "WHATSAPP", False),
            mock.patch.object(config, "DEEPSEEK_API_KEY", None),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", []),
            mock.patch.object(config, "OPENROUTER_API_KEY", None),
            mock.patch.object(config, "GUEST_CLUSTER", True),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
            mock.patch.object(main, "Telegram", FakeTelegram),
            mock.patch.object(main, "run_agent", side_effect=AssertionError("perintah sampai ke otak AI")),
            mock.patch.object(node, "installed", return_value=True),
            mock.patch.object(think_pool, "report", return_value=list(STATUS_LINES)),
            mock.patch.object(think_pool, "describe", return_value="lokal bersama: 2 komputer berpikir (PC-A qwen3:4b)"),
        ):
            p.start()
            self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()
        self.tg = self.app.tg

    async def say(self, text):
        message = {"chat": {"id": 7}, "from": {"id": 42}, "text": text, "message_id": 100}
        await self.app._handle_message(message)
        return self.tg.sent[-1][1] if self.tg.sent else ""


class CommandTest(CommandBase):
    async def test_bersama_mengganti_otak(self):
        out = await self.say("/bersama")
        self.assertEqual(brain.current_backend(), "kumpulan")
        self.assertIn("Otak sekarang: lokal bersama: 2 komputer berpikir", out)
        self.assertIn("Konteks obrolan direset", out)

    async def test_bersama_tanpa_node_dijelaskan(self):
        with mock.patch.object(node, "installed", return_value=False):
            out = await self.say("/bersama")
        self.assertEqual(brain.current_backend(), "ollama")
        self.assertIn("Belum bisa ganti otak", out)
        self.assertIn("belum terpasang", out)

    async def test_bersama_status(self):
        out = await self.say("/bersama_status")
        self.assertTrue(out.startswith("🧠 Otak lokal bersama"))
        for line in STATUS_LINES:
            self.assertIn(line, out)
        self.assertIn("Pakai dengan /bersama", out)
        self.assertIn("Tamu WhatsApp/Telegram juga dijawab otak ini.", out)
        await self.say("/bersama")
        self.assertIn("Otak yang sedang dipakai: ini.", await self.say("/bersama_status"))

    async def test_status_menyebut_tamu_dimatikan(self):
        with mock.patch.object(config, "GUEST_CLUSTER", False):
            self.assertIn("ITSME_GUEST_CLUSTER=0", await self.say("/bersama_status"))

    async def test_otak_menampilkan_pilihan_bersama(self):
        out = await self.say("/otak")
        self.assertIn("/bersama — lokal bersama: 2 komputer berpikir", out)
        self.assertIn("/bersama_status", out)

    async def test_bantuan_menyebut_perintah(self):
        out = await self.say("/help")
        self.assertIn("/bersama", out)
        self.assertIn("/bersama_status", out)
        self.assertIn("tidak melenceng", out)

    async def test_bersama_status_tetap_dijawab_hub_walau_ada_perangkat_tujuan(self):
        self.assertIn("/bersama_status", main._HUB_ONLY)

    async def test_tamu_telegram_tidak_boleh_memakainya(self):
        for p in (mock.patch.object(config, "GUEST_IDS", {99}), mock.patch.object(config, "PASSPHRASE", "")):
            p.start()
            self.addCleanup(p.stop)
        message = {"chat": {"id": 9}, "from": {"id": 99}, "text": "/bersama", "message_id": 5}
        await self.app._handle_message(message)
        self.assertEqual(brain.current_backend(), "ollama")


class GuestOrderTest(unittest.TestCase):
    """Urutan otak untuk tamu: lihat brain_guest.py."""

    def setUp(self):
        keypool.reset_all()
        self.addCleanup(keypool.reset_all)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.order = []
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        for p in (
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", ["nsk_" + "a" * 32]),
            mock.patch.object(config, "DEEPSEEK_API_KEY", None),
            mock.patch.object(config, "NUSAGATE_MODELS", []),
            mock.patch.object(config, "DEEPSEEK_URL", ""),
            mock.patch.object(config, "OPENROUTER_API_KEY", "sk-or-test"),
            mock.patch.object(config, "WA_GUEST_LOCAL", False),
            mock.patch.object(config, "GUEST_CLUSTER", True),
            mock.patch.object(config, "CLUSTER_GUEST_TIMEOUT", 300.0),
            mock.patch.object(node, "installed", return_value=True),
            mock.patch.dict(brain._state, {"backend": "claude_code"}),
            mock.patch.object(brain_deepseek, "guest_chat", side_effect=self.track("nusagate", "dari key")),
            mock.patch.object(brain_openrouter, "guest_chat", side_effect=self.track("openrouter", "dari cloud")),
            mock.patch.object(brain_cluster, "guest_chat", side_effect=self.track("klaster", "dari komputer-komputer")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def track(self, name, text):
        def run(system, chat):
            self.order.append(name)
            if isinstance(text, Exception):
                raise text
            return text
        return run

    def fail(self, which, exc):
        target = {"nusagate": brain_deepseek, "openrouter": brain_openrouter, "klaster": brain_cluster}[which]
        target.guest_chat.side_effect = self.track(which, exc)

    def reply(self):
        messages = [{"role": "user", "content": "halo kak"}]
        out = brain_guest.reply(messages, {"memory": None, "guest": True})
        return out, messages

    def test_urutan_bawaan_key_lalu_klaster_lalu_cloud(self):
        self.fail("nusagate", CloudUnavailable("key habis"))
        self.fail("klaster", brain_cluster.ClusterUnavailable("tak ada komputer"))
        out, _ = self.reply()
        self.assertEqual(self.order, ["nusagate", "klaster", "openrouter"])
        self.assertEqual(out, "dari cloud")

    def test_key_menjawab_klaster_tidak_dipakai(self):
        out, msgs = self.reply()
        self.assertEqual((out, self.order), ("dari key", ["nusagate"]))
        self.assertEqual(msgs[-1], {"role": "assistant", "content": "dari key"})

    def test_tanpa_key_klaster_menjawab(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEYS", []):
            out, msgs = self.reply()
        self.assertEqual((out, self.order), ("dari komputer-komputer", ["klaster"]))
        self.assertEqual(msgs[-1]["content"], "dari komputer-komputer")     # jawaban tercatat di riwayat tamu

    def test_pemilik_memilih_bersama_klaster_pertama(self):
        with mock.patch.dict(brain._state, {"backend": "kumpulan"}):
            out, _ = self.reply()
        self.assertEqual((out, self.order), ("dari komputer-komputer", ["klaster"]))

    def test_klaster_gagal_lalu_cadangan(self):
        self.fail("klaster", brain_cluster.ClusterUnavailable("tak ada komputer"))
        with mock.patch.dict(brain._state, {"backend": "kumpulan"}):
            out, _ = self.reply()
        self.assertEqual(self.order, ["klaster", "nusagate"])
        self.assertEqual(out, "dari key")

    def test_dimatikan_lewat_config(self):
        with mock.patch.object(config, "GUEST_CLUSTER", False), mock.patch.object(config, "DEEPSEEK_API_KEYS", []):
            out, _ = self.reply()
        self.assertEqual((out, self.order), ("dari cloud", ["openrouter"]))

    def test_node_tidak_terpasang_klaster_dilewati(self):
        with mock.patch.object(node, "installed", return_value=False), mock.patch.object(config, "DEEPSEEK_API_KEYS", []):
            out, _ = self.reply()
        self.assertEqual((out, self.order), ("dari cloud", ["openrouter"]))

    def test_semua_gagal_permintaan_maaf(self):
        self.fail("nusagate", CloudUnavailable("a"))
        self.fail("klaster", brain_cluster.ClusterUnavailable("b"))
        self.fail("openrouter", CloudUnavailable("c"))
        out, msgs = self.reply()
        self.assertEqual(out, brain_guest.SORRY)
        self.assertEqual(msgs, [{"role": "user", "content": "halo kak"}])     # permintaan maaf tidak masuk riwayat

    def test_batas_tunggu_tamu(self):
        self.assertEqual(brain_guest.wait_cap(60), 300.0)                     # klaster butuh waktu lebih lega
        with mock.patch.object(config, "GUEST_CLUSTER", False):
            self.assertEqual(brain_guest.wait_cap(60), 60)
        with mock.patch.object(node, "installed", return_value=False):
            self.assertEqual(brain_guest.wait_cap(60), 60)
        with mock.patch.object(config, "CLUSTER_GUEST_TIMEOUT", 20.0):
            self.assertEqual(brain_guest.wait_cap(60), 60)                    # tidak pernah menyempitkan

    def test_klaster_dipanggil_dalam_anggaran_waktu(self):
        seen = []

        def run(system, chat):
            seen.append(think_pool._remaining())
            return "ok"

        brain_cluster.guest_chat.side_effect = run
        with mock.patch.object(config, "DEEPSEEK_API_KEYS", []):
            self.reply()
        self.assertTrue(seen and 0 < seen[0] <= 290.0)                        # CLUSTER_GUEST_TIMEOUT - 10
        self.assertIsNone(think_pool._remaining())                            # anggaran dilepas setelahnya


if __name__ == "__main__":
    unittest.main()
