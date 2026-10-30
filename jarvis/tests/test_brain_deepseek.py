"""Uji otak DeepSeek (loop agen kompatibel OpenAI) tanpa jaringan sungguhan."""

import shutil
import tempfile
import unittest
from collections import Counter
from unittest import mock

import httpx

from itsme import brain_deepseek, config, keypool
from itsme.brain_openrouter import CloudUnavailable


class FakeMemory:
    def text(self):
        return ""


def _ctx():
    return {"memory": FakeMemory()}


def _resp(status=200, body=None, headers=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.headers = headers or {}
    return r


def _answer(text, finish="stop"):
    return _resp(body={"choices": [{"message": {"content": text}, "finish_reason": finish}]})


class DeepSeekAgentTest(unittest.TestCase):
    def setUp(self):
        keypool.reset_all()  # keadaan kumpulan key (ditolak/dibatasi) tidak boleh bocor antar uji
        self.addCleanup(keypool.reset_all)
        for p in (
            mock.patch.object(config, "DEEPSEEK_API_KEY", "nsk_" + "d" * 32),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", []),
            mock.patch.object(config, "NUSAGATE_MODELS", []),
            mock.patch.object(config, "DEEPSEEK_MODEL", "deepseek-chat"),
            mock.patch.object(config, "MAX_STEPS", 6),
            mock.patch.object(config, "DATA_DIR", tempfile.mkdtemp()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_direct_answer(self):
        messages = [{"role": "user", "content": "halo"}]
        with mock.patch.object(brain_deepseek._http, "post", return_value=_answer("hai!")) as post:
            reply = brain_deepseek.run_agent(messages, _ctx())
        self.assertEqual(reply, "hai!")
        # Alamat & model yang dipakai benar.
        self.assertTrue(post.call_args.args[0].endswith("/chat/completions"))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-chat")
        # Jawaban asisten masuk ke riwayat.
        self.assertEqual(messages[-1], {"role": "assistant", "content": "hai!"})

    def test_tool_call_then_answer(self):
        tool_turn = _resp(body={"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "t1", "function": {"name": "cari_web", "arguments": "{\"q\": \"x\"}"}}],
        }, "finish_reason": "tool_calls"}]})
        with mock.patch.object(brain_deepseek._http, "post", side_effect=[tool_turn, _answer("sudah")]):
            with mock.patch.object(brain_deepseek, "validate_input", return_value=None), \
                 mock.patch.object(brain_deepseek, "handle_tool", return_value="hasil-alat") as handle:
                messages = [{"role": "user", "content": "carikan x"}]
                reply = brain_deepseek.run_agent(messages, _ctx())
        self.assertEqual(reply, "sudah")
        handle.assert_called_once()
        self.assertEqual(handle.call_args.args[0], "cari_web")
        # Hasil alat terkirim balik sebagai pesan tool dengan id yang benar.
        tool_msg = next(m for m in messages if m.get("role") == "tool")
        self.assertEqual(tool_msg["tool_call_id"], "t1")
        self.assertEqual(tool_msg["content"], "hasil-alat")

    def test_rejected_key_raises(self):
        body = {"error": {"message": "invalid key"}}
        with mock.patch.object(brain_deepseek._http, "post", return_value=_resp(401, body)):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertFalse(cm.exception.transient)
        self.assertEqual(cm.exception.status, 401)

    def test_transient_error_retried(self):
        with mock.patch.object(brain_deepseek, "RETRY_WAITS", (0,)), \
             mock.patch.object(brain_deepseek.time, "sleep"):
            with mock.patch.object(brain_deepseek._http, "post",
                                   side_effect=[_resp(503, {"error": {"message": "busy"}}), _answer("akhirnya")]):
                reply = brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertEqual(reply, "akhirnya")

    def test_no_key_unavailable(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEY", None):
            with self.assertRaises(CloudUnavailable):
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())


