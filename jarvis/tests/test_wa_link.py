"""Penautan WhatsApp: yang dikatakan bot harus sama dengan keadaan sebenarnya.

Kejadian nyata 29 Okt 2026: pemilik mengirim /wa <nomor> dan dijanjikan "QR-nya kukirim ke chat
ini", padahal akun itu SUDAH tertaut (sesi tersimpan di data/whatsapp, nomor sama) - WhatsApp
langsung tersambung dan memang tidak membuat QR. Pemilik menunggu QR yang tak mungkin datang, dan
tidak ada perintah untuk meminta QR baru. Juga: akun yang dicabut dari HP membuat jembatan keluar
dan menyerah tanpa pernah mengirim QR.

Tanpa jaringan & tanpa Node: jembatan diganti tiruan; yang diuji murni logika.
"""

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from itsme import config, main
from itsme import whatsapp as wa_module
from itsme.whatsapp import WhatsApp
from test_main import FakeTelegram

NOMOR = "6283125645146"


class FakeProc:
    def __init__(self, lines=(), running=True):
        self.stdout = iter(lines)
        self.stdin = io.StringIO()
        self._running = running
        self.terminated = False

    def poll(self):
        return None if self._running else 0

    def terminate(self):
        self.terminated = True
        self._running = False

    def wait(self, timeout=None):
        return 0


def line(**obj):
    return json.dumps(obj)


class BridgeStateTest(unittest.TestCase):
    def _read(self, *lines, **kw):
        seen = []
        wa = WhatsApp(lambda *a, **k: None, on_state=lambda s, me: seen.append((s, me)), **kw)
        wa._proc = FakeProc(lines, running=False)
        wa._stop = True  # jangan menyalakan ulang jembatan di dalam uji
        wa._read()
        return wa, seen

    def test_open_state_carries_the_linked_account(self):
        wa, seen = self._read(line(state="open", me=NOMOR))
        self.assertEqual((wa.state, wa.me), ("tersambung", NOMOR))
        self.assertEqual(seen, [("tersambung", NOMOR)])
        self.assertIn(NOMOR, wa.status_text())

    def test_qr_close_and_logged_out_states(self):
        wa, seen = self._read(line(state="qr"), line(state="close", code=515),
                              line(state="open", me=NOMOR), line(state="logged_out"))
        self.assertEqual([s for s, _ in seen], ["qr", "terputus", "tersambung", "keluar"])
        self.assertEqual((wa.state, wa.me), ("keluar", ""))  # akun lama tak berlaku lagi

    def test_bridge_log_lines_are_never_mistaken_for_state(self):
        # Baileys menulis log pino ke stdout; sebuah log yang kebetulan bernama "state" tak boleh dipakai.
        wa, seen = self._read(line(level=30, time="x", msg="halo", state="open"),
                              line(state="aneh"), "bukan json", line(foo="bar"))
        self.assertEqual(seen, [])
        self.assertEqual(wa.state, "mulai")

    def test_qr_image_still_reaches_the_qr_callback(self):
        got = []
        wa = WhatsApp(lambda *a, **k: None, on_qr=got.append)
        wa._proc = FakeProc([line(qr=base64.b64encode(b"\x89PNGdata").decode())], running=False)
        wa._stop = True
        wa._read()
        self.assertEqual(got, [b"\x89PNGdata"])

    def test_unexpected_exit_marks_disconnected_and_restarts(self):
        wa = WhatsApp(lambda *a, **k: None)
        wa._proc = FakeProc(running=False)
        with mock.patch.object(wa, "start") as start, mock.patch.object(wa_module.time, "sleep"):
            wa._read()
        self.assertEqual(wa.state, "terputus")
        start.assert_called_once()

    def test_a_replaced_bridge_does_not_restart_another_one(self):
        # Pembaca proses LAMA yang telat berakhir tidak boleh menyalakan jembatan kedua.
        wa = WhatsApp(lambda *a, **k: None)
        old, new = FakeProc(running=False), FakeProc()
        wa._proc = old
        real_poll = old.poll

        def poll_then_get_replaced():
            wa._proc = new  # saat pembaca lama sampai di ujung, jembatan baru sudah terpasang
            return real_poll()

        old.poll = poll_then_get_replaced
        with mock.patch.object(wa, "start") as start, mock.patch.object(wa_module.time, "sleep"):
            wa._read()
        start.assert_not_called()
        self.assertEqual(wa.state, "mulai")  # keadaan juga tidak dirusak oleh pembaca lama

    def test_status_text_covers_every_state(self):
        wa = WhatsApp(lambda *a, **k: None)
        for state in ("mulai", "qr", "tersambung", "terputus", "keluar"):
            wa.state = state
            self.assertTrue(wa.status_text())
        wa.state, wa.me = "tersambung", NOMOR
        self.assertIn(NOMOR, wa.status_text())

    def test_on_state_errors_never_break_the_reader(self):
        def boom(*a):
            raise RuntimeError("rusak")

        wa = WhatsApp(lambda *a, **k: None, on_state=boom)
        wa._proc = FakeProc([line(state="open", me=NOMOR), line(state="qr")], running=False)
        wa._stop = True
        with self.assertLogs("itsme", level="ERROR"):
            wa._read()
        self.assertEqual(wa.state, "qr")  # pembaca tetap lanjut ke baris berikutnya


