"""Uji rute unggahan REA di dashboard lewat HTTP sungguhan (server sungguhan, soket sungguhan).

Yang paling diperhatikan: badan permintaan mentah per potongan, dan SAMBUNGAN keep-alive yang harus tetap sehat
setelah sebuah potongan ditolak (kalau badan yang belum terbaca dibiarkan, byte-nya dibaca sebagai permintaan
berikutnya dan seluruh unggahan rusak).
"""

import hashlib
import http.client
import json
import os
import shutil
import socket
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

import httpx

from itsme import brain, config, dashboard, memory, rea, rea_upload


class FakeApp:
    def __init__(self, data_dir):
        self.memory = memory.Memory(os.path.join(data_dir, "memory.md"))
        self.histories = {}
        self.loop = None
        self.started = datetime.now()
        self.full_access = False
        self.confirm_answer = True
        self.confirmed = []

    async def ask(self, text):
        return "Jawaban: " + text

    def owner_ctx(self):
        return {"confirm": self._confirm, "notify": lambda t: None, "full_access": self.full_access,
                "website_dir": None, "memory": self.memory, "seen_urls": set(), "in_background": False}

    def _confirm(self, text):
        self.confirmed.append(text)
        return self.confirm_answer


def sha(data):
    return hashlib.sha256(data).hexdigest()


