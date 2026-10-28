"""Uji /pasang: undangan sekali pakai untuk memasang It's Me di komputer lain.

Tanpa Tailscale: komputer baru menukar kodenya di dashboard Hub lalu menyambung
keluar ke sana (lihat dashboard /api/node/invite dan inode/bridge.py).
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import test_main

from itsme import config, node


class InviteHelpersTest(unittest.TestCase):
    def test_code_is_readable_and_random(self):
        a, b = node.new_invite_code(), node.new_invite_code()
        self.assertRegex(a, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertNotEqual(a, b)
        self.assertFalse(set(a) & set("01OIL"))

    def test_env_skips_machine_specific_values(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# komentar\nTELEGRAM_BOT_TOKEN=123:abc\nOWNER_TELEGRAM_ID=42\n"
                    "OPENROUTER_API_KEY=sk-or-x\nWEBSITE_REPO_DIR=C:\\web\nOLLAMA_URL=http://x\n"
                    "ITSME_FOLLOW_NODE=0\nITSME_BACKEND=\n")
        env = node.invite_env(path)
        self.assertEqual(env, {"TELEGRAM_BOT_TOKEN": "123:abc", "OWNER_TELEGRAM_ID": "42",
                               "OPENROUTER_API_KEY": "sk-or-x"})

    def test_install_command_carries_hub(self):
        cmd = node.install_command("ABCD-EFGH-JKMN", "https://hub.example")
        self.assertEqual(
            cmd,
            "$env:ITSME_INVITE='ABCD-EFGH-JKMN'; $env:ITSME_HUB='https://hub.example'; "
            "irm " + node.INSTALL_URL + " | iex",
        )

    def test_hub_public_url_from_config(self):
        with mock.patch.object(config, "PUBLIC_URL", "https://a.example/"), \
             mock.patch.object(config, "DASHBOARD_DOMAINS", ""):
            self.assertEqual(node.hub_public_url(), "https://a.example")
        with mock.patch.object(config, "PUBLIC_URL", ""), \
             mock.patch.object(config, "DASHBOARD_DOMAINS", "b.example, c.example"):
            self.assertEqual(node.hub_public_url(), "https://b.example")
        with mock.patch.object(config, "PUBLIC_URL", ""), \
             mock.patch.object(config, "DASHBOARD_DOMAINS", ""):
            self.assertEqual(node.hub_public_url(), "")


class PasangCommandTest(unittest.IsolatedAsyncioTestCase):
    # Persiapan yang sama dengan uji bot utama, tanpa mewarisi uji-ujinya.
    asyncSetUp = test_main.AppTest.asyncSetUp
    _msg = test_main.AppTest._msg

    async def test_without_node_explains_first_step(self):
        with mock.patch.object(node, "installed", return_value=False):
            await self.app._handle_message(self._msg("/pasang"))
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("belum terpasang", self.tg.sent[0][1])
        self.assertIn(node.INSTALL_URL, self.tg.sent[0][1])

    async def test_without_public_url_asks_for_domain(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "hub_public_url", return_value=""), \
             mock.patch.object(node, "create_invite") as create:
            await self.app._handle_message(self._msg("/pasang"))
        create.assert_not_called()
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("ITSME_PUBLIC_URL", self.tg.sent[0][1])

    async def test_sends_one_line_command_with_registered_code(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "hub_public_url", return_value="https://hub.example"), \
             mock.patch.object(node, "invite_env", return_value={"TELEGRAM_BOT_TOKEN": "t"}), \
             mock.patch.object(node, "create_invite", return_value=True) as create:
            await self.app._handle_message(self._msg("/pasang"))
        code, env = create.call_args[0][:2]
        self.assertEqual(env, {"TELEGRAM_BOT_TOKEN": "t"})
        self.assertEqual(len(self.tg.sent), 2)
        self.assertIn("TANPA Tailscale", self.tg.sent[0][1])
        self.assertEqual(self.tg.sent[1][1], node.install_command(code, "https://hub.example"))
        # Token tidak pernah tampil di chat.
        self.assertFalse(any("TELEGRAM_BOT_TOKEN" in s[1] for s in self.tg.sent))

    async def test_node_not_answering(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "hub_public_url", return_value="https://hub.example"), \
             mock.patch.object(node, "invite_env", return_value={}), \
             mock.patch.object(node, "create_invite", return_value=False):
            await self.app._handle_message(self._msg("/pasang"))
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("belum menjawab", self.tg.sent[0][1])

    async def test_only_owner(self):
        msg = self._msg("/pasang")
        msg["from"]["id"] = 99
        with mock.patch.object(node, "create_invite") as create:
            await self.app._handle_message(msg)
        create.assert_not_called()
        self.assertEqual(self.tg.sent, [])

    async def test_pasang_is_one_time_not_reusable(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "hub_public_url", return_value="https://hub.example"), \
             mock.patch.object(node, "invite_env", return_value={}), \
             mock.patch.object(node, "create_invite", return_value=True) as create:
            await self.app._handle_message(self._msg("/pasang"))
        # (code, env, ttl, timeout, reusable)
        self.assertEqual(create.call_args[0][2], node.INVITE_TTL)
        self.assertEqual(create.call_args[0][4], False)
        self.assertIn("sekali", self.tg.sent[0][1])

    async def test_pasang_tetap_creates_reusable_invite(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "hub_public_url", return_value="https://hub.example"), \
             mock.patch.object(node, "invite_env", return_value={"TELEGRAM_BOT_TOKEN": "t"}), \
             mock.patch.object(node, "create_invite", return_value=True) as create:
            await self.app._handle_message(self._msg("/pasang_tetap"))
        code, env, ttl, timeout, reusable = create.call_args[0]
        self.assertEqual(env, {"TELEGRAM_BOT_TOKEN": "t"})
        self.assertEqual(ttl, node.REUSABLE_INVITE_TTL)
        self.assertTrue(reusable)
        self.assertIn("BERKALI-KALI", self.tg.sent[0][1])
        self.assertIn("Siapa pun yang punya tautan ini", self.tg.sent[0][1])  # peringatan risiko
        self.assertEqual(self.tg.sent[1][1], node.install_command(code, "https://hub.example"))

    async def test_pasang_tetap_only_owner(self):
        msg = self._msg("/pasang_tetap")
        msg["from"]["id"] = 99
        with mock.patch.object(node, "create_invite") as create:
            await self.app._handle_message(msg)
        create.assert_not_called()
        self.assertEqual(self.tg.sent, [])


if __name__ == "__main__":
    unittest.main()