def _keys(n):
    return [f"nsk_{i:02d}" + "z" * 28 + f"{i:04d}" for i in range(n)]


class FakeNusaGate:
    """Penyedia tiruan yang menjawab menurut KEY di header Authorization (dan mencatat tiap panggilan)."""

    def __init__(self, behaviour=None, default=None):
        self.behaviour = behaviour or {}  # key -> daftar jawaban berurutan (yang terakhir diulang)
        self.default = default or (lambda body: _answer("ok"))
        self.calls = []  # (key, model)

    def __call__(self, url, json=None, headers=None):
        key = headers["Authorization"].split(" ", 1)[1]
        self.calls.append((key, json.get("model")))
        plan = self.behaviour.get(key)
        if plan:
            item = plan.pop(0) if len(plan) > 1 else plan[0]
            # Mock juga "callable": jawaban siap pakai (Mock) dikembalikan apa adanya, fungsi dipanggil.
            return item if isinstance(item, mock.Mock) else item(json)
        return self.default(json)

    def used(self):
        return Counter(k for k, _ in self.calls)


def _limited(retry_after=30):
    return _resp(429, {"error": {"code": "rate_limit", "message": "terlalu cepat"}, "retry_after": retry_after})


class PoolTestBase(unittest.TestCase):
    N = 5

    def setUp(self):
        keypool.reset_all()
        self.addCleanup(keypool.reset_all)
        self.keys = _keys(self.N)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "DEEPSEEK_API_KEY", None),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", list(self.keys)),
            mock.patch.object(config, "NUSAGATE_MODELS", []),
            mock.patch.object(config, "DEEPSEEK_URL", ""),
            mock.patch.object(config, "DEEPSEEK_MODEL", ""),
            mock.patch.object(config, "MAX_STEPS", 6),
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(brain_deepseek.time, "sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def ask(self, gate, text="halo"):
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            return brain_deepseek.run_agent([{"role": "user", "content": text}], _ctx())


class MultiKeyTest(PoolTestBase):
    N = 10

    def test_ten_keys_all_work_and_none_is_overloaded(self):
        gate = FakeNusaGate()
        for _ in range(50):
            self.assertEqual(self.ask(gate), "ok")
        used = gate.used()
        self.assertEqual(len(used), 10)  # semua key dipakai
        self.assertEqual(set(used.values()), {5})  # dan rata
        self.assertEqual({r["uses"] for r in keypool.snapshot()}, {5})

    def test_a_rate_limited_key_is_replaced_in_the_same_request(self):
        gate = FakeNusaGate({self.keys[0]: [_limited(40)]})
        self.assertEqual(self.ask(gate), "ok")  # pemilik tidak melihat kegagalan
        self.assertEqual(gate.calls[0][0], self.keys[0])  # key pertama dicoba dulu...
        self.assertEqual(len(gate.calls), 2)  # ...lalu langsung key lain
        gate.calls.clear()
        for _ in range(9):
            self.ask(gate)
        self.assertNotIn(self.keys[0], gate.used())  # selama dibatasi tidak dicoba lagi

    def test_empty_balance_invalid_and_busy_keys_are_all_skipped_to_a_healthy_one(self):
        gate = FakeNusaGate({
            self.keys[0]: [_resp(402, {"error": {"code": "insufficient_balance", "message": "saldo"}})],
            self.keys[1]: [_resp(401, {"error": {"code": "invalid_api_key", "message": "x"}})],
            self.keys[2]: [_resp(503, {"error": {"code": "upstream_busy", "message": "x"}})],
            self.keys[3]: [_resp(429, {"error": {"code": "pass_window_cap", "message": "x"}})],
        })
        self.assertEqual(self.ask(gate), "ok")
        self.assertEqual([k for k, _ in gate.calls], self.keys[:5])  # berurutan sampai ketemu yang sehat
        status = {r["no"]: r["status"] for r in keypool.snapshot()}
        self.assertEqual([status[i] for i in (1, 2, 3, 4, 5)], ["balance", "invalid", "sibuk", "cap", "siap"])

    def test_all_keys_failing_gives_a_clear_message_not_a_crash(self):
        gate = FakeNusaGate(default=lambda body: _resp(402, {"error": {"code": "insufficient_balance", "message": "saldo"}}))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertIn("Saldo", str(cm.exception))
        self.assertEqual(len(gate.calls), 10)  # semua key dicoba tepat sekali
        # permintaan berikutnya: tidak lagi menghajar penyedia, langsung pesan yang jelas
        gate.calls.clear()
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertEqual(gate.calls, [])
        self.assertIn("10 key saldo habis", str(cm.exception))

    def test_all_rate_limited_briefly_waits_and_retries(self):
        plans = {k: [_limited(3), _answer("sudah lega")] for k in self.keys}
        gate = FakeNusaGate(plans)
        now = [1000.0]
        slept = []

        def fake_sleep(seconds):
            # Tidur tiruan: waktu maju, jadi key yang tadi dibatasi sudah pulih saat dicoba lagi.
            slept.append(seconds)
            now[0] += 60

        with mock.patch.object(keypool, "_clock", lambda: now[0]), \
             mock.patch.object(brain_deepseek.time, "sleep", side_effect=fake_sleep):
            self.assertEqual(self.ask(gate), "sudah lega")
        self.assertEqual(slept, [3.0])  # menunggu persis selama yang diminta penyedia (retry_after)

    def test_bad_request_is_not_the_keys_fault(self):
        gate = FakeNusaGate(default=lambda body: _resp(400, {"error": {"code": "validation_error", "message": "rusak"}}))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(len(gate.calls), 1)  # tidak menghabiskan 10 key untuk kesalahan yang sama
        self.assertEqual({r["status"] for r in keypool.snapshot()}, {"siap"})

    def test_a_too_long_conversation_says_so_and_does_not_blame_any_key(self):
        gate = FakeNusaGate(default=lambda body: _resp(400, {"error": {"code": "context_length_exceeded",
                                                                       "message": "terlalu panjang"}}))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertIn("/reset", str(cm.exception))
        self.assertFalse(cm.exception.transient)
        self.assertEqual(len(gate.calls), 1)  # satu percobaan: key lain akan gagal dengan alasan yang sama
        self.assertEqual({r["status"] for r in keypool.snapshot()}, {"siap"})

    def test_timeout_moves_on_to_the_next_key(self):
        def slow(body):
            raise httpx.ReadTimeout("lama")

        gate = FakeNusaGate({self.keys[0]: [slow]})
        self.assertEqual(self.ask(gate), "ok")
        self.assertEqual(len(gate.calls), 2)

    def test_connection_error_moves_on_to_the_next_key(self):
        def down(body):
            raise httpx.ConnectError("putus")

        gate = FakeNusaGate({self.keys[0]: [down]})
        self.assertEqual(self.ask(gate), "ok")

    def test_unexpected_exception_never_leaves_a_key_locked(self):
        def bug(body):
            raise ValueError("bug")

        gate = FakeNusaGate({self.keys[0]: [bug]})
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(ValueError):
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        self.assertEqual({r["inflight"] for r in keypool.snapshot()}, {0})

    def test_each_request_carries_the_right_key_and_nothing_else(self):
        gate = FakeNusaGate()
        self.ask(gate)
        seen = []
        with mock.patch.object(brain_deepseek._http, "post",
                               side_effect=lambda url, json=None, headers=None: seen.append((url, headers)) or _answer("x")):
            brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        url, headers = seen[0]
        self.assertEqual(url, "https://api.nusagate.com/v1/chat/completions")
        self.assertEqual(set(headers), {"Authorization"})

    def test_tool_conversation_keeps_working_across_rotating_keys(self):
        tool_turn = _resp(body={"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "t1", "function": {"name": "cari_web", "arguments": "{\"q\": \"x\"}"}}],
        }, "finish_reason": "tool_calls"}]})
        gate = FakeNusaGate(default=lambda body: tool_turn if not any(m.get("role") == "tool" for m in body["messages"]) else _answer("selesai"))
        with mock.patch.object(brain_deepseek, "validate_input", return_value=None), \
             mock.patch.object(brain_deepseek, "handle_tool", return_value="hasil"):
            self.assertEqual(self.ask(gate), "selesai")
        self.assertEqual(len({k for k, _ in gate.calls}), 2)  # dua langkah, dua key berbeda