class UploadHttpBase(unittest.TestCase):
    PASSWORD = None
    HOST = "127.0.0.1"      # "0.0.0.0" = dibuka ke jaringan -> wajib token

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patches = [
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
            mock.patch.object(config, "DASHBOARD_ENABLED", True),
            mock.patch.object(config, "DASHBOARD_HOST", self.HOST),
            mock.patch.object(config, "DASHBOARD_PORT", 0),
            mock.patch.object(config, "DASHBOARD_USER", "admin"),
            mock.patch.object(config, "DASHBOARD_PASSWORD", self.PASSWORD),
            mock.patch.dict(brain._state, {"backend": "ollama"}),
            mock.patch.dict(dashboard._SESSIONS, {}, clear=True),
            mock.patch.object(rea_upload, "DISK_MARGIN", 0),
            mock.patch.object(rea_upload, "chunk_bytes", return_value=2048),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        open(os.path.join(self.dir, "itsme.log"), "w").close()
        rea_upload._META_CACHE.clear()
        rea_upload._LOCKS.clear()
        self.app = FakeApp(self.dir)
        self.server = dashboard.start(self.app)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.headers = {"X-Itsme-Dashboard": "1"}

    # -- pembantu --
    def conn(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(c.close)
        return c

    def call(self, conn, method, path, body=None, headers=None):
        hdr = dict(self.headers)
        hdr.update(headers or {})
        conn.request(method, path, body=body, headers=hdr)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {"_raw": raw[:200]}
        return resp.status, parsed, resp

    def jpost(self, conn, path, obj):
        return self.call(conn, "POST", path, json.dumps(obj).encode(), {"Content-Type": "application/json"})

    def chunk(self, conn, uid, index, offset, data, with_sha=True, extra=None):
        hdr = {"Content-Type": "application/octet-stream"}
        if with_sha:
            hdr["X-Chunk-Sha256"] = sha(data)
        hdr.update(extra or {})
        return self.call(conn, "POST", f"/api/rea/upload/chunk?id={uid}&file={index}&offset={offset}", data, hdr)

    def upload(self, conn, name, data, fingerprint=None):
        """Seluruh siklus lewat HTTP -> (upload_view, jumlah_potongan)."""
        st, j, _ = self.jpost(conn, "/api/rea/upload/start", {"kind": "file", "name": name, "size": len(data),
                                                              "fingerprint": fingerprint})
        self.assertEqual(st, 200, j)
        uid, step = j["upload"]["id"], j["chunk"]
        pos = n = 0
        while pos < len(data):
            st, r, _ = self.chunk(conn, uid, 0, pos, data[pos:pos + step])
            self.assertEqual(st, 200, r)
            pos += step
            n += 1
        st, j, _ = self.jpost(conn, "/api/rea/upload/finish", {"id": uid})
        self.assertEqual(st, 200, j)
        return j["upload"], n


class UploadFlowTest(UploadHttpBase):
    def test_full_cycle_over_http_and_listed_in_info(self):
        c = self.conn()
        data = os.urandom(5000)
        view, chunks = self.upload(c, "notepad.exe", data)
        self.assertEqual(chunks, 3)          # 2048 + 2048 + 904: server-lah yang menentukan ukuran potongan
        self.assertEqual((view["state"], view["sha256"], view["size"]), ("ready", sha(data), 5000))
        info = rea_upload.resolve(view["id"])
        with open(info["path"], "rb") as f:
            self.assertEqual(f.read(), data)
        st, j, _ = self.call(c, "GET", "/api/rea/info")
        self.assertEqual(st, 200)
        self.assertEqual([u["id"] for u in j["uploads"]], [view["id"]])
        self.assertEqual(j["limits"]["chunk_bytes"], 2048)
        st, j, _ = self.call(c, "GET", f"/api/rea/upload?id={view['id']}")
        self.assertEqual((st, j["upload"]["state"]), (200, "ready"))

    def test_start_returns_chunk_size_and_limits(self):
        st, j, _ = self.jpost(self.conn(), "/api/rea/upload/start", {"kind": "file", "name": "a.bin", "size": 10})
        self.assertEqual((st, j["ok"], j["chunk"], j["reused"]), (200, True, 2048, False))
        for key in ("max_bytes", "chunk_bytes", "max_files", "keep", "days"):
            self.assertIn(key, j["limits"])

    def test_resume_via_status_endpoint_and_fingerprint(self):
        c = self.conn()
        data = os.urandom(4096 + 100)
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "file", "name": "app.apk", "size": len(data),
                                                           "fingerprint": "app.apk|4196|7"})
        uid = j["upload"]["id"]
        self.chunk(c, uid, 0, 0, data[:2048])
        # klien "tertutup", buka sambungan baru, minta posisi
        c2 = self.conn()
        st, j, _ = self.call(c2, "GET", f"/api/rea/upload?id={uid}")
        self.assertEqual(j["upload"]["next"], {"file": 0, "offset": 2048})
        st, j2, _ = self.jpost(c2, "/api/rea/upload/start", {"kind": "file", "name": "app.apk", "size": len(data),
                                                             "fingerprint": "app.apk|4196|7"})
        self.assertEqual((j2["upload"]["id"], j2["reused"], j2["upload"]["received"]), (uid, False, 2048))
        st, r, _ = self.chunk(c2, uid, 0, 2048, data[2048:4096])
        st, r, _ = self.chunk(c2, uid, 0, 4096, data[4096:])
        self.assertTrue(r["done"])
        st, j, _ = self.jpost(c2, "/api/rea/upload/finish", {"id": uid})
        self.assertEqual(j["upload"]["sha256"], sha(data))

    def test_folder_upload_over_http(self):
        c = self.conn()
        files = {"App/app.exe": os.urandom(3000), "App/res/app.asar": os.urandom(100), "App/res/empty": b""}
        manifest = [{"path": p, "size": len(d)} for p, d in files.items()]
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "dir", "files": manifest, "fingerprint": "App|3|1"})
        self.assertEqual(st, 200, j)
        uid = j["upload"]["id"]
        for i, d in enumerate(files.values()):
            for pos in range(0, len(d), 2048):
                st, r, _ = self.chunk(c, uid, i, pos, d[pos:pos + 2048])
                self.assertEqual(st, 200, r)
        st, j, _ = self.jpost(c, "/api/rea/upload/finish", {"id": uid})
        self.assertEqual((st, j["upload"]["state"], j["upload"]["files_done"]), (200, "ready", 3))
        self.assertEqual([x["path"] for x in j["upload"]["candidates"]], ["app.exe", "res/app.asar"])

    def test_delete_over_http(self):
        c = self.conn()
        view, _ = self.upload(c, "a.exe", b"x" * 100)
        st, j, _ = self.jpost(c, "/api/rea/upload/delete", {"id": view["id"]})
        self.assertEqual((st, j["ok"]), (200, True))
        st, j, _ = self.jpost(c, "/api/rea/upload/delete", {"id": view["id"]})
        self.assertEqual(st, 404)

    def test_errors_are_json_with_error_field(self):
        c = self.conn()
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "file", "name": "", "size": 1})
        self.assertEqual(st, 400)
        self.assertIn("error", j)
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "dir", "files": [{"path": "../x/y", "size": 1}]})
        self.assertEqual(st, 400)
        self.assertIn("error", j)
        st, j, _ = self.jpost(c, "/api/rea/upload/finish", {"id": "../../etc"})
        self.assertEqual(st, 404)
        st, j, _ = self.jpost(c, "/api/rea/upload/start", ["bukan", "objek"])   # JSON valid tapi bukan objek
        self.assertEqual(st, 400)


