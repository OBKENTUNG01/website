"""Uji pemeriksaan pembaruan: permintaan bersyarat supaya kuota GitHub tidak habis.

Gejala nyata di mesin pemilik: "Pemeriksaan pembaruan gagal: GitHub menjawab 403"
tiap 2 menit. Penyebabnya pemeriksaan tanpa ETag: 60 permintaan/jam habis dalam
<1 jam, lalu pembaruan berhenti sampai berjam-jam. Jawaban 304 tidak dihitung
ke batas itu, jadi permintaan bersyarat adalah perbaikannya.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import config, updater


class FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class ConditionalRequestTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        p = mock.patch.object(config, "DATA_DIR", self.d)
        p.start()
        self.addCleanup(p.stop)

    def latest(self, resp):
        with mock.patch.object(updater.httpx, "get", return_value=resp) as g:
            out = updater._latest_commit()
        return out, g

    def test_first_call_stores_etag(self):
        (sha, msg), g = self.latest(FakeResp(200, {"sha": "abc123", "commit": {"message": "halo\n\nbadan"}},
                                            {"ETag": 'W/"v1"'}))
        self.assertEqual((sha, msg), ("abc123", "halo"))
        self.assertEqual(updater._etag_read(), 'W/"v1"')
        self.assertNotIn("If-None-Match", g.call_args.kwargs["headers"])

    def test_second_call_is_conditional(self):
        updater._etag_write('W/"v1"')
        (sha, msg), g = self.latest(FakeResp(304))
        self.assertEqual((sha, msg), (None, ""))
        self.assertEqual(g.call_args.kwargs["headers"]["If-None-Match"], 'W/"v1"')
        self.assertEqual(updater._etag_read(), 'W/"v1"')  # tidak berubah

    def test_304_means_no_update(self):
        updater._etag_write('W/"v1"')
        with mock.patch.object(updater.httpx, "get", return_value=FakeResp(304)), \
                mock.patch.object(updater, "installed_version", return_value="abc123"):
            self.assertIsNone(updater._check_archive(set()))

    def test_403_still_reported(self):
        with mock.patch.object(updater.httpx, "get", return_value=FakeResp(403)):
            with self.assertRaises(updater.UpdateError):
                updater._latest_commit()

    def test_new_etag_replaces_old(self):
        updater._etag_write('W/"v1"')
        self.latest(FakeResp(200, {"sha": "def456", "commit": {"message": "baru"}}, {"ETag": 'W/"v2"'}))
        self.assertEqual(updater._etag_read(), 'W/"v2"')


if __name__ == "__main__":
    unittest.main()
