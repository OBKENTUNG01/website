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
NSK_A = "nsk_" + "a" * 32


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

    def test_deepseek_key_is_added_to_the_pool_not_replacing_others(self):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\n")
        second = "nsk_" + "e" * 32
        with mock.patch.object(config, "DEEPSEEK_API_KEY", None), \
             mock.patch.object(config, "DEEPSEEK_API_KEYS", []):
            keys.save("deepseek", DEEPSEEK)
            keys.save("deepseek", second)
            keys.save("deepseek", DEEPSEEK)  # kirim ulang key yang sama: tidak dobel
            self.assertEqual(config.deepseek_keys(), [DEEPSEEK, second])
        self.assertIn(f"DEEPSEEK_API_KEYS={DEEPSEEK},{second}", self._env())


def _nsk(letter, n=32):
    return "nsk_" + letter * n


class PoolStoreTest(unittest.TestCase):
    """Kumpulan key di .env: banyak sekaligus, tanpa duplikat, bisa dikeluarkan satu per satu."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.env = os.path.join(folder, ".env")
        with open(self.env, "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=t\n")
        for patch in (
            mock.patch.object(keys, "ENV_FILE", self.env),
            mock.patch.object(config, "DEEPSEEK_API_KEY", None),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", []),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _env(self):
        with open(self.env, encoding="utf-8") as f:
            return f.read()

    def test_many_keys_are_stored_in_one_write_in_order(self):
        batch = [_nsk(c) for c in "abcdefghij"]
        added, existing = keys.pool_add_many(batch)
        self.assertEqual(added, batch)
        self.assertEqual(existing, [])
        self.assertEqual(config.deepseek_keys(), batch)
        self.assertEqual(self._env().count("DEEPSEEK_API_KEYS="), 1)
        self.assertIn("DEEPSEEK_API_KEYS=" + ",".join(batch), self._env())
        self.assertIn("TELEGRAM_BOT_TOKEN=t", self._env())  # isi lain tidak rusak

    def test_duplicates_inside_the_batch_and_against_the_pool_are_not_added_twice(self):
        keys.pool_add_many([_nsk("a")])
        added, existing = keys.pool_add_many([_nsk("b"), _nsk("a"), _nsk("b")])
        self.assertEqual(added, [_nsk("b")])
        self.assertEqual(existing, [_nsk("a"), _nsk("b")])
        self.assertEqual(config.deepseek_keys(), [_nsk("a"), _nsk("b")])

    def test_nothing_new_leaves_the_env_untouched(self):
        keys.pool_add_many([_nsk("a")])
        before = self._env()
        self.assertEqual(keys.pool_add_many([_nsk("a")]), ([], [_nsk("a")]))
        self.assertEqual(self._env(), before)

    def test_old_single_key_still_counts_and_is_not_duplicated(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEY", _nsk("z")):
            added, existing = keys.pool_add_many([_nsk("z"), _nsk("a")])
            self.assertEqual((added, existing), ([_nsk("a")], [_nsk("z")]))
            self.assertEqual(config.deepseek_keys(), [_nsk("z"), _nsk("a")])

    def test_remove_one_key_keeps_the_rest(self):
        keys.pool_add_many([_nsk("a"), _nsk("b"), _nsk("c")])
        self.assertTrue(keys.pool_remove(_nsk("b")))
        self.assertEqual(config.deepseek_keys(), [_nsk("a"), _nsk("c")])
        self.assertIn(f"DEEPSEEK_API_KEYS={_nsk('a')},{_nsk('c')}", self._env())
        self.assertNotIn(_nsk("b"), self._env())

    def test_remove_the_old_single_key(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEY", _nsk("z")):
            keys._set_env(self.env, "DEEPSEEK_API_KEY", _nsk("z"))
            keys.pool_add_many([_nsk("a")])
            self.assertTrue(keys.pool_remove(_nsk("z")))
            self.assertEqual(config.deepseek_keys(), [_nsk("a")])
            self.assertNotIn(_nsk("z"), self._env())

    def test_removing_an_unknown_key_changes_nothing(self):
        keys.pool_add_many([_nsk("a")])
        before = self._env()
        self.assertFalse(keys.pool_remove(_nsk("q")))
        self.assertEqual(self._env(), before)

    def test_removing_the_last_key_empties_the_pool(self):
        keys.pool_add_many([_nsk("a")])
        keys.pool_remove(_nsk("a"))
        self.assertEqual(config.deepseek_keys(), [])
        self.assertNotIn(_nsk("a"), self._env())

    def test_models_are_stored_without_duplicates(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", []):
            keys.set_models(["m-a", "m-b", "m-a"])
            self.assertEqual(config.NUSAGATE_MODELS, ["m-a", "m-b"])
            self.assertIn("ITSME_NUSAGATE_MODELS=m-a,m-b", self._env())
            keys.set_models([])
            self.assertEqual(config.NUSAGATE_MODELS, [])


class ParseTokensTest(unittest.TestCase):
    def test_separators_and_the_command_word(self):
        a, b, c = _nsk("a"), _nsk("b"), _nsk("c")
        for text in (f"/kunci {a} {b} {c}", f"/kunci\n{a}\n{b}\n{c}", f"/kunci {a},{b};{c}", f"{a}\n\n{b}  {c}"):
            with self.subTest(text=text):
                self.assertEqual(keys.parse_tokens(text), [a, b, c])

    def test_quotes_brackets_and_names_are_removed(self):
        a = _nsk("a")
        self.assertEqual(keys.parse_tokens(f"/kunci <{a}>"), [a])
        self.assertEqual(keys.parse_tokens(f'/kunci "{a}"'), [a])
        self.assertEqual(keys.parse_tokens(f"/kunci `{a}`"), [a])
        self.assertEqual(keys.parse_tokens(f"DEEPSEEK_API_KEY={a}"), [a])
        self.assertEqual(keys.parse_tokens(f"DEEPSEEK_API_KEYS='{a},{_nsk('b')}'"), [a, _nsk("b")])

    def test_duplicates_collapse_and_order_is_kept(self):
        a, b = _nsk("a"), _nsk("b")
        self.assertEqual(keys.parse_tokens(f"{a} {b} {a}"), [a, b])

    def test_command_with_nothing_after_it(self):
        self.assertEqual(keys.parse_tokens("/kunci"), [])
        self.assertEqual(keys.parse_tokens("   "), [])
        self.assertEqual(keys.parse_tokens(None), [])

    def test_only_keys_accepts_bare_keys(self):
        self.assertTrue(keys.only_keys(_nsk("a")))
        self.assertTrue(keys.only_keys(f"{_nsk('a')}\n{_nsk('b')}\n{_nsk('c')}"))
        self.assertTrue(keys.only_keys(f"  {OPENROUTER} "))
        self.assertTrue(keys.only_keys("sk-" + "q" * 40))  # key resmi DeepSeek/OpenAI

    def test_only_keys_also_catches_a_mistyped_command_word(self):
        # Salah ketik perintah tidak boleh membuat key terkirim ke AI sebagai pesan biasa.
        a, b = _nsk("a"), _nsk("b")
        for text in (f"/kunci {a}", f"/kunc {a}", f"/kunci_ {a}", f"/kunci-nsk {a} {b}", f"/key\n{a}\n{b}", f"/KUNCI {a}"):
            with self.subTest(text=text):
                self.assertTrue(keys.only_keys(text))

    def test_only_keys_leaves_real_commands_with_ordinary_arguments_alone(self):
        for text in (
            "/pakai Laptop", "/kunci_hapus 2", "/layar KantorLantai2", "/wa 6283125645146",
            "/model_pakai deepseek-v4-1-flash-preview-0925",  # panjang & hanya huruf/angka/-, tapi bukan key
            "/wa_info Toko buka setiap hari", "/kunci_daftar", "/kunci", "/otak",
        ):
            with self.subTest(text=text):
                self.assertFalse(keys.only_keys(text))

    def test_only_keys_rejects_ordinary_messages(self):
        for text in (
            "halo, tolong cekkan harga emas hari ini",
            f"tolong simpan key ini {_nsk('a')}",  # ada kata lain: bukan pemasangan, tetap pesan biasa
            f"/kunci {_nsk('a')} tolong",  # kata lain di dalamnya: diurus jalur /kunci (kata asing dilaporkan)
            "",
            "pneumonoultramicroscopicsilicovolcanoconiosis",  # satu kata panjang tapi bukan key
            "https://example.com/halaman/panjang-sekali-sekali",
        ):
            with self.subTest(text=text):
                self.assertFalse(keys.only_keys(text))


class SecretDetectionTest(unittest.TestCase):
    """Dipakai WhatsApp: pesan berisi key tidak boleh diproses (key hanya lewat Telegram)."""

    def test_every_known_key_shape_is_caught_anywhere_in_a_message(self):
        samples = [
            DEEPSEEK, CLAUDE, ACCOUNT, OPENROUTER, GITHUB, GITHUB_FG,
            "sk-" + "p" * 40, "AIza" + "S" * 35, "xoxb-" + "1" * 12 + "-abcdef",
            "AKIA" + "ABCDEFGHIJKLMNOP", "-----BEGIN PRIVATE KEY-----",
        ]
        for secret in samples:
            for template in ("{}", "ini keyku: {} tolong dipasang", "tulis\n{}\nsudah"):
                with self.subTest(secret=secret[:10], template=template):
                    self.assertTrue(keys.contains_secret(template.format(secret)))

    def test_install_commands_with_a_payload_are_caught_even_with_an_odd_key(self):
        for text in ("/kunci apaaja", "/deepseek xyz", "/KUNCI abc", "/kunci@itsme_bot abc", "  /key abc"):
            with self.subTest(text=text):
                self.assertTrue(keys.contains_secret(text))

    def test_commands_without_a_payload_or_other_commands_are_not_secrets(self):
        for text in ("/kunci", "/deepseek", "/kunci_daftar", "/kunci_hapus 2", "/claude apa kabar", "/cloud", "/model_daftar"):
            with self.subTest(text=text):
                self.assertFalse(keys.contains_secret(text))

    def test_a_bare_random_token_is_caught(self):
        self.assertTrue(keys.contains_secret("aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5"))

    def test_ordinary_messages_are_not_secrets(self):
        for text in (
            "halo, apa kabar?",
            "Tolong jelaskan apa itu API key dan bagaimana cara kerjanya",
            "Pesananku nomor INV20241018000123456789012345678 belum sampai",  # nomor panjang huruf besar + angka
            "5d41402abc4b2a76b9719d911017c592ae0f6e2c6e0f9bd1f1c0f4a1b2c3d4e5",  # hash heksadesimal
            "https://example.com/produk/baju-koko-lengan-panjang-warna-putih",
            "sk",
            "task-1234",
            "Besok jam 8 ya",
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertFalse(keys.contains_secret(text))

    def test_scrub_hides_keys_inside_foreign_text(self):
        text = f"Incorrect API key provided: {DEEPSEEK}. Also {GITHUB} was used."
        cleaned = keys.scrub(text)
        self.assertNotIn(DEEPSEEK, cleaned)
        self.assertNotIn(GITHUB, cleaned)
        self.assertIn("Incorrect API key provided", cleaned)
        self.assertEqual(keys.scrub("biasa saja"), "biasa saja")
        self.assertEqual(keys.scrub(None), "")


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

    def test_gateway_models_reads_the_openai_shape(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"object": "list", "data": [{"id": "glm-5.3"}, {"id": "deepseek-v4.1-flash"},
                                                              {"id": "glm-5.3"}, {"nama": "bukan-id"}, 123, None,
                                                              {"id": ""}, {"id": 7}]}
        with mock.patch.object(keys.httpx, "get", return_value=resp) as get:
            names = keys.gateway_models(NSK_A)
        self.assertEqual(names, ["deepseek-v4.1-flash", "glm-5.3"])  # terurut, tanpa dobel
        self.assertEqual(get.call_args.args[0], "https://api.nusagate.com/v1/models")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {NSK_A}")

    def test_gateway_models_also_reads_other_reasonable_shapes(self):
        for payload, expected in (
            (["b", "a"], ["a", "b"]),
            ({"models": [{"id": "x"}, {"model": "y"}, {"name": "z"}]}, ["x", "y", "z"]),
            ({"data": ["m1", " m2 "]}, ["m1", "m2"]),
            ({"data": []}, []),
            ({"data": None, "models": [{"id": "q"}]}, ["q"]),
            ({}, []),
            (None, []),
            ("bukan daftar", []),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(keys._model_ids(payload), expected)

    def test_gateway_models_rejects_a_revoked_key_and_reports_other_http_errors(self):
        with mock.patch.object(keys.httpx, "get", return_value=mock.Mock(status_code=401)):
            with self.assertRaises(keys.KeyRejected):
                keys.gateway_models(NSK_A)
        broken = mock.Mock(status_code=500)
        broken.raise_for_status.side_effect = httpx.HTTPStatusError("x", request=mock.Mock(), response=broken)
        with mock.patch.object(keys.httpx, "get", return_value=broken):
            with self.assertRaises(httpx.HTTPStatusError):
                keys.gateway_models(NSK_A)

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