class ChunkHardeningTest(UploadHttpBase):
    def start(self, c, size=5000):
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "file", "name": "a.bin", "size": size})
        return j["upload"]["id"]

    def assert_connection_still_good(self, c):
        """Sambungan yang SAMA masih menjawab permintaan berikutnya dengan benar."""
        st, j, _ = self.call(c, "GET", "/api/status")
        self.assertEqual(st, 200)
        self.assertIn("brain", j)

    def test_keepalive_survives_every_kind_of_rejected_chunk(self):
        c = self.conn()
        uid = self.start(c)
        data = os.urandom(2048)
        self.assertEqual(self.chunk(c, uid, 0, 0, data)[0], 200)
        self.assert_connection_still_good(c)
        rejected = [
            (self.chunk(c, uid, 0, 0, data), 409),                                       # offset salah
            (self.chunk(c, uid, 0, 2048, data, extra={"X-Chunk-Sha256": "0" * 64}), 422),  # checksum salah
            (self.chunk(c, "0" * 12, 0, 0, data), 404),                                  # unggahan tak ada
            (self.chunk(c, uid, 7, 2048, data), 400),                                    # nomor berkas
            (self.chunk(c, uid, 0, 2048, b"x" * 2048 + b""), 200),                       # (sehat) lanjut normal
        ]
        for (st, j, _), want in rejected:
            self.assertEqual(st, want, j)
            self.assert_connection_still_good(c)     # <- inti uji: byte badan tidak bocor ke permintaan berikutnya

    def test_409_reports_real_position(self):
        c = self.conn()
        uid = self.start(c)
        self.chunk(c, uid, 0, 0, os.urandom(2048))
        st, j, _ = self.chunk(c, uid, 0, 0, os.urandom(100))
        self.assertEqual((st, j["received"]), (409, 2048))

    def test_missing_guard_header_is_refused_and_body_not_trusted(self):
        c = self.conn()
        uid = self.start(c)
        data = os.urandom(2048)
        c.request("POST", f"/api/rea/upload/chunk?id={uid}&file=0&offset=0", body=data,
                  headers={"Content-Type": "application/octet-stream"})
        resp = c.getresponse()
        resp.read()
        self.assertEqual(resp.status, 403)
        self.assertEqual(rea_upload.status(uid)["received"], 0)
        c2 = self.conn()        # sambungan yang ditolak boleh ditutup; sambungan baru harus sehat
        self.assert_connection_still_good(c2)

    def test_oversize_chunk_is_413_without_reading_the_body(self):
        c = self.conn()
        uid = self.start(c)
        c.putrequest("POST", f"/api/rea/upload/chunk?id={uid}&file=0&offset=0")
        for k, v in {**self.headers, "Content-Type": "application/octet-stream",
                     "Content-Length": str(10 * 1024 * 1024)}.items():
            c.putheader(k, v)
        c.endheaders()                      # kepala saja; 10 MB badan tidak pernah dikirim
        resp = c.getresponse()
        body = json.loads(resp.read())
        self.assertEqual(resp.status, 413)
        self.assertIn("error", body)
        self.assertEqual(resp.getheader("Connection"), "close")
        self.assertEqual(rea_upload.status(uid)["received"], 0)

    def test_chunked_transfer_encoding_is_411(self):
        c = self.conn()
        uid = self.start(c)
        c.putrequest("POST", f"/api/rea/upload/chunk?id={uid}&file=0&offset=0")
        for k, v in {**self.headers, "Content-Type": "application/octet-stream",
                     "Transfer-Encoding": "chunked"}.items():
            c.putheader(k, v)
        c.endheaders()    # hanya header: server menolak dari header, jadi tak ada badan tak terbaca (RST) yang membalap respons
        resp = c.getresponse()
        resp.read()
        self.assertEqual(resp.status, 411)
        self.assertEqual(rea_upload.status(uid)["received"], 0)

    def test_missing_content_length_is_411(self):
        c = self.conn()
        uid = self.start(c)
        c.putrequest("POST", f"/api/rea/upload/chunk?id={uid}&file=0&offset=0")
        for k, v in {**self.headers, "Content-Type": "application/octet-stream"}.items():
            c.putheader(k, v)
        c.endheaders()
        resp = c.getresponse()
        resp.read()
        self.assertEqual(resp.status, 411)

    def test_empty_body_and_bad_numbers_are_400(self):
        c = self.conn()
        uid = self.start(c)
        for path, body in ((f"/api/rea/upload/chunk?id={uid}&file=0&offset=0", b""),
                           (f"/api/rea/upload/chunk?id={uid}&file=a&offset=0", b"xx"),
                           (f"/api/rea/upload/chunk?id={uid}&file=0", b"xx"),
                           (f"/api/rea/upload/chunk?id={uid}&file=0&offset=-5", b"xx")):
            st, j, _ = self.call(c, "POST", path, body, {"Content-Type": "application/octet-stream"})
            self.assertEqual(st, 400, (path, j))
            self.assert_connection_still_good(c)

    def test_client_that_disconnects_mid_chunk_leaves_nothing_behind(self):
        uid = self.start(self.conn())
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        head = (f"POST /api/rea/upload/chunk?id={uid}&file=0&offset=0 HTTP/1.1\r\nHost: x\r\n"
                "X-Itsme-Dashboard: 1\r\nContent-Type: application/octet-stream\r\nContent-Length: 2000\r\n\r\n")
        s.sendall(head.encode() + b"y" * 700)       # baru 700 dari 2000 byte
        s.close()
        time.sleep(0.3)
        self.assertEqual(rea_upload.status(uid)["received"], 0)   # semua-atau-tidak-sama-sekali
        self.assert_connection_still_good(self.conn())
        st, j, _ = self.chunk(self.conn(), uid, 0, 0, os.urandom(2048))
        self.assertEqual(st, 200)

    def test_stalled_client_times_out_and_server_survives(self):
        uid = self.start(self.conn())
        with mock.patch.object(dashboard, "UPLOAD_IO_TIMEOUT", 0.5):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            head = (f"POST /api/rea/upload/chunk?id={uid}&file=0&offset=0 HTTP/1.1\r\nHost: x\r\n"
                    "X-Itsme-Dashboard: 1\r\nContent-Type: application/octet-stream\r\nContent-Length: 2000\r\n\r\n")
            s.sendall(head.encode() + b"y" * 100)    # lalu diam
            t0 = time.time()
            s.settimeout(10)
            leftover = s.recv(65536)                 # server menutup sambungan setelah batas diam
            self.assertLess(time.time() - t0, 8)
            s.close()
        self.assertIn(leftover[:12], (b"", b"HTTP/1.1 408", b"HTTP/1.0 408"))
        self.assertEqual(rea_upload.status(uid)["received"], 0)
        self.assert_connection_still_good(self.conn())

    def test_two_parallel_uploads_do_not_mix(self):
        import threading
        results = {}

        def run(name, data):
            results[name] = self.upload(self.conn(), name, data)[0]

        blobs = {"one.bin": os.urandom(7000), "two.bin": os.urandom(6500)}
        threads = [threading.Thread(target=run, args=a) for a in blobs.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        for name, data in blobs.items():
            self.assertEqual(results[name]["sha256"], sha(data), name)


class UploadAuthTest(UploadHttpBase):
    PASSWORD = "rahasia123"

    def test_every_upload_route_needs_a_session(self):
        c = self.conn()
        data = os.urandom(1000)
        probes = [
            ("POST", "/api/rea/upload/start", json.dumps({"kind": "file", "name": "a", "size": 1}).encode()),
            ("POST", "/api/rea/upload/finish", b'{"id": "000000000000"}'),
            ("POST", "/api/rea/upload/delete", b'{"id": "000000000000"}'),
            ("POST", "/api/rea/upload/chunk?id=000000000000&file=0&offset=0", data),
            ("GET", "/api/rea/upload?id=000000000000", None),
            ("GET", "/api/rea/info", None),
            ("POST", "/api/rea/analyze", b'{"upload": "000000000000"}'),
        ]
        for method, path, body in probes:
            st, j, resp = self.call(c, method, path, body, {"Content-Type": "application/octet-stream"})
            self.assertEqual(st, 401, (method, path, j))
            if not resp.will_close:
                self.assert_alive(c)
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "rea-uploads")))

    def assert_alive(self, c):
        st, j, _ = self.call(c, "GET", "/api/status")
        self.assertEqual(st, 401)       # tetap 401 (bukan 400/hang karena badan bocor)

    def test_logged_in_session_can_upload(self):
        with httpx.Client(base_url=self.base, timeout=10) as client:
            r = client.post("/login", json={"user": "admin", "password": "rahasia123"}, headers=self.headers)
            self.assertTrue(r.json()["ok"])
            r = client.post("/api/rea/upload/start", headers=self.headers,
                            json={"kind": "file", "name": "a.bin", "size": 600})
            uid = r.json()["upload"]["id"]
            data = os.urandom(600)
            r = client.post(f"/api/rea/upload/chunk?id={uid}&file=0&offset=0", content=data,
                            headers={**self.headers, "Content-Type": "application/octet-stream"})
            self.assertEqual((r.status_code, r.json()["done"]), (200, True))
            r = client.post("/api/rea/upload/finish", headers=self.headers, json={"id": uid})
            self.assertEqual(r.json()["upload"]["sha256"], sha(data))


