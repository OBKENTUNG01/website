"""Uji perintah /komputer dan /layar (akses komputer lain lewat node)."""

import asyncio
import logging
import os
import shutil
import tempfile
import unittest
from unittest import mock

from test_main import FakeTelegram

from itsme import config, main, node

NODES = [
    {"name": "PC-UTAMA", "id": "a", "leader": True, "bot": True},
    {"name": "Laptop", "id": "b", "leader": False, "bot": True},
]


class ComputerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "OWNER_ID", 42),
                  mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "m.md")),
                  mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "l.log")),
                  mock.patch.object(config, "FOLLOW_NODE", False),
                  mock.patch.object(config, "PASSPHRASE", ""),
                  mock.patch.object(main, "Telegram", FakeTelegram)):
            p.start()
            self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()
        self.tg = self.app.tg

    def _msg(self, text):
        return {"chat": {"id": 7}, "from": {"id": 42}, "text": text, "message_id": 1}

    async def test_komputer_lists_connected(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "computers", return_value=NODES):
            await self.app._handle_message(self._msg("/komputer"))
        out = self.tg.sent[-1][1]
        self.assertIn("PC-UTAMA", out)
        self.assertIn("Laptop", out)
        self.assertIn("/layar", out)

    async def test_komputer_without_node(self):
        with mock.patch.object(node, "installed", return_value=False):
            await self.app._handle_message(self._msg("/komputer"))
        self.assertIn("belum terpasang", self.tg.sent[-1][1])

    async def test_layar_sends_photo_of_named_computer(self):
        png = b"\x89PNG\r\n\x1a\nFAKE"
        shot = mock.Mock(return_value=png)
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "resolve", return_value=NODES[1]), \
             mock.patch.object(node, "screenshot", shot):
            await self.app._handle_message(self._msg("/layar Laptop"))
        self.assertEqual(shot.call_args[0][0], "b")          # id Laptop diminta
        self.assertTrue(self.tg.photos)
        chat, data, _, caption = self.tg.photos[-1]
        self.assertEqual((chat, data), (7, png))
        self.assertIn("Laptop", caption)

    async def test_layar_this_computer_when_no_name(self):
        shot = mock.Mock(return_value=b"\x89PNG\r\n\x1a\n")
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "screenshot", shot):
            await self.app._handle_message(self._msg("/layar"))
        self.assertIsNone(shot.call_args[0][0])              # node_id None = komputer ini
        self.assertTrue(self.tg.photos)

    async def test_layar_unknown_name(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "resolve", return_value=None):
            await self.app._handle_message(self._msg("/layar Tablet"))
        self.assertIn("tidak ketemu", self.tg.sent[-1][1])
        self.assertFalse(getattr(self.tg, "photos", []))

    async def test_layar_capture_fails(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "resolve", return_value=NODES[1]), \
             mock.patch.object(node, "screenshot", return_value=None):
            await self.app._handle_message(self._msg("/layar Laptop"))
        self.assertIn("tidak bisa diambil", self.tg.sent[-1][1])


class NodeHelperTest(unittest.TestCase):
    def test_resolve_prefix_and_ambiguous(self):
        with mock.patch.object(node, "computers", return_value=NODES):
            self.assertEqual(node.resolve("laptop")["id"], "b")
            self.assertEqual(node.resolve("PC-")["id"], "a")
            self.assertIsNone(node.resolve(""))
            with mock.patch.object(node, "computers", return_value=[
                    {"name": "PC1", "id": "x"}, {"name": "PC2", "id": "y"}]):
                self.assertIsNone(node.resolve("PC"))        # dua cocok -> tak jelas

    def test_screenshot_builds_url_and_reads_png(self):
        png = b"\x89PNG\r\n\x1a\n"

        class R:
            headers = {"Content-Type": "image/png"}
            def read(self): return png
            def __enter__(self): return self
            def __exit__(self, *a): return False
        seen = {}
        def fake_urlopen(url, timeout=0):
            seen["url"] = url
            return R()
        with mock.patch.object(node.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(node.screenshot("b", w=800), png)
            self.assertIn("node=b", seen["url"])
            self.assertIn("w=800", seen["url"])


if __name__ == "__main__":
    unittest.main()