class ModelRotationTest(PoolTestBase):
    N = 2

    def test_each_answer_uses_the_next_model(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b", "m-c"]):
            gate = FakeNusaGate()
            for _ in range(6):
                self.ask(gate)
        self.assertEqual(Counter(m for _, m in gate.calls), {"m-a": 2, "m-b": 2, "m-c": 2})

    def test_default_model_when_none_configured(self):
        gate = FakeNusaGate()
        self.ask(gate)
        self.assertEqual(gate.calls[0][1], "deepseek-v4.1-flash")

    def test_one_model_per_answer_even_with_tool_steps(self):
        tool_turn = _resp(body={"choices": [{"message": {
            "content": "", "tool_calls": [{"id": "t1", "function": {"name": "cari_web", "arguments": "{}"}}],
        }, "finish_reason": "tool_calls"}]})
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]), \
             mock.patch.object(brain_deepseek, "validate_input", return_value=None), \
             mock.patch.object(brain_deepseek, "handle_tool", return_value="hasil"):
            gate = FakeNusaGate(default=lambda body: tool_turn if not any(m.get("role") == "tool" for m in body["messages"]) else _answer("ok"))
            self.ask(gate)
        self.assertEqual(len({m for _, m in gate.calls}), 1)

    def test_a_model_the_provider_rejects_is_replaced_and_left_alone(self):
        def reject_a(body):
            if body["model"] == "m-a":
                return _resp(404, {"error": {"code": "model_not_found", "message": "tidak ada"}})
            return _answer("dari m-b")

        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]):
            gate = FakeNusaGate(default=reject_a)
            self.assertEqual(self.ask(gate), "dari m-b")  # dialihkan di permintaan yang sama
            gate.calls.clear()
            for _ in range(3):
                self.ask(gate)
        self.assertEqual({m for _, m in gate.calls}, {"m-b"})  # m-a ditinggalkan
        self.assertEqual({r["status"] for r in keypool.snapshot()}, {"siap"})  # key tidak disalahkan

    def test_tool_unsupported_model_is_replaced(self):
        def no_tools(body):
            if body["model"] == "m-a":
                return _resp(400, {"error": {"code": "validation_error", "message": "model does not support tools"}})
            return _answer("ok")

        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]):
            self.assertEqual(self.ask(FakeNusaGate(default=no_tools)), "ok")

    def test_official_deepseek_key_keeps_its_own_model(self):
        official = "sk-" + "o" * 40
        with mock.patch.object(config, "DEEPSEEK_API_KEYS", [official]), \
             mock.patch.object(config, "NUSAGATE_MODELS", ["m-a"]):
            gate = FakeNusaGate()
            self.ask(gate)
        self.assertEqual(gate.calls[0], (official, "deepseek-chat"))

    def test_no_model_error_when_every_model_is_rejected_falls_to_the_error(self):
        gate = FakeNusaGate(default=lambda body: _resp(404, {"error": {"code": "model_not_found", "message": "x"}}))
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a"]):
            with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
                with self.assertRaises(CloudUnavailable):
                    brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())