class WaitStateTest(unittest.TestCase):
    def test_returns_at_once_when_already_in_a_wanted_state(self):
        wa = WhatsApp(lambda *a, **k: None)
        wa._set_state("tersambung", NOMOR)
        t0 = time.monotonic()
        self.assertEqual(wa.wait_state(5), "tersambung")
        self.assertLess(time.monotonic() - t0, 1)

    def test_times_out_with_the_current_state(self):
        wa = WhatsApp(lambda *a, **k: None)
        t0 = time.monotonic()
        self.assertEqual(wa.wait_state(0.2), "mulai")
        self.assertGreaterEqual(time.monotonic() - t0, 0.15)

    def test_wakes_up_when_another_thread_reports(self):
        wa = WhatsApp(lambda *a, **k: None)
        threading.Timer(0.1, wa._set_state, ("qr",)).start()
        t0 = time.monotonic()
        self.assertEqual(wa.wait_state(5), "qr")
        self.assertLess(time.monotonic() - t0, 2)

    def test_only_waits_for_the_states_asked_for(self):
        wa = WhatsApp(lambda *a, **k: None)
        wa._set_state("terputus")
        self.assertEqual(wa.wait_state(0.2), "terputus")  # bukan yang diminta -> habis waktu


class RelinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patch = mock.patch.object(config, "DATA_DIR", self.dir)
        patch.start()
        self.addCleanup(patch.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)

    def _auth(self):
        d = os.path.join(self.dir, "whatsapp")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "creds.json"), "w") as f:
            f.write("{}")
        return d

    def test_asks_the_live_bridge_to_log_out_and_waits_for_the_new_qr(self):
        wa = WhatsApp(lambda *a, **k: None)
        wa._proc = FakeProc()  # hidup
        wa._set_state("tersambung", NOMOR)

        def logout(obj):
            self.assertEqual(obj, {"cmd": "logout"})
            threading.Timer(0.05, wa._set_state, ("keluar",)).start()
            return True

        with mock.patch.object(wa, "_send_obj", side_effect=logout), \
             mock.patch.object(wa, "start") as start, mock.patch.object(wa, "stop") as stop:
            self.assertEqual(wa.relink(), (True, ""))
        start.assert_not_called()  # wa.js sendiri yang memindahkan sesi & menyalakan ulang soketnya
        stop.assert_not_called()

    def test_falls_back_to_moving_the_session_when_the_bridge_is_silent(self):
        auth = self._auth()
        wa = WhatsApp(lambda *a, **k: None)
        proc = FakeProc()
        wa._proc = proc
        with mock.patch.object(wa, "_send_obj", return_value=True), \
             mock.patch.object(wa, "wait_state", return_value="mulai"), \
             mock.patch.object(wa, "start", return_value=(True, "")) as start:
            self.assertEqual(wa.relink(), (True, ""))
        self.assertTrue(proc.terminated)
        self.assertFalse(os.path.isdir(auth))  # sesi lama pergi dari tempatnya...
        saved = [n for n in os.listdir(self.dir) if n.startswith("whatsapp.lama-")]
        self.assertEqual(len(saved), 1)  # ...tapi DISIMPAN, bukan dihapus
        self.assertTrue(os.path.exists(os.path.join(self.dir, saved[0], "creds.json")))
        start.assert_called_once()

    def test_works_when_there_is_no_bridge_at_all(self):
        self._auth()
        wa = WhatsApp(lambda *a, **k: None)
        with mock.patch.object(wa, "start", return_value=(True, "")) as start:
            self.assertEqual(wa.relink(), (True, ""))
        start.assert_called_once()
        self.assertEqual(wa._tries, 0)

    def test_reports_when_the_old_session_cannot_be_moved(self):
        wa = WhatsApp(lambda *a, **k: None)
        with mock.patch.object(wa_module, "_set_aside_session", side_effect=OSError("terkunci")), \
             mock.patch.object(wa, "start") as start:
            self.assertEqual(wa.relink(), (False, "sesi"))
        start.assert_not_called()

    def test_set_aside_session_without_a_session_does_nothing(self):
        self.assertIsNone(wa_module._set_aside_session())