class UploadTokenTest(UploadHttpBase):
    """Dashboard dibuka ke jaringan (mis. dari HP lewat Tailscale): wajib token. Halaman mengirimnya lewat header
    X-Itsme-Token pada SETIAP permintaan, termasuk potongan unggahan mentah."""
    HOST = "0.0.0.0"

    def setUp(self):
        super().setUp()
        self.token = dashboard._load_or_make_token()
        self.assertTrue(self.token)

    def test_chunk_and_json_routes_need_the_token(self):
        c = self.conn()
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "file", "name": "a.bin", "size": 10})
        self.assertEqual(st, 401)
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "rea-uploads")))
        self.headers["X-Itsme-Token"] = self.token
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "file", "name": "a.bin", "size": 10})
        self.assertEqual(st, 200, j)
        uid = j["upload"]["id"]
        st, j, _ = self.chunk(c, uid, 0, 0, b"0123456789")
        self.assertEqual((st, j["done"]), (200, True))
        self.headers["X-Itsme-Token"] = "salah"
        st, j, _ = self.chunk(self.conn(), uid, 0, 10, b"x")
        self.assertEqual(st, 401)
        self.headers.pop("X-Itsme-Token")
        st, j, _ = self.chunk(self.conn(), uid, 0, 10, b"x")
        self.assertEqual(st, 401)


