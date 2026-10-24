"""Uji jembatan HP: dashboard <-> aplikasi HP (disimulasikan) lewat long-poll."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import config, dashboard, hp, memory


class FakeApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.started = datetime.now()
        self.loop = None


class FakePhone(threading.Thread):
    """Meniru aplikasi HP: long-poll perintah, balas layar & berkas."""
    daemon = True

    def __init__(self, base, token, name="HP-Uji", kind="hp"):
        super().__init__()
        self.base, self.token, self.name, self.kind = base, token, name, kind
        self.stop = threading.Event()
        self.jpeg = b"\xff\xd8\xff\xe0JFIF-palsu\xff\xd9"
        self.last_input = None

    def run(self):
        with httpx.Client(timeout=5) as c:
            while not self.stop.is_set():
                try:
                    r = c.post(self.base + "/api/hp/poll",
                               json={"name": self.name, "token": self.token, "caps": ["screen", "fs"], "kind": self.kind})
                    cmd = r.json()
                except httpx.HTTPError:
                    self.stop.wait(0.3)  # server mati / jaringan: jangan busy-loop
                    continue
                if not cmd:
                    continue
                cid = cmd.get("id")
                if cmd.get("type") == "screen":
                    c.post(f"{self.base}/api/hp/frame?id={cid}&name={self.name}&token={self.token}",
                           content=self.jpeg, headers={"Content-Type": "image/jpeg"})
                elif cmd.get("type") == "fs_list":
                    c.post(self.base + "/api/hp/result", json={
                        "id": cid, "name": self.name, "token": self.token,
                        "value": {"path": cmd.get("path") or "/sdcard", "parent": "/",
                                  "entries": [{"name": "DCIM", "dir": True, "size": 0},
                                              {"name": "catatan.txt", "dir": False, "size": 12}]}})
                elif cmd.get("type") == "fs_read":
                    c.post(self.base + "/api/hp/result", json={
                        "id": cid, "name": self.name, "token": self.token,
                        "value": {"text": "isi dari HP", "size": 11, "truncated": False}})
                elif cmd.get("type") == "input":
                    self.last_input = cmd.get("events")
                    c.post(self.base + "/api/hp/result", json={
                        "id": cid, "name": self.name, "token": self.token, "value": {"ok": True}})


class HpBridgeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        hp._phones.clear(); hp._queues.clear(); hp._waiters.clear()
        for p in (mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "l.log")),
                  mock.patch.object(config, "DASHBOARD_ENABLED", True),
                  mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
                  mock.patch.object(config, "DASHBOARD_PORT", 0),
                  mock.patch.object(config, "DASHBOARD_PASSWORD", ""),
                  mock.patch.object(config, "HP_SECRET", ""),
                  mock.patch.object(hp, "POLL_WAIT", 1)):
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.start(FakeApp(self.dir))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token = httpx.get(self.base + "/api/hp", timeout=5).json()["token"]
        self.assertTrue(self.token)

    def _phone(self):
        ph = FakePhone(self.base, self.token)
        ph.start()
        self.addCleanup(lambda: (ph.stop.set(), ph.join(timeout=3)))
        return ph

    def _wait_online(self, name="HP-Uji", timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            ph = httpx.get(self.base + "/api/hp", timeout=5).json()["phones"]
            if any(p["name"] == name and p["online"] for p in ph):
                return True
            time.sleep(0.2)
        return False

    def test_wrong_token_rejected(self):
        r = httpx.post(self.base + "/api/hp/poll", json={"name": "X", "token": "salah", "caps": []}, timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_phone_appears_online(self):
        phone = self._phone()
        self.assertTrue(self._wait_online())
        phones = httpx.get(self.base + "/api/hp", timeout=5).json()["phones"]
        self.assertEqual([p["name"] for p in phones], ["HP-Uji"])
        self.assertIn("screen", phones[0]["caps"])

    def test_screen_bridged_as_jpeg(self):
        phone = self._phone()
        self.assertTrue(self._wait_online())
        r = httpx.get(self.base + "/api/hp/screen?name=HP-Uji", timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/jpeg", r.headers["content-type"])
        self.assertEqual(r.content, phone.jpeg)

    def test_files_bridged(self):
        phone = self._phone()
        self.assertTrue(self._wait_online())
        d = httpx.get(self.base + "/api/hp/fs/list?name=HP-Uji&path=/sdcard", timeout=10).json()
        self.assertEqual([e["name"] for e in d["entries"]], ["DCIM", "catatan.txt"])
        r = httpx.get(self.base + "/api/hp/fs/read?name=HP-Uji&path=/sdcard/catatan.txt", timeout=10).json()
        self.assertEqual(r["text"], "isi dari HP")

    def test_input_bridged_to_phone(self):
        phone = self._phone()
        self.assertTrue(self._wait_online())
        events = [{"t": "tap", "x": 0.5, "y": 0.5}, {"t": "key", "key": "home"}]
        r = httpx.post(self.base + "/api/hp/input", json={"name": "HP-Uji", "events": events},
                       headers={"X-Itsme-Dashboard": "1"}, timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        self.assertEqual(phone.last_input, events)

    def test_computer_via_bridge_listed_and_screened(self):
        pc = FakePhone(self.base, self.token, name="PC-Jembatan", kind="computer")
        pc.jpeg = b"\x89PNG\r\n\x1a\nKOMPUTER"   # komputer kirim PNG
        pc.start(); self.addCleanup(lambda: (pc.stop.set(), pc.join(timeout=3)))
        self.assertTrue(self._wait_online("PC-Jembatan"))
        # Muncul di daftar komputer (digabung dengan node Tailscale), ditandai via jembatan.
        st = httpx.get(self.base + "/api/node/status", timeout=5).json()
        row = next((n for n in st.get("nodes", []) if n["name"] == "PC-Jembatan"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "bridge:PC-Jembatan")
        self.assertEqual(row.get("via"), "bridge")
        # Layar diteruskan lewat jembatan, dikirim sebagai PNG.
        r = httpx.get(self.base + "/api/node/screen?node=bridge:PC-Jembatan", timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/png", r.headers["content-type"])
        self.assertEqual(r.content, pc.jpeg)

    def test_offline_phone_gives_504(self):
        r = httpx.get(self.base + "/api/hp/screen?name=TidakAda", timeout=5)
        self.assertEqual(r.status_code, 504)

    def test_command_to_registered_but_silent_phone_times_out(self):
        # HP mendaftar sekali lalu berhenti merespons -> perintah menunggu lalu gagal.
        httpx.post(self.base + "/api/hp/poll", json={"name": "Diam", "token": self.token, "caps": ["screen"]}, timeout=5)
        with mock.patch.object(hp, "RESULT_WAIT", 1):
            r = httpx.get(self.base + "/api/hp/screen?name=Diam", timeout=5)
        self.assertEqual(r.status_code, 504)

    def test_app_builder_status_and_trigger(self):
        from itsme import hpbuild
        with mock.patch.object(hpbuild, "status", return_value={"configured": True, "repo": "o/r", "apk": "u"}):
            d = httpx.get(self.base + "/api/hp/build", timeout=5).json()
        self.assertEqual(d["repo"], "o/r")
        with mock.patch.object(hpbuild, "trigger", return_value=True) as trig:
            r = httpx.post(self.base + "/api/hp/build", json={"app_name": "X", "accent": "#fff"},
                           headers={"X-Itsme-Dashboard": "1"}, timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        self.assertEqual(trig.call_args.args[0], {"app_name": "X", "accent": "#fff"})

    def test_app_builder_trigger_error_is_400(self):
        from itsme import hpbuild
        with mock.patch.object(hpbuild, "trigger", side_effect=hpbuild.BuildError("belum ada token")):
            r = httpx.post(self.base + "/api/hp/build", json={}, headers={"X-Itsme-Dashboard": "1"}, timeout=5)
        self.assertEqual(r.status_code, 400)
        self.assertIn("token", r.json().get("error", ""))


if __name__ == "__main__":
    unittest.main()
