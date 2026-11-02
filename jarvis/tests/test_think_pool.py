"""Uji think_pool.py: membagi pekerjaan berpikir ke semua komputer, pindah ke yang lain saat gagal,
sabar menunggu saat sibuk / pemilihan pemimpin. Node tiruan: status & /think diganti (kecuali
RealHttpTest yang memakai server HTTP sungguhan lewat httpx seperti di produksi)."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import httpx

from itsme import config, node, think_pool


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def row(name, model="qwen3:1.7b", ok=True, tps=10.0, inflight=0, waiting=0, ctl=True, leader=False, think=True):
    t = {"model": model, "ok": ok, "up": ok, "loaded": True, "ctl": ctl, "tps": tps, "inflight": inflight,
         "waiting": waiting, "slots": 1, "served": 3, "restarts": 0, "cpu": True, "ctx": 8192}
    return {"id": name.lower(), "name": name, "leader": leader, "think": t if think else None}


def status(*rows, phase="running"):
    return {"me": rows[0]["id"] if rows else "", "phase": phase, "leader": rows[0]["id"] if rows else None,
            "nodes": list(rows)}


def good(name, text="halo", model="m"):
    return {"ok": True, "message": {"role": "assistant", "content": text}, "name": name, "model": model,
            "ms": 1500, "tps": 7.5}


class Base(unittest.TestCase):
    def setUp(self):
        think_pool.reset()
        self.addCleanup(think_pool.reset)
        self.clock = Clock()
        self.st = status(row("A"))
        self.posts = []          # (payload, timeout)
        self.script = {}         # id komputer -> antrean jawaban /think (dict | Exception)
        for p in (
            mock.patch.object(think_pool, "_clock", self.clock),
            mock.patch.object(think_pool, "_sleep", self.clock.advance),
            mock.patch.object(node, "installed", return_value=True),
            mock.patch.object(node, "status", side_effect=lambda timeout=3: self.st),
            mock.patch.object(think_pool, "_post", side_effect=self.fake_post),
            mock.patch.object(config, "CLUSTER_WAIT", 30.0),
            mock.patch.object(config, "CLUSTER_TIMEOUT", 600.0),
            mock.patch.object(config, "CLUSTER_FAST_TIMEOUT", 100.0),
        ):
            p.start()
            self.addCleanup(p.stop)

    def set_status(self, st):
        """Ganti keadaan jaringan di tengah uji (status disimpan 2 detik, jadi simpanan dikosongkan)."""
        self.st = st
        think_pool._snap["at"] = -1e9

    def fake_post(self, payload, timeout):
        self.posts.append((payload, timeout))
        queue = self.script.get(payload["node"])
        item = queue.pop(0) if queue else good(payload["node"].upper())
        if isinstance(item, Exception):
            raise item
        return item

    def targets(self):
        return [p["node"] for p, _ in self.posts]

    def http_503(self):
        req = httpx.Request("POST", "http://x/think")
        return httpx.HTTPStatusError("503", request=req, response=httpx.Response(503, request=req))


class SizeAndScoreTest(Base):
    def test_ukuran_model_dari_nama(self):
        for name, size in (("qwen3:1.7b", 1.7), ("qwen3:4b-instruct-2507-q4_K_M", 4.0), ("qwen3.5:9b", 9.0),
                           ("qwen3:8b-q8_0", 8.0), ("gemma3:12b", 12.0), ("llama3", 1.0), ("", 1.0)):
            self.assertEqual(think_pool.size_b(name), size, name)

    def test_jawaban_mengutamakan_model_besar(self):
        self.st = status(row("Kecil", "qwen3:1.7b", tps=12), row("Besar", "qwen3:8b", tps=5))
        self.assertEqual([r["name"] for r in think_pool.candidates("answer")], ["Besar", "Kecil"])

    def test_langkah_cepat_mengutamakan_yang_tercepat(self):
        self.st = status(row("Kecil", "qwen3:1.7b", tps=12), row("Besar", "qwen3:8b", tps=5))
        self.assertEqual([r["name"] for r in think_pool.candidates("fast")], ["Kecil", "Besar"])

    def test_beban_menggeser_ke_komputer_lowong(self):
        # Yang besar sedang bekerja dua permintaan: yang lowong didahulukan walau modelnya lebih kecil.
        self.st = status(row("Besar", "qwen3:8b", tps=5, inflight=1, waiting=1), row("Kecil", "qwen3:4b", tps=5))
        self.assertEqual(think_pool.candidates("answer")[0]["name"], "Kecil")

    def test_model_selalu_berpikir_dipakai_terakhir(self):
        self.st = status(row("Dalam", "qwen3:8b", tps=9, ctl=False), row("Biasa", "qwen3:1.7b", tps=3))
        self.assertEqual([r["name"] for r in think_pool.candidates("answer")], ["Biasa"])
        self.set_status(status(row("Dalam", "qwen3:8b", tps=9, ctl=False)))
        self.assertEqual([r["name"] for r in think_pool.candidates("answer")], ["Dalam"])   # tak ada pilihan lain

    def test_think_false_belum_teruji_sedikit_diturunkan(self):
        self.st = status(row("Baru", "qwen3:4b", ctl=None), row("Teruji", "qwen3:4b", ctl=True))
        self.assertEqual(think_pool.candidates("answer")[0]["name"], "Teruji")

    def test_yang_tidak_siap_atau_istirahat_atau_dikecualikan_tidak_masuk(self):
        self.st = status(row("A"), row("B", ok=False), row("C", model=""), row("D", think=False), row("E"), row("F"))
        think_pool._rest("e", "crashed")
        self.assertEqual(sorted(r["name"] for r in think_pool.candidates("answer", exclude=("f",))), ["A"])
        self.clock.advance(121)   # istirahat E habis
        self.assertEqual(sorted(r["name"] for r in think_pool.candidates("answer", exclude=("f",))), ["A", "E"])


class AskTest(Base):
    def test_memilih_terbaik_dan_mengirim_permintaan_lengkap(self):
        self.st = status(row("Kecil", "qwen3:1.7b", tps=12), row("Besar", "qwen3:8b", tps=5))
        msgs = [{"role": "user", "content": "halo"}]
        reply = think_pool.ask(msgs, role="answer", think="auto", options={"temperature": 0.3}, tools=[{"t": 1}], fmt="json")
        payload, timeout = self.posts[0]
        self.assertEqual(payload["node"], "besar")
        self.assertEqual((payload["messages"], payload["options"], payload["tools"], payload["format"]),
                         (msgs, {"temperature": 0.3}, [{"t": 1}], "json"))
        self.assertIs(payload["think"], False)               # 'auto' untuk model yang bisa dikendalikan = jawab langsung
        self.assertEqual(payload["timeout"], 600.0)
        self.assertEqual(timeout, 630.0)                    # + jeda jaringan
        self.assertEqual((reply.node, reply.text, reply.model, reply.tps), ("BESAR", "halo", "m", 7.5))

    def test_think_true_dipaksa_dan_model_selalu_berpikir_selalu_true(self):
        think_pool.ask([{"role": "user", "content": "x"}], think=True)
        self.assertIs(self.posts[-1][0]["think"], True)
        self.set_status(status(row("Dalam", "qwen3:8b", ctl=False)))
        think_pool.ask([{"role": "user", "content": "x"}], think=False)
        self.assertIs(self.posts[-1][0]["think"], True)     # think:false diabaikan model itu; penalaran harus terpisah

    def test_langkah_cepat_timeout_lebih_pendek(self):
        think_pool.ask([{"role": "user", "content": "x"}], role="fast")
        self.assertEqual(self.posts[0][0]["timeout"], 100.0)

    def test_pindah_ke_komputer_lain_saat_gagal(self):
        self.st = status(row("Besar", "qwen3:8b"), row("Kecil", "qwen3:1.7b"))
        self.script["besar"] = [{"ok": False, "kind": "crashed", "error": "model mati"}]
        reply = think_pool.ask([{"role": "user", "content": "x"}])
        self.assertEqual(self.targets(), ["besar", "kecil"])
        self.assertEqual(reply.node, "KECIL")
        # Yang gagal diistirahatkan (120 dtk): permintaan berikutnya langsung ke yang sehat.
        think_pool.ask([{"role": "user", "content": "y"}])
        self.assertEqual(self.targets(), ["besar", "kecil", "kecil"])
        self.clock.advance(121)
        think_pool.ask([{"role": "user", "content": "z"}])
        self.assertEqual(self.targets()[-1], "besar")

    def test_koneksi_ke_komputer_putus(self):
        self.st = status(row("Besar", "qwen3:8b"), row("Kecil", "qwen3:1.7b"))
        self.script["besar"] = [{"ok": False, "kind": "unreachable", "error": "tak menjawab"}]
        self.assertEqual(think_pool.ask([{"role": "user", "content": "x"}]).node, "KECIL")

    def test_sibuk_dicoba_lagi_nanti_bukan_dicoret(self):
        self.script["a"] = [{"ok": False, "kind": "busy", "error": "antrean penuh"}]
        reply = think_pool.ask([{"role": "user", "content": "x"}])    # satu-satunya komputer
        self.assertEqual(self.targets(), ["a", "a"])
        self.assertEqual(reply.text, "halo")
        self.assertGreaterEqual(self.clock.now, 1006.0)               # menunggu istirahat 6 detik, lalu berhasil

    def test_http_503_berarti_pemilihan_pemimpin(self):
        self.script["a"] = [self.http_503()]
        self.assertEqual(think_pool.ask([{"role": "user", "content": "x"}]).text, "halo")
        self.assertEqual(self.targets(), ["a", "a"])

    def test_sedang_memilih_pemimpin_ditunggu_lalu_lanjut_sendiri(self):
        self.st = status(row("A"), phase="electing")
        calls = []

        def pulih(seconds):
            calls.append(seconds)
            self.clock.advance(seconds)
            if len(calls) == 3:
                self.st = status(row("A"), phase="running")

        with mock.patch.object(think_pool, "_sleep", pulih):
            reply = think_pool.ask([{"role": "user", "content": "x"}], wait=60)
        self.assertEqual(reply.text, "halo")
        self.assertEqual(len(self.posts), 1)       # tidak ada permintaan selama pemilihan, hanya setelah pulih

    def test_pemilihan_tak_kunjung_selesai_pesan_jelas(self):
        self.st = status(row("A"), phase="electing")
        with self.assertRaises(think_pool.ClusterUnavailable) as cm:
            think_pool.ask([{"role": "user", "content": "x"}], wait=10)
        self.assertIn("memilih pemimpin", str(cm.exception))
        self.assertGreaterEqual(self.clock.now, 1010.0)

    def test_semua_gagal_pesan_menyebut_komputernya(self):
        self.st = status(row("Besar", "qwen3:8b"), row("Kecil", "qwen3:1.7b"))
        self.script["besar"] = [{"ok": False, "kind": "crashed", "error": "x"}]
        self.script["kecil"] = [{"ok": False, "kind": "timeout", "error": "y"}]
        with self.assertRaises(think_pool.ClusterUnavailable) as cm:
            think_pool.ask([{"role": "user", "content": "x"}])
        text = str(cm.exception)
        self.assertIn("Besar modelnya terus berhenti mendadak", text)
        self.assertIn("Kecil terlalu lama menjawab", text)

    def test_tidak_ada_yang_punya_model(self):
        self.st = status(row("Tanpa", model=""), row("Lama", think=False), row("Mati", ok=False))
        with self.assertRaises(think_pool.ClusterUnavailable) as cm:
            think_pool.ask([{"role": "user", "content": "x"}])
        text = str(cm.exception)
        self.assertIn("Tanpa belum punya model", text)
        self.assertIn("Lama belum diperbarui", text)
        self.assertIn("Mati Ollama-nya belum menyala", text)
        self.assertEqual(self.posts, [])

    def test_node_tidak_terpasang(self):
        with mock.patch.object(node, "installed", return_value=False):
            with self.assertRaises(think_pool.ClusterUnavailable) as cm:
                think_pool.ask([{"role": "user", "content": "x"}])
        self.assertIn("belum terpasang", str(cm.exception))

    def test_node_di_komputer_ini_tidak_menjawab(self):
        self.st = None
        with self.assertRaises(think_pool.ClusterUnavailable) as cm:
            think_pool.ask([{"role": "user", "content": "x"}], wait=5)
        self.assertIn("tidak menjawab", str(cm.exception))

    def test_node_lokal_putus_di_tengah_jalan(self):
        self.script["a"] = [httpx.ConnectError("ditolak")]
        with self.assertRaises(think_pool.ClusterUnavailable) as cm:
            think_pool.ask([{"role": "user", "content": "x"}])
        self.assertIn("tidak menjawab", str(cm.exception))

    def test_komputer_yang_dikecualikan(self):
        self.st = status(row("Besar", "qwen3:8b"), row("Kecil", "qwen3:1.7b"))
        think_pool.ask([{"role": "user", "content": "x"}], exclude=("besar",))
        self.assertEqual(self.targets(), ["kecil"])
        with self.assertRaises(think_pool.ClusterUnavailable):
            think_pool.ask([{"role": "user", "content": "x"}], exclude=("besar", "kecil"))

    def test_permintaan_bersamaan_menyebar_ke_semua_komputer(self):
        """Dua permintaan serentak tidak menumpuk di satu komputer, walau kabar gosip belum berubah."""
        self.st = status(row("Besar", "qwen3:8b", tps=6), row("Kecil", "qwen3:4b", tps=6))
        gate, results = threading.Event(), []

        def slow_post(payload, timeout):
            self.posts.append((payload, timeout))
            gate.wait(5)                # panggilan pertama masih berjalan ketika yang kedua memilih
            return good(payload["node"].upper())

        with mock.patch.object(think_pool, "_post", slow_post):
            threads = [threading.Thread(target=lambda: results.append(
                think_pool.ask([{"role": "user", "content": "x"}]).node)) for _ in range(2)]
            for t in threads:
                t.start()
            end = time.time() + 5
            while len(self.posts) < 2 and time.time() < end:
                time.sleep(0.01)
            targets = sorted(p["node"] for p, _ in self.posts)
            gate.set()
            for t in threads:
                t.join(10)
        self.assertEqual(targets, ["besar", "kecil"])           # tidak menumpuk di satu komputer
        self.assertEqual(sorted(results), ["BESAR", "KECIL"])
        self.assertEqual(think_pool._reserved, {"besar": 0, "kecil": 0})   # pesanan dilepas lagi

    def test_wait_membatasi_retry_node_yang_terus_busy(self):
        self.script["a"] = [{"ok": False, "kind": "busy", "error": "antrean penuh"}] * 10
        with self.assertRaises(think_pool.ClusterUnavailable):
            think_pool.ask([ {"role": "user", "content": "x"} ], wait=3)
        self.assertLessEqual(self.clock.now, 1004.0)
        self.assertEqual(len(self.posts), 1)

    def test_http_timeout_tidak_melebihi_budget_yang_tersisa(self):
        with think_pool.budget(10.0):
            self.clock.advance(8.0)
            think_pool.ask([{"role": "user", "content": "x"}])
        self.assertLessEqual(self.posts[0][1], 2.0)

    def test_pemesanan_dilepas_walau_gagal(self):
        self.script["a"] = [{"ok": False, "kind": "crashed", "error": "x"}]
        with self.assertRaises(think_pool.ClusterUnavailable):
            think_pool.ask([{"role": "user", "content": "x"}])
        self.assertEqual(think_pool._reserved.get("a", 0), 0)


class BudgetTest(Base):
    def test_waktu_habis_melempar(self):
        with think_pool.budget(5.0):
            self.clock.advance(4.5)
            with self.assertRaises(think_pool.ClusterUnavailable) as cm:
                think_pool.ask([{"role": "user", "content": "x"}])
        self.assertIn("habis", str(cm.exception))
        self.assertEqual(self.posts, [])
        self.assertIsNone(think_pool._remaining())     # anggaran dilepas di luar blok

    def test_panggilan_dibatasi_sisa_waktu(self):
        with think_pool.budget(120.0):
            think_pool.ask([{"role": "user", "content": "x"}])
        self.assertEqual(self.posts[0][0]["timeout"], 120.0)    # bukan 600 detik bawaan
        self.assertEqual(self.posts[0][1], 120.0)                # anggaran total juga membatasi HTTP

    def test_menunggu_dibatasi_anggaran(self):
        self.st = status(row("A", ok=True), phase="electing")
        with think_pool.budget(8.0):
            with self.assertRaises(think_pool.ClusterUnavailable):
                think_pool.ask([{"role": "user", "content": "x"}], wait=600)
        self.assertLessEqual(self.clock.now, 1000.0 + 12)       # tidak menunggu 600 detik

    def test_bersarang_memakai_yang_lebih_ketat(self):
        with think_pool.budget(100.0):
            with think_pool.budget(10.0):
                self.assertLessEqual(think_pool._remaining(), 10.0)
            self.assertGreater(think_pool._remaining(), 50.0)


class ReportTest(Base):
    def test_describe_dan_report(self):
        self.st = status(row("Besar", "qwen3:8b", tps=5.0, leader=True), row("Kecil", "qwen3:1.7b", tps=12.0, inflight=1),
                         row("Tanpa", model=""), row("Lama", think=False), row("Mati", ok=False))
        self.assertIn("2 komputer berpikir", think_pool.describe())
        text = "\n".join(think_pool.report())
        self.assertIn("2 dari 5 komputer siap berpikir", text)
        self.assertIn("✅ Besar ⭐ pemimpin — qwen3:8b · 5.0 tok/dtk · CPU saja · lowong · sudah melayani 3x", text)
        self.assertIn("✅ Kecil — qwen3:1.7b · 12.0 tok/dtk · CPU saja · sedang mengerjakan 1", text)
        self.assertIn("Tanpa — belum punya model", text)
        self.assertIn("Lama — belum memperbarui It's Me Node", text)
        self.assertIn("Mati — qwen3:1.7b: Ollama belum menyala", text)

    def test_report_menyebut_istirahat_dan_pemilihan(self):
        self.st = status(row("A"), phase="electing")
        think_pool._last_error["a"] = "HTTP 503"
        think_pool._rest("a", "paused")
        text = "\n".join(think_pool.report())
        self.assertIn("memilih pemimpin baru", text)
        self.assertIn("istirahat 5 dtk (HTTP 503)", text)

    def test_describe_tanpa_komputer_siap(self):
        self.st = status(row("A", ok=False))
        self.assertIn("belum ada komputer yang siap", think_pool.describe())

    def test_report_saat_node_tidak_menjawab(self):
        self.st = None
        self.assertIn("tidak menjawab", think_pool.report()[0])


class _FakeNode(BaseHTTPRequestHandler):
    """Node It's Me tiruan di komputer ini: /status dan /think lewat HTTP sungguhan."""

    server_state = None

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, self.server.state["status"]) if self.path == "/status" else self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.server.state["requests"].append((self.path, body))
        steps = self.server.state["think"]
        item = steps.pop(0) if steps else good(body["node"].upper())
        if isinstance(item, int):
            return self._send(item, {"error": "sedang tidak bisa"})
        self._send(200, item)