class FakeWa:
    """Pengganti jembatan untuk menguji App: merekam panggilan, jawaban wait_state bisa diatur."""

    def __init__(self, state="tersambung", me=NOMOR, waits=None, start=(True, ""), relink=(True, "")):
        self.state, self.me = state, me
        self.waits = list(waits or [state])
        self._start, self._relink = start, relink
        self.calls = []

    def start(self):
        self.calls.append("start")
        return self._start

    def stop(self):
        self.calls.append("stop")

    def relink(self):
        self.calls.append("relink")
        return self._relink

    def wait_state(self, timeout=20, want=None):
        self.calls.append(("wait", timeout, want))
        return self.waits.pop(0) if len(self.waits) > 1 else self.waits[0]

    def status_text(self):
        return f"FAKE:{self.state}"


class FakeLoop:
    """Loop berjam yang bisa diatur; sisanya diteruskan ke loop sungguhan."""

    def __init__(self, real, now=1000.0):
        self.real, self.now = real, now

    def time(self):
        return self.now

    def call_soon_threadsafe(self, *a):
        return self.real.call_soon_threadsafe(*a)


class WaAppBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "FOLLOW_NODE", False),
            mock.patch.object(config, "WHATSAPP", False),
            mock.patch.object(config, "WA_OWNER", ""),
            mock.patch.object(main, "Telegram", FakeTelegram),
            mock.patch.object(main.keys, "_set_env"),  # jangan menulis .env sungguhan
        ):
            p.start()
            self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.real_loop = asyncio.get_running_loop()
        self.app.loop = self.real_loop
        self.tg = self.app.tg

    def _use(self, fake):
        p = mock.patch.object(main.App, "_new_wa", lambda app: fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    async def _say(self, text):
        await self.app._handle_message({"chat": {"id": 7}, "from": {"id": 42}, "text": text})
        return self.tg.sent[-1][1]

    async def _settle(self):
        await asyncio.sleep(0.05)  # beri giliran ke task yang dijadwalkan dari thread jembatan


class WaCommandTruthTest(WaAppBase):
    async def test_already_linked_account_is_told_plainly_that_no_qr_is_coming(self):
        self._use(FakeWa("tersambung", NOMOR))
        await self._say(f"/wa {NOMOR}")
        out = self.tg.sent[-1][1]
        self.assertIn("TERSAMBUNG", out)
        self.assertIn("tidak ada QR", out)
        self.assertIn("/wa_ulang", out)
        self.assertNotIn("QR-nya kukirim", " ".join(m[1] for m in self.tg.sent))  # janji palsu yang lama
        self.assertFalse(getattr(self.tg, "photos", []))

    async def test_a_different_linked_account_is_flagged(self):
        self._use(FakeWa("tersambung", "6281111111111"))
        await self._say(f"/wa {NOMOR}")
        out = self.tg.sent[-1][1]
        self.assertIn("6281111111111", out)
        self.assertIn(NOMOR, out)
        self.assertIn("/wa_ulang", out)

    async def test_waiting_for_a_qr_says_it_was_sent_and_warns_about_the_same_phone(self):
        self._use(FakeWa("mulai", "", waits=["qr"]))
        await self._say(f"/wa {NOMOR}")
        out = self.tg.sent[-1][1]
        self.assertIn("QR sudah kukirim", out)
        self.assertIn("HP yang sama", out)

    async def test_no_news_from_whatsapp_in_time_is_reported_as_such(self):
        self._use(FakeWa("mulai", "", waits=["mulai"]))
        await self._say(f"/wa {NOMOR}")
        self.assertIn("belum ada kabar", self.tg.sent[-1][1])

    async def test_logged_out_state_is_reported(self):
        self._use(FakeWa("mulai", "", waits=["keluar"]))
        await self._say(f"/wa {NOMOR}")
        self.assertIn("keluar dari Perangkat tertaut", self.tg.sent[-1][1])

    async def test_same_number_and_already_connected_is_not_restarted(self):
        fake = FakeWa("tersambung", NOMOR)
        self.app.wa = fake
        self._use(FakeWa())  # kalau salah menyalakan ulang, ini yang terpakai
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            await self._say(f"/wa {NOMOR}")
        self.assertEqual(fake.calls, [])
        self.assertIs(self.app.wa, fake)
        self.assertIn("tidak berubah", self.tg.sent[-1][1])

    async def test_changing_the_number_restarts_the_bridge_and_resets_the_qr_budget(self):
        old = FakeWa("tersambung", NOMOR)
        self.app.wa = old
        self.app._qr_at, self.app._qr_sent = 5.0, 3
        new = self._use(FakeWa("mulai", "", waits=["qr"]))
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            await self._say("/wa 6289999999999")
        self.assertIn("stop", old.calls)
        self.assertIn("start", new.calls)
        self.assertEqual((self.app._qr_at, self.app._qr_sent), (None, 0))

    async def test_status_shows_the_connection_and_the_relink_command(self):
        self.app.wa = FakeWa("qr", "")
        with mock.patch.object(config, "WA_OWNER", NOMOR), mock.patch.object(config, "WHATSAPP", True):
            out = await self._say("/wa")
        self.assertIn("Sambungan: FAKE:qr", out)
        self.assertIn("/wa_ulang", out)
        self.assertIn(NOMOR, out)

    async def test_help_and_routing_know_the_new_command(self):
        self.assertIn("/wa_ulang", main.HELP)
        self.assertIn("/wa_ulang", main._HUB_ONLY)


class WaRelinkCommandTest(WaAppBase):
    async def test_relink_needs_a_yes_while_the_account_is_working(self):
        fake = FakeWa("tersambung", NOMOR, waits=["qr"])
        self.app.wa = fake
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            out = await self._say("/wa_ulang")
            self.assertIn("/wa_ulang ya", out)
            self.assertNotIn("relink", fake.calls)  # belum ada yang diputus
            out = await self._say("/wa_ulang ya")
        self.assertIn("relink", fake.calls)
        self.assertIn("QR sudah kukirim", out)

    async def test_relink_without_an_owner_number_explains_what_to_do_first(self):
        out = await self._say("/wa_ulang")
        self.assertIn("/wa 6281234567890", out)

    async def test_relink_when_not_connected_goes_straight_ahead(self):
        fake = FakeWa("terputus", "", waits=["qr"])
        self.app.wa = fake
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            out = await self._say("/wa_ulang")
        self.assertIn("relink", fake.calls)
        self.assertIn("QR sudah kukirim", out)

    async def test_relink_starts_a_bridge_when_there_is_none(self):
        new = self._use(FakeWa("mulai", "", waits=["qr"]))
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            out = await self._say("/wa_ulang")
        self.assertIn("start", new.calls)
        self.assertIs(self.app.wa, new)
        self.assertIn("QR sudah kukirim", out)

    async def test_relink_failures_are_explained(self):
        self.app.wa = FakeWa("terputus", "", relink=(False, "sesi"))
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            out = await self._say("/wa_ulang")
        self.assertIn("tidak bisa dipindahkan", out)
        self.assertIsNone(self.app.wa)
        self._use(FakeWa("mulai", "", start=(False, "node")))
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            out = await self._say("/wa_ulang")
        self.assertIn("Node.js", out)

    async def test_relinking_flag_is_cleared_even_when_it_fails(self):
        self.app.wa = FakeWa("terputus", "", relink=(False, "sesi"))
        with mock.patch.object(config, "WA_OWNER", NOMOR):
            await self._say("/wa_ulang")
        self.assertFalse(self.app._wa_relinking)


class QrDeliveryTest(WaAppBase):
    def _fake_clock(self):
        loop = FakeLoop(self.real_loop)
        self.app.loop = loop
        return loop

    async def test_first_qr_is_sent_even_when_the_machine_just_booted(self):
        # Dulu _qr_at mulai dari 0.0 dan loop.time() = detik sejak boot: QR pertama dibuang kalau
        # It's Me menyala dalam 45 detik pertama sejak komputer menyala.
        loop = self._fake_clock()
        loop.now = 5.0
        self.app._wa_qr(b"\x89PNGx")
        await self._settle()
        self.assertEqual(len(self.tg.photos), 1)

    async def test_qr_goes_to_the_owner_with_instructions(self):
        self._fake_clock()
        self.app._wa_qr(b"\x89PNGx")
        await self._settle()
        chat, data, name, caption = self.tg.photos[0]
        self.assertEqual((chat, data), (42, b"\x89PNGx"))
        self.assertIn("Perangkat tertaut", caption)
        self.assertIn("/wa_ulang", caption)

    async def test_gap_between_qrs_and_a_cap_per_request(self):
        loop = self._fake_clock()
        for now in (100.0, 110.0, 150.0, 200.0, 260.0, 320.0):
            loop.now = now
            self.app._wa_qr(b"x")
        await self._settle()
        # 100 terkirim; 110 terlalu dekat; 150 & 200 terkirim; sisanya melewati batas 3 per permintaan
        self.assertEqual(len(self.tg.photos), 3)

    async def test_a_new_request_gets_a_fresh_qr_budget(self):
        loop = self._fake_clock()
        for now in (100.0, 150.0, 200.0, 250.0):
            loop.now = now
            self.app._wa_qr(b"x")
        self.assertEqual(self.app._qr_sent, 3)
        self.app._wa_state("keluar", "")  # dicabut dari HP -> QR baru akan datang
        loop.now = 300.0
        self.app._wa_qr(b"x")
        await self._settle()
        self.assertEqual(len(self.tg.photos), 4)


class WaStateNoticeTest(WaAppBase):
    async def test_logged_out_is_reported_once_with_a_promise_of_a_new_qr(self):
        self.app._wa_state("keluar", "")
        await self._settle()
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("keluar dari \"Perangkat tertaut\"", self.tg.sent[0][1])

    async def test_logged_out_is_silent_when_the_owner_asked_for_the_relink(self):
        self.app._wa_relinking = True
        self.app._qr_sent = 2
        self.app._wa_state("keluar", "")
        await self._settle()
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(self.app._qr_sent, 0)

    async def test_scanning_the_qr_is_confirmed_once(self):
        self.app._qr_sent = 1
        self.app._wa_state("tersambung", NOMOR)
        self.app._wa_state("tersambung", NOMOR)
        await self._settle()
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn(NOMOR, self.tg.sent[0][1])
        self.assertEqual(self.app._qr_sent, 0)

    async def test_routine_reconnects_say_nothing(self):
        for state in ("mulai", "qr", "terputus", "tersambung"):
            self.app._wa_state(state, NOMOR)
        await self._settle()
        self.assertEqual(self.tg.sent, [])


class DiagnosaShowsWhatsappStateTest(WaAppBase):
    async def _diagnosa(self):
        with mock.patch.object(main.node, "installed", return_value=False), \
             mock.patch.object(main, "wa_prerequisites", return_value=("C:/node.exe", True)), \
             mock.patch.object(main.updater, "installed_version", return_value="abcdef1234567"), \
             mock.patch.object(main.dashboard, "_logs", return_value=""):
            return await self.app._diagnosa()

    async def test_diagnosa_includes_the_connection_state(self):
        self.app.wa = FakeWa("tersambung", NOMOR)
        self.assertIn("Bot: menyala - FAKE:tersambung", await self._diagnosa())

    async def test_diagnosa_says_when_no_whatsapp_message_has_arrived_yet(self):
        self.app.wa = FakeWa("tersambung", NOMOR)
        out = await self._diagnosa()
        self.assertIn("belum ada sejak It's Me menyala", out)
        self.assertIn("Pesan ke diri sendiri", out)

    async def test_an_incoming_whatsapp_message_leaves_proof_but_no_content(self):
        self.app.wa = FakeWa("tersambung", NOMOR)
        with mock.patch.object(self.app, "_run_wa", new=mock.AsyncMock()), \
             self.assertLogs("itsme", level="INFO") as cm:
            logging.getLogger("itsme").disabled = False
            self.app._wa_inbox("6281234567890@s.whatsapp.net", "rahasia banget", False, None)
            await self._settle()
        logs = "\n".join(cm.output)
        self.assertIn("pesan masuk (pemilik, 14 karakter)", logs)
        self.assertNotIn("rahasia", logs)  # isi pesan tak pernah masuk log
        self.assertNotIn("6281234567890", logs)  # nomor pengirim juga tidak
        out = await self._diagnosa()
        self.assertIn("Pesan WhatsApp terakhir masuk:", out)
        self.assertIn("0 menit lalu", out)

    async def test_empty_whatsapp_events_do_not_count_as_messages(self):
        self.app.wa = FakeWa("tersambung", NOMOR)
        with mock.patch.object(self.app, "_run_wa", new=mock.AsyncMock()):
            self.app._wa_inbox("x@s.whatsapp.net", "   ", False, None)
            await self._settle()
        self.assertIsNone(self.app._wa_last_in)


if __name__ == "__main__":
    unittest.main()
