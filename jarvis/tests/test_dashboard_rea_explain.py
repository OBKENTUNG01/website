"""Uji rute penjelasan Claude di dashboard lewat HTTP sungguhan (tanpa memanggil Claude: _ask_claude ditiru)."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import brain, config, dashboard, memory, rea, rea_explain
from itsme.rea_explain import ExplainError

SUMMARY = "Inventaris berkas: notepad.exe\n- Berkas: notepad.exe\n- SHA-256: " + "ab" * 32
VIEW = {"id": "0123abcd", "state": "done", "command": "inspect-artifact", "summary_text": SUMMARY,
        "label": "notepad.exe", "target": "x"}


class FakeApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.histories = {}
        self.loop = None
        self.started = datetime.now()

    def owner_ctx(self):
        return {"confirm": lambda t: True, "notify": lambda t: None, "full_access": False, "website_dir": None,
                "memory": self.memory, "seen_urls": set(), "in_background": False}


class Base(unittest.TestCase):
    PASSWORD = None

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
                  mock.patch.object(config, "DASHBOARD_ENABLED", True),
                  mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
                  mock.patch.object(config, "DASHBOARD_PORT", 0),
                  mock.patch.object(config, "DASHBOARD_USER", "admin"),
                  mock.patch.object(config, "DASHBOARD_PASSWORD", self.PASSWORD),
                  mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "Q" * 40),
                  mock.patch.dict(brain._state, {"backend": "ollama"}),
                  mock.patch.dict(dashboard._SESSIONS, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        open(os.path.join(self.dir, "itsme.log"), "w").close()
        os.makedirs(os.path.join(self.dir, "rea-jobs", "0123abcd"))
        rea_explain._RUNS.clear()
        self.addCleanup(rea_explain._RUNS.clear)
        self.job = dict(VIEW)
        for p in (mock.patch.object(rea, "job", side_effect=lambda jid: self.job if jid == "0123abcd" else None),
                  mock.patch.object(rea, "list_jobs", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.start(FakeApp(self.dir))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.hdr = {"X-Itsme-Dashboard": "1"}

    def get(self, path, client=None):
        return (client or httpx).get(self.base + path, timeout=10)

    def post(self, path, body, client=None, guard=True):
        return (client or httpx).post(self.base + path, json=body, headers=self.hdr if guard else {}, timeout=10)

    def wait_done(self, eid, client=None):
        end = time.time() + 10
        while time.time() < end:
            run = self.get(f"/api/rea/explain?id={eid}", client).json()
            if run["state"] != "running":
                return run
            time.sleep(0.03)
        self.fail("penjelasan tidak selesai")


class ExplainRoutesTest(Base):
    def test_full_flow_start_poll_done_then_saved(self):
        with mock.patch.object(rea_explain, "_ask_claude",
                               return_value={"text": "1) Ini notepad.", "model": "claude-x", "seconds": 3.1}) as ask:
            r = self.post("/api/rea/explain", {"id": "0123abcd", "via": "claude_code"})
            self.assertEqual(r.status_code, 200, r.text)
            eid = r.json()["explain"]["id"]
            run = self.wait_done(eid)
        self.assertEqual((run["state"], run["text"], run["model"]), ("done", "1) Ini notepad.", "claude-x"))
        sent = ask.call_args[0][0]
        self.assertIn("<<<DATA", sent)
        self.assertIn("notepad.exe", sent)
        saved = self.get("/api/rea/explain?job=0123abcd").json()["saved"]
        self.assertEqual((saved["text"], saved["model"]), ("1) Ini notepad.", "claude-x"))
        self.assertIsNone(self.get("/api/rea/explain?job=ffffffff").json()["saved"])

    def test_errors_carry_message_code_and_status_for_the_page(self):
        r = self.post("/api/rea/explain", {"id": "ffffffff"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.json())
        self.job = dict(VIEW, state="running")
        r = self.post("/api/rea/explain", {"id": "0123abcd"})
        self.assertEqual(r.status_code, 409)
        self.job = dict(VIEW)
        r = self.post("/api/rea/explain", {"id": "0123abcd", "via": "ollama"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.get("/api/rea/explain?id=tidak-ada").status_code, 404)
        with mock.patch.object(brain, "claude_code_ready", return_value=False):
            r = self.post("/api/rea/explain", {"id": "0123abcd"})
        self.assertEqual((r.status_code, r.json()["code"]), (400, "login"))
        self.assertIn("claude_login", r.json()["error"])

    def test_claude_failure_shows_up_in_the_poll(self):
        with mock.patch.object(rea_explain, "_ask_claude",
                               side_effect=ExplainError("Kuota penuh", 429, "quota")):
            eid = self.post("/api/rea/explain", {"id": "0123abcd"}).json()["explain"]["id"]
            run = self.wait_done(eid)
        self.assertEqual((run["state"], run["code"], run["error"]), ("error", "quota", "Kuota penuh"))

    def test_one_at_a_time_over_http(self):
        gate = threading.Event()

        def slow(prompt):
            gate.wait(10)
            return {"text": "ok", "model": None, "seconds": 1}

        other = dict(VIEW, id="89abcdef")
        os.makedirs(os.path.join(self.dir, "rea-jobs", "89abcdef"))
        with mock.patch.object(rea_explain, "_ask_claude", side_effect=slow), \
                mock.patch.object(rea, "job", side_effect=lambda jid: {"0123abcd": self.job, "89abcdef": other}.get(jid)):
            first = self.post("/api/rea/explain", {"id": "0123abcd"})
            second = self.post("/api/rea/explain", {"id": "89abcdef"})
            gate.set()
            self.wait_done(first.json()["explain"]["id"])
        self.assertEqual(first.status_code, 200)
        self.assertEqual((second.status_code, second.json()["code"]), (409, "busy"))

    def test_info_reports_whether_claude_is_ready_and_how_to_log_in(self):
        info = self.get("/api/rea/info").json()["explain"]["claude_code"]
        self.assertEqual((info["ready"], info["hint"]), (True, ""))
        with mock.patch.object(brain, "claude_code_ready", return_value=False):
            info = self.get("/api/rea/info").json()["explain"]["claude_code"]
        self.assertFalse(info["ready"])
        self.assertIn("claude_login", info["hint"])

    def test_desktop_prompt_route(self):
        r = self.get("/api/rea/explain-prompt?id=0123abcd")
        self.assertEqual(r.status_code, 200)
        prompt = r.json()["prompt"]
        self.assertIn("JANGAN memakai alat", prompt)
        self.assertIn("notepad.exe", prompt)
        self.assertNotIn("sk-ant-oat", prompt)               # tak ada token di teks yang disalin
        self.assertEqual(self.get("/api/rea/explain-prompt?id=ffffffff").status_code, 404)
        self.job = dict(VIEW, state="running")
        self.assertEqual(self.get("/api/rea/explain-prompt?id=0123abcd").status_code, 409)

    def test_post_needs_the_dashboard_guard_header_and_nothing_is_started(self):
        with mock.patch.object(rea_explain, "_ask_claude") as ask:
            r = self.post("/api/rea/explain", {"id": "0123abcd"}, guard=False)
        self.assertEqual(r.status_code, 403)
        ask.assert_not_called()
        self.assertEqual(rea_explain._RUNS, {})


class ExplainAuthTest(Base):
    PASSWORD = "rahasia123"

    def test_every_explain_route_needs_a_login_session(self):
        with mock.patch.object(rea_explain, "_ask_claude") as ask:
            for r in (self.get("/api/rea/explain?id=x"), self.get("/api/rea/explain?job=0123abcd"),
                      self.get("/api/rea/explain-prompt?id=0123abcd"), self.get("/api/rea/info"),
                      self.post("/api/rea/explain", {"id": "0123abcd"})):
                self.assertEqual(r.status_code, 401, r.request.url)
            ask.assert_not_called()
        with httpx.Client(base_url=self.base, timeout=10) as c:
            c.post("/login", json={"user": "admin", "password": "rahasia123"}, headers=self.hdr)
            with mock.patch.object(rea_explain, "_ask_claude", return_value={"text": "t", "model": None, "seconds": 1}):
                r = c.post("/api/rea/explain", json={"id": "0123abcd"}, headers=self.hdr)
                self.assertEqual(r.status_code, 200)
                self.wait_done(r.json()["explain"]["id"], c)


class PageTest(Base):
    def test_page_has_the_claude_buttons_and_box(self):
        html = self.get("/").text
        for needle in ("reaExplainClaude", "/api/rea/explain", "Jelaskan dengan Claude-ku", "Salin untuk Claude Desktop",
                       "Bahas di Ngobrol", 'id="reaExplain"', "reaCopyForDesktop"):
            self.assertIn(needle, html, needle)

    def test_explanation_text_only_goes_through_textcontent(self):
        """Jawaban Claude dan teks berkas tak tepercaya: tidak boleh jadi HTML."""
        page = dashboard._PAGE
        block = page[page.index("// --- REA: penjelasan oleh Claude"):page.index("function reaExplain(){")]
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(bad, block, bad)

    def test_page_script_is_still_valid_javascript(self):
        import re
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node tidak terpasang")
        js = re.findall(r"<script>(.*?)</script>", dashboard._PAGE, flags=re.S)[0]
        path = os.path.join(self.dir, "page.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(js)
        done = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[:1500])


if __name__ == "__main__":
    unittest.main()
