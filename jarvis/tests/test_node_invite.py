"""Uji ingress gabung node tanpa Tailscale: dashboard publik meneruskan tukar
undangan / sidik jari ke It's Me Node di komputer ini, lalu menambah token jembatan."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import config, dashboard, hp, memory


class FakeApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.started = datetime.now()
        self.loop = None


class NodeInviteIngressTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "l.log")),
                  mock.patch.object(config, "DASHBOARD_ENABLED", True),
                  mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
                  mock.patch.object(config, "DASHBOARD_PORT", 0),
                  mock.patch.object(config, "DASHBOARD_PASSWORD", ""),
                  mock.patch.object(config, "HP_SECRET", ""),
                  mock.patch.object(config, "AGENT_MODE", False)):
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.start(FakeApp(self.dir))
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_invite_forwarded_and_bridge_token_added(self):
        node_reply = (200, json.dumps({"join_code": "JC-123", "from": "Hub",
                                       "env": {"TELEGRAM_BOT_TOKEN": "t"}}).encode())
        with mock.patch.object(dashboard, "_node_post", return_value=node_reply) as post:
            r = httpx.post(self.base + "/api/node/invite",
                           json={"code": "ABCD-EFGH-JKMN", "name": "PC-2"}, timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["join_code"], "JC-123")
        self.assertEqual(body["env"], {"TELEGRAM_BOT_TOKEN": "t"})
        # Token jembatan ditambahkan dashboard supaya komputer baru bisa menyambung balik.
        self.assertEqual(body["bridge_token"], hp.token())
        # Diteruskan ke node lokal lewat /invite dengan kode & nama.
        self.assertEqual(post.call_args[0][0], "/invite")
        self.assertEqual(post.call_args[0][1], {"code": "ABCD-EFGH-JKMN", "name": "PC-2"})

    def test_enroll_forwarded_to_node(self):
        node_reply = (200, json.dumps({"join_code": "JC", "from": "Hub", "env": {}}).encode())
        with mock.patch.object(dashboard, "_node_post", return_value=node_reply) as post:
            r = httpx.post(self.base + "/api/node/enroll",
                           json={"fingerprint": "ab12", "name": "PC-2"}, timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(post.call_args[0][0], "/enroll")
        self.assertIn("bridge_token", r.json())

    def test_bad_invite_passthrough(self):
        with mock.patch.object(dashboard, "_node_post",
                               return_value=(404, b'{"error":"kode salah"}')):
            r = httpx.post(self.base + "/api/node/invite",
                           json={"code": "X", "name": "Y"}, timeout=5)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("bridge_token", r.json())

    def test_empty_code_rejected(self):
        r = httpx.post(self.base + "/api/node/invite", json={"name": "Y"}, timeout=5)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