class RealHttpTest(unittest.TestCase):
    """Jalur httpx sungguhan ke node (tanpa mengganti _post)."""

    def setUp(self):
        think_pool.reset()
        self.addCleanup(think_pool.reset)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeNode)
        self.server.daemon_threads = True
        self.server.state = {"status": status(row("Besar", "qwen3:8b"), row("Kecil", "qwen3:1.7b")),
                             "think": [], "requests": []}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        for p in (mock.patch.object(config, "NODE_URL", url), mock.patch.object(node, "installed", return_value=True),
                  mock.patch.object(config, "CLUSTER_WAIT", 5.0),
                  mock.patch.object(think_pool, "_sleep", lambda s: time.sleep(0.05)),
                  mock.patch.dict(think_pool.COOLDOWN, {"paused": 0.1})):
            p.start()
            self.addCleanup(p.stop)

    def test_satu_putaran_penuh(self):
        reply = think_pool.ask([{"role": "user", "content": "halo"}], options={"temperature": 0.3}, fmt="json")
        path, body = self.server.state["requests"][0]
        self.assertEqual(path, "/think")
        self.assertEqual((body["node"], body["messages"][0]["content"], body["format"]), ("besar", "halo", "json"))
        self.assertEqual((reply.node, reply.text), ("BESAR", "halo"))

    def test_503_dari_node_lalu_pulih(self):
        self.server.state["think"] = [503]
        reply = think_pool.ask([{"role": "user", "content": "halo"}])
        self.assertEqual(reply.text, "halo")
        self.assertEqual(len(self.server.state["requests"]), 2)

    def test_pindah_komputer_lewat_http(self):
        self.server.state["think"] = [{"ok": False, "kind": "crashed", "error": "x"}]
        reply = think_pool.ask([{"role": "user", "content": "halo"}])
        self.assertEqual([b["node"] for _, b in self.server.state["requests"]], ["besar", "kecil"])
        self.assertEqual(reply.node, "KECIL")

    def test_status_dibaca_lewat_http(self):
        rows = think_pool.snapshot(force=True)
        self.assertEqual([(r["name"], r["model"], r["ok"], r["size_b"]) for r in rows],
                         [("Besar", "qwen3:8b", True, 8.0), ("Kecil", "qwen3:1.7b", True, 1.7)])


if __name__ == "__main__":
    unittest.main()
