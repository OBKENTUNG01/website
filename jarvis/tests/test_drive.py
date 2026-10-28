"""Uji alat Google Drive & drive_login tanpa memanggil Google sungguhan."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from itsme import config, drive, tools


class Ctx(dict):
    def __init__(self, answer=True, full_access=False):
        self.asked = []
        self.answer = answer
        super().__init__(
            website_dir=None, memory=mock.Mock(), seen_urls=set(), in_background=False,
            full_access=full_access, confirm=self._confirm, notify=lambda t: None,
        )

    def _confirm(self, text):
        self.asked.append(text)
        return self.answer


def _file(d, name, text="x"):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class DriveToolTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_search_formats_list_with_ids(self):
        items = [{"id": "ID1", "name": "laporan.pdf", "folder": False, "size": 2048, "mime": "", "modified": ""}]
        with mock.patch.object(drive, "search", return_value=items):
            out = tools.handle_drive_tool("drive_cari", {"kata_kunci": "laporan"}, Ctx())
        self.assertIn("laporan.pdf", out)
        self.assertIn("id: ID1", out)

    def test_upload_asks_then_uploads(self):
        local = _file(self.dir, "berkas.txt")
        with mock.patch.object(drive, "upload", return_value={"id": "X", "name": "berkas.txt"}) as up:
            ctx = Ctx(answer=True)
            out = tools.handle_drive_tool("drive_unggah", {"path_lokal": local, "folder_id": "F1"}, ctx)
        up.assert_called_once_with(local, "F1", None)
        self.assertIn("Terunggah", out)
        self.assertEqual(len(ctx.asked), 1)

    def test_upload_declined(self):
        local = _file(self.dir, "berkas.txt")
        with mock.patch.object(drive, "upload") as up:
            out = tools.handle_drive_tool("drive_unggah", {"path_lokal": local}, Ctx(answer=False))
        up.assert_not_called()
        self.assertEqual(out, "Dibatalkan oleh pemilik.")

    def test_upload_secret_refused(self):
        secret = _file(self.dir, ".env", "SECRET=1")
        with mock.patch.object(drive, "upload") as up:
            out = tools.handle_drive_tool("drive_unggah", {"path_lokal": secret}, Ctx())
        up.assert_not_called()
        self.assertIn("Ditolak", out)

    def test_download_asks_then_downloads(self):
        with mock.patch.object(config, "DRIVE_DOWNLOAD_DIR", self.dir), \
             mock.patch.object(drive, "name_of", return_value="laporan.pdf"), \
             mock.patch.object(drive, "download", return_value={"path": os.path.join(self.dir, "laporan.pdf")}) as dl:
            ctx = Ctx(answer=True)
            out = tools.handle_drive_tool("drive_unduh", {"file_id": "ID1"}, ctx)
        dl.assert_called_once()
        self.assertIn("Terunduh", out)
        self.assertIn(self.dir, ctx.asked[0])  # tujuan unduhan ditampilkan

    def test_make_folder(self):
        with mock.patch.object(drive, "make_folder", return_value={"id": "F", "name": "Baru"}) as mk:
            out = tools.handle_drive_tool("drive_kelola", {"aksi": "buat_folder", "nama": "Baru"}, Ctx(answer=True))
        mk.assert_called_once_with("Baru", None)
        self.assertIn("Folder dibuat", out)

    def test_rename_and_move(self):
        with mock.patch.object(drive, "name_of", return_value="lama"), \
             mock.patch.object(drive, "rename") as rn, mock.patch.object(drive, "move") as mv:
            tools.handle_drive_tool("drive_kelola", {"aksi": "ganti_nama", "file_id": "1", "nama": "baru"}, Ctx(answer=True))
            tools.handle_drive_tool("drive_kelola", {"aksi": "pindah", "file_id": "1", "folder_id": "F2"}, Ctx(answer=True))
        rn.assert_called_once_with("1", "baru")
        mv.assert_called_once_with("1", "F2")

    def test_delete_always_asks_even_full_access(self):
        with mock.patch.object(drive, "name_of", return_value="x"), mock.patch.object(drive, "delete") as d:
            ctx = Ctx(answer=False, full_access=True)
            out = tools.handle_drive_tool("drive_kelola", {"aksi": "hapus", "file_id": "1"}, ctx)
        d.assert_not_called()  # ditolak karena tetap minta konfirmasi
        self.assertEqual(len(ctx.asked), 1)
        self.assertIn("Dibatalkan", out)

    def test_not_connected_returns_login_hint(self):
        with mock.patch.object(drive, "search", side_effect=drive.DriveError(drive.login_hint())):
            out = tools.handle_drive_tool("drive_cari", {"kata_kunci": "x"}, Ctx())
        self.assertIn("drive_login", out)


class DriveTokenRevokedTest(unittest.TestCase):
    """_service() harus mengubah RefreshError Google (pesan tuple/dict mentah,
    tak enak dibaca) jadi DriveError yang jelas - bukan menyebar apa adanya
    (itu yang terjadi sebelum perbaikan ini: 'Google Drive error: RefreshError:
    (...)' muncul di dashboard/chat tanpa penjelasan apa pun)."""

    def test_refresh_failure_becomes_clear_drive_error(self):
        fake_creds = mock.Mock(valid=False, expired=True, refresh_token="x")
        fake_creds.refresh.side_effect = Exception(
            "invalid_grant: Token has been expired or revoked.")
        fake_lib = (mock.Mock(from_authorized_user_file=mock.Mock(return_value=fake_creds)),
                    mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock())
        with mock.patch.object(drive, "ready", return_value=True), \
             mock.patch.object(drive, "load_libs", return_value=fake_lib):
            with self.assertRaises(drive.DriveError) as cm:
                drive._service()
        msg = str(cm.exception)
        self.assertNotIn("RefreshError", msg)  # bukan pesan mentah Google
        self.assertIn("PUBLISH APP", msg)       # perbaikan permanen disebutkan
        self.assertIn("drive_login", msg)       # perbaikan cepat disebutkan

    def test_expired_without_refresh_token_also_gets_clear_hint(self):
        fake_creds = mock.Mock(valid=False, expired=True, refresh_token=None)
        fake_lib = (mock.Mock(from_authorized_user_file=mock.Mock(return_value=fake_creds)),
                    mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock())
        with mock.patch.object(drive, "ready", return_value=True), \
             mock.patch.object(drive, "load_libs", return_value=fake_lib):
            with self.assertRaises(drive.DriveError) as cm:
                drive._service()
        self.assertIn("PUBLISH APP", str(cm.exception))


class DriveLoginTest(unittest.TestCase):
    def test_missing_credentials_explains_steps(self):
        from itsme import drive_login

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with mock.patch.object(config, "DRIVE_CREDENTIALS_FILE", os.path.join(d, "nope.json")):
            self.assertEqual(drive_login.main(), 1)


if __name__ == "__main__":
    unittest.main()
