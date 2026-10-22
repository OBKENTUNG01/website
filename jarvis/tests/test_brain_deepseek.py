"""Uji otak DeepSeek (loop agen kompatibel OpenAI) tanpa jaringan sungguhan."""

import unittest
from unittest import mock

from itsme import brain_deepseek, config
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
        for p in (
            mock.patch.object(config, "DEEPSEEK_API_KEY", "nsk_" + "d" * 32),
            mock.patch.object(config, "DEEPSEEK_MODEL", "deepseek-chat"),
            mock.patch.object(config, "MAX_STEPS", 6),
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


if __name__ == "__main__":
    unittest.main()