class GuestChatTest(PoolTestBase):
    N = 3

    def test_guest_gets_no_tools_no_memory_and_a_short_limit(self):
        seen = []

        def spy(body):
            seen.append(body)
            return _answer("halo juga")

        gate = FakeNusaGate(default=spy)
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            out = brain_deepseek.guest_chat("SISTEM-TAMU", [{"role": "user", "content": "halo"}])
        self.assertEqual(out, "halo juga")
        body = seen[0]
        self.assertNotIn("tools", body)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SISTEM-TAMU"})
        self.assertEqual(body["max_tokens"], brain_deepseek.GUEST_MAX_TOKENS)

    def test_guest_chat_also_spreads_over_keys_and_fails_over(self):
        gate = FakeNusaGate({self.keys[0]: [_limited()]})
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            for _ in range(6):
                brain_deepseek.guest_chat("S", [{"role": "user", "content": "x"}])
        self.assertEqual(set(gate.used()), set(self.keys))

    def test_guest_chat_uses_the_model_rotation(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]):
            gate = FakeNusaGate()
            with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
                for _ in range(4):
                    brain_deepseek.guest_chat("S", [{"role": "user", "content": "x"}])
        self.assertEqual(Counter(m for _, m in gate.calls), {"m-a": 2, "m-b": 2})

    def test_guest_chat_cleans_thinking_blocks(self):
        gate = FakeNusaGate(default=lambda body: _answer("<think>rahasia</think>Jawabannya 42"))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            self.assertEqual(brain_deepseek.guest_chat("S", [{"role": "user", "content": "x"}]), "Jawabannya 42")


