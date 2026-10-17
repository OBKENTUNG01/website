"""Uji otak "Claude di komputer" (/claudepc) tanpa memanggil Claude Code."""

import asyncio
import importlib.util
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from itsme import brain_claude_code as cc
from itsme import config

TOKEN = "sk-ant-oat01-" + "t" * 40
HAS_SDK = importlib.util.find_spec("claude_agent_sdk") is not None


class DecideTest(unittest.TestCase):
    """Aturan alat Claude Code sama dengan otak lain."""

    def setUp(self):
        self.site = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.site, ignore_errors=True)
        self.confirm = mock.Mock(return_value=True)
        self.ctx = {"confirm": self.confirm, "website_dir": self.site}

    def decide(self, name, inp):
        return cc.decide(name, inp, self.ctx, self.site)

    def test_search_and_listing_run_directly(self):
        for name, inp in (("WebSearch", {"query": "berita"}), ("Glob", {"pattern": "*.php"})):
            self.assertEqual(self.decide(name, inp), (True, None))
        self.confirm.assert_not_called()

    def test_read_only_command_runs_directly(self):
        self.assertEqual(self.decide("Bash", {"command": "git status"}), (True, None))
        self.confirm.assert_not_called()

    def test_other_command_needs_owner(self):
        self.confirm.return_value = False
        allowed, reason = self.decide("PowerShell", {"command": "Remove-Item x", "description": "hapus"})
        self.assertFalse(allowed)
        self.assertIn("Dibatalkan", reason)
        text = self.confirm.call_args[0][0]
        self.assertIn("Remove-Item x", text)
        self.assertIn("hapus", text)

    def test_secret_file_refused_without_asking(self):
        for name in ("Read", "Write", "Grep"):
            allowed, reason = self.decide(name, {"file_path": os.path.join(self.site, ".env"), "content": "x"})
            self.assertFalse(allowed)
            self.assertIn("rahasia", reason)
        self.confirm.assert_not_called()

    def test_read_inside_site_direct_outside_asks(self):
        self.assertEqual(self.decide("Read", {"file_path": os.path.join(self.site, "index.php")}), (True, None))
        self.confirm.assert_not_called()
        outside = os.path.join(os.path.dirname(self.site), "lain.txt")
        self.assertEqual(self.decide("Read", {"file_path": outside}), (True, None))
        self.assertIn(outside, self.confirm.call_args[0][0])

    def test_write_shows_new_content(self):
        self.decide("Write", {"file_path": os.path.join(self.site, "a.html"), "content": "<h1>Halo</h1>"})
        self.assertIn("<h1>Halo</h1>", self.confirm.call_args[0][0])

    def test_unknown_tool_asks(self):
        self.confirm.return_value = False
        allowed, _ = self.decide("mcp__lain__hapus_semua", {"x": 1})
        self.assertFalse(allowed)
        self.assertIn("mcp__lain__hapus_semua", self.confirm.call_args[0][0])

    def test_hook_answers_in_claude_code_format(self):
        self.confirm.return_value = False
        hook = cc._hook(self.ctx, self.site)
        out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}, "t1", {}))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


def _result(**extra):
    base = dict(is_error=False, result="Halo Tuan", session_id="sesi-1", api_error_status=None, subtype="success")
    base.update(extra)
    return SimpleNamespace(**base)


@unittest.skipUnless(HAS_SDK, "claude-agent-sdk tidak terpasang")
class RunAgentTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        for patch in (
            mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", TOKEN),
            mock.patch.object(config, "ANTHROPIC_API_KEY", "sk-ant-api03-milik-claude-api"),
            mock.patch.object(config, "DATA_DIR", self.data),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.ctx = {"confirm": mock.Mock(return_value=True), "website_dir": None, "memory": mock.Mock()}
        self.ctx["memory"].text.return_value = "suka kopi"

    def test_reply_and_session_continue(self):
        messages = [{"role": "user", "content": "halo"}]
        collect = mock.AsyncMock(return_value=_result())
        with mock.patch.object(cc, "_collect", collect):
            self.assertEqual(cc.run_agent(messages, self.ctx), "Halo Tuan")
            prompt, options = collect.call_args[0]
            self.assertEqual(prompt, "halo")
            self.assertIsNone(options.resume)
            self.assertIn("suka kopi", options.system_prompt["append"])
            # Akun Claude dipakai, bukan API key; rahasia lain tidak ikut.
            self.assertEqual(options.env["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)
            self.assertEqual(options.env["ANTHROPIC_API_KEY"], "")
            self.assertEqual(options.env["TELEGRAM_BOT_TOKEN"], "")
            self.assertIn("PreToolUse", options.hooks)

            messages.append({"role": "user", "content": "lanjut"})
            cc.run_agent(messages, self.ctx)
            self.assertEqual(collect.call_args[0][1].resume, "sesi-1")
        self.assertEqual(messages[-1]["session_id"], "sesi-1")

    def test_rejected_token_explains_login(self):
        result = _result(is_error=True, result="Failed to authenticate. API Error: 401", api_error_status=401)
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=result)):
            reply = cc.run_agent([{"role": "user", "content": "halo"}], self.ctx)
        self.assertIn("ditolak", reply)
        self.assertIn("itsme.claude_login", reply)

    def test_without_token_explains_login(self):
        collect = mock.AsyncMock()
        with mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", None), mock.patch.object(cc, "_collect", collect):
            reply = cc.run_agent([{"role": "user", "content": "halo"}], self.ctx)
        self.assertIn("itsme.claude_login", reply)
        collect.assert_not_called()

    def test_check_login(self):
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=_result(result="OK"))):
            self.assertEqual(cc.check_login(TOKEN), "ok")
        rejected = _result(is_error=True, result="401 OAuth access token is invalid.", api_error_status=401)
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=rejected)) as collect:
            self.assertEqual(cc.check_login(TOKEN), "rejected")
        self.assertEqual(collect.call_args[0][1].env["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)


if __name__ == "__main__":
    unittest.main()
