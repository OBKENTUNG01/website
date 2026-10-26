"""Uji penjaga SSRF level-koneksi: DNS rebinding tidak boleh lolos ke alamat lokal."""

import http.server
import threading
import unittest
from unittest import mock

from itsme import web


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><title>lokal</title><body>rahasia</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class RebindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_koneksi_ke_loopback_ditolak(self):
        """_check_url meloloskan (disimulasikan), tapi peer asli 127.0.0.1 harus ditolak."""
        url = f"http://localhost:{self.port}/"
        with mock.patch.object(web, "_check_url", return_value=None):
            hasil = web.fetch(url)
        self.assertIn("bukan alamat internet publik", hasil)
        self.assertNotIn("rahasia", hasil)

    def test_halaman_publik_tetap_jalan(self):
        """Alamat publik normal tidak boleh ikut ditolak."""
        hasil = web.fetch("https://example.com", max_chars=200)
        self.assertTrue(hasil.startswith("[200]"), hasil[:80])
        self.assertIn("Example Domain", hasil)


if __name__ == "__main__":
    unittest.main()
