"""Uji otak lokal (Ollama) dengan jawaban tiruan — Ollama tidak dihubungi."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import brain_ollama, config


class SafeLevelTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.state_file = os.path.join(self.dir, "ollama_state.json")
        patch = mock.patch.object(config, "OLLAMA_STATE_FILE", self.state_file)
        patch.start()
        self.addCleanup(patch.stop)
        old = dict(brain_ollama._state)
        self.addCleanup(brain_ollama._state.update, old)
        brain_ollama._state.update(level=0, noted=0)

    def _crash_then_ok(self, calls):
        def fake_call(body):
            calls.append(body["options"])
            if len(calls) == 1:
                return None, "an error was encountered while running the model", True
            return {"role": "assistant", "content": "halo"}, None, False

        return fake_call

    def test_gpu_crash_goes_straight_to_cpu_and_is_remembered(self):
        calls = []
        with mock.patch.object(brain_ollama, "_call_ollama", side_effect=self._crash_then_ok(calls)), \
                mock.patch.object(brain_ollama, "_gpu_crashed", return_value=True):
            message, error = brain_ollama._ask([{"role": "user", "content": "hai"}], "")
        self.assertIsNone(error)
        self.assertEqual(message["content"], "halo")
        self.assertEqual(calls[1].get("num_gpu"), 0)
        with open(self.state_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"model": config.OLLAMA_MODEL, "level": 2})
        self.assertEqual(brain_ollama._load_level(), 2)

    def test_other_crash_tries_smaller_context_first(self):
        calls = []
        with mock.patch.object(brain_ollama, "_call_ollama", side_effect=self._crash_then_ok(calls)), \
                mock.patch.object(brain_ollama, "_gpu_crashed", return_value=False):
            brain_ollama._ask([{"role": "user", "content": "hai"}], "")
        self.assertEqual(calls[1]["num_ctx"], 8192)
        self.assertEqual(brain_ollama._state["level"], 1)

    def test_saved_level_ignored_for_other_model(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"model": "model-lain", "level": 2}, f)
        self.assertEqual(brain_ollama._load_level(), 0)

    def test_gpu_crash_read_from_ollama_log(self):
        log = os.path.join(self.dir, "server.log")
        with mock.patch.object(brain_ollama, "_ollama_log_path", return_value=log):
            with open(log, "w", encoding="utf-8") as f:
                f.write("ggml_vulkan: Found 1 Vulkan devices\nggml_vulkan: device lost on Vulkan0\n")
            self.assertTrue(brain_ollama._gpu_crashed())
            with open(log, "w", encoding="utf-8") as f:
                f.write("ggml_vulkan: Found 1 Vulkan devices\n")
            self.assertFalse(brain_ollama._gpu_crashed())


class AgentLoopTest(unittest.TestCase):
    def test_tool_call_then_answer(self):
        replies = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "cari_web", "arguments": {"kata_kunci": "cuaca Samarinda"}}},
            ]},
            {"role": "assistant", "content": "<think>hmm</think>Cerah, Tuan."},
        ]
        calls = iter(replies)
        ctx = {"memory": mock.Mock(text=mock.Mock(return_value=""))}
        messages = [{"role": "user", "content": "cuaca hari ini?"}]
        with mock.patch.object(brain_ollama, "_ask", side_effect=lambda m, t: (next(calls), None)), \
                mock.patch.object(brain_ollama, "handle_tool", return_value="hasil cuaca") as tool:
            reply = brain_ollama.run_agent(messages, ctx)
        self.assertEqual(reply, "Cerah, Tuan.")
        tool.assert_called_once_with("cari_web", {"kata_kunci": "cuaca Samarinda"}, ctx)
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["content"], "hasil cuaca")


if __name__ == "__main__":
    unittest.main()