class SecretsStayOutOfErrorsTest(PoolTestBase):
    N = 2

    def test_a_provider_that_echoes_the_key_does_not_leak_it_into_the_error(self):
        echo = lambda body: _resp(400, {"error": {"code": "validation_error",
                                                    "message": f"bad request for key {self.keys[0]} and {self.keys[1]}"}})
        gate = FakeNusaGate(default=echo)
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.run_agent([{"role": "user", "content": "x"}], _ctx())
        shown = str(cm.exception) + repr(getattr(cm.exception, "raw", ""))
        for key in self.keys:
            self.assertNotIn(key, shown)
        self.assertIn("bad request", shown)  # sisa pesannya tetap berguna

    def test_the_log_line_for_a_failed_guest_answer_carries_no_key(self):
        echo = lambda body: _resp(402, {"error": {"code": "insufficient_balance", "message": f"key {self.keys[0]} kosong"}})
        gate = FakeNusaGate(default=echo)
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            with self.assertRaises(CloudUnavailable) as cm:
                brain_deepseek.guest_chat("S", [{"role": "user", "content": "x"}])
        for key in self.keys:
            self.assertNotIn(key, str(cm.exception))


class PingTest(PoolTestBase):
    N = 2

    def test_ping_asks_one_specific_key_for_a_tiny_answer(self):
        gate = FakeNusaGate(default=lambda body: _answer("siap"))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            ok, info, seconds = brain_deepseek.ping(self.keys[1])
        self.assertTrue(ok)
        self.assertEqual(info, "deepseek-v4.1-flash")
        self.assertEqual([k for k, _ in gate.calls], [self.keys[1]])  # tepat key itu, bukan giliran
        self.assertGreaterEqual(seconds, 0)

    def test_ping_sends_no_tools_and_only_a_few_tokens(self):
        seen = []
        gate = FakeNusaGate(default=lambda body: seen.append(body) or _answer("siap"))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            brain_deepseek.ping(self.keys[0])
        self.assertNotIn("tools", seen[0])
        self.assertLessEqual(seen[0]["max_tokens"], 16)

    def test_ping_failure_is_reported_and_the_pool_learns_from_it(self):
        gate = FakeNusaGate(default=lambda body: _resp(402, {"error": {"code": "insufficient_balance", "message": "habis"}}))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            ok, info, _ = brain_deepseek.ping(self.keys[0])
        self.assertFalse(ok)
        self.assertIn("Saldo", info)
        self.assertEqual(keypool.snapshot()[0]["status"], "balance")  # langsung ditinggalkan dari giliran

    def test_ping_success_clears_old_marks(self):
        keypool.release(keypool.Lease(self.keys[0]), "balance")
        gate = FakeNusaGate(default=lambda body: _answer("siap"))
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            brain_deepseek.ping(self.keys[0])
        self.assertEqual(keypool.snapshot()[0]["status"], "siap")

    def test_ping_survives_a_network_error(self):
        def down(body):
            raise httpx.ConnectError("putus")

        gate = FakeNusaGate(default=down)
        with mock.patch.object(brain_deepseek._http, "post", side_effect=gate):
            ok, info, _ = brain_deepseek.ping(self.keys[0])
        self.assertFalse(ok)
        self.assertIn("ConnectError", info)


