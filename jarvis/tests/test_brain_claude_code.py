"""Uji otak "Claude di komputer" (/claudepc) tanpa memanggil Claude Code."""

import asyncio
import importlib.util
import json
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

    def test_search_of_whole_home_or_appdata_refused(self):
        for path in (os.path.expanduser("~"), os.path.join(os.path.expanduser("~"), "AppData")):
            allowed, reason = self.decide("Grep", {"pattern": "token", "path": path})
            self.assertFalse(allowed, path)
            self.assertIn("terlalu luas", reason)
        self.confirm.assert_not_called()

    def test_full_access_acts_without_buttons_and_reports(self):
        notes = []
        self.ctx.update(full_access=True, notify=notes.append)
        self.confirm.return_value = False
        outside = os.path.join(os.path.dirname(self.site), "kerjaan", "laporan.txt")
        self.assertEqual(self.decide("Read", {"file_path": outside}), (True, None))
        self.assertEqual(self.decide("PowerShell", {"command": "New-Item -Type Directory baru"}), (True, None))
        self.assertEqual(self.decide("Write", {"file_path": outside, "content": "isi"}), (True, None))
        self.confirm.assert_not_called()
        self.assertEqual(len(notes), 2)  # perintah & tulis dikabari; membaca tidak
        self.assertIn("New-Item", notes[0])
        # Rahasia tetap dijaga di akses penuh.
        self.assertFalse(self.decide("Read", {"file_path": os.path.join(self.site, ".env")})[0])
        allowed, _ = self.decide("PowerShell", {"command": f"Get-Content {os.path.join(self.site, '.env')}"})
        self.assertFalse(allowed)  # ditanyakan, dan pemilik menjawab Batal
        self.confirm.assert_called_once()

    def test_owner_work_listed_for_claude(self):
        from itsme import persona

        appdata = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, appdata, ignore_errors=True)
        sessions = os.path.join(appdata, "Claude", "claude-code-sessions", "a", "b")
        os.makedirs(os.path.join(sessions, "deleted_x"))
        sample = {"title": "Website toko", "cwd": r"C:\proyek\toko", "lastActivityAt": 1792046672876, "isArchived": False}
        for folder, title in ((sessions, "Website toko"), (os.path.join(sessions, "deleted_x"), "Sudah dihapus")):
            with open(os.path.join(folder, "local_1.json" if title == "Website toko" else "local_2.json"), "w", encoding="utf-8") as f:
                json.dump(dict(sample, title=title), f)
        with mock.patch.dict(os.environ, {"APPDATA": appdata, "LOCALAPPDATA": appdata}):
            text = persona.claude_code_prompt("")
        self.assertIn(os.path.join(os.path.expanduser("~"), ".claude", "projects"), text)
        self.assertIn(r"Website toko — C:\proyek\toko", text)
        self.assertNotIn("Sudah dihapus", text)

    def test_archive_command_always_asks_even_in_full_access(self):
        notes = []
        self.ctx.update(full_access=True, notify=notes.append)
        self.confirm.return_value = False
        allowed, _ = self.decide("PowerShell", {"command": "Compress-Archive -Path proyek -DestinationPath p.zip"})
        self.assertFalse(allowed)  # nomor 5: zip/ekstrak tetap minta tombol
        self.confirm.assert_called_once()
        self.assertEqual(notes, [])

    def test_drive_mcp_tools_allowed_without_asking(self):
        # Alat Drive lewat MCP dibolehkan hook; persetujuan diurus di dalam alat.
        self.confirm.return_value = False
        for name in ("drive_cari", "drive_unggah", "drive_kelola"):
            self.assertEqual(self.decide(f"mcp__itsme_drive__{name}", {"x": 1}), (True, None))
        self.confirm.assert_not_called()

    def test_drive_mcp_handler_calls_tool(self):
        handler = cc._drive_handler("drive_cari", {"ctx": "milik-pemilik"})
        with mock.patch.object(cc.tools, "handle_drive_tool", return_value="📄 laporan [id: 1]") as h:
            out = asyncio.run(handler({"kata_kunci": "laporan"}))
        h.assert_called_once_with("drive_cari", {"kata_kunci": "laporan"}, {"ctx": "milik-pemilik"})
        self.assertEqual(out["content"][0]["text"], "📄 laporan [id: 1]")

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

    def test_flagged_session_retried_in_fresh_session(self):
        flagged = _result(is_error=True, result="API Error: Opus 5.5's safeguards flagged this session "
                          "(https://www.anthropic.com/legal/aup).")
        messages = [
            {"role": "user", "content": "lama"},
            {"role": "assistant", "content": "ok", "session_id": "sesi-lama"},
            {"role": "user", "content": "mana hasilnya?"},
        ]
        collect = mock.AsyncMock(side_effect=[flagged, _result(session_id="sesi-baru")])
        with mock.patch.object(cc, "_collect", collect):
            self.assertEqual(cc.run_agent(messages, self.ctx), "Halo Tuan")
        first, second = (call[0][1] for call in collect.call_args_list)
        self.assertEqual(first.resume, "sesi-lama")
        self.assertIsNone(second.resume)
        self.assertEqual(collect.call_args_list[1][0][0], "mana hasilnya?")
        # Konteks lama dibuang; giliran berikutnya melanjutkan sesi baru.
        self.assertEqual([m["content"] for m in messages], ["mana hasilnya?", "Halo Tuan"])
        self.assertEqual(messages[-1]["session_id"], "sesi-baru")

    def test_flagged_again_resets_context_and_explains(self):
        flagged = _result(is_error=True, result="API Error: Opus 5.5's safeguards flagged this session")
        messages = [
            {"role": "assistant", "content": "ok", "session_id": "sesi-lama"},
            {"role": "user", "content": "lanjut"},
        ]
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=flagged)) as collect:
            reply = cc.run_agent(messages, self.ctx)
        self.assertEqual(reply, cc.FLAGGED_REPLY)
        self.assertNotIn("API Error", reply)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(messages, [])

    def test_flagged_new_session_not_retried(self):
        flagged = _result(is_error=True, result="safeguards flagged this session")
        messages = [{"role": "user", "content": "halo"}]
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=flagged)) as collect:
            self.assertEqual(cc.run_agent(messages, self.ctx), cc.FLAGGED_REPLY)
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(messages, [])

    def test_other_errors_keep_context(self):
        messages = [
            {"role": "assistant", "content": "ok", "session_id": "sesi-lama"},
            {"role": "user", "content": "lanjut"},
        ]
        result = _result(is_error=True, result="Overloaded")
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=result)) as collect:
            self.assertIn("Overloaded", cc.run_agent(messages, self.ctx))
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(len(messages), 2)

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

    def test_drive_tools_wired_when_connected(self):
        from itsme import drive

        collect = mock.AsyncMock(return_value=_result())
        with mock.patch.object(cc, "_collect", collect), mock.patch.object(drive, "ready", return_value=True):
            cc.run_agent([{"role": "user", "content": "cari laporan di drive"}], self.ctx)
        opts = collect.call_args[0][1]
        self.assertIn("itsme_drive", opts.mcp_servers)
        self.assertIn("GOOGLE DRIVE", opts.system_prompt["append"])

    def test_no_drive_tools_when_not_connected(self):
        from itsme import drive

        collect = mock.AsyncMock(return_value=_result())
        with mock.patch.object(cc, "_collect", collect), mock.patch.object(drive, "ready", return_value=False):
            cc.run_agent([{"role": "user", "content": "halo"}], self.ctx)
        opts = collect.call_args[0][1]
        self.assertEqual(opts.mcp_servers, {})
        self.assertNotIn("GOOGLE DRIVE", opts.system_prompt["append"])

    def test_check_login(self):
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=_result(result="OK"))):
            self.assertEqual(cc.check_login(TOKEN), "ok")
        rejected = _result(is_error=True, result="401 OAuth access token is invalid.", api_error_status=401)
        with mock.patch.object(cc, "_collect", mock.AsyncMock(return_value=rejected)) as collect:
            self.assertEqual(cc.check_login(TOKEN), "rejected")
        self.assertEqual(collect.call_args[0][1].env["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)


if __name__ == "__main__":
    unittest.main()
