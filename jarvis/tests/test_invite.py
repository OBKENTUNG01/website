"""Uji /pasang: undangan sekali pakai untuk memasang It's Me di komputer lain."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import test_main

from itsme import config, keys, node


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
                    "OPENROUTER_API_KEY=sk-or-x\nWEBSITE_REPO_DIR=C:\web\nOLLAMA_URL=http://x\n"
                    "ITSME_FOLLOW_NODE=0\nITSME_BACKEND=\n")
        env = node.invite_env(path)
        self.assertEqual(env, {"TELEGRAM_BOT_TOKEN": "123:abc", "OWNER_TELEGRAM_ID": "42",
                               "OPENROUTER_API_KEY": "sk-or-x"})

    def test_install_command(self):
        cmd = node.install_command("ABCD-EFGH-JKMN")
        self.assertEqual(cmd, "$env:ITSME_INVITE='ABCD-EFGH-JKMN'; irm " + node.INSTALL_URL + " | iex")
        self.assertIn("'ABCD-EFGH-JKMN~tskey-auth-1'", node.install_command("ABCD-EFGH-JKMN", "tskey-auth-1"))

    def test_tailscale_key_kind(self):
        self.assertEqual(keys.detect("tskey-api-kAbc123CNTRL-xyz789abcdefghij"), "tailscale")
        self.assertIsNone(keys.detect("tskey-auth-kAbc123CNTRL-xyz789abcdefghij"))


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

    async def test_sends_one_line_command_with_registered_code(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "invite_env", return_value={"TELEGRAM_BOT_TOKEN": "t"}), \
             mock.patch.object(node, "create_invite", return_value=True) as create, \
             mock.patch.object(config, "TAILSCALE_API_KEY", ""):
            await self.app._handle_message(self._msg("/pasang"))
        code, env = create.call_args[0][:2]
        self.assertEqual(env, {"TELEGRAM_BOT_TOKEN": "t"})
        self.assertEqual(len(self.tg.sent), 2)
        self.assertIn("login Tailscale", self.tg.sent[0][1])
        self.assertEqual(self.tg.sent[1][1], node.install_command(code))
        # Token tidak pernah tampil di chat.
        self.assertFalse(any("TELEGRAM_BOT_TOKEN" in s[1] for s in self.tg.sent))

    async def test_tailscale_key_makes_it_login_free(self):
        with mock.patch.object(node, "installed", return_value=True), \
             mock.patch.object(node, "invite_env", return_value={}), \
             mock.patch.object(node, "create_invite", return_value=True) as create, \
             mock.patch.object(node, "tailscale_auth_key", return_value="tskey-auth-zz"), \
             mock.patch.object(config, "TAILSCALE_API_KEY", "tskey-api-x"):
            await self.app._handle_message(self._msg("/pasang"))
        code = create.call_args[0][0]
        self.assertIn("tersambung otomatis", self.tg.sent[0][1])
        self.assertEqual(self.tg.sent[1][1], node.install_command(code, "tskey-auth-zz"))

    async def test_node_not_answering(self):
        with mock.patch.object(node, "installed", return_value=True), \
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


if __name__ == "__main__":
    unittest.main()
