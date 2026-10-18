"""Uji It's Me yang mengikuti pemimpin It's Me Node (tanpa node sungguhan)."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from test_main import FakeTelegram

from itsme import config, main, node

ME, OTHER = "aaa111", "bbb222"


def _status(leader=ME, phase="running"):
    return {
        "me": ME,
        "leader": leader,
        "phase": phase,
        "nodes": [{"id": ME, "name": "PC-Utama"}, {"id": OTHER, "name": "Laptop"}],
    }


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FollowerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        for patch in (
            mock.patch.object(config, "FOLLOW_NODE", True),
            mock.patch.object(node, "installed", return_value=True),
            mock.patch.object(node.time, "monotonic", self.clock),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.status = mock.patch.object(node, "status").start()
        self.addCleanup(mock.patch.stopall)
        self.f = node.Follower()

    def test_without_node_always_active(self):
        with mock.patch.object(node, "installed", return_value=False):
            f = node.Follower()
        self.assertFalse(f.enabled)
        self.assertEqual(f.decide(node.STANDBY), node.ACTIVE)
        self.assertEqual(f.first_role(), node.ACTIVE)
        with mock.patch.object(config, "FOLLOW_NODE", False):
            self.assertFalse(node.Follower().enabled)

    def test_node_installed_while_running_is_noticed(self):
        with mock.patch.object(node, "installed", return_value=False):
            f = node.Follower()
        self.status.return_value = _status(leader=OTHER)
        with mock.patch.object(node, "installed", return_value=True):
            self.assertEqual(f.decide(node.ACTIVE), node.ACTIVE)  # belum waktunya periksa lagi
            self.clock.now += node.RECHECK + 1
            self.assertEqual(f.decide(node.ACTIVE), node.ACTIVE)  # baru tahu, belum mundur
            self.assertTrue(f.enabled)
            self.clock.now += node.STEP_DOWN_AFTER + 1
            self.assertEqual(f.decide(node.ACTIVE), node.STANDBY)

    def test_active_only_on_leader(self):
        self.status.return_value = _status(leader=ME)
        self.assertEqual(self.f.decide(node.STANDBY), node.ACTIVE)
        self.status.return_value = _status(leader=OTHER)
        self.assertEqual(self.f.decide(node.STANDBY), node.STANDBY)
        self.assertEqual(self.f.leader, "Laptop")

    def test_standby_while_electing(self):
        self.status.return_value = _status(leader=None, phase="electing")
        self.assertEqual(self.f.decide(node.STANDBY), node.STANDBY)

    def test_active_bot_steps_down_only_after_a_while(self):
        self.status.return_value = _status(leader=OTHER)
        self.assertEqual(self.f.decide(node.ACTIVE), node.ACTIVE)
        self.clock.now += node.STEP_DOWN_AFTER - 1
        self.assertEqual(self.f.decide(node.ACTIVE), node.ACTIVE)
        self.clock.now += 2
        self.assertEqual(self.f.decide(node.ACTIVE), node.STANDBY)

    def test_short_blip_does_not_step_down(self):
        self.status.return_value = _status(leader=OTHER)
        self.f.decide(node.ACTIVE)
        self.clock.now += 5
        self.status.return_value = _status(leader=ME)
        self.assertEqual(self.f.decide(node.ACTIVE), node.ACTIVE)
        self.status.return_value = _status(leader=OTHER)
        self.clock.now += node.STEP_DOWN_AFTER - 1
        self.assertEqual(self.f.decide(node.ACTIVE), node.ACTIVE)  # hitungan mulai lagi

    def test_unreachable_node_keeps_role_then_fails_open(self):
        self.status.return_value = None
        self.assertEqual(self.f.decide(node.ACTIVE), node.ACTIVE)
        self.assertEqual(self.f.decide(node.STANDBY), node.STANDBY)
        self.clock.now += node.GRACE + 1
        self.assertEqual(self.f.decide(node.STANDBY), node.ACTIVE)  # bot tidak ikut mati

    def test_first_role_waits_for_election(self):
        self.status.side_effect = [None, _status(leader=None, phase="electing"), _status(leader=OTHER)]
        with mock.patch.object(node.time, "sleep"):
            self.assertEqual(self.f.first_role(), node.STANDBY)
        self.assertEqual(self.status.call_count, 3)

    def test_first_role_fails_open_without_answer(self):
        self.status.return_value = None

        def sleep(seconds):
            self.clock.now += seconds

        with mock.patch.object(node.time, "sleep", sleep):
            self.assertEqual(self.f.first_role(wait=10), node.ACTIVE)


class StatusTest(unittest.TestCase):
    def test_reads_local_node_api(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(_status()).encode()
                self.send_response(200 if self.path == "/status" else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with mock.patch.object(config, "NODE_URL", f"http://127.0.0.1:{srv.server_port}"):
            self.assertEqual(node.status()["leader"], ME)
        srv.shutdown()
        srv.server_close()
        with mock.patch.object(config, "NODE_URL", f"http://127.0.0.1:{srv.server_port}"):
            self.assertIsNone(node.status(timeout=1))


class AppFollowsLeaderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for patch in (
            mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "AUTO_UPDATE", False),
            mock.patch.object(config, "FOLLOW_NODE", True),
            mock.patch.object(node, "installed", return_value=True),
            mock.patch.object(main, "Telegram", FakeTelegram),
            mock.patch.object(main, "_NODE_CHECK_EVERY", 0.02),
            mock.patch.object(main.dashboard, "start"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.tg = self.app.tg
        self.polls = 0
        get_updates = self.tg.get_updates

        async def counting_get_updates():
            self.polls += 1
            return await get_updates()

        self.tg.get_updates = counting_get_updates

    async def test_standby_never_polls_telegram_then_takes_over(self):
        roles = iter([node.STANDBY, node.STANDBY, node.ACTIVE])
        with mock.patch.object(node, "status", return_value=_status(leader=OTHER)), mock.patch.object(
            node.Follower, "decide", side_effect=lambda current: next(roles)
        ):
            code = await asyncio.wait_for(self.app.run(), 5)
        self.assertEqual(code, main.EXIT_UPDATED)  # menyala ulang sebagai bot aktif
        self.assertEqual(self.polls, 0)
        self.assertEqual(self.tg.sent, [])
        with open(os.path.join(self.dir, "update_notice.txt"), encoding="utf-8") as f:
            self.assertIn("aktif di komputer", f.read())

    async def test_active_bot_finishes_work_then_steps_down(self):
        self.tg.incoming.append([{"update_id": 1, "message": {"chat": {"id": 7}, "from": {"id": 42}, "text": "halo"}}])
        stepped = threading.Event()

        def slow_agent(messages, ctx):
            time.sleep(0.3)
            return "Halo Tuan"

        def decide(current):
            if self.polls >= 1:  # pesan pertama sudah diambil, lalu pemimpin pindah
                stepped.set()
                return node.STANDBY
            return node.ACTIVE

        with mock.patch.object(node, "status", return_value=_status(leader=ME)), mock.patch.object(
            node.Follower, "decide", side_effect=decide
        ), mock.patch.object(main, "run_agent", side_effect=slow_agent), mock.patch.object(
            self.tg, "ack_updates", mock.AsyncMock()
        ) as ack:
            code = await asyncio.wait_for(self.app.run(), 5)
        self.assertTrue(stepped.is_set())
        self.assertEqual(code, main.EXIT_UPDATED)
        self.assertIn("Halo Tuan", [t for _, t, _ in self.tg.sent])  # pekerjaan selesai dulu
        ack.assert_awaited()


if __name__ == "__main__":
    unittest.main()