class AnalyzeUploadTest(UploadHttpBase):
    def job(self, **extra):
        base = {"id": "ab12cd34", "state": "running", "command": "inspect-artifact", "target": "x"}
        base.update(extra)
        return base

    def test_analysis_runs_on_the_stored_file_and_asks_with_the_original_name(self):
        c = self.conn()
        data = b"MZ" + os.urandom(3000)
        view, _ = self.upload(c, "notepad.exe", data)
        with mock.patch.object(rea, "start", return_value=self.job()) as start:
            st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": view["id"], "rea_command": ""})
        self.assertEqual((st, j["ok"], j["job"]["id"]), (200, True, "ab12cd34"))
        stored = rea_upload.resolve(view["id"])["path"]
        args, kwargs = start.call_args
        self.assertEqual(args, (stored, None))
        self.assertEqual(kwargs["upload"]["id"], view["id"])
        self.assertEqual(kwargs["upload"]["label"], "notepad.exe")
        self.assertEqual(kwargs["upload"]["sha256"], sha(data))
        self.assertEqual(len(self.app.confirmed), 1)
        ask = self.app.confirmed[0]
        self.assertIn("notepad.exe", ask)
        self.assertIn(sha(data)[:16], ask)
        self.assertNotIn("rea-uploads", ask)           # path internal tidak ditampilkan ke pemilik

    def test_owner_is_still_asked_in_full_access_mode_and_can_decline(self):
        c = self.conn()
        view, _ = self.upload(c, "a.exe", b"MZ" * 50)
        self.app.full_access = True
        self.app.confirm_answer = False
        with mock.patch.object(rea, "start") as start:
            st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": view["id"]})
        self.assertEqual(st, 400)
        self.assertIn("Dibatalkan", j["error"])
        self.assertEqual(len(self.app.confirmed), 1)
        start.assert_not_called()

    def test_unknown_unfinished_or_garbage_uploads_are_refused_before_asking(self):
        c = self.conn()
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "file", "name": "half.bin", "size": 500})
        half = j["upload"]["id"]
        for body, needle in (({"upload": "ffffffffffff"}, "tidak ada"), ({"upload": half}, "belum selesai"),
                             ({"upload": "../../etc/passwd"}, "tidak ada"), ({"upload": 12345}, "tidak ada")):
            with mock.patch.object(rea, "start") as start:
                st, j, _ = self.jpost(c, "/api/rea/analyze", body)
            self.assertEqual(st, 400, body)
            self.assertIn(needle, j["error"])
            start.assert_not_called()
        self.assertEqual(self.app.confirmed, [])

    def test_folder_member_can_be_analysed_by_index_and_bad_index_refused(self):
        c = self.conn()
        files = {"App/app.exe": b"MZ" + b"1" * 500, "App/res/app.asar": b"asar" * 100}
        manifest = [{"path": p, "size": len(d)} for p, d in files.items()]
        st, j, _ = self.jpost(c, "/api/rea/upload/start", {"kind": "dir", "files": manifest})
        uid = j["upload"]["id"]
        for i, d in enumerate(files.values()):
            self.chunk(c, uid, i, 0, d)
        self.jpost(c, "/api/rea/upload/finish", {"id": uid})
        with mock.patch.object(rea, "start", return_value=self.job()) as start:
            st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": uid, "file": 1})
            self.assertEqual(st, 200, j)
            self.assertTrue(start.call_args[0][0].endswith(os.path.join("res", "app.asar")))
            self.assertEqual(start.call_args[1]["upload"]["label"], "App/res/app.asar")
            start.reset_mock()
            st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": uid})        # seluruh folder
            self.assertEqual(st, 200, j)
            self.assertTrue(os.path.isdir(start.call_args[0][0]))
            start.reset_mock()
            for bad in (99, -1, "x", True, 1.5):
                st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": uid, "file": bad})
                self.assertEqual(st, 400, bad)
            start.assert_not_called()

    def test_rea_refusal_is_reported_with_error_field(self):
        c = self.conn()
        view, _ = self.upload(c, "a.exe", b"MZ" * 50)
        with mock.patch.object(rea, "start", side_effect=rea.ReaError("Node.js belum terpasang")):
            st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": view["id"]})
        self.assertEqual(st, 400)
        self.assertIn("Node.js", j["error"])

    def test_deleted_file_says_so(self):
        c = self.conn()
        view, _ = self.upload(c, "a.exe", b"MZ" * 50)
        os.remove(rea_upload.resolve(view["id"])["path"])
        with mock.patch.object(rea, "start") as start:
            st, j, _ = self.jpost(c, "/api/rea/analyze", {"upload": view["id"]})
        self.assertEqual(st, 400)
        self.assertIn("tidak ada di disk", j["error"])
        start.assert_not_called()

    def test_cannot_delete_an_upload_a_running_job_is_using(self):
        c = self.conn()
        view, _ = self.upload(c, "a.exe", b"MZ" * 50)
        with mock.patch.object(rea, "active_upload_ids", return_value={view["id"]}):
            st, j, _ = self.jpost(c, "/api/rea/upload/delete", {"id": view["id"]})
        self.assertEqual(st, 409)
        self.assertIsNotNone(rea_upload.get(view["id"]))


