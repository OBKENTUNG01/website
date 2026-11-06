"""Uji kontrol Ollama per komputer lewat It's Me Node: fungsi ollama_* di node.py, dan alat
copot_ollama / pasang_ollama / daftar_ollama (tools.py) di atasnya.

Semua lewat SERVER HTTP SUNGGUHAN di localhost (FakeNode) yang meniru perilaku itsme-node
(inode/server.py + inode/ollama_ctl.py), termasuk kebiasaan nyatanya: penolakan komputer lain
(409 sedang ada pekerjaan, bukan Windows, node lama) hanya diteruskan sebagai 502 "tak terjawab".
Yang ditiru adalah node-nya; kode bot (urllib, JSON, polling, alat) berjalan apa adanya.
Tidak ada Ollama yang dipasang atau dicopot, di mesin ini maupun di mana pun.
"""

import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from itsme import config, node, ollama_admin, tools

DONE_MSG = {
    "uninstall": "Ollama sudah dicopot. Model yang sudah diunduh tetap tersimpan di ~/.ollama.",
    "install": "Ollama 0.40.1 terpasang lewat winget.",
}
FAIL_MSG = {
    "uninstall": "Uninstall belum tuntas (registry/berkas masih ada). Lihat log: C:\\Temp\\u.log",
    "install": "Ollama gagal dipasang (winget gagal, pemasang resmi kode 1).",
}


class Computer:
    """Satu komputer di jaringan node tiruan: perilaku ollama_ctl.py yang disederhanakan."""

    def __init__(self, cid, name, installed=False, version=None, supported=True, old=False, alive=True,
                 leftover=False, path=None, polls=2, outcome="done", stale=False, job=None, on_start=None):
        self.id, self.name = cid, name
        self.installed, self.version = installed, version
        self.supported, self.old, self.alive = supported, old, alive
        self.leftover, self.path = leftover, path
        self.polls = polls            # berapa kali status dibaca sebelum pekerjaan selesai
        self.outcome = outcome        # "done" / "failed"
        self.stale = stale            # gosip tertinggal: ringkasan tak menampilkan pekerjaan yang sedang jalan
        self.job = job
        self.left = 999 if job and job.get("state") == "running" else 0
        self.on_start = on_start      # dipanggil sebelum POST dijawab (mis. Barrier)

    def summary(self):
        """Yang digosipkan ke node lain (ollama_ctl.summary)."""
        if self.old:
            return None
        job = None
        if self.job and not (self.stale and self.job["state"] == "running"):
            job = {k: self.job[k] for k in ("action", "state", "step")}
        return {"installed": self.installed, "version": self.version, "job": job}

    def status(self):
        """Jawaban GET /ollama. Tiap pembacaan memajukan pekerjaan yang sedang jalan."""
        if self.job and self.job["state"] == "running":
            self.left -= 1
            if self.left <= 0:
                self._finish()
        return {"supported": self.supported, "installed": self.installed, "version": self.version,
                "path": self.path, "leftover": self.leftover, "running": False, "models_kept": True,
                "job": dict(self.job) if self.job else None}

    def start(self, action):
        """ollama_ctl.start: {"ok": True, "job": ...} atau {"ok": False, "error": ...}."""
        if not self.supported:
            return {"ok": False, "error": "Pasang/copot Ollama otomatis baru didukung di Windows."}
        if action not in ("install", "uninstall"):
            return {"ok": False, "error": f"aksi tidak dikenal: {action!r}"}
        if self.job and self.job["state"] == "running":
            return {"ok": False, "error": f"masih ada pekerjaan '{self.job['action']}' yang berjalan",
                    "job": dict(self.job)}
        self.job = {"action": action, "state": "running", "step": "mulai", "started": time.time(),
                    "ended": None, "message": "", "log": ""}
        self.left = self.polls
        return {"ok": True, "job": dict(self.job)}

    def _finish(self):
        job = self.job
        if self.outcome == "failed":
            job.update(state="failed", step="selesai", message=FAIL_MSG[job["action"]], ended=time.time())
            return
        self.installed, self.version = (False, None) if job["action"] == "uninstall" else (True, "0.40.1")
        job.update(state="done", step="selesai", message=DONE_MSG[job["action"]], ended=time.time())


