"""Uji dashboard web (nomor 4) lewat HTTP sungguhan ke server tiruan."""

import asyncio
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

from itsme import access, brain, config, dashboard, memory, node


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
        self.addCleanup(self.server.server_close)
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

    # --- Google Drive (panel) ---
    def test_drive_status_not_ready(self):
        self.assertFalse(self.get("/api/drive/status").json()["ready"])

    def test_drive_list_without_login_gives_hint(self):
        r = self.getp("/api/drive/list", folder="root")
        self.assertEqual(r.status_code, 400)
        self.assertIn("drive_login", r.json()["error"])

    def test_drive_list_renders_items(self):
        with mock.patch.object(dashboard, "drive_list", return_value=({"items": [{"id": "F", "name": "Foto", "folder": True, "size": 0}]}, None)):
            d = self.getp("/api/drive/list", folder="root").json()
        self.assertEqual(d["items"][0]["name"], "Foto")

    def test_drive_upload_routes_through_tool(self):
        with mock.patch.object(dashboard.tools, "handle_drive_tool", return_value="Terunggah ke Drive: f.txt") as h:
            r = self.post("/api/drive/upload", {"path_lokal": "C:/x/f.txt", "folder_id": "F1"}).json()
        self.assertIn("Terunggah", r["message"])
        self.assertEqual(h.call_args[0][0], "drive_unggah")

    def test_drive_upload_needs_guard_header(self):
        self.assertEqual(self.post("/api/drive/upload", {"path_lokal": "x"}, guard=False).status_code, 403)

    # -- Wake-on-LAN (tab Komputer): lihat itsme-node inode/wol.py --------------
    def test_node_wake_success(self):
        with mock.patch.object(node, "wake", return_value={"ok": True, "mac": "aabbcc", "via": "PC"}) as wake:
            r = self.post("/api/node/wake", {"name": "Laptop"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        wake.assert_called_once_with("Laptop")

    def test_node_wake_not_found(self):
        with mock.patch.object(node, "wake", return_value={"ok": False, "error": "tidak tahu"}):
            r = self.post("/api/node/wake", {"name": "TidakAda"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["ok"])

    def test_node_wake_empty_name_rejected(self):
        r = self.post("/api/node/wake", {"name": "  "})
        self.assertEqual(r.status_code, 400)

    def test_node_wake_without_node_installed(self):
        with mock.patch.object(node, "wake", return_value=None):
            r = self.post("/api/node/wake", {"name": "Laptop"})
        self.assertEqual(r.status_code, 400)

    def test_node_status_merges_sleeping_known_computers(self):
        fake_body = json.dumps({
            "nodes": [{"name": "PC-Utama", "id": "A"}],
            "known": [{"name": "PC-Utama", "ts": 1}, {"name": "PC-Tidur", "ts": 2}],
            "phase": "running", "epoch": 1,
        }).encode()
        with mock.patch.object(dashboard, "_node_get",
                                return_value=(200, "application/json", fake_body, "")), \
             mock.patch.object(dashboard.hp, "listing", return_value=[]):
            r = self.get("/api/node/status")
        by_name = {n["name"]: n for n in r.json()["nodes"]}
        self.assertIn("PC-Tidur", by_name)
        self.assertTrue(by_name["PC-Tidur"].get("asleep"))
        self.assertFalse(by_name["PC-Utama"].get("asleep"))  # sudah hidup, bukan "tidur"

    def test_node_fs_write_forwards_target_node(self):
        # Tanpa "node" diteruskan, unggahan ke komputer LAIN diam-diam malah
        # mendarat di komputer tempat dashboard ini sendiri berjalan.
        with mock.patch.object(dashboard, "_node_post", return_value=(200, b'{"ok": true}')) as post:
            r = self.post("/api/node/fs/write", {"node": "PC-LAIN", "path": "C:/x.txt", "b64": "aGk="})
        self.assertEqual(r.status_code, 200)
        sent_path, sent_body = post.call_args[0]
        self.assertEqual(sent_path, "/fs/write")
        self.assertEqual(sent_body.get("node"), "PC-LAIN")


class DashboardLoginTest(unittest.TestCase):
    """Login user+password saat ITSME_DASHBOARD_PASSWORD diisi."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "DASHBOARD_ENABLED", True),
            mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
            mock.patch.object(config, "DASHBOARD_PORT", 0),
            mock.patch.object(config, "DASHBOARD_USER", "admin"),
            mock.patch.object(config, "DASHBOARD_PASSWORD", "rahasia123"),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
            mock.patch.dict(dashboard._SESSIONS, {}, clear=True),
        ):
            p.start()
            self.addCleanup(p.stop)
        open(os.path.join(self.dir, "itsme.log"), "w").close()
        self.app = FakeApp(self.dir)
        self.server = dashboard.start(self.app)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.hdr = {"X-Itsme-Dashboard": "1"}

    def test_login_page_shown_and_api_blocked(self):
        page = httpx.get(self.base + "/", timeout=5)
        self.assertIn("Masuk", page.text)
        self.assertNotIn("data-tab", page.text)  # bukan dashboard penuh
        self.assertEqual(httpx.get(self.base + "/api/status", timeout=5).status_code, 401)

    def test_correct_login_opens_dashboard(self):
        with httpx.Client(base_url=self.base, timeout=5) as c:
            r = c.post("/login", json={"user": "admin", "password": "rahasia123"}, headers=self.hdr)
            self.assertTrue(r.json()["ok"])
            self.assertIn("data-tab", c.get("/").text)  # sekarang dashboard penuh
            self.assertEqual(c.get("/api/status").status_code, 200)

    def test_wrong_password_rejected(self):
        r = httpx.post(self.base + "/login", json={"user": "admin", "password": "salah"}, headers=self.hdr, timeout=5)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.json()["ok"])

    def test_logout_ends_session(self):
        with httpx.Client(base_url=self.base, timeout=5) as c:
            c.post("/login", json={"user": "admin", "password": "rahasia123"}, headers=self.hdr)
            self.assertEqual(c.get("/api/status").status_code, 200)
            c.post("/logout", headers=self.hdr)
            self.assertEqual(c.get("/api/status").status_code, 401)


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
        self.addCleanup(self.server.server_close)
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


class DashboardDevicesTest(unittest.TestCase):
    """Hub: terima pendaftaran perangkat (secret), tampilkan & lupakan.

    Satu bot/token/id — perangkat lain hanya mendaftar lewat HTTP, tidak membuat
    bot baru. Pendaftaran wajib secret bersama; daftar & lupakan butuh login biasa.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "DASHBOARD_ENABLED", True),
            mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
            mock.patch.object(config, "DASHBOARD_PORT", 0),
            mock.patch.object(config, "HUB_SECRET", "s3cr3t"),
            mock.patch.object(config, "AGENT_MODE", False),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        open(os.path.join(self.dir, "itsme.log"), "w").close()
        self.app = FakeApp(self.dir)
        self.server = dashboard.start(self.app)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path):
        return httpx.get(self.base + path, timeout=5)

    def post(self, path, body, guard=True):
        headers = {"X-Itsme-Dashboard": "1"} if guard else {}
        return httpx.post(self.base + path, json=body, headers=headers, timeout=5)

    def register(self, name="Laptop", secret="s3cr3t", **extra):
        return self.post("/api/devices/register", {"name": name, "secret": secret, **extra})

    def test_empty_list_and_is_hub(self):
        d = self.get("/api/devices").json()
        self.assertEqual(d["devices"], [])
        self.assertTrue(d["is_hub"])

    def test_register_requires_correct_secret(self):
        self.assertEqual(self.register(secret="salah").status_code, 403)
        self.assertEqual(self.get("/api/devices").json()["devices"], [])

    def test_register_adds_device_with_client_url(self):
        r = self.register(port=8751)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["new"])
        devs = self.get("/api/devices").json()["devices"]
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["name"], "Laptop")
        self.assertEqual(devs[0]["url"], "http://127.0.0.1:8751")
        self.assertTrue(devs[0]["online"])

    def test_second_register_not_new(self):
        self.register()
        self.assertFalse(self.register().json()["new"])

    def test_register_needs_guard_header(self):
        self.assertEqual(self.post("/api/devices/register", {"name": "L", "secret": "s3cr3t"}, guard=False).status_code, 403)

    def test_notifies_owner_only_on_new_device(self):
        with mock.patch.object(dashboard, "_notify_owner") as notify:
            self.register()   # baru -> kabari
            self.register()   # sudah ada -> diam
        self.assertEqual(notify.call_count, 1)
        self.assertIn("Laptop", notify.call_args[0][1])

    def test_forget_device(self):
        self.register()
        self.assertTrue(self.post("/api/devices/forget", {"name": "Laptop"}).json()["ok"])
        self.assertEqual(self.get("/api/devices").json()["devices"], [])

    def test_forget_needs_guard_header(self):
        self.assertEqual(self.post("/api/devices/forget", {"name": "x"}, guard=False).status_code, 403)

    def test_agent_mode_reports_not_hub(self):
        with mock.patch.object(config, "AGENT_MODE", True):
            self.assertFalse(self.get("/api/devices").json()["is_hub"])


if __name__ == "__main__":
    unittest.main()
