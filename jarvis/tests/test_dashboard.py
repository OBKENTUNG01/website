"""Uji dashboard web (nomor 4) lewat HTTP sungguhan ke server tiruan."""

import asyncio
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import access, brain, config, dashboard, memory


class FakeApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.histories = {7: [{"role": "user", "content": "lama"}]}
        self.loop = None  # histories.clear dipanggil langsung kalau None
        self.started = datetime.now()
        self.full_access = False
        self.confirm_answer = True
        self.confirmed = []

    async def ask(self, text):
        return "Jawaban: " + text

    def owner_ctx(self):
        return {
            "confirm": self._confirm, "notify": lambda t: None, "full_access": self.full_access,
            "website_dir": None, "memory": self.memory, "seen_urls": set(), "in_background": False,
        }

    def _confirm(self, text):
        self.confirmed.append(text)
        return self.confirm_answer


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patches = [
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "DASHBOARD_ENABLED", True),
            mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
            mock.patch.object(config, "DASHBOARD_PORT", 0),  # port bebas
            mock.patch.object(config, "OPENROUTER_API_KEY", None),
            mock.patch.object(config, "ANTHROPIC_API_KEY", "sk-ant-api03-ada"),
            mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", None),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with open(config.LOG_FILE, "w", encoding="utf-8") as f:
            f.write("It's Me jalan\ntoken bocor 123456789:AAbbccddeeffgghh_ii-jj11223344556\n")
        self.app = FakeApp(self.dir)
        self.tree = os.path.join(self.dir, "tree")
        os.makedirs(os.path.join(self.tree, "sub"))
        with open(os.path.join(self.tree, "a.txt"), "w", encoding="utf-8") as f:
            f.write("isi A")
        with open(os.path.join(self.tree, ".env"), "w", encoding="utf-8") as f:
            f.write("SECRET=x")
        self.server = dashboard.start(self.app)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path):
        return httpx.get(self.base + path, timeout=5)

    def getp(self, url, **params):
        return httpx.get(self.base + url, params=params, timeout=5)

    def post(self, path, body, guard=True):
        headers = {"X-Itsme-Dashboard": "1"} if guard else {}
        return httpx.post(self.base + path, json=body, headers=headers, timeout=5)

    def test_page_served(self):
        r = self.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("dashboard di komputermu", r.text)

    def test_status_shows_readiness(self):
        s = self.get("/api/status").json()
        self.assertEqual(s["brain"], "ollama")
        self.assertTrue(s["brains"]["claude"]["ready"])       # ada API key
        self.assertFalse(s["brains"]["openrouter"]["ready"])  # tanpa key
        self.assertFalse(s["brains"]["claude_code"]["ready"]) # belum login
        self.assertEqual(s["access"], "safe")

    def test_switch_brain_affects_live_bot_and_resets_history(self):
        r = self.post("/api/brain", {"backend": "claude"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(brain.current_backend(), "claude")
        self.assertEqual(self.app.histories, {})  # riwayat direset

    def test_switch_to_unavailable_brain_errors(self):
        r = self.post("/api/brain", {"backend": "claude_code"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("login", r.json()["message"].lower())
        self.assertEqual(brain.current_backend(), "ollama")  # tidak berubah

    def test_access_toggle_persists(self):
        self.assertFalse(access.full())
        self.post("/api/access", {"full": True})
        self.assertTrue(access.full())
        self.post("/api/access", {"full": False})
        self.assertFalse(access.full())

    def test_post_without_guard_header_refused(self):
        r = self.post("/api/access", {"full": True}, guard=False)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(access.full())  # tidak jadi berubah

    def test_memory_edit_and_clear(self):
        r = self.post("/api/memory", {"action": "set", "text": "suka kopi"})
        self.assertIn("suka kopi", r.json()["text"])
        self.assertIn("suka kopi", self.app.memory.text())
        self.post("/api/memory", {"action": "clear"})
        self.assertNotIn("suka kopi", self.app.memory.text())

    def test_logs_redacted(self):
        text = self.get("/api/logs").json()["text"]
        self.assertIn("It's Me jalan", text)
        self.assertNotIn("123456789:AAbbccddeeffgghh", text)
        self.assertIn("***", text)

    def test_status_has_uptime(self):
        self.assertGreaterEqual(self.get("/api/status").json()["uptime"], 0)

    def test_info_reports_ollama(self):
        with mock.patch.object(dashboard, "_ollama_up", return_value=True):
            d = self.get("/api/info").json()
        self.assertTrue(d["ollama_up"])
        self.assertIsNone(d["quota"])  # tanpa key OpenRouter

    def test_chat_round_trip(self):
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        self.addCleanup(lambda: loop.call_soon_threadsafe(loop.stop))
        self.app.loop = loop
        job = self.post("/api/chat", {"text": "halo"}).json()["job"]
        reply = None
        for _ in range(100):
            d = self.get("/api/chat?id=" + job).json()
            if d.get("done"):
                reply = d["reply"]
                break
            time.sleep(0.05)
        self.assertEqual(reply, "Jawaban: halo")

    def test_chat_empty_refused(self):
        self.assertEqual(self.post("/api/chat", {"text": "  "}).status_code, 400)

    def test_chat_needs_guard_header(self):
        self.assertEqual(self.post("/api/chat", {"text": "halo"}, guard=False).status_code, 403)

    # --- berkas & perintah komputer ---
    def test_fs_list_marks_folders_and_secrets(self):
        names = {e["name"]: e for e in self.getp("/api/fs/list", path=self.tree).json()["entries"]}
        self.assertTrue(names["sub"]["dir"])
        self.assertFalse(names["a.txt"]["dir"])
        self.assertTrue(names[".env"]["secret"])

    def test_fs_read_text(self):
        self.assertEqual(self.getp("/api/fs/read", path=os.path.join(self.tree, "a.txt")).json()["text"], "isi A")

    def test_fs_read_secret_refused(self):
        r = self.getp("/api/fs/read", path=os.path.join(self.tree, ".env"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("rahasia", r.json()["error"])

    def test_readonly_command_runs_without_confirm(self):
        self.app.confirm_answer = False
        with mock.patch.object(dashboard.tools, "_run", return_value=(0, "OK")) as run:
            r = self.post("/api/fs/run", {"command": "whoami"}).json()
        self.assertEqual(r["message"], "OK")
        self.assertEqual(self.app.confirmed, [])  # read-only: tak minta konfirmasi
        run.assert_called_once()

    def test_command_asks_then_runs(self):
        with mock.patch.object(dashboard.tools, "_run", return_value=(0, "HASIL")) as run:
            r = self.post("/api/fs/run", {"command": "Start-Process notepad"}).json()
        self.assertEqual(r["message"], "HASIL")
        self.assertEqual(len(self.app.confirmed), 1)
        run.assert_called_once()

    def test_command_declined_not_run(self):
        self.app.confirm_answer = False
        with mock.patch.object(dashboard.tools, "_run") as run:
            r = self.post("/api/fs/run", {"command": "Remove-Item x"}).json()
        self.assertIn("Dibatalkan", r["message"])
        run.assert_not_called()

    def test_fs_zip_always_asks(self):
        r = self.post("/api/fs/zip", {"sumber": self.tree}).json()
        self.assertIn("Zip dibuat", r["message"])
        self.assertTrue(self.app.confirmed)  # nomor 5: zip selalu minta konfirmasi

    def test_fs_run_needs_guard_header(self):
        self.assertEqual(self.post("/api/fs/run", {"command": "whoami"}, guard=False).status_code, 403)


class DashboardTokenTest(unittest.TestCase):
    """Saat dibuka ke jaringan (bukan localhost), wajib token."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "DASHBOARD_ENABLED", True),
            # 0.0.0.0 = "ke jaringan" -> butuh token; sambungannya tetap via 127.0.0.1.
            mock.patch.object(config, "DASHBOARD_HOST", "0.0.0.0"),
            mock.patch.object(config, "DASHBOARD_PORT", 0),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.app = FakeApp(self.dir)
        self.server = dashboard.start(self.app)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token = dashboard._load_or_make_token()

    def test_token_generated_and_required(self):
        self.assertTrue(self.token)
        self.assertEqual(httpx.get(self.base + "/api/status", timeout=5).status_code, 401)
        ok = httpx.get(self.base + "/api/status", headers={"X-Itsme-Token": self.token}, timeout=5)
        self.assertEqual(ok.status_code, 200)

    def test_wrong_token_refused(self):
        r = httpx.get(self.base + "/api/status", headers={"X-Itsme-Token": "salah"}, timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_token_via_query_sets_cookie(self):
        r = httpx.get(self.base + "/?token=" + self.token, timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("itsme_token=", r.headers.get("set-cookie", ""))


if __name__ == "__main__":
    unittest.main()