class FakeNode:
    """It's Me Node tiruan di komputer ini (HTTP sungguhan): /status dan /ollama."""

    def __init__(self, computers, me, known=(), old_local=False):
        self.computers = {c.id: c for c in computers}
        self.me = me
        self.known = list(known)
        self.old_local = old_local      # node di komputer ini versi lama: tak punya /ollama, /status tanpa "ollama"
        self.requests = []              # (metode, path, parameter)
        self.fail_next = 0              # jawab 500 untuk sekian GET /ollama berikutnya (gangguan sesaat)
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlsplit(self.path)
                params = {k: v[0] for k, v in parse_qs(url.query).items()}
                self._reply(*outer.handle("GET", url.path, params))

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                params = json.loads(self.rfile.read(n) or b"{}")
                self._reply(*outer.handle("POST", urlsplit(self.path).path, params))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, method, path):
        return sum(1 for m, p, _ in self.requests if (m, p) == (method, path))

    def posts(self):
        return [params for m, p, params in self.requests if (m, p) == ("POST", "/ollama")]

    def status(self):
        nodes = []
        for c in self.computers.values():
            if not c.alive:
                continue
            row = {"name": c.name, "id": c.id, "addr": "127.0.0.1:7171", "bot": True, "leader": c.id == self.me}
            if not self.old_local:
                row["ollama"] = c.summary()
            nodes.append(row)
        return {"me": self.me, "addr": "127.0.0.1:7171", "leader": self.me, "phase": "running", "epoch": 1,
                "nodes": nodes, "known": self.known}

    def handle(self, method, path, params):
        """Meniru server.py remote_route untuk /ollama: kode HTTP dan isi jawaban."""
        with self.lock:
            self.requests.append((method, path, dict(params)))
        if path == "/status":
            with self.lock:
                return 200, self.status()
        if path != "/ollama" or self.old_local:
            return 404, {"error": "tidak ada"}
        comp = self.computers.get(params.get("node") or self.me)
        if comp is None or not comp.alive:
            return 404, {"error": "komputer tidak ditemukan atau sedang mati"}
        remote = comp.id != self.me
        if method == "POST" and comp.on_start:
            comp.on_start()
        with self.lock:
            if method == "GET":
                if self.fail_next > 0:
                    self.fail_next -= 1
                    return 500, {"error": "sedang tidak bisa"}
                return (404, {"error": "tidak ada"}) if comp.old else (200, comp.status())
            if comp.old:  # komputer lama: rpc mendapat 404 -> None -> 502; node lokal yang lama: 404 langsung
                return (502, {"error": "komputer tujuan tak terjawab"}) if remote else (404, {"error": "tidak ada"})
            action = str(params.get("action") or "status").strip()
            if action == "status":
                return 200, comp.status()
            res = comp.start(action)
            if res["ok"]:
                return 200, res
            # node tujuan menjawab 409; node di sini meneruskannya sebagai 502 (rpc hanya menerima 200)
            return (502, {"error": "komputer tujuan tak terjawab"}) if remote else (409, res)


def fleet_of(*computers, me=None, known=(), old_local=False):
    return FakeNode(list(computers), me or computers[0].id, known=known, old_local=old_local)


