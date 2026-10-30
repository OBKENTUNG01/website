"""Perintah Telegram untuk kumpulan key NusaGate: pasang banyak key sekaligus, daftar, hapus, tes, model.

Tanpa jaringan: penyedia diganti tiruan. Yang diuji adalah apa yang DIBACA pemilik (isi pesan Telegram) dan
apa yang tersimpan (.env, config), terutama bahwa key utuh tidak pernah tampil di chat.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import httpx

from itsme import brain, config, keypool, keys, main
from test_main import FakeTelegram


def nsk(letter, n=32):
    return "nsk_" + letter * n


A, B, C, D, E = (nsk(c) for c in "abcde")


class PoolCommandBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        keypool.reset_all()
        self.addCleanup(keypool.reset_all)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.env = os.path.join(self.dir, ".env")
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\n")
        for p in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "FOLLOW_NODE", False),
            mock.patch.object(config, "WHATSAPP", False),
            mock.patch.object(config, "DEEPSEEK_API_KEY", None),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", []),
            mock.patch.object(config, "NUSAGATE_MODELS", []),
            mock.patch.object(config, "DEEPSEEK_URL", ""),
            mock.patch.object(config, "DEEPSEEK_MODEL", ""),
            mock.patch.object(config, "OPENROUTER_API_KEY", None),
            mock.patch.object(keys, "ENV_FILE", self.env),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
            mock.patch.object(main, "Telegram", FakeTelegram),
            mock.patch.object(main, "run_agent", side_effect=AssertionError("key/perintah sampai ke otak AI")),
        ):
            p.start()
            self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()
        self.tg = self.app.tg

    def msg(self, text, **extra):
        message = {"chat": {"id": 7}, "from": {"id": 42}, "text": text, "message_id": 100}
        message.update(extra)
        return message

    async def say(self, text, **extra):
        await self.app._handle_message(self.msg(text, **extra))
        return self.tg.sent[-1][1] if self.tg.sent else ""

    def texts(self):
        return [t for _, t, _ in self.tg.sent]

    def env_text(self):
        with open(self.env, encoding="utf-8") as f:
            return f.read()

    def preload(self, *pool):
        """Isi kumpulan key langsung (tanpa lewat Telegram)."""
        config.DEEPSEEK_API_KEYS = list(pool)
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\nDEEPSEEK_API_KEYS=" + ",".join(pool) + "\n")

    def assertNoRawKeyShown(self, *raw):
        shown = "\n".join(self.texts())
        for key in raw:
            self.assertNotIn(key, shown, "key utuh tampil di chat")


class InstallManyKeysTest(PoolCommandBase):
    async def test_many_keys_in_one_message_are_checked_added_and_never_shown(self):
        with mock.patch.object(keys, "verify", return_value=None) as verify:
            out = await self.say(f"/kunci {A}\n{B}\n{C}", message_id=55)
        self.assertEqual(verify.call_count, 3)
        self.assertEqual(config.deepseek_keys(), [A, B, C])
        self.assertIn(f"DEEPSEEK_API_KEYS={A},{B},{C}", self.env_text())
        self.assertEqual(self.tg.deleted, [(7, 55)])  # pesan berisi key dihapus
        self.assertIn("3 key NusaGate/DeepSeek baru ditambahkan", out)
        self.assertIn("nsk…aaaa", out)
        self.assertIn("Sekarang ada 3 key", out)
        self.assertNoRawKeyShown(A, B, C)
        self.assertNotIn(7, self.app.histories)

    async def test_adding_more_later_keeps_the_earlier_keys(self):
        with mock.patch.object(keys, "verify", return_value=None):
            await self.say(f"/kunci {A} {B}")
            out = await self.say(f"/kunci {C}")
        self.assertEqual(config.deepseek_keys(), [A, B, C])
        self.assertIn("3 key", out)  # satu key: jawaban ringkas, tetap menyebut total

    async def test_message_is_deleted_before_any_key_is_checked(self):
        seen = []

        def verify(kind, key):
            seen.append(list(self.tg.deleted))

        with mock.patch.object(keys, "verify", side_effect=verify):
            await self.say(f"/kunci {A} {B}", message_id=77)
        self.assertTrue(seen and all(s == [(7, 77)] for s in seen), seen)

    async def test_keys_are_checked_at_the_same_time_not_one_by_one(self):
        running, peak, lock = 0, 0, threading.Lock()

        def verify(kind, key):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.1)
            with lock:
                running -= 1

        with mock.patch.object(keys, "verify", side_effect=verify):
            await self.say("/kunci " + " ".join(nsk(c) for c in "abcdef"))
        self.assertGreaterEqual(peak, 3)

    async def test_mixed_results_are_reported_key_by_key(self):
        def verify(kind, key):
            if key == B:
                raise keys.KeyRejected("ditolak")
            if key == C:
                raise httpx.ConnectError("putus")

        self.preload(D)
        with mock.patch.object(keys, "verify", side_effect=verify):
            out = await self.say(f"/kunci {A} {B} {C} {D} {A} pastinya-bukan-key")
        self.assertEqual(config.deepseek_keys(), [D, A])  # hanya yang baik, tanpa dobel
        self.assertIn("1 key NusaGate/DeepSeek baru ditambahkan: nsk…aaaa", out)
        self.assertIn("1 key sudah ada sebelumnya", out)
        self.assertIn("nsk…dddd", out)
        self.assertIn("1 key DITOLAK", out)
        self.assertIn("nsk…bbbb", out)
        self.assertIn("1 key belum bisa diperiksa", out)
        self.assertIn("nsk…cccc", out)
        self.assertIn("1 potongan teks tidak dikenali", out)
        self.assertNotIn(B, self.env_text())
        self.assertNotIn(C, self.env_text())
        self.assertNoRawKeyShown(A, B, C, D)

    async def test_nothing_recognised_explains_and_still_deletes(self):
        out = await self.say("/kunci halo dunia", message_id=9)
        self.assertEqual(self.tg.deleted, [(7, 9)])
        self.assertIn("tidak dikenali", out)

    async def test_every_key_rejected_changes_nothing(self):
        with mock.patch.object(keys, "verify", side_effect=keys.KeyRejected("ditolak")):
            out = await self.say(f"/kunci {A} {B}")
        self.assertEqual(config.deepseek_keys(), [])
        self.assertIn("2 key DITOLAK", out)
        self.assertNotIn("DEEPSEEK_API_KEYS", self.env_text())

    async def test_other_provider_keys_still_replace_and_extra_ones_are_ignored(self):
        first = "sk-or-v1-" + "y" * 40 + "c3d4"
        second = "sk-or-v1-" + "z" * 40 + "e5f6"
        with mock.patch.object(keys, "verify", return_value=None), \
             mock.patch.object(config, "OPENROUTER_API_KEY", None):
            out = await self.say(f"/kunci {first} {second} {A}")
            self.assertEqual(config.OPENROUTER_API_KEY, first)
        self.assertIn("diabaikan", out)
        self.assertEqual(config.deepseek_keys(), [A])

    async def test_deepseek_command_takes_many_keys_and_switches_the_brain(self):
        official = "sk-" + "o" * 40
        with mock.patch.object(keys, "verify", return_value=None):
            out = await self.say(f"/deepseek {A} {official}", message_id=5)
        self.assertEqual(config.deepseek_keys(), [A, official])
        self.assertEqual(brain.current_backend(), "deepseek")
        self.assertIn("sekarang aktif", out)
        self.assertEqual(self.tg.deleted, [(7, 5)])
        self.assertNoRawKeyShown(A, official)

    async def test_single_key_reply_stays_short_and_mentions_the_pool_size(self):
        with mock.patch.object(keys, "verify", return_value=None):
            out = await self.say(f"/kunci {A}")
        self.assertIn("nsk…aaaa", out)
        self.assertIn("1 key", out)
        with mock.patch.object(keys, "verify", return_value=None):
            again = await self.say(f"/kunci {A}")
        self.assertIn("sudah ada", again)
        self.assertEqual(config.deepseek_keys(), [A])

    async def test_unwritable_env_is_reported_without_losing_the_message_deletion(self):
        with mock.patch.object(keys, "verify", return_value=None), \
             mock.patch.object(keys, "_set_env", side_effect=OSError(13, "Permission denied")):
            out = await self.say(f"/kunci {A} {B}", message_id=3)
        self.assertIn("gagal disimpan", out)
        self.assertEqual(self.tg.deleted, [(7, 3)])


class FortyKeysTest(PoolCommandBase):
    """"10 atau mungkin lebih": 40 key dalam satu pesan Telegram."""

    BATCH = ["nsk_" + f"{i:02d}" + "q" * 26 + f"{i:04d}" for i in range(40)]

    async def test_forty_keys_in_one_message_are_all_added_in_order(self):
        with mock.patch.object(keys, "verify", return_value=None) as verify:
            out = await self.say("/kunci\n" + "\n".join(self.BATCH), message_id=9)
        self.assertEqual(verify.call_count, 40)
        self.assertEqual(config.deepseek_keys(), self.BATCH)
        self.assertIn("40 key NusaGate/DeepSeek baru ditambahkan", out)
        self.assertIn("Sekarang ada 40 key", out)
        self.assertLess(len(out), 4000)  # muat dalam satu pesan Telegram
        self.assertNoRawKeyShown(*self.BATCH)
        self.assertEqual(self.tg.deleted, [(7, 9)])

    async def test_the_env_line_with_forty_keys_reads_back_exactly(self):
        with mock.patch.object(keys, "verify", return_value=None):
            await self.say("/kunci " + " ".join(self.BATCH))
        line = next(l for l in self.env_text().splitlines() if l.startswith("DEEPSEEK_API_KEYS="))
        self.assertEqual(config._csv(line.split("=", 1)[1]), self.BATCH)

    async def test_the_list_shows_all_forty_with_their_numbers(self):
        self.preload(*self.BATCH)
        out = await self.say("/kunci_daftar")
        self.assertIn("40 key NusaGate/DeepSeek dipakai bergiliran — 40 siap sekarang", out)
        for number in (1, 20, 40):
            self.assertIn(f"{number}. nsk…{number - 1:04d} — siap", out)
        self.assertNoRawKeyShown(*self.BATCH)

    async def test_removing_several_of_forty_keeps_the_rest_in_order(self):
        self.preload(*self.BATCH)
        await self.say("/kunci_hapus 1 20 40")
        expected = [k for i, k in enumerate(self.BATCH, 1) if i not in (1, 20, 40)]
        self.assertEqual(config.deepseek_keys(), expected)

    async def test_checking_forty_keys_reports_each_one(self):
        self.preload(*self.BATCH)
        with mock.patch.object(keys, "gateway_models", return_value=["m1", "m2"]):
            out = await self.say("/kunci_tes")
        self.assertIn("🔎 Hasil pemeriksaan 40 key: 40 baik", out)
        self.assertEqual(out.count("✅ dikenali penyedia"), 40)


class BareKeyTest(PoolCommandBase):
    async def test_a_key_pasted_without_the_command_is_installed_and_removed_from_chat(self):
        with mock.patch.object(keys, "verify", return_value=None):
            out = await self.say(A, message_id=12)
        self.assertEqual(config.deepseek_keys(), [A])
        self.assertEqual(self.tg.deleted, [(7, 12)])
        self.assertIn("ditambahkan", out)
        self.assertNotIn(7, self.app.histories)  # tidak sampai ke riwayat/otak AI (run_agent akan gagal)

    async def test_many_bare_keys_one_per_line(self):
        with mock.patch.object(keys, "verify", return_value=None):
            await self.say(f"{A}\n{B}\n{C}")
        self.assertEqual(config.deepseek_keys(), [A, B, C])

    async def test_bare_key_from_a_stranger_is_ignored(self):
        message = self.msg(A)
        message["from"] = {"id": 999}
        with mock.patch.object(keys, "verify", return_value=None):
            await self.app._handle_message(message)
        self.assertEqual(config.deepseek_keys(), [])
        self.assertEqual(self.tg.deleted, [])

    async def test_a_mistyped_command_with_a_key_is_still_kept_away_from_the_ai(self):
        with mock.patch.object(keys, "verify", return_value=None):
            out = await self.say(f"/kunc {A}", message_id=33)
        self.assertEqual(config.deepseek_keys(), [A])
        self.assertEqual(self.tg.deleted, [(7, 33)])
        self.assertNoRawKeyShown(A)
        self.assertIn("ditambahkan", out)

    async def test_a_sentence_with_a_key_is_an_ordinary_message(self):
        with mock.patch.object(main, "run_agent", return_value="jawaban biasa") as agent:
            await self.say(f"tolong jelaskan key ini {A}")
        agent.assert_called_once()
        self.assertEqual(config.deepseek_keys(), [])

    async def test_bare_key_is_never_forwarded_to_another_computer(self):
        with mock.patch.object(main.devices, "get_target", return_value="Laptop"), \
             mock.patch.object(main.App, "_forward", side_effect=AssertionError("key diteruskan ke komputer lain")), \
             mock.patch.object(keys, "verify", return_value=None):
            await self.say(A)
        self.assertEqual(config.deepseek_keys(), [A])


class ListAndRemoveTest(PoolCommandBase):
    async def test_empty_pool_says_how_to_start(self):
        out = await self.say("/kunci_daftar")
        self.assertIn("Belum ada key", out)
        self.assertIn("/kunci <key>", out)
        self.assertIn("jangan lewat WhatsApp", out)

    async def test_list_shows_numbers_masks_and_states_but_never_a_raw_key(self):
        self.preload(A, B, C, D)
        lease = keypool.acquire()
        keypool.release(lease, "balance")
        out = await self.say("/kunci_daftar")
        self.assertIn("4 key NusaGate/DeepSeek dipakai bergiliran", out)
        self.assertIn("3 siap sekarang", out)
        for number, mask in ((1, "nsk…aaaa"), (2, "nsk…bbbb"), (3, "nsk…cccc"), (4, "nsk…dddd")):
            self.assertIn(f"{number}. {mask}", out)
        self.assertIn("saldo habis", out)
        self.assertIn("siap · dipakai", out)
        self.assertNoRawKeyShown(A, B, C, D)

    async def test_every_status_has_a_human_sentence(self):
        now = time.time()
        row = {"no": 3, "uses": 5, "inflight": 0, "until": now + 40}
        for status, needle in (
            ("siap", "siap"), ("sibuk", "sempat error"), ("penuh", "penuh menit ini"),
            ("limit", "dibatasi penyedia"), ("cap", "batas paket"), ("balance", "saldo habis"),
            ("invalid", "/kunci_hapus 3"),
        ):
            with self.subTest(status=status):
                self.assertIn(needle, main.App._pool_status({**row, "status": status}, now))

    async def test_remove_by_number(self):
        self.preload(A, B, C)
        out = await self.say("/kunci_hapus 2")
        self.assertEqual(config.deepseek_keys(), [A, C])
        self.assertIn("nsk…bbbb", out)
        self.assertIn("Sisa 2 key", out)
        self.assertNotIn(B, self.env_text())
        self.assertNoRawKeyShown(A, B, C)

    async def test_remove_several_at_once(self):
        self.preload(A, B, C, D)
        await self.say("/kunci_hapus 1 4")
        self.assertEqual(config.deepseek_keys(), [B, C])

    async def test_remove_validates_the_number(self):
        self.preload(A, B)
        self.assertIn("Tidak ada key bernomor 5", await self.say("/kunci_hapus 5"))
        self.assertIn("Tidak ada key bernomor 0", await self.say("/kunci_hapus 0"))
        self.assertIn("Pakai: /kunci_hapus", await self.say("/kunci_hapus"))
        self.assertIn("Pakai: /kunci_hapus", await self.say("/kunci_hapus semua"))
        self.assertEqual(config.deepseek_keys(), [A, B])  # tidak ada yang terhapus

    async def test_removing_the_last_key_explains_what_guests_get_now(self):
        self.preload(A)
        out = await self.say("/kunci_hapus 1")
        self.assertIn("Sisa 0 key", out)
        self.assertIn("kosong", out)
        self.assertEqual(config.deepseek_keys(), [])

    async def test_remove_with_nothing_to_remove(self):
        self.assertIn("Belum ada key", await self.say("/kunci_hapus 1"))

    async def test_commands_are_not_mistaken_for_the_install_command(self):
        # "/kunci_daftar" diawali "/kunci": dulu ini akan masuk jalur pemasangan key.
        out = await self.say("/kunci_daftar")
        self.assertNotIn("Kirim API key dengan", out)
        self.assertEqual(self.tg.deleted, [])


class TestKeysCommandTest(PoolCommandBase):
    async def test_free_check_reports_each_key(self):
        self.preload(A, B, C)

        def models(key):
            if key == B:
                raise keys.KeyRejected("ditolak")
            if key == C:
                raise httpx.ConnectError("putus")
            return ["deepseek-v4.1-flash", "glm-5.3-flash"]

        with mock.patch.object(keys, "gateway_models", side_effect=models):
            out = await self.say("/kunci_tes")
        self.assertIn("1. nsk…aaaa ✅ dikenali penyedia (2 model)", out)
        self.assertIn("2. nsk…bbbb ❌ ditolak penyedia", out)
        self.assertIn("3. nsk…cccc ⚠️ belum terjangkau (ConnectError)", out)
        self.assertIn("1 baik, 2 bermasalah", out)
        self.assertIn("/kunci_tes chat", out)  # tawarkan bukti yang lebih kuat
        self.assertIn("/kunci_hapus 2 3", out)
        self.assertNoRawKeyShown(A, B, C)
        # key yang ditolak langsung dikeluarkan sementara dari giliran
        status = {r["no"]: r["status"] for r in keypool.snapshot()}
        self.assertEqual(status[2], "invalid")

    async def test_chat_check_asks_every_key_to_answer(self):
        self.preload(A, B)
        from itsme import brain_deepseek

        def ping(key, model=None):
            return (True, "deepseek-v4.1-flash", 0.8) if key == A else (False, "Saldo penyedia DeepSeek-mu habis.", 0.2)

        with mock.patch.object(keys, "gateway_models", return_value=["deepseek-v4.1-flash"]), \
             mock.patch.object(brain_deepseek, "ping", side_effect=ping):
            out = await self.say("/kunci_tes chat")
        self.assertIn("1. nsk…aaaa ✅ bisa menjawab (model deepseek-v4.1-flash, 0.8 dtk)", out)
        self.assertIn("2. nsk…bbbb ❌ tidak bisa menjawab: Saldo penyedia DeepSeek-mu habis.", out)
        self.assertNotIn("/kunci_tes chat", out)  # sudah dijalankan

    async def test_free_check_does_not_forgive_an_empty_balance(self):
        self.preload(A)
        keypool.release(keypool.acquire(), "balance")
        with mock.patch.object(keys, "gateway_models", return_value=["m"]):
            await self.say("/kunci_tes")
        self.assertEqual(keypool.snapshot()[0]["status"], "balance")

    async def test_without_keys(self):
        self.assertIn("Belum ada key", await self.say("/kunci_tes"))


class ModelCommandsTest(PoolCommandBase):
    NAMES = ["deepseek-v4.1-flash", "glm-5.3-flash", "qwen-3-coder"]

    async def test_list_marks_the_models_in_rotation(self):
        self.preload(A)
        config.NUSAGATE_MODELS = ["glm-5.3-flash"]
        with mock.patch.object(keys, "gateway_models", return_value=self.NAMES):
            out = await self.say("/model_daftar")
        self.assertIn("• glm-5.3-flash  ← dipakai", out)
        self.assertIn("• qwen-3-coder", out)
        self.assertIn("/model_pakai", out)

    async def test_list_without_keys(self):
        self.assertIn("Belum ada key NusaGate", await self.say("/model_daftar"))

    async def test_list_reports_rejected_or_unreachable_provider(self):
        self.preload(A)
        with mock.patch.object(keys, "gateway_models", side_effect=keys.KeyRejected("x")):
            self.assertIn("ditolak", await self.say("/model_daftar"))
        with mock.patch.object(keys, "gateway_models", side_effect=httpx.ConnectError("putus")):
            self.assertIn("belum bisa diambil", await self.say("/model_daftar"))

    async def test_use_several_models_in_turn(self):
        self.preload(A)
        with mock.patch.object(keys, "gateway_models", return_value=self.NAMES):
            out = await self.say("/model_pakai deepseek-v4.1-flash glm-5.3-flash")
        self.assertEqual(config.NUSAGATE_MODELS, ["deepseek-v4.1-flash", "glm-5.3-flash"])
        self.assertIn("ITSME_NUSAGATE_MODELS=deepseek-v4.1-flash,glm-5.3-flash", self.env_text())
        self.assertIn("bergilir (2 model)", out)
        self.assertEqual({keypool.next_model() for _ in range(4)}, {"deepseek-v4.1-flash", "glm-5.3-flash"})

    async def test_use_all_models_asks_first_and_warns_about_price_and_tools(self):
        self.preload(A)
        with mock.patch.object(keys, "gateway_models", return_value=self.NAMES), \
             mock.patch.object(self.app, "_ask_confirm", return_value=True) as ask:
            out = await self.say("/model_pakai semua")
        warning = ask.call_args[0][1]
        self.assertIn("3 model", warning)
        self.assertIn("harga", warning)
        self.assertIn("alat", warning)
        self.assertEqual(config.NUSAGATE_MODELS, self.NAMES)
        self.assertIn("bergilir (3 model)", out)

    async def test_declining_leaves_the_models_as_they_were(self):
        self.preload(A)
        config.NUSAGATE_MODELS = ["glm-5.3-flash"]
        with mock.patch.object(keys, "gateway_models", return_value=self.NAMES), \
             mock.patch.object(self.app, "_ask_confirm", return_value=False):
            out = await self.say("/model_pakai semua")
        self.assertIn("tidak diubah", out)
        self.assertEqual(config.NUSAGATE_MODELS, ["glm-5.3-flash"])

    async def test_use_all_models_needs_a_key_and_a_reachable_provider(self):
        with mock.patch.object(self.app, "_ask_confirm", side_effect=AssertionError("tanpa key tidak ada yang ditanyakan")):
            self.assertIn("Belum ada key NusaGate", await self.say("/model_pakai semua"))
            self.preload(A)
            with mock.patch.object(keys, "gateway_models", side_effect=httpx.ConnectError("putus")):
                self.assertIn("belum bisa diambil", await self.say("/model_pakai semua"))
            with mock.patch.object(keys, "gateway_models", return_value=[]):
                self.assertIn("tidak mengirim daftar model", await self.say("/model_pakai semua"))
        self.assertEqual(config.NUSAGATE_MODELS, [])

    async def test_all_is_a_keyword_not_a_model_name_even_in_other_spelling(self):
        self.preload(A)
        with mock.patch.object(keys, "gateway_models", return_value=self.NAMES), \
             mock.patch.object(self.app, "_ask_confirm", return_value=True):
            await self.say("/model_pakai ALL")
        self.assertEqual(config.NUSAGATE_MODELS, self.NAMES)

    async def test_unknown_model_name_is_refused_and_nothing_changes(self):
        self.preload(A)
        with mock.patch.object(keys, "gateway_models", return_value=self.NAMES):
            out = await self.say("/model_pakai glm-5.3-flash model-ngawur")
        self.assertIn("model-ngawur", out)
        self.assertIn("Tidak disimpan", out)
        self.assertEqual(config.NUSAGATE_MODELS, [])

    async def test_back_to_the_default_model(self):
        self.preload(A)
        config.NUSAGATE_MODELS = ["glm-5.3-flash"]
        out = await self.say("/model_pakai bawaan")
        self.assertEqual(config.NUSAGATE_MODELS, [])
        self.assertIn("deepseek-v4.1-flash", out)

    async def test_use_without_a_name_explains(self):
        self.assertIn("Pakai: /model_pakai", await self.say("/model_pakai"))

    async def test_unreachable_provider_still_saves_with_a_warning(self):
        self.preload(A)
        with mock.patch.object(keys, "gateway_models", side_effect=httpx.ConnectError("putus")):
            out = await self.say("/model_pakai glm-5.3-flash")
        self.assertEqual(config.NUSAGATE_MODELS, ["glm-5.3-flash"])
        self.assertIn("belum bisa diperiksa", out)


class PoolAlertTest(PoolCommandBase):
    async def test_dead_keys_are_announced_in_one_message_without_raw_keys(self):
        self.preload(A, B, C)
        with mock.patch.object(main, "_POOL_ALERT_WAIT", 0.05):
            for number, kind in ((1, "balance"), (2, "balance"), (3, "invalid")):
                await asyncio.to_thread(self.app._pool_alert, kind, f"nsk…{'abc'[number - 1] * 4}")
            await asyncio.sleep(0.3)
        balance = [t for t in self.texts() if "saldonya habis" in t]
        invalid = [t for t in self.texts() if "ditolak penyedia" in t]
        self.assertEqual(len(balance), 1)
        self.assertIn("2 key NusaGate tidak bisa dipakai sementara", balance[0])
        self.assertIn("nsk…aaaa", balance[0])
        self.assertIn("nsk…bbbb", balance[0])
        self.assertEqual(len(invalid), 1)
        self.assertIn("/kunci_daftar", balance[0])

    async def test_alert_is_wired_to_the_pool_by_run(self):
        # App.run() sungguhan (bukan memasang sendiri): setelah menyala, key yang mati sampai ke pemilik.
        self.preload(A, B)
        self.addCleanup(keypool.set_alert, None)
        with mock.patch.object(main, "_POOL_ALERT_WAIT", 0.05), mock.patch.object(config, "AUTO_UPDATE", False):
            serving = asyncio.create_task(self.app.run())
            await asyncio.sleep(0.2)
            self.assertEqual(keypool._alert, self.app._pool_alert)
            await asyncio.to_thread(lambda: keypool.release(keypool.acquire(), "balance"))
            await asyncio.sleep(0.4)
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        self.assertTrue(any("saldonya habis" in t for t in self.texts()), self.texts())


class HelpAndRoutingTest(unittest.TestCase):
    def test_help_lists_the_new_commands_and_the_telegram_only_rule(self):
        for command in ("/kunci_daftar", "/kunci_hapus", "/kunci_tes", "/model_daftar", "/model_pakai", "/wa_tamu",
                        "/wa_blokir", "/wa_buka", "/wa_info"):
            self.assertIn(command, main.HELP)
            self.assertIn(command, main._HUB_ONLY)
        self.assertIn("tidak pernah lewat WhatsApp", main.HELP)

    def test_whatsapp_never_handles_key_or_settings_commands(self):
        for command in ("/kunci", "/deepseek", "/kunci_daftar", "/kunci_hapus", "/kunci_tes", "/model_daftar",
                        "/model_pakai", "/wa_tamu", "/wa_blokir", "/wa_buka", "/wa_info"):
            self.assertIn(command, main._WA_TELEGRAM_ONLY)
            self.assertNotIn(command, main._WA_OWNER_COMMANDS)

    def test_help_text_of_kunci_mentions_many_keys_and_telegram(self):
        self.assertIn("/kunci <key>", main.KUNCI_HELP)
        self.assertIn("BANYAK", main.KUNCI_HELP)
        self.assertIn("jangan lewat WhatsApp", main.KUNCI_HELP)


class BrainStatusTest(PoolCommandBase):
    async def test_brain_status_counts_the_pool(self):
        self.preload(A, B)
        keypool.release(keypool.acquire(), "balance")
        text = await asyncio.to_thread(self.app._brain_status)
        self.assertIn("/deepseek —", text)
        self.assertIn("Kumpulan key: 2 key, 1 siap sekarang", text)

    async def test_brain_status_without_keys_points_to_kunci(self):
        text = await asyncio.to_thread(self.app._brain_status)
        self.assertIn("/deepseek — belum aktif (kirim /kunci", text)


if __name__ == "__main__":
    unittest.main()
