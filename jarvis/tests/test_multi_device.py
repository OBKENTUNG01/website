"""Uji perintah lintas perangkat: antrean Hub, alur /pakai di Telegram, dan
perangkat yang mengerjakan tugas serta menyamakan diri dengan Hub."""

import asyncio
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import access, brain, config, dashboard, devices, main, memory
from test_main import FakeTelegram


class _TempData(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        p = mock.patch.object(config, "DATA_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(devices._queues.clear)
        self.addCleanup(devices._waiters.clear)


class QueueTest(_TempData):
    def test_enqueue_take_resolve(self):
        task_id, fut = devices.enqueue("Laptop", "halo")
        task = devices.take("laptop", wait=0)            # tak peduli huruf
        self.assertEqual(task, {"id": task_id, "text": "halo"})
        self.assertIsNone(devices.take("laptop", wait=0))  # sudah diambil
        self.assertTrue(devices.resolve(task_id, "jawab"))
        self.assertEqual(fut.result(timeout=1), "jawab")
        self.assertFalse(devices.resolve(task_id, "lagi"))  # tak ada yang menunggu

    def test_take_waits_for_new_task(self):
        threading.Timer(0.2, devices.enqueue, args=("PC", "nanti")).start()
        start = time.monotonic()
        task = devices.take("PC", wait=3)
        self.assertEqual(task["text"], "nanti")
        self.assertLess(time.monotonic() - start, 2)

    def test_take_times_out(self):
        self.assertIsNone(devices.take("PC", wait=0.1))

    def test_stop_waiting_keeps_task(self):
        task_id, _ = devices.enqueue("PC", "x")
        devices.stop_waiting(task_id)
        self.assertFalse(devices.resolve(task_id, "telat"))
        self.assertIsNotNone(devices.take("PC", wait=0))  # tetap dikerjakan

    def test_find_exact_and_unique_prefix(self):
        devices.register("Laptop", "u")
        devices.register("Lemari", "u")
        devices.register("PC-Kantor", "u")
        self.assertEqual(devices.find("laptop")["name"], "Laptop")
        self.assertEqual(devices.find("pc")["name"], "PC-Kantor")
        self.assertIsNone(devices.find("l"))       # awalan ganda
        self.assertIsNone(devices.find("tidakada"))

    def test_target_persists_and_forget_clears_it(self):
        devices.register("Laptop", "u")
        devices.set_target("Laptop")
        self.assertEqual(devices.get_target(), "Laptop")
        devices.forget("laptop")
        self.assertIsNone(devices.get_target())


class HubMemoryTest(_TempData):
    def test_changes_are_sent_to_hub(self):
        mem = devices.HubMemory(os.path.join(self.dir, "m.md"))
        with mock.patch.object(devices, "_hub_post") as post:
            mem.add("suka kopi")
            mem.replace_from_hub("# Memori It's Me\n- dari hub")
        self.assertEqual(post.call_count, 1)  # replace_from_hub tidak dikirim balik
        self.assertEqual(post.call_args[0][1], {"action": "add", "text": "suka kopi"})
        self.assertIn("dari hub", mem.text())


class FakeHubApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.histories = {}
        self.loop = None
        self.started = datetime.now()
        self.tg = None
        self.asked = []


class HubEndpointsTest(_TempData):
    def setUp(self):
        super().setUp()
        for p in (
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "DASHBOARD_ENABLED", True),
            mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
            mock.patch.object(config, "DASHBOARD_PORT", 0),
            mock.patch.object(config, "HUB_SECRET", "s3cr3t"),
            mock.patch.object(config, "AGENT_MODE", False),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        open(config.LOG_FILE, "w").close()
        self.app = FakeHubApp(self.dir)
        self.server = dashboard.start(self.app)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def post(self, path, body, secret="s3cr3t"):
        return httpx.post(
            self.base + path, json={"name": "Laptop", "secret": secret, **body},
            headers={"X-Itsme-Dashboard": "1"}, timeout=10,
        )

    def test_all_device_paths_need_secret(self):
        for p in ("poll", "result", "message", "confirm", "memory", "sync"):
            self.assertEqual(self.post(f"/api/devices/{p}", {}, secret="salah").status_code, 403, p)

    def test_poll_registers_and_returns_task(self):
        task_id, fut = devices.enqueue("Laptop", "kerjakan")
        with mock.patch.object(dashboard, "_notify_owner"):
            r = self.post("/api/devices/poll", {"wait": 1}).json()
        self.assertEqual(r["task"]["id"], task_id)
        self.assertTrue(r["sync"])
        self.assertEqual(devices.find("laptop")["name"], "Laptop")

    def test_poll_empty_after_wait(self):
        with mock.patch.object(dashboard, "_notify_owner"):
            r = self.post("/api/devices/poll", {"wait": 0.2}).json()
        self.assertIsNone(r["task"])

    def test_result_resolves_waiter(self):
        task_id, fut = devices.enqueue("Laptop", "x")
        self.post("/api/devices/result", {"id": task_id, "text": "selesai"})
        self.assertEqual(fut.result(timeout=1), "selesai")

    def test_late_result_goes_to_owner(self):
        with mock.patch.object(dashboard, "_notify_owner") as notify:
            self.post("/api/devices/result", {"id": "kadaluarsa", "text": "telat"})
        self.assertIn("telat", notify.call_args[0][1])

    def test_confirm_asks_owner(self):
        with mock.patch.object(dashboard, "_ask_owner", return_value=True) as ask:
            r = self.post("/api/devices/confirm", {"text": "Hapus berkas?"}).json()
        self.assertTrue(r["yes"])
        self.assertIn("Laptop", ask.call_args[0][1])

    def test_memory_add_lands_on_hub_and_changes_sync(self):
        before = self.post("/api/devices/sync", {}).json()
        self.post("/api/devices/memory", {"action": "add", "text": "suka teh"})
        after = self.post("/api/devices/sync", {}).json()
        self.assertIn("suka teh", self.app.memory.text())
        self.assertIn("suka teh", after["memory"])
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual(after["brain"], "ollama")

    def test_target_from_dashboard(self):
        devices.register("Laptop", "u")
        r = httpx.post(self.base + "/api/devices/target", json={"name": "lap"},
                       headers={"X-Itsme-Dashboard": "1"}, timeout=5).json()
        self.assertEqual(r["target"], "Laptop")

    def test_device_refuses_hub_paths(self):
        with mock.patch.object(config, "AGENT_MODE", True):
            self.assertEqual(self.post("/api/devices/sync", {}).status_code, 404)


class HubTelegramTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for patch in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "AGENT_MODE", False),
            mock.patch.object(main, "Telegram", FakeTelegram),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(devices._queues.clear)
        self.addCleanup(devices._waiters.clear)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()
        self.tg = self.app.tg

    def _msg(self, text):
        return {"chat": {"id": 7}, "from": {"id": 42}, "text": text}

    async def test_perangkat_lists_devices(self):
        await self.app._handle_message(self._msg("/perangkat"))
        self.assertIn("Belum ada", self.tg.sent[-1][1])
        devices.register("Laptop", "u")
        await self.app._handle_message(self._msg("/perangkat"))
        self.assertIn("🟢 Laptop", self.tg.sent[-1][1])

    async def test_pakai_unknown_and_back(self):
        await self.app._handle_message(self._msg("/pakai Nope"))
        self.assertIn("tidak ditemukan", self.tg.sent[-1][1])
        devices.register("Laptop", "u")
        await self.app._handle_message(self._msg("/pakai laptop"))
        self.assertEqual(devices.get_target(), "Laptop")
        await self.app._handle_message(self._msg("/pakai"))
        self.assertIsNone(devices.get_target())

    async def test_message_forwarded_and_reply_relayed(self):
        devices.register("Laptop", "u")
        devices.set_target("Laptop")

        def device_side():
            task = devices.take("Laptop", wait=3)
            devices.resolve(task["id"], "Dari laptop: " + task["text"])

        threading.Thread(target=device_side).start()
        with mock.patch.object(main, "run_agent") as local:
            await self.app._handle_message(self._msg("cek disk"))
        local.assert_not_called()  # tidak dikerjakan di Hub
        self.assertEqual(self.tg.sent[-1][1], "🖥️ Laptop:\nDari laptop: cek disk")

    async def test_hub_only_commands_stay_local(self):
        devices.register("Laptop", "u")
        devices.set_target("Laptop")
        await self.app._handle_message(self._msg("/memori"))
        self.assertIsNone(devices.take("Laptop", wait=0))
        self.assertIn("Memori", self.tg.sent[-1][1])

    async def test_alamat_stays_local_even_with_device_target(self):
        devices.register("Laptop", "u")
        devices.set_target("Laptop")
        with mock.patch.object(config, "QUICK_TUNNEL", False), \
             mock.patch.object(config, "PUBLIC_URL", ""):
            await self.app._handle_message(self._msg("/alamat"))
        self.assertIsNone(devices.take("Laptop", wait=0))  # tidak diteruskan ke Laptop
        self.assertIn("quick-tunnel.ps1", self.tg.sent[-1][1])  # dijawab Hub sendiri

    async def test_attachment_not_forwarded(self):
        devices.register("Laptop", "u")
        devices.set_target("Laptop")
        msg = {"chat": {"id": 7}, "from": {"id": 42}, "photo": [{"file_id": "f", "file_unique_id": "u"}]}
        await self.app._handle_message(msg)
        self.assertIn("belum bisa diteruskan", self.tg.sent[-1][1])
        self.assertIsNone(devices.take("Laptop", wait=0))


class DeviceAppTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for patch in (
            mock.patch.object(config, "OWNER_ID", 0),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "AGENT_MODE", True),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()

    async def test_no_telegram_and_hub_memory(self):
        self.assertIsNone(self.app.tg)
        self.assertIsInstance(self.app.memory, devices.HubMemory)

    async def test_task_answered_through_hub(self):
        with mock.patch.object(main, "run_agent", return_value="beres"), \
             mock.patch.object(devices, "send_result") as send:
            await self.app._handle_remote({"id": "t1", "text": "tolong"})
        send.assert_called_once_with("t1", "beres")

    async def test_confirm_goes_to_hub(self):
        with mock.patch.object(devices, "ask_confirm", return_value=True) as ask:
            self.assertTrue(self.app._confirm_blocking(0, "Jalankan?"))
        ask.assert_called_once_with("Jalankan?")

    async def test_reset_command(self):
        self.app.histories[0] = [{"role": "user", "content": "x"}]
        with mock.patch.object(devices, "send_result") as send:
            await self.app._handle_remote({"id": "t", "text": "/reset"})
        self.assertNotIn(0, self.app.histories)
        self.assertIn("direset", send.call_args[0][1])

    async def test_sync_copies_memory_and_access(self):
        bundle = {"memory": "# Memori It's Me\n- dari hub", "brain": "ollama", "full_access": True, "version": "v1"}
        with mock.patch.object(devices, "fetch_sync", return_value=bundle):
            version = await self.app._sync_from_hub()
        self.assertEqual(version, "v1")
        self.assertIn("dari hub", self.app.memory.text())
        self.assertTrue(access.full())

    async def test_sync_reports_unavailable_brain(self):
        bundle = {"memory": "", "brain": "claude", "full_access": False, "version": "v2"}
        with mock.patch.object(config, "ANTHROPIC_API_KEY", None), \
             mock.patch.object(devices, "fetch_sync", return_value=bundle), \
             mock.patch.object(devices, "send_message") as msg:
            await self.app._sync_from_hub()
        self.assertEqual(brain.current_backend(), "ollama")
        self.assertIn("belum bisa dipakai", msg.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