class ErrorShapeTest(unittest.TestCase):
    """Bentuk galat resmi NusaGate: {"error": {"message","type","code",...}} dan retry_after di atas."""

    def test_retry_after_from_body_header_and_error_object(self):
        self.assertEqual(brain_deepseek._retry_after(_resp(429, {}, {"retry-after": "7"})), 7.0)
        self.assertEqual(brain_deepseek._retry_after(_resp(429), {"retry_after": 12}), 12.0)
        self.assertEqual(brain_deepseek._retry_after(_resp(429), {"error": {"retry_after": "5"}}), 5.0)
        self.assertIsNone(brain_deepseek._retry_after(_resp(429), {"retry_after": "bukan angka"}))

    def test_outcome_mapping(self):
        out = brain_deepseek._outcome
        self.assertEqual(out(401), "invalid")
        self.assertEqual(out(200, "invalid_api_key"), "invalid")
        self.assertEqual(out(402), "balance")
        self.assertEqual(out(429, "rate_limit"), "limit")
        self.assertEqual(out(429, "pass_window_cap"), "cap")
        self.assertEqual(out(503), "busy")
        self.assertEqual(out(500), "busy")
        self.assertEqual(out(None), "busy")
        self.assertEqual(out(400, "validation_error"), "client")
        self.assertEqual(out(404, "model_not_found"), "client")


class DeepSeekUrlModelTest(unittest.TestCase):
    """Alamat & model dipilih otomatis dari awalan key: nsk- -> NusaGate, sk- -> resmi."""

    def test_nusagate_for_nsk_key(self):
        with mock.patch.object(config, "DEEPSEEK_URL", ""), mock.patch.object(config, "DEEPSEEK_MODEL", ""):
            self.assertEqual(config.deepseek_url("nsk_abc123"), "https://api.nusagate.com/v1")
            self.assertEqual(config.deepseek_model("nsk_abc123"), "deepseek-v4.1-flash")

    def test_official_for_sk_key(self):
        with mock.patch.object(config, "DEEPSEEK_URL", ""), mock.patch.object(config, "DEEPSEEK_MODEL", ""):
            self.assertEqual(config.deepseek_url("sk-abc123"), "https://api.deepseek.com/v1")
            self.assertEqual(config.deepseek_model("sk-abc123"), "deepseek-chat")

    def test_env_override_wins(self):
        with mock.patch.object(config, "DEEPSEEK_URL", "https://x.test/v1"), \
             mock.patch.object(config, "DEEPSEEK_MODEL", "mymodel"):
            self.assertEqual(config.deepseek_url("nsk_abc"), "https://x.test/v1")
            self.assertEqual(config.deepseek_model("nsk_abc"), "mymodel")

    def test_falls_back_to_config_key(self):
        with mock.patch.object(config, "DEEPSEEK_URL", ""), \
             mock.patch.object(config, "DEEPSEEK_API_KEY", "nsk_zzz"):
            self.assertEqual(config.deepseek_url(), "https://api.nusagate.com/v1")


if __name__ == "__main__":
    unittest.main()
