"""Uji pemeriksa & penyimpan API key yang dikirim lewat /kunci (tanpa jaringan)."""

import importlib.util
import os
import shutil
import tempfile
import unittest
from unittest import mock

import anthropic
import httpx

from itsme import config, keys

CLAUDE = "sk-ant-api03-" + "x" * 40 + "a1b2"
OPENROUTER = "sk-or-v1-" + "y" * 40 + "c3d4"
ACCOUNT = "sk-ant-oat01-" + "t" * 40 + "e5f6"
DEEPSEEK = "nsk_" + "d" * 32 + "a1b2"
GITHUB = "ghp_" + "z" * 32 + "a1b2"
GITHUB_FG = "github_pat_" + "A" * 40 + "c3d4"


class DetectTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(keys.detect(CLAUDE), "claude")
        self.assertEqual(keys.detect(OPENROUTER), "openrouter")
        self.assertEqual(keys.detect(ACCOUNT), "claude_code")
        self.assertEqual(keys.detect(DEEPSEEK), "deepseek")
        self.assertEqual(keys.detect(GITHUB), "github")
        self.assertEqual(keys.detect(GITHUB_FG), "github")
        self.assertEqual(keys.mask(ACCOUNT), "sk-ant-oat…e5f6")
        self.assertEqual(keys.mask(DEEPSEEK), "nsk…a1b2")
        self.assertEqual(keys.mask(GITHUB), "ghp_…a1b2")
        self.assertEqual(keys.mask(GITHUB_FG), "github_pat_…c3d4")
        self.assertIsNone(keys.detect("sk-lain-" + "z" * 40))
        self.assertIsNone(keys.detect("sk-ant-pendek"))

    def test_looks_like_key(self):
        self.assertTrue(keys.looks_like_key(DEEPSEEK))
        self.assertTrue(keys.looks_like_key("sk-" + "z" * 40))  # key resmi DeepSeek
        self.assertFalse(keys.looks_like_key("sk-ant-pendek"))  # terlalu pendek
        self.assertFalse(keys.looks_like_key(DEEPSEEK + " #"))  # ada spasi/#

    def test_characters_that_could_break_env_refused(self):
        self.assertIsNone(keys.detect(CLAUDE + "\nITSME_BACKEND=claude"))
        self.assertIsNone(keys.detect(CLAUDE + " #"))

    def test_mask_hides_the_middle(self):
        self.assertEqual(keys.mask(CLAUDE), "sk-ant-…a1b2")
        self.assertEqual(keys.mask(OPENROUTER), "sk-or-…c3d4")


class SaveTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.env = os.path.join(folder, ".env")
        for patch in (
            mock.patch.object(keys, "ENV_FILE", self.env),
            mock.patch.object(config, "ANTHROPIC_API_KEY", None),
            mock.patch.object(config, "OPENROUTER_API_KEY", None),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _env(self):
        with open(self.env, encoding="utf-8") as f:
            return f.read()

    def test_commented_line_replaced_in_place(self):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\n# ANTHROPIC_API_KEY=\nOWNER_TELEGRAM_ID=42\n")
        keys.save("claude", CLAUDE)
        self.assertEqual(self._env(), f"TELEGRAM_BOT_TOKEN=t\nANTHROPIC_API_KEY={CLAUDE}\nOWNER_TELEGRAM_ID=42\n")
        self.assertEqual(config.ANTHROPIC_API_KEY, CLAUDE)

    def test_old_key_replaced(self):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("OPENROUTER_API_KEY=sk-or-lama\n")
        keys.save("openrouter", OPENROUTER)
        self.assertEqual(self._env(), f"OPENROUTER_API_KEY={OPENROUTER}\n")
        self.assertEqual(config.OPENROUTER_API_KEY, OPENROUTER)

    def test_value_saved_by_another_process_is_picked_up(self):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write(f"CLAUDE_CODE_OAUTH_TOKEN={ACCOUNT}\n")
        with mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", None):
            keys.refresh_from_env("CLAUDE_CODE_OAUTH_TOKEN")
            self.assertEqual(config.CLAUDE_CODE_OAUTH_TOKEN, ACCOUNT)

    def test_appended_when_missing(self):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t")
        keys.save("claude", CLAUDE)
        self.assertEqual(self._env(), f"TELEGRAM_BOT_TOKEN=t\nANTHROPIC_API_KEY={CLAUDE}\n")

    def test_deepseek_key_saved(self):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\n")
        with mock.patch.object(config, "DEEPSEEK_API_KEY", None):
            keys.save("deepseek", DEEPSEEK)
            self.assertEqual(config.DEEPSEEK_API_KEY, DEEPSEEK)
        self.assertIn(f"DEEPSEEK_API_KEY={DEEPSEEK}", self._env())


class VerifyTest(unittest.TestCase):
    def _anthropic_error(self, cls, status):
        response = httpx.Response(status, request=httpx.Request("GET", "https://api.anthropic.com/v1/models"))
        return cls("ditolak", response=response, body=None)

    def test_anthropic_key_rejected(self):
        client = mock.Mock()
        client.models.list.side_effect = self._anthropic_error(anthropic.AuthenticationError, 401)
        with mock.patch.object(anthropic, "Anthropic", return_value=client):
            with self.assertRaises(keys.KeyRejected):
                keys.verify("claude", CLAUDE)

    def test_anthropic_key_accepted_without_calling_a_model(self):
        client = mock.Mock()
        with mock.patch.object(anthropic, "Anthropic", return_value=client) as make:
            keys.verify("claude", CLAUDE)
        self.assertEqual(make.call_args.kwargs["api_key"], CLAUDE)
        client.models.list.assert_called_once()
        client.messages.create.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("claude_agent_sdk"), "claude-agent-sdk tidak terpasang")
    def test_account_token_checked_with_claude_code(self):
        from itsme import brain_claude_code

        with mock.patch.object(brain_claude_code, "check_login", return_value="rejected"):
            with self.assertRaises(keys.KeyRejected):
                keys.verify("claude_code", ACCOUNT)
        with mock.patch.object(brain_claude_code, "check_login", return_value="ok") as check:
            keys.verify("claude_code", ACCOUNT)
        check.assert_called_once_with(ACCOUNT)

    def test_openrouter_key_rejected(self):
        with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=401)):
            with self.assertRaises(keys.KeyRejected):
                keys.verify("openrouter", OPENROUTER)

    def test_openrouter_key_accepted(self):
        with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=200)) as get:
            keys.verify("openrouter", OPENROUTER)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {OPENROUTER}")

    def test_deepseek_key_rejected(self):
        with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=401)):
            with self.assertRaises(keys.KeyRejected):
                keys.verify("deepseek", DEEPSEEK)

    def test_deepseek_key_accepted_hits_models_endpoint(self):
        resp = mock.Mock(status_code=200)
        with mock.patch.object(config, "DEEPSEEK_URL", "https://api.deepseek.com/v1"):
            with mock.patch.object(keys.httpx, "get", return_value=resp) as get:
                keys.verify("deepseek", DEEPSEEK)
        self.assertEqual(get.call_args.args[0], "https://api.deepseek.com/v1/models")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {DEEPSEEK}")

    def test_deepseek_nsk_key_verifies_against_nusagate(self):
        # Tanpa DEEPSEEK_URL dipaksa, key nsk- diperiksa ke NusaGate otomatis.
        with mock.patch.object(config, "DEEPSEEK_URL", ""):
            with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=200)) as get:
                keys.verify("deepseek", DEEPSEEK)  # DEEPSEEK diawali nsk_
        self.assertEqual(get.call_args.args[0], "https://api.nusagate.com/v1/models")

    def test_github_token_rejected(self):
        with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=401)):
            with self.assertRaises(keys.KeyRejected):
                keys.verify("github", GITHUB)

    def test_github_token_accepted_hits_user_endpoint(self):
        with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=200)) as get:
            keys.verify("github", GITHUB)
        self.assertEqual(get.call_args.args[0], "https://api.github.com/user")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {GITHUB}")


class ClaudeClientTest(unittest.TestCase):
    def test_new_key_used_without_restart(self):
        from itsme import brain_claude

        with mock.patch.object(config, "ANTHROPIC_API_KEY", CLAUDE), mock.patch.object(brain_claude, "_client", None):
            first = brain_claude._anthropic()
            self.assertIs(brain_claude._anthropic(), first)
            config.ANTHROPIC_API_KEY = "sk-ant-api03-" + "b" * 44
            self.assertIsNot(brain_claude._anthropic(), first)


if __name__ == "__main__":
    unittest.main()
