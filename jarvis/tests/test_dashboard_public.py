"""Uji dashboard mode publik / login berkas kunci + pengamanan domain."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import config, dashboard, memory


class FakeApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.started = datetime.now()
        self.loop = None


def _start(dir_, **extra):
    patches = [
        mock.patch.object(config, "DATA_DIR", dir_),
        mock.patch.object(config, "LOG_FILE", os.path.join(dir_, "l.log")),
        mock.patch.object(config, "DASHBOARD_ENABLED", True),
        mock.patch.object(config, "DASHBOARD_HOST", "127.0.0.1"),
        mock.patch.object(config, "DASHBOARD_PORT", 0),
        mock.patch.object(config, "DASHBOARD_PASSWORD", ""),
        mock.patch.object(config, "DASHBOARD_PUBLIC", True),
        mock.patch.object(config, "DASHBOARD_KEY_LOGIN", False),
        mock.patch.object(config, "DASHBOARD_DOMAINS", ""),
        mock.patch.object(config, "DASHBOARD_TRUST_PROXY", True),
    ]
    for k, v in extra.items():
        patches.append(mock.patch.object(config, k, v))
    return patches


class PublicDashboardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        dashboard._LOGIN_FAILS.clear()
        for p in _start(self.dir):
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.start(FakeApp(self.dir))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        with open(os.path.join(self.dir, "dashboard.key"), encoding="utf-8") as f:
            self.key = f.read().strip()

    def c(self):
        return httpx.Client(base_url=self.base, timeout=5, headers={"X-Itsme-Dashboard": "1"})

    def test_requires_login_even_from_localhost(self):
        r = httpx.get(self.base + "/api/status", timeout=5)
        self.assertEqual(r.status_code, 401)            # tidak ada jalan pintas localhost
        r = httpx.get(self.base + "/", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Berkas kunci", r.text)           # halaman login minta berkas kunci
        self.assertIn("type=\"file\"", r.text)

    def test_key_file_login_opens_access(self):
        with self.c() as cl:
            r = cl.post("/login", json={"key": self.key})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json().get("ok"))
            s = cl.get("/api/status")                   # cookie sesi dipakai
            self.assertEqual(s.status_code, 200)

    def test_key_with_trailing_newline_ok(self):
        with self.c() as cl:
            r = cl.post("/login", json={"key": self.key + "\n"})
            self.assertTrue(r.json().get("ok"))

    def test_wrong_key_then_bruteforce_block(self):
        with self.c() as cl:
            for _ in range(5):
                r = cl.post("/login", json={"key": "salah"})
                self.assertEqual(r.status_code, 401)
            r = cl.post("/login", json={"key": "salah"})
            self.assertEqual(r.status_code, 429)        # diblokir
            # kunci benar pun ikut diblokir selama hukuman
            r = cl.post("/login", json={"key": self.key})
            self.assertEqual(r.status_code, 429)

    def test_security_headers(self):
        r = httpx.get(self.base + "/", timeout=5)
        self.assertIn("content-security-policy", r.headers)
        self.assertEqual(r.headers.get("x-frame-options"), "DENY")
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertIn("max-age=", r.headers.get("strict-transport-security", ""))  # publik -> HSTS

    def test_keyfile_download_needs_auth(self):
        r = httpx.get(self.base + "/api/keyfile", timeout=5)
        self.assertEqual(r.status_code, 401)
        with self.c() as cl:
            cl.post("/login", json={"key": self.key})
            r = cl.get("/api/keyfile")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.text.strip(), self.key)

    def test_protected_action_blocked_without_login(self):
        r = httpx.post(self.base + "/api/brain", json={"backend": "ollama"},
                       headers={"X-Itsme-Dashboard": "1"}, timeout=5)
        self.assertEqual(r.status_code, 401)


class DomainAllowlistTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        dashboard._LOGIN_FAILS.clear()
        for p in _start(self.dir, DASHBOARD_DOMAINS="domainku.com"):
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.start(FakeApp(self.dir))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_wrong_host_forbidden(self):
        r = httpx.get(self.base + "/", headers={"Host": "jahat.com"}, timeout=5)
        self.assertEqual(r.status_code, 403)

    def test_allowed_host_passes_to_login(self):
        r = httpx.get(self.base + "/", headers={"Host": "domainku.com"}, timeout=5)
        self.assertEqual(r.status_code, 200)             # lolos domain -> tampil login
        r = httpx.get(self.base + "/", headers={"Host": "domainku.com:8750"}, timeout=5)
        self.assertEqual(r.status_code, 200)             # port diabaikan


class KeyLoginOnlyTest(unittest.TestCase):
    """ITSME_DASHBOARD_KEY_LOGIN tanpa mode publik: tetap wajib berkas kunci, tanpa HSTS."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        dashboard._LOGIN_FAILS.clear()
        for p in _start(self.dir, DASHBOARD_PUBLIC=False, DASHBOARD_KEY_LOGIN=True):
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.start(FakeApp(self.dir))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_login_required_no_hsts(self):
        r = httpx.get(self.base + "/api/status", timeout=5)
        self.assertEqual(r.status_code, 401)
        r = httpx.get(self.base + "/", timeout=5)
        self.assertNotIn("strict-transport-security", r.headers)  # bukan publik


if __name__ == "__main__":
    unittest.main()