class PageTest(UploadHttpBase):
    def test_page_has_the_drop_zone_and_upload_code(self):
        html = httpx.get(self.base + "/", timeout=5).text
        for needle in ("reaDrop", "reaPickFiles", "/api/rea/upload/chunk", "webkitGetAsEntry", "X-Chunk-Sha256",
                       "reaUploads", "type=\"file\""):
            self.assertIn(needle, html, needle)

    def test_page_script_is_valid_javascript(self):
        """Satu salah ketik di JavaScript halaman mematikan SELURUH dashboard (semua tab): periksa sintaksnya."""
        import re
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node tidak terpasang")
        scripts = re.findall(r"<script>(.*?)</script>", dashboard._PAGE, flags=re.S)
        self.assertTrue(scripts)
        for i, js in enumerate(scripts):
            path = os.path.join(self.dir, f"page{i}.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(js)
            done = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr[:1500])

    def test_upload_code_never_puts_file_names_into_html(self):
        """Nama berkas/folder datang dari komputer pengunggah: hanya boleh lewat textContent (bukan innerHTML)."""
        page = dashboard._PAGE
        start = page.index("// --- REA: unggah berkas / folder aplikasi")
        end = page.index("function ollamaUninstall()")
        block = page[start:end]
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(bad, block, bad)

    def test_info_exposes_uploads_and_limits_for_the_page(self):
        c = self.conn()
        view, _ = UploadFlowTest.upload(self, c, "x.exe", b"MZ" * 10)
        st, j, _ = self.call(c, "GET", "/api/rea/info")
        self.assertEqual(st, 200)
        self.assertEqual(j["uploads"][0]["name"], "x.exe")
        self.assertIn("chunk_bytes", j["limits"])


if __name__ == "__main__":
    unittest.main()