def closed_url():
    """Alamat di localhost yang baru saja dibebaskan: tidak ada yang mendengarkan di sana."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


class NodeTestCase(unittest.TestCase):
    def serve(self, *computers, me=None, known=(), old_local=False):
        fake = fleet_of(*computers, me=me, known=known, old_local=old_local)
        self.addCleanup(fake.close)
        patch = mock.patch.object(config, "NODE_URL", fake.url)
        patch.start()
        self.addCleanup(patch.stop)
        return fake


class FleetTest(NodeTestCase):
    def test_lists_every_computer_with_its_ollama_state(self):
        self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1"),
                   Computer("b2", "Laptop", installed=False))
        fleet = node.ollama_fleet()
        self.assertEqual([c["name"] for c in fleet], ["PC-Utama", "Laptop"])
        utama, laptop = fleet
        self.assertEqual(utama, {"id": "a1", "name": "PC-Utama", "online": True, "asleep": False,
                                 "installed": True, "version": "0.40.1", "job": None})
        self.assertEqual(laptop, {"id": "b2", "name": "Laptop", "online": True, "asleep": False,
                                  "installed": False, "version": None, "job": None})

    def test_computer_that_does_not_report_ollama_is_unknown_not_false(self):
        self.serve(Computer("a1", "PC-Utama", installed=True, version="1"), Computer("b2", "Lama", old=True))
        lama = node.ollama_fleet()[1]
        self.assertIsNone(lama["installed"])
        self.assertIsNone(lama["version"])
        self.assertTrue(lama["online"])

    def test_old_local_node_without_ollama_field_is_unknown(self):
        self.serve(Computer("a1", "PC-Utama"), old_local=True)
        self.assertIsNone(node.ollama_fleet()[0]["installed"])

    def test_running_job_is_visible(self):
        job = {"action": "install", "state": "running", "step": "mengunduh", "started": 1, "ended": None,
               "message": "", "log": ""}
        self.serve(Computer("a1", "PC-Utama", job=job))
        got = node.ollama_fleet()[0]
        self.assertEqual(got["job"], {"action": "install", "state": "running", "step": "mengunduh"})

    def test_known_but_silent_computers_are_listed_as_asleep(self):
        self.serve(Computer("a1", "PC-Utama", installed=True, version="1"),
                   known=[{"name": "Dapur", "ts": 1.0}, {"name": "pc-utama", "ts": 2.0}, {"name": "Dapur", "ts": 3.0},
                          {"name": "  ", "ts": 4.0}, "bukan-dict"])
        fleet = node.ollama_fleet()
        self.assertEqual([c["name"] for c in fleet], ["PC-Utama", "Dapur"])   # tanpa duplikat, tanpa nama kosong
        dapur = fleet[1]
        self.assertEqual(dapur, {"id": None, "name": "Dapur", "online": False, "asleep": True,
                                 "installed": None, "version": None, "job": None})

    def test_node_entry_flagged_asleep_is_not_online(self):
        st = {"me": "a1", "nodes": [{"id": "a1", "name": "A", "ollama": {"installed": True, "version": "1"}},
                                    {"id": None, "name": "B", "asleep": True}]}
        a, b = node.ollama_fleet(st)
        self.assertTrue(a["online"])
        self.assertFalse(b["online"])
        self.assertTrue(b["asleep"])
        self.assertIsNone(b["installed"])

    def test_node_down_gives_empty_list(self):
        with mock.patch.object(config, "NODE_URL", closed_url()):
            self.assertEqual(node.ollama_overview(), (None, []))   # ollama_fleet() sendiri membaca /status yang sama

    def test_garbage_status_is_ignored(self):
        for bad in ([], "x", 5, {}, {"nodes": "x"}, {"nodes": 7, "known": 5}, {"nodes": [1, None], "known": "y"}):
            self.assertEqual(node.ollama_fleet(bad), [], bad)
        fleet = node.ollama_fleet({"nodes": [1, None, {"id": "z", "name": "Z", "ollama": "x"}]})
        self.assertEqual([(c["name"], c["installed"]) for c in fleet], [("Z", None)])


class StatusTest(NodeTestCase):
    def test_local_status_has_no_node_parameter(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1"))
        st = node.ollama_status()
        self.assertTrue(st["installed"])
        self.assertEqual(st["version"], "0.40.1")
        self.assertEqual(fake.requests[-1], ("GET", "/ollama", {}))

    def test_other_computer_is_asked_through_the_local_node(self):
        fake = self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Laptop", installed=True, version="0.39.0"))
        self.assertEqual(node.ollama_status("b2")["version"], "0.39.0")
        self.assertEqual(fake.requests[-1], ("GET", "/ollama", {"node": "b2"}))

    def test_none_for_old_dead_or_unreachable(self):
        self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Lama", old=True), Computer("c3", "Mati", alive=False))
        self.assertIsNone(node.ollama_status("b2"))        # node lama: 404 "tidak ada"
        self.assertIsNone(node.ollama_status("c3"))        # tidak ditemukan / sedang mati
        self.assertIsNone(node.ollama_status("tidak-ada"))

    def test_none_when_the_node_is_down(self):
        with mock.patch.object(config, "NODE_URL", closed_url()):
            self.assertIsNone(node.ollama_status())

    def test_old_local_node(self):
        self.serve(Computer("a1", "PC-Utama"), old_local=True)
        self.assertIsNone(node.ollama_status())


class StartTest(NodeTestCase):
    def test_local_success(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="1"))
        code, res = node.ollama_start("a1", "uninstall")
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(res["job"]["state"], "running")
        self.assertEqual(fake.posts(), [{"node": "a1", "action": "uninstall"}])

    def test_remote_success_and_no_node_means_this_computer(self):
        fake = self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Laptop"))
        self.assertTrue(node.ollama_start("b2", "install")[1]["ok"])
        self.assertTrue(node.ollama_start(None, "install")[1]["ok"])
        self.assertEqual(fake.posts(), [{"node": "b2", "action": "install"}, {"action": "install"}])

    def test_busy_local_computer(self):
        self.serve(Computer("a1", "PC-Utama", polls=50))
        self.assertTrue(node.ollama_start("a1", "install")[1]["ok"])
        code, res = node.ollama_start("a1", "uninstall")
        self.assertEqual(code, 409)
        self.assertFalse(res["ok"])
        self.assertIn("masih ada pekerjaan 'install'", res["error"])
        self.assertEqual(res["job"]["action"], "install")

    def test_busy_remote_computer_gets_its_real_reason_not_a_bare_502(self):
        fake = self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Laptop", polls=50))
        node.ollama_start("b2", "install")
        code, res = node.ollama_start("b2", "uninstall")
        self.assertEqual(code, 502)                       # itulah yang dikatakan node: "tak terjawab"
        self.assertFalse(res["ok"])
        self.assertIn("masih ada pekerjaan 'install' yang berjalan di komputer itu", res["error"])
        self.assertEqual(res["job"]["action"], "install")
        self.assertEqual(fake.count("GET", "/ollama"), 1)  # alasannya dicari lewat satu pembacaan status

    def test_unsupported_computers_say_so(self):
        self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Mac", supported=False))
        _code, res = node.ollama_start("b2", "install")     # komputer lain: node hanya meneruskan 502
        self.assertIn("baru didukung di Windows", res["error"])
        self.assertIn("bukan Windows", res["error"])

    def test_unsupported_local_computer_keeps_the_nodes_own_message(self):
        self.serve(Computer("a1", "Mac", supported=False))
        code, res = node.ollama_start("a1", "install")
        self.assertEqual(code, 409)
        self.assertEqual(res["error"], "Pasang/copot Ollama otomatis baru didukung di Windows.")

    def test_old_nodes_ask_to_update(self):
        self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Lama", old=True))
        _code, res = node.ollama_start("b2", "install")     # lewat node baru -> 502 -> dicari tahu lewat status
        self.assertFalse(res["ok"])
        self.assertIn("versi lama", res["error"])
        self.assertIn("perbarui", res["error"])

    def test_old_local_node(self):
        self.serve(Computer("a1", "PC-Utama"), old_local=True)
        code, res = node.ollama_start("a1", "install")
        self.assertEqual(code, 404)
        self.assertIn("versi lama", res["error"])

    def test_dead_computer_keeps_the_nodes_message(self):
        self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Mati", alive=False))
        code, res = node.ollama_start("b2", "install")
        self.assertEqual(code, 404)
        self.assertIn("sedang mati", res["error"])

    def test_unknown_action_is_refused_without_calling_the_node(self):
        fake = self.serve(Computer("a1", "PC-Utama"))
        code, res = node.ollama_start("a1", "status")
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertIn("aksi tidak dikenal", res["error"])
        self.assertEqual(fake.posts(), [])

    def test_node_down_never_raises(self):
        with mock.patch.object(config, "NODE_URL", closed_url()):
            code, res = node.ollama_start("a1", "install", timeout=2)
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertIn("tidak menjawab", res["error"])


class WaitTest(NodeTestCase):
    def test_polls_until_done_and_returns_the_final_job(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="1", polls=4))
        node.ollama_start("a1", "uninstall")
        job = node.ollama_wait("a1", timeout=10, every=0.02)
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["message"], DONE_MSG["uninstall"])
        self.assertGreaterEqual(fake.count("GET", "/ollama"), 4)       # benar-benar menunggu, bukan sekali tanya

    def test_failed_job(self):
        self.serve(Computer("a1", "PC-Utama", installed=True, polls=2, outcome="failed"))
        node.ollama_start("a1", "uninstall")
        job = node.ollama_wait("a1", timeout=10, every=0.02)
        self.assertEqual(job["state"], "failed")
        self.assertIn("belum tuntas", job["message"])

    def test_waits_on_a_forwarded_computer(self):
        fake = self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Laptop", polls=3))
        node.ollama_start("b2", "install")
        job = node.ollama_wait("b2", timeout=10, every=0.02)
        self.assertEqual((job["action"], job["state"]), ("install", "done"))
        self.assertTrue(all(p.get("node") == "b2" for m, path, p in fake.requests if (m, path) == ("GET", "/ollama")))

    def test_gives_back_the_running_job_when_time_runs_out(self):
        self.serve(Computer("a1", "PC-Utama", polls=10_000))
        node.ollama_start("a1", "install")
        started = time.monotonic()
        job = node.ollama_wait("a1", timeout=0.4, every=0.05)
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["action"], "install")
        self.assertLess(time.monotonic() - started, 3)

    def test_no_job_recorded(self):
        self.serve(Computer("a1", "PC-Utama"))
        job = node.ollama_wait("a1", timeout=5, every=0.02)
        self.assertEqual(job["state"], "unknown")
        self.assertIn("tidak melaporkan pekerjaan", job["message"])

    def test_old_node_returns_at_once_instead_of_polling_until_timeout(self):
        fake = self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Lama", old=True))
        started = time.monotonic()
        job = node.ollama_wait("b2", timeout=30, every=0.05)
        self.assertEqual(job["state"], "unknown")
        self.assertIn("versi lama", job["message"])
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(fake.count("GET", "/ollama"), 1)

    def test_node_down_gives_unknown_after_the_timeout_without_raising(self):
        with mock.patch.object(config, "NODE_URL", closed_url()):
            started = time.monotonic()
            job = node.ollama_wait("a1", timeout=0.5, every=0.1)
        self.assertEqual(job["state"], "unknown")
        self.assertIn("tidak bisa dibaca", job["message"])
        self.assertLess(time.monotonic() - started, 10)

    def test_a_short_network_blip_does_not_stop_the_wait(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, polls=3))
        node.ollama_start("a1", "uninstall")
        fake.fail_next = 3        # tiga pembacaan berikutnya gagal (500)
        job = node.ollama_wait("a1", timeout=10, every=0.02)
        self.assertEqual(job["state"], "done")

    def test_computer_that_drops_off_the_network_for_a_while_is_waited_for(self):
        """Komputer yang sibuk memasang bisa sesaat tak terlihat node lain (404 'sedang mati'), lalu muncul lagi."""
        laptop = Computer("b2", "Laptop", polls=2)
        self.serve(Computer("a1", "PC-Utama"), laptop)
        node.ollama_start("b2", "install")
        laptop.alive = False
        threading.Timer(0.3, lambda: setattr(laptop, "alive", True)).start()
        job = node.ollama_wait("b2", timeout=10, every=0.05)
        self.assertEqual(job["state"], "done")


class UntrustedNamesTest(unittest.TestCase):
    """Nama komputer datang dari gosip peer: tidak tepercaya, masuk ke kartu persetujuan pemilik."""

    def test_names_are_one_line_truncated_text_and_never_raise(self):
        st = {"nodes": [{"id": "a", "name": ["bukan", "teks"]}, {"id": "b", "name": "X\nSetuju: ya"},
                        {"id": "c", "name": "N" * 500}, {"id": "d", "name": None}, {"id": "e", "name": 42}],
              "known": [{"name": "Tidur\r\nPalsu"}, {"name": 7}]}
        fleet = node.ollama_fleet(st)
        for c in fleet:
            self.assertIsInstance(c["name"], str)
            self.assertNotRegex(c["name"], r"[\x00-\x1f]")
            self.assertLessEqual(len(c["name"]), 60)
        self.assertIn("X Setuju: ya", [c["name"] for c in fleet])


class PickComputerTest(unittest.TestCase):
    ITEMS = [{"name": "PC-Utama"}, {"name": "Laptop-Budi"}, {"name": "Laptop-Ani"}, {"name": "Dapur"}]

    def test_exact_name_ignores_case(self):
        item, hits = node.pick_computer(self.ITEMS, "  laptop-ani ")
        self.assertEqual(item["name"], "Laptop-Ani")
        self.assertEqual(len(hits), 1)

    def test_unique_prefix_then_unique_fragment(self):
        self.assertEqual(node.pick_computer(self.ITEMS, "pc")[0]["name"], "PC-Utama")
        self.assertEqual(node.pick_computer(self.ITEMS, "apur")[0]["name"], "Dapur")

    def test_ambiguous_returns_candidates_and_no_pick(self):
        item, hits = node.pick_computer(self.ITEMS, "laptop")
        self.assertIsNone(item)
        self.assertEqual([c["name"] for c in hits], ["Laptop-Budi", "Laptop-Ani"])

    def test_exact_beats_longer_names_that_start_the_same(self):
        items = [{"name": "Laptop"}, {"name": "Laptop-2"}]
        self.assertEqual(node.pick_computer(items, "laptop")[0]["name"], "Laptop")

    def test_nothing_or_empty(self):
        self.assertEqual(node.pick_computer(self.ITEMS, "zzz"), (None, []))
        self.assertEqual(node.pick_computer(self.ITEMS, ""), (None, []))
        self.assertEqual(node.pick_computer(self.ITEMS, None), (None, []))
        self.assertEqual(node.pick_computer([{"name": None}, {}], "x"), (None, []))


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass   # klien yang memutus sambungan (timeout) bukan masalah


class MisbehavingNodeTest(unittest.TestCase):
    """Node yang rusak/lambat/ngawur: tidak ada fungsi ollama_* yang boleh melempar."""

    def serve(self, handler_cls):
        srv = QuietServer(("127.0.0.1", 0), handler_cls)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        patch = mock.patch.object(config, "NODE_URL", f"http://127.0.0.1:{srv.server_port}")
        patch.start()
        self.addCleanup(patch.stop)

    def check_all_calls_survive(self):
        self.assertIsNone(node.ollama_status(timeout=1))
        code, res = node.ollama_start("a1", "install", timeout=1)
        self.assertFalse(res["ok"])
        self.assertIn("error", res)
        self.assertIn(node.ollama_wait("a1", timeout=0.3, every=0.05)["state"], ("unknown",))
        self.assertEqual(node.ollama_fleet(), [])

    def test_connection_closed_without_an_answer(self):
        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                pass   # langsung tutup

        self.serve(Handler)
        self.check_all_calls_survive()

    def test_body_cut_short(self):
        class Handler(BaseHTTPRequestHandler):
            def _cut(self):
                self.send_response(200)
                self.send_header("Content-Length", "500")
                self.end_headers()
                self.wfile.write(b'{"ok": tr')

            do_GET = do_POST = _cut

            def log_message(self, *a):
                pass

        self.serve(Handler)
        self.check_all_calls_survive()

    def test_html_instead_of_json(self):
        class Handler(BaseHTTPRequestHandler):
            def _page(self):
                body = b"<html>Bad gateway</html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _page

            def log_message(self, *a):
                pass

        self.serve(Handler)
        self.check_all_calls_survive()

    def test_json_that_is_not_an_object(self):
        class Handler(BaseHTTPRequestHandler):
            def _list(self):
                body = b"[1, 2, 3]"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _list

            def log_message(self, *a):
                pass

        self.serve(Handler)
        self.check_all_calls_survive()

    def test_server_error_with_non_json_body(self):
        class Handler(BaseHTTPRequestHandler):
            def _boom(self):
                body = b"internal error"
                self.send_response(500)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _boom

            def log_message(self, *a):
                pass

        self.serve(Handler)
        self.check_all_calls_survive()

    def test_slow_node_times_out(self):
        class Handler(BaseHTTPRequestHandler):
            def _slow(self):
                time.sleep(1.5)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = do_POST = _slow

            def log_message(self, *a):
                pass

        self.serve(Handler)
        started = time.monotonic()
        self.assertIsNone(node.ollama_status(timeout=0.3))
        code, res = node.ollama_start("a1", "install", timeout=0.3)
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertLess(time.monotonic() - started, 1.4)

    def test_empty_node_url(self):
        with mock.patch.object(config, "NODE_URL", ""):
            self.check_all_calls_survive()


# --- alat copot_ollama / pasang_ollama / daftar_ollama di atas node (HTTP sungguhan) --------
class Ctx(dict):
    """ctx alat dengan konfirmasi palsu yang mencatat teksnya."""

    def __init__(self, answer=True, **extra):
        self.asked = []
        self.answer = answer
        super().__init__(confirm=self._confirm, notify=lambda text: None, full_access=False, guest=False)
        self.update(extra)

    def _confirm(self, text):
        self.asked.append(text)
        return self.answer


class ToolsOverHttpTest(NodeTestCase):
    def setUp(self):
        original = node.ollama_wait
        patch = mock.patch.object(node, "ollama_wait",
                                  lambda node_id, timeout=150.0, every=0.02: original(node_id, timeout=timeout, every=every))
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, name, args=None, ctx=None):
        ctx = ctx or Ctx()
        return tools.handle_tool(name, args or {}, ctx), ctx

    def test_single_computer_uninstall_end_to_end(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1", polls=3))
        out, ctx = self.call("copot_ollama")
        self.assertEqual(len(ctx.asked), 1)
        self.assertIn("PC-Utama", ctx.asked[0])
        self.assertIn("PC-Utama: " + DONE_MSG["uninstall"], out)
        self.assertEqual(fake.posts(), [{"node": "a1", "action": "uninstall"}])
        self.assertFalse(fake.computers["a1"].installed)

    def test_several_computers_without_a_name_ask_which_and_change_nothing(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1"),
                          Computer("b2", "Laptop"), Computer("c3", "PC-Kantor", installed=True, version="0.39.0"))
        for name in ("copot_ollama", "pasang_ollama"):
            out, ctx = self.call(name)
            for computer in ("PC-Utama", "Laptop", "PC-Kantor"):
                self.assertIn(computer, out)
            self.assertIn("semua", out)
            self.assertIn("tidak menebak", out)
            self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_named_computer_is_forwarded_and_polled_to_the_end(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True), Computer("b2", "Laptop-Budi", installed=True,
                                                                                version="0.39.0", polls=4))
        out, ctx = self.call("copot_ollama", {"komputer": "budi"})        # potongan nama yang unik
        self.assertEqual(len(ctx.asked), 1)
        self.assertIn("Laptop-Budi", ctx.asked[0])
        self.assertNotIn("PC-Utama", ctx.asked[0])
        self.assertIn("Laptop-Budi: " + DONE_MSG["uninstall"], out)
        self.assertEqual(fake.posts(), [{"node": "b2", "action": "uninstall"}])
        self.assertTrue(fake.computers["a1"].installed)                    # komputer lain tidak tersentuh

    def test_install_end_to_end_reports_the_version(self):
        self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Laptop", polls=2))
        out, ctx = self.call("pasang_ollama", {"komputer": "Laptop"})
        self.assertIn("Laptop: " + DONE_MSG["install"], out)
        self.assertIn("Pasang Ollama di Laptop", ctx.asked[0])

    def test_name_not_found_ambiguous_and_asleep(self):
        fake = self.serve(Computer("a1", "Laptop-Budi", installed=True), Computer("b2", "Laptop-Ani", installed=True),
                          known=[{"name": "Dapur", "ts": 1.0}])
        out, ctx = self.call("copot_ollama", {"komputer": "xyz"})
        self.assertIn("tidak ketemu", out)
        self.assertIn("Laptop-Budi", out)
        self.assertIn("Dapur", out)
        out, ctx = self.call("copot_ollama", {"komputer": "laptop"})
        self.assertIn("cocok dengan beberapa komputer", out)
        self.assertIn("Laptop-Budi dan Laptop-Ani", out)
        out, ctx = self.call("copot_ollama", {"komputer": "dapur"})
        self.assertIn("sedang tidur/mati", out)
        self.assertIn("/nyalakan Dapur", out)
        self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_ini_means_the_computer_the_bot_runs_on(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, polls=1), Computer("b2", "Laptop", installed=True),
                          me="a1")
        out, ctx = self.call("copot_ollama", {"komputer": "komputer ini"})
        self.assertIn("PC-Utama: " + DONE_MSG["uninstall"], out)
        self.assertEqual(fake.posts(), [{"node": "a1", "action": "uninstall"}])

    def test_semua_uninstall_takes_only_computers_with_ollama_and_explains_the_rest(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1", polls=2),
                          Computer("b2", "Laptop"), Computer("c3", "PC-Lama", old=True),
                          Computer("d4", "PC-Kantor", installed=True, version="0.39.0", polls=3),
                          known=[{"name": "Dapur", "ts": 1.0}])
        out, ctx = self.call("copot_ollama", {"komputer": "semua"})
        self.assertEqual(len(ctx.asked), 1)                                   # SEKALI untuk seluruh batch
        self.assertIn("PC-Utama dan PC-Kantor", ctx.asked[0])
        self.assertNotIn("Laptop", ctx.asked[0])
        self.assertIn("PC-Utama: " + DONE_MSG["uninstall"], out)
        self.assertIn("PC-Kantor: " + DONE_MSG["uninstall"], out)
        self.assertIn("Dilewati:", out)
        self.assertIn("Laptop: Ollama belum terpasang", out)
        self.assertIn("PC-Lama: status Ollama tidak diketahui", out)
        self.assertNotIn("Dapur", out)                                        # tidur: bukan 'online', tak disebut
        self.assertEqual(sorted(p["node"] for p in fake.posts()), ["a1", "d4"])

    def test_semua_install_takes_only_computers_without_ollama(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1"), Computer("b2", "Laptop", polls=2),
                          Computer("c3", "PC-Kantor", polls=2))
        out, ctx = self.call("pasang_ollama", {"komputer": "SEMUA"})
        self.assertIn("Laptop dan PC-Kantor", ctx.asked[0])
        self.assertIn("Laptop: " + DONE_MSG["install"], out)
        self.assertIn("PC-Utama: Ollama sudah terpasang v0.40.1", out)
        self.assertEqual(sorted(p["node"] for p in fake.posts()), ["b2", "c3"])

    def test_semua_with_nothing_to_do(self):
        fake = self.serve(Computer("a1", "PC-Utama"), Computer("b2", "Laptop"))
        out, ctx = self.call("copot_ollama", {"komputer": "semua"})
        self.assertIn("Tidak ada komputer online yang perlu dicopot Ollama-nya", out)
        self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_computers_are_handled_at_the_same_time(self):
        """Dua POST harus tiba bersamaan: Barrier ini hanya terbuka kalau keduanya sedang berjalan (kalau
        dikerjakan satu per satu, yang pertama menunggu sia-sia sampai Barrier habis waktu dan gagal)."""
        barrier = threading.Barrier(2, timeout=5)
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, polls=2, on_start=barrier.wait),
                          Computer("b2", "Laptop", installed=True, polls=2, on_start=barrier.wait))
        out, ctx = self.call("copot_ollama", {"komputer": "semua"})
        self.assertEqual(len(fake.posts()), 2)
        self.assertIn("PC-Utama: " + DONE_MSG["uninstall"], out)
        self.assertIn("Laptop: " + DONE_MSG["uninstall"], out)

    def test_declined_changes_nothing(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True))
        out, ctx = self.call("copot_ollama", ctx=Ctx(answer=False))
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        self.assertEqual(len(ctx.asked), 1)
        self.assertEqual(fake.posts(), [])

    def test_full_access_still_asks(self):
        self.serve(Computer("a1", "PC-Utama", installed=True, polls=1))
        out, ctx = self.call("copot_ollama", {}, Ctx(full_access=True))
        self.assertEqual(len(ctx.asked), 1)
        self.assertIn(DONE_MSG["uninstall"], out)
        self.serve(Computer("a1", "PC-Utama", installed=False, polls=1))
        out, ctx = self.call("pasang_ollama", {}, Ctx(full_access=True))
        self.assertEqual(len(ctx.asked), 1)
        self.assertIn(DONE_MSG["install"], out)

    def test_already_in_the_wanted_state_needs_no_approval(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1"), Computer("b2", "Laptop"))
        out, ctx = self.call("pasang_ollama", {"komputer": "PC-Utama"})
        self.assertIn("sudah terpasang di PC-Utama (versi 0.40.1)", out)
        out2, ctx2 = self.call("copot_ollama", {"komputer": "Laptop"})
        self.assertIn("memang tidak terpasang di Laptop", out2)
        self.assertEqual(ctx.asked + ctx2.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_leftover_files_without_installer_are_explained(self):
        self.serve(Computer("a1", "PC-Utama", installed=False, leftover=True,
                            path=r"C:\Users\Ani\AppData\Local\Programs\Ollama\ollama.exe"))
        out, ctx = self.call("copot_ollama")
        self.assertIn("memang tidak terpasang di PC-Utama", out)
        self.assertIn(r"C:\Users\Ani\AppData\Local\Programs\Ollama\ollama.exe", out)
        self.assertEqual(ctx.asked, [])

    def test_computer_with_a_running_job_is_not_started_again(self):
        job = {"action": "install", "state": "running", "step": "mengunduh", "started": 1, "ended": None,
               "message": "", "log": ""}
        fake = self.serve(Computer("a1", "PC-Utama", job=job))
        out, ctx = self.call("copot_ollama")
        self.assertIn("Masih ada pekerjaan pasang yang berjalan di PC-Utama (mengunduh)", out)
        self.assertIn("daftar_ollama", out)
        self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_gossip_that_lags_behind_a_busy_computer_is_caught_by_the_node(self):
        job = {"action": "install", "state": "running", "step": "mengunduh", "started": 1, "ended": None,
               "message": "", "log": ""}
        fake = self.serve(Computer("a1", "PC-Utama", installed=True), Computer("b2", "Laptop", job=job, stale=True))
        out, ctx = self.call("pasang_ollama", {"komputer": "semua"})          # gosip belum menampilkan pekerjaannya
        self.assertIn("Laptop: gagal dimulai - masih ada pekerjaan 'install' yang berjalan di komputer itu", out)
        self.assertEqual(len(ctx.asked), 1)

    def test_failed_job_is_reported_with_the_nodes_reason(self):
        self.serve(Computer("a1", "PC-Utama", installed=True, polls=2, outcome="failed"))
        out, ctx = self.call("copot_ollama")
        self.assertIn("PC-Utama: gagal - " + FAIL_MSG["uninstall"], out)

    def test_job_still_running_after_the_wait_points_to_daftar_ollama(self):
        self.serve(Computer("a1", "PC-Utama", polls=10_000))
        with mock.patch.object(tools, "_OLLAMA_WAIT", 0.4):
            out, ctx = self.call("pasang_ollama")
        self.assertIn("PC-Utama: masih berjalan (mulai)", out)
        self.assertIn("daftar_ollama", out)

    def test_old_nodes_are_told_to_update_without_asking(self):
        fake = self.serve(Computer("a1", "PC-Utama"), old_local=True)       # seperti node yang berjalan sekarang
        for name in ("copot_ollama", "pasang_ollama"):
            out, ctx = self.call(name)
            self.assertIn("versi lama", out)
            self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_non_windows_computer_is_refused_before_asking(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True), Computer("b2", "MacBook", supported=False))
        out, ctx = self.call("pasang_ollama", {"komputer": "macbook"})
        self.assertIn("bukan komputer Windows", out)
        self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_daftar_ollama_shows_every_computer_and_asks_nothing(self):
        job = {"action": "install", "state": "running", "step": "mengunduh", "started": 1, "ended": None,
               "message": "", "log": ""}
        fake = self.serve(Computer("a1", "PC-Utama", installed=True, version="0.40.1"),
                          Computer("b2", "Laptop", job=job), Computer("c3", "PC-Lama", old=True),
                          me="a1", known=[{"name": "Dapur", "ts": 1.0}])
        out, ctx = self.call("daftar_ollama")
        self.assertIn("PC-Utama (komputer ini): Ollama terpasang v0.40.1", out)
        self.assertIn("Laptop: Ollama belum terpasang; sedang pasang (mengunduh)", out)
        self.assertIn("PC-Lama: status Ollama tidak diketahui", out)
        self.assertIn("Dapur: sedang tidur/mati", out)
        self.assertIn("3 online, 1 tidur/mati", out)
        self.assertIn("versi lama", out)
        self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.posts(), [])

    def test_guest_gets_none_of_them(self):
        fake = self.serve(Computer("a1", "PC-Utama", installed=True))
        for name in ("copot_ollama", "pasang_ollama", "daftar_ollama"):
            out, ctx = self.call(name, ctx=Ctx(guest=True))
            self.assertIn("Mode tamu", out)
            self.assertEqual(ctx.asked, [])
        self.assertEqual(fake.requests, [])


class ToolsWithoutNodeTest(unittest.TestCase):
    """Tidak ada node yang menjawab: copot untuk komputer ini lewat ollama_admin, selebihnya pesan yang jelas."""

    def setUp(self):
        for patch in (mock.patch.object(node, "ollama_overview", return_value=(None, [])),
                      mock.patch.object(ollama_admin, "installed", return_value=True),
                      mock.patch.object(ollama_admin, "uninstall", return_value=(True, "sudah dicopot")),
                      mock.patch.object(config, "DEVICE_NAME", "PC-Utama")):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, name, args=None, ctx=None):
        ctx = ctx or Ctx()
        return tools.handle_tool(name, args or {}, ctx), ctx

    def test_uninstall_this_computer_directly(self):
        out, ctx = self.call("copot_ollama")
        self.assertEqual(out, "sudah dicopot")
        self.assertEqual(len(ctx.asked), 1)
        ollama_admin.uninstall.assert_called_once()

    def test_own_name_and_aliases_are_accepted(self):
        for name in ("pc-utama", "ini", "semua"):
            out, ctx = self.call("copot_ollama", {"komputer": name})
            self.assertEqual(out, "sudah dicopot", name)

    def test_other_computers_cannot_be_reached(self):
        out, ctx = self.call("copot_ollama", {"komputer": "Laptop"})
        self.assertIn("Laptop", out)
        self.assertIn("tidak bisa dijangkau", out)
        self.assertEqual(ctx.asked, [])
        ollama_admin.uninstall.assert_not_called()

    def test_install_needs_the_node(self):
        out, ctx = self.call("pasang_ollama")
        self.assertIn("butuh It's Me Node", out)
        self.assertEqual(ctx.asked, [])

    def test_list_reports_only_this_computer(self):
        out, ctx = self.call("daftar_ollama")
        self.assertIn("hanya komputer ini", out)
        self.assertEqual(ctx.asked, [])

    def test_nothing_to_uninstall_needs_no_approval(self):
        ollama_admin.installed.return_value = False
        out, ctx = self.call("copot_ollama")
        self.assertIn("tidak terpasang", out)
        self.assertEqual(ctx.asked, [])
        ollama_admin.uninstall.assert_not_called()

    def test_declined(self):
        out, ctx = self.call("copot_ollama", ctx=Ctx(answer=False))
        self.assertEqual(out, "Dibatalkan oleh pemilik.")
        ollama_admin.uninstall.assert_not_called()


class ToolsWithNodeNotListeningTest(unittest.TestCase):
    """Node benar-benar tidak ada di NODE_URL (tanpa menggantikan ollama_overview)."""

    def test_uninstall_falls_back_to_the_local_uninstaller(self):
        with mock.patch.object(config, "NODE_URL", closed_url()),              mock.patch.object(ollama_admin, "installed", return_value=True),              mock.patch.object(ollama_admin, "uninstall", return_value=(True, "sudah dicopot")) as uninstall:
            ctx = Ctx()
            out = tools.handle_tool("copot_ollama", {}, ctx)
        self.assertEqual(out, "sudah dicopot")
        self.assertEqual(len(ctx.asked), 1)
        uninstall.assert_called_once()


if __name__ == "__main__":
    unittest.main()
