"""Uji rea_upload.py (unggahan berkas/folder aplikasi untuk REA).

Semua uji memakai disk sungguhan (folder sementara), tanpa mock sistem berkas: yang diuji adalah apa yang benar-benar
tertulis (isi berkas, hash, nama, sisa potongan) dan bagaimana ia pulih (ingatan proses dibuang, lalu keadaan dibaca
lagi dari disk).
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from itsme import config, rea, rea_upload
from itsme.rea_upload import UploadError


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(rea_upload, "DISK_MARGIN", 0),
                  mock.patch.object(rea_upload, "chunk_bytes", return_value=1024)):
            p.start()
            self.addCleanup(p.stop)
        rea_upload._META_CACHE.clear()
        rea_upload._LOCKS.clear()

    def push(self, uid, index, data, start=0, step=1024):
        """Kirim `data` sebagai potongan berurutan ke berkas `index` mulai di `start`."""
        pos = start
        last = None
        while pos < len(data) + start:
            piece = data[pos - start:pos - start + step]
            last = rea_upload.write_chunk(uid, index, pos, piece, sha(piece))
            pos += len(piece)
        return last

    def upload_file(self, name, data, fingerprint=None):
        view, _ = rea_upload.create("file", name=name, size=len(data), fingerprint=fingerprint)
        if data:
            self.push(view["id"], 0, data)
        return rea_upload.finish(view["id"])


class CleanNameTest(unittest.TestCase):
    def test_strips_folders_both_styles(self):
        self.assertEqual(rea_upload.clean_name(r"..\..\evil.exe"), "evil.exe")
        self.assertEqual(rea_upload.clean_name("/etc/passwd"), "passwd")
        self.assertEqual(rea_upload.clean_name(r"C:\Users\x\app.exe"), "app.exe")
        self.assertEqual(rea_upload.clean_name("a/b/c/app.apk"), "app.apk")

    def test_windows_device_names_get_prefix(self):
        for raw in ("NUL.txt", "con", "COM1", "lpt9.exe", "aux.tar.gz", "PRN", "COM\u00b9.txt",
                    "NUL .txt", "con  .exe", "Com1 .dll"):    # spasi sebelum titik: Windows tetap menganggapnya perangkat
            out = rea_upload.clean_name(raw)
            self.assertTrue(out.startswith("_"), (raw, out))
        self.assertEqual(rea_upload.clean_name("console.exe"), "console.exe")   # bukan nama perangkat
        self.assertEqual(rea_upload.clean_name("communicate.dll"), "communicate.dll")

    def test_trailing_dots_and_spaces_removed(self):
        self.assertEqual(rea_upload.clean_name("app.exe. ."), "app.exe")
        self.assertEqual(rea_upload.clean_name("  app.exe  "), "app.exe")

    def test_colon_alternate_data_stream_is_neutralised(self):
        out = rea_upload.clean_name("a.exe:evil.txt")
        self.assertNotIn(":", out)

    def test_bidi_override_and_invisible_chars_removed(self):
        out = rea_upload.clean_name("invoice\u202efdp.exe")
        self.assertNotIn("\u202e", out)
        self.assertEqual(rea_upload.clean_name("a\u200bb.exe"), "a_b.exe")
        self.assertNotIn("\x00", rea_upload.clean_name("a\x00b.exe"))

    def test_empty_and_dot_names_fall_back(self):
        for raw in ("", ".", "..", "...", "   ", None, "/"):
            self.assertEqual(rea_upload.clean_name(raw), "berkas", repr(raw))

    def test_long_name_keeps_extension_and_length(self):
        out = rea_upload.clean_name("x" * 400 + ".apk")
        self.assertTrue(out.endswith(".apk"))
        self.assertLessEqual(len(out), rea_upload.MAX_NAME)

    def test_never_contains_separators_or_forbidden_chars_and_is_idempotent(self):
        nasty = ["a<b>c.exe", 'q"r.dll', "x|y?.apk", "star*.jar", "tab\there.exe", "ünï.exe", "日本語.zip",
                 "..\\..\\x", "a/b\\c", " . ", "NUL", "end. ", "e\u0301.exe"]
        for raw in nasty:
            out = rea_upload.clean_name(raw)
            self.assertFalse(any(c in out for c in '<>:"/\\|?*'), (raw, out))
            self.assertTrue(all(ord(c) >= 32 for c in out), (raw, out))
            self.assertEqual(rea_upload.clean_name(out), out, (raw, out))


class CleanComponentTest(unittest.TestCase):
    def test_accepts_normal_names(self):
        for ok in ("app.exe", "My App", ".git", "a b.dll", "日本語.txt", "file (1).apk", "lib-x64"):
            self.assertEqual(rea_upload.clean_component(ok), ok)

    def test_rejects_unsafe_names(self):
        for bad in ("", ".", "..", "a/b", "a\\b", "a:b", "a*", "a?", "x\x00y", "trail.", "trail ", " lead",
                    "CON", "nul.txt", "COM3", "a" * 200, "evil\u202e.exe", "NUL .txt", "lpt1  .dll"):
            with self.assertRaises(UploadError, msg=repr(bad)):
                rea_upload.clean_component(bad)

    def test_dot_names_are_rejected_explicitly_not_by_accident(self):
        for bad in (".", ".."):
            with self.assertRaises(UploadError) as cm:
                rea_upload.clean_component(bad)
            self.assertIn("'..'", cm.exception.message)


class LimitsTest(unittest.TestCase):
    def test_env_overrides_and_garbage_falls_back(self):
        with mock.patch.dict(os.environ, {"ITSME_REA_UPLOAD_MAX_MB": "10"}):
            self.assertEqual(rea_upload.max_bytes(), 10 << 20)
        for garbage in ("inf", "nan", "abc", "", "-5", "0"):
            with mock.patch.dict(os.environ, {"ITSME_REA_UPLOAD_MAX_MB": garbage}):
                value = rea_upload.max_bytes()
                self.assertGreaterEqual(value, 1 << 20, garbage)
                self.assertLessEqual(value, 102400 << 20, garbage)
        with mock.patch.dict(os.environ, {"ITSME_REA_CHUNK_MB": "9999"}):
            self.assertEqual(rea_upload.chunk_bytes(), 32 << 20)   # dijepit
        with mock.patch.dict(os.environ, {"ITSME_REA_CHUNK_MB": "0"}):
            self.assertEqual(rea_upload.chunk_bytes(), 1 << 20)

    def test_limits_dict_has_what_the_page_needs(self):
        lim = rea_upload.limits()
        for key in ("max_bytes", "chunk_bytes", "max_files", "keep", "keep_bytes", "days", "max_active"):
            self.assertIn(key, lim)


class SingleFileTest(Base):
    def test_full_cycle_writes_exact_bytes_and_hash(self):
        data = os.urandom(5000)
        view, reused = rea_upload.create("file", name="app.exe", size=len(data))
        self.assertFalse(reused)
        self.assertEqual((view["state"], view["received"], view["next"]), ("uploading", 0, {"file": 0, "offset": 0}))
        last = self.push(view["id"], 0, data)
        self.assertTrue(last["done"])
        done = rea_upload.finish(view["id"])
        self.assertEqual(done["state"], "ready")
        self.assertEqual(done["sha256"], sha(data))
        info = rea_upload.resolve(done["id"])
        with open(info["path"], "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(info["label"], "app.exe")
        self.assertEqual(os.path.basename(info["path"]), "app.exe")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "rea-uploads", done["id"], "parts")))

    def test_irregular_chunk_sizes_are_accepted(self):
        """Browser memperkecil potongan saat jaringan buruk dan membesarkannya lagi: server harus menerima ukuran
        apa pun yang <= batas, selama offsetnya pas."""
        data = os.urandom(5000)
        view, _ = rea_upload.create("file", name="a.bin", size=len(data))
        pos = 0
        for size in (1, 100, 1023, 512, 1024, 7, 1024, 1024, 285):
            piece = data[pos:pos + size]
            res = rea_upload.write_chunk(view["id"], 0, pos, piece, sha(piece))
            pos += len(piece)
            self.assertEqual(res["received"], pos)
        self.assertEqual(pos, 5000)
        self.assertEqual(rea_upload.finish(view["id"])["sha256"], sha(data))

    def test_offset_must_match_and_reply_tells_the_real_position(self):
        data = os.urandom(3000)
        view, _ = rea_upload.create("file", name="a.bin", size=len(data))
        uid = view["id"]
        rea_upload.write_chunk(uid, 0, 0, data[:1024])
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(uid, 0, 0, data[:1024])          # kirim ulang potongan yang sama
        self.assertEqual((cm.exception.status, cm.exception.extra["received"]), (409, 1024))
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(uid, 0, 2048, data[2048:])       # loncat
        self.assertEqual((cm.exception.status, cm.exception.extra["received"]), (409, 1024))
        self.assertEqual(rea_upload.status(uid)["received"], 1024)   # tidak ada yang bergeser
        rea_upload.write_chunk(uid, 0, 1024, data[1024:2048])
        rea_upload.write_chunk(uid, 0, 2048, data[2048:])
        self.assertEqual(rea_upload.finish(uid)["sha256"], sha(data))

    def test_bad_chunks_are_rejected_without_touching_state(self):
        data = os.urandom(2048)
        view, _ = rea_upload.create("file", name="a.bin", size=len(data))
        uid = view["id"]
        cases = [
            (dict(index=0, offset=0, data=b""), 400),                           # kosong
            (dict(index=0, offset=0, data=b"x" * 1025), 413),                  # lebih besar dari potongan
            (dict(index=0, offset=0, data=b"x" * 1024, sha256="0" * 64), 422),  # checksum salah
            (dict(index=0, offset=0, data=b"x", sha256="zz"), 400),            # checksum bukan hex
            (dict(index=1, offset=0, data=b"x"), 400),                          # nomor berkas
            (dict(index=-1, offset=0, data=b"x"), 400),
            (dict(index=True, offset=0, data=b"x"), 400),
            (dict(index=0, offset=-1, data=b"x"), 400),
            (dict(index=0, offset="0", data=b"x"), 400),
        ]
        for kw, code in cases:
            with self.assertRaises(UploadError, msg=str(kw)) as cm:
                rea_upload.write_chunk(uid, **kw)
            self.assertEqual(cm.exception.status, code, kw)
        self.assertEqual(rea_upload.status(uid)["received"], 0)

    def test_cannot_exceed_declared_size_and_complete_file_refuses_more(self):
        view, _ = rea_upload.create("file", name="a.bin", size=1500)
        uid = view["id"]
        rea_upload.write_chunk(uid, 0, 0, b"a" * 1024)
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(uid, 0, 1024, b"b" * 600)     # 1024+600 > 1500
        self.assertEqual(cm.exception.status, 413)
        rea_upload.write_chunk(uid, 0, 1024, b"b" * 476)
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(uid, 0, 1500, b"c")
        self.assertEqual(cm.exception.status, 409)

    def test_finish_before_complete_is_refused_and_is_idempotent_after(self):
        view, _ = rea_upload.create("file", name="a.bin", size=2000)
        uid = view["id"]
        rea_upload.write_chunk(uid, 0, 0, b"a" * 1000)
        with self.assertRaises(UploadError) as cm:
            rea_upload.finish(uid)
        self.assertEqual(cm.exception.status, 409)
        rea_upload.write_chunk(uid, 0, 1000, b"a" * 1000)
        first = rea_upload.finish(uid)
        second = rea_upload.finish(uid)
        self.assertEqual((first["state"], second["state"], first["sha256"]), ("ready", "ready", second["sha256"]))
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(uid, 0, 2000, b"x")
        self.assertEqual(cm.exception.status, 409)

    def test_resolve_refuses_unfinished_upload(self):
        view, _ = rea_upload.create("file", name="a.bin", size=10)
        with self.assertRaises(UploadError) as cm:
            rea_upload.resolve(view["id"])
        self.assertEqual(cm.exception.status, 409)

    def test_zero_byte_file(self):
        done = self.upload_file("kosong.dll", b"")
        self.assertEqual((done["state"], done["size"], done["sha256"]), ("ready", 0, sha(b"")))
        info = rea_upload.resolve(done["id"])
        self.assertEqual(os.path.getsize(info["path"]), 0)

    def test_hostile_name_is_stored_inside_its_own_folder(self):
        done = self.upload_file(r"..\..\..\Windows\evil.exe", b"MZ" + b"\x00" * 100)
        info = rea_upload.resolve(done["id"])
        root = os.path.realpath(os.path.join(self.dir, "rea-uploads"))
        self.assertEqual(os.path.commonpath([os.path.realpath(info["path"]), root]), root)
        self.assertEqual(os.path.basename(info["path"]), "evil.exe")
        self.assertEqual(os.listdir(self.dir), ["rea-uploads"])      # tak ada yang keluar dari folder penyimpanan

    def test_size_above_limit_is_413_and_nothing_is_created(self):
        with mock.patch.object(rea_upload, "max_bytes", return_value=5000):
            with self.assertRaises(UploadError) as cm:
                rea_upload.create("file", name="big.bin", size=5001)
        self.assertEqual(cm.exception.status, 413)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "rea-uploads")) and
                         os.listdir(os.path.join(self.dir, "rea-uploads")))

    def test_invalid_create_arguments(self):
        for kw in (dict(kind="file", name="", size=1), dict(kind="file", name=None, size=1),
                   dict(kind="file", name="a", size=-1), dict(kind="file", name="a", size=True),
                   dict(kind="file", name="a", size="5"), dict(kind="zip", name="a", size=1),
                   dict(kind="dir", files=[]), dict(kind="dir", files="x")):
            with self.assertRaises(UploadError, msg=str(kw)):
                rea_upload.create(**kw)

    def test_disk_space_is_checked_before_accepting(self):
        with mock.patch.object(rea_upload, "_free_bytes", return_value=1000):
            with self.assertRaises(UploadError) as cm:
                rea_upload.create("file", name="a.bin", size=5000)
        self.assertEqual(cm.exception.status, 507)

    def test_disk_write_error_is_reported_and_state_unchanged(self):
        view, _ = rea_upload.create("file", name="a.bin", size=2048)
        real_open = open

        def fake_open(path, mode="r", *a, **kw):
            if str(path).endswith(".part"):
                raise OSError(28, "No space left on device")
            return real_open(path, mode, *a, **kw)

        with mock.patch("builtins.open", fake_open):
            with self.assertRaises(UploadError) as cm:
                rea_upload.write_chunk(view["id"], 0, 0, b"x" * 100)
        self.assertEqual(cm.exception.status, 507)
        self.assertEqual(rea_upload.status(view["id"])["received"], 0)


class ResumeTest(Base):
    def test_partial_upload_is_resumed_from_disk_after_a_restart(self):
        data = os.urandom(4000)
        view, _ = rea_upload.create("file", name="app.apk", size=len(data), fingerprint="app.apk|4000|111")
        uid = view["id"]
        rea_upload.write_chunk(uid, 0, 0, data[:1024])
        rea_upload.write_chunk(uid, 0, 1024, data[1024:2048])
        # "It's Me menyala ulang": semua ingatan proses hilang
        rea_upload._META_CACHE.clear()
        rea_upload._LOCKS.clear()
        again, reused = rea_upload.create("file", name="app.apk", size=len(data), fingerprint="app.apk|4000|111")
        self.assertFalse(reused)
        self.assertEqual((again["id"], again["received"], again["next"]), (uid, 2048, {"file": 0, "offset": 2048}))
        self.push(uid, 0, data[2048:], start=2048)
        self.assertEqual(rea_upload.finish(uid)["sha256"], sha(data))

    def test_finished_upload_with_same_fingerprint_is_reused(self):
        data = os.urandom(1500)
        first = self.upload_file("same.exe", data, fingerprint="same.exe|1500|9")
        again, reused = rea_upload.create("file", name="same.exe", size=1500, fingerprint="same.exe|1500|9")
        self.assertTrue(reused)
        self.assertEqual((again["id"], again["state"]), (first["id"], "ready"))
        uploads = [u for u in rea_upload.list_uploads()]
        self.assertEqual(len(uploads), 1)

    def test_reuse_is_skipped_when_files_vanished_from_disk(self):
        data = os.urandom(1500)
        first = self.upload_file("same.exe", data, fingerprint="fp1")
        os.remove(rea_upload.resolve(first["id"])["path"])
        again, reused = rea_upload.create("file", name="same.exe", size=1500, fingerprint="fp1")
        self.assertFalse(reused)
        self.assertNotEqual(again["id"], first["id"])

    def test_different_fingerprint_or_size_is_a_new_upload(self):
        a, _ = rea_upload.create("file", name="a.bin", size=100, fingerprint="x")
        b, _ = rea_upload.create("file", name="a.bin", size=100, fingerprint="y")
        c, _ = rea_upload.create("file", name="a.bin", size=101, fingerprint="x")
        self.assertEqual(len({a["id"], b["id"], c["id"]}), 3)

    def test_concurrent_identical_chunks_never_double_write(self):
        data = os.urandom(1024)
        view, _ = rea_upload.create("file", name="a.bin", size=2048)
        uid = view["id"]
        outcomes = []
        gate = threading.Barrier(8)

        def worker():
            gate.wait()
            try:
                outcomes.append(rea_upload.write_chunk(uid, 0, 0, data)["received"])
            except UploadError as exc:
                outcomes.append(exc.status)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(outcomes.count(1024), 1, outcomes)           # tepat satu yang berhasil
        self.assertTrue(all(o == 409 for o in outcomes if o != 1024), outcomes)
        self.assertEqual(rea_upload.status(uid)["received"], 1024)
        part = os.path.join(self.dir, "rea-uploads", uid, "parts", "0.part")
        with open(part, "rb") as f:
            self.assertEqual(f.read(), data)


class FolderTest(Base):
    FILES = {"MyApp/MyApp.exe": b"MZ" + b"1" * 2500, "MyApp/resources/app.asar": b"asar" * 600,
             "MyApp/resources/empty.txt": b"", "MyApp/locales/id.pak": b"pak" * 100, "MyApp/readme.md": b"hi"}

    def make(self):
        files = [{"path": p, "size": len(d)} for p, d in self.FILES.items()]
        view, _ = rea_upload.create("dir", files=files, fingerprint="MyApp|5|1")
        for i, d in enumerate(self.FILES.values()):
            if d:
                self.push(view["id"], i, d)
        return rea_upload.finish(view["id"])

    def test_full_folder_roundtrip_keeps_the_tree(self):
        done = self.make()
        self.assertEqual((done["kind"], done["state"], done["name"]), ("dir", "ready", "MyApp"))
        self.assertEqual((done["files_total"], done["files_done"]), (5, 5))
        info = rea_upload.resolve(done["id"])
        self.assertTrue(os.path.isdir(info["path"]))
        self.assertEqual(info["label"], "MyApp")
        for rel, data in self.FILES.items():
            with open(os.path.join(os.path.dirname(info["path"]), *rel.split("/")), "rb") as f:
                self.assertEqual(f.read(), data, rel)

    def test_candidates_list_the_notable_files_biggest_first(self):
        done = self.make()
        cands = done["candidates"]
        self.assertEqual([c["path"] for c in cands], ["MyApp.exe", "resources/app.asar"])
        self.assertGreater(cands[0]["size"] + 1, cands[1]["size"])
        picked = rea_upload.resolve(done["id"], cands[1]["index"])
        self.assertTrue(picked["path"].endswith(os.path.join("resources", "app.asar")))
        self.assertEqual(picked["label"], "MyApp/resources/app.asar")
        self.assertEqual(picked["upload"]["rel"], "resources/app.asar")

    def test_resume_points_at_first_incomplete_file(self):
        files = [{"path": p, "size": len(d)} for p, d in self.FILES.items()]
        view, _ = rea_upload.create("dir", files=files, fingerprint="fp-dir")
        uid = view["id"]
        self.push(uid, 0, self.FILES["MyApp/MyApp.exe"])
        rea_upload.write_chunk(uid, 1, 0, self.FILES["MyApp/resources/app.asar"][:1024])
        st = rea_upload.status(uid)
        self.assertEqual(st["next"], {"file": 1, "offset": 1024})
        self.assertEqual(st["files_done"], 2)      # termasuk berkas kosong yang langsung lengkap

    def test_rejects_unsafe_or_inconsistent_manifests(self):
        def attempt(paths, sizes=None):
            files = [{"path": p, "size": (sizes or {}).get(p, 1)} for p in paths]
            with self.assertRaises(UploadError, msg=str(paths)):
                rea_upload.create("dir", files=files)

        attempt(["App/../../evil.dll"])
        attempt(["../evil.dll"])
        attempt(["/abs/evil.dll"])
        attempt(["C:/Windows/evil.dll"])
        attempt(["App/a.dll", "Other/b.dll"])                  # dua folder induk
        attempt(["loose.dll"])                                 # tanpa folder induk
        attempt(["App/a.dll", "app/A.DLL"])                    # sama kalau huruf diabaikan
        attempt(["App/x", "App/x/y.dll"])                      # berkas sekaligus folder
        attempt(["App/CON/x.dll"])
        attempt(["App/sub./x.dll"])
        attempt(["App/" + "/".join(["d"] * 40) + "/x.dll"])    # terlalu dalam
        attempt(["App/a\x00.dll"])

    def test_backslash_paths_are_treated_as_separators_not_names(self):
        view, _ = rea_upload.create("dir", files=[{"path": "App\\sub\\a.dll", "size": 1}])
        self.assertEqual(view["name"], "App")

    def test_too_many_files(self):
        with mock.patch.object(rea_upload, "max_files", return_value=3):
            files = [{"path": f"App/f{i}.bin", "size": 1} for i in range(4)]
            with self.assertRaises(UploadError) as cm:
                rea_upload.create("dir", files=files)
        self.assertEqual(cm.exception.status, 413)

    def test_total_size_is_the_sum_of_all_files(self):
        with mock.patch.object(rea_upload, "max_bytes", return_value=2000):
            files = [{"path": "App/a.bin", "size": 1200}, {"path": "App/b.bin", "size": 1200}]
            with self.assertRaises(UploadError) as cm:
                rea_upload.create("dir", files=files)
        self.assertEqual(cm.exception.status, 413)

    def test_resolve_a_file_index_out_of_range(self):
        done = self.make()
        for bad in (-1, 99, True, "1"):
            with self.assertRaises(UploadError, msg=repr(bad)):
                rea_upload.resolve(done["id"], bad)


class IntegrityTest(Base):
    def test_resolve_after_file_vanished_says_so(self):
        done = self.upload_file("a.exe", os.urandom(1500))
        os.remove(rea_upload.resolve(done["id"])["path"])
        with self.assertRaises(UploadError) as cm:
            rea_upload.resolve(done["id"])
        self.assertEqual(cm.exception.status, 410)

    def test_finish_when_antivirus_removed_the_file_mentions_it(self):
        view, _ = rea_upload.create("file", name="sample.exe", size=1024)
        rea_upload.write_chunk(view["id"], 0, 0, b"x" * 1024)
        final = os.path.join(self.dir, "rea-uploads", view["id"], "content", "sample.exe")
        os.remove(final)       # seperti Windows Defender mengarantina sampel
        with self.assertRaises(UploadError) as cm:
            rea_upload.finish(view["id"])
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("antivirus", cm.exception.message.lower())

    def test_corrupt_meta_is_treated_as_missing(self):
        done = self.upload_file("a.exe", b"x" * 100)
        meta_path = os.path.join(self.dir, "rea-uploads", done["id"], "meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        rea_upload._META_CACHE.clear()
        with self.assertRaises(UploadError) as cm:
            rea_upload.status(done["id"])
        self.assertEqual(cm.exception.status, 404)

    def test_tampered_meta_with_traversal_path_is_not_trusted(self):
        done = self.upload_file("a.exe", b"x" * 100)
        meta_path = os.path.join(self.dir, "rea-uploads", done["id"], "meta.json")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["files"][0]["path"] = "../../../evil.exe"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        rea_upload._META_CACHE.clear()
        with self.assertRaises(UploadError) as cm:
            rea_upload.resolve(done["id"])
        self.assertEqual(cm.exception.status, 404)

    def test_tampered_folder_meta_with_dotdot_is_not_trusted(self):
        files = [{"path": "App/a.dll", "size": 3}, {"path": "App/b.dll", "size": 3}]
        view, _ = rea_upload.create("dir", files=files)
        meta_path = os.path.join(self.dir, "rea-uploads", view["id"], "meta.json")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["files"][1]["path"] = "App/../../../evil.dll"      # tetap berawalan 'App/'
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        rea_upload._META_CACHE.clear()
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(view["id"], 1, 0, b"xyz")
        self.assertEqual(cm.exception.status, 404)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "evil.dll")))

    def _link(self, link, target, directory):
        """Buat tautan `link` -> `target`; di Windows tanpa hak symlink dipakai junction (tanpa hak khusus)."""
        try:
            os.symlink(target, link, target_is_directory=directory)
            return
        except (OSError, NotImplementedError, AttributeError):
            pass
        if os.name == "nt" and directory:
            import subprocess
            done = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
            if done.returncode == 0:
                return
        self.skipTest("tidak bisa membuat tautan di mesin ini")

    def test_file_symlink_pointing_outside_is_refused(self):
        done = self.upload_file("a.exe", b"x" * 100)
        info = rea_upload.resolve(done["id"])
        outside = os.path.join(self.dir, "outside.bin")
        with open(outside, "wb") as f:
            f.write(b"x" * 100)
        os.remove(info["path"])
        self._link(info["path"], outside, False)
        with self.assertRaises(UploadError) as cm:
            rea_upload.resolve(done["id"])
        self.assertEqual(cm.exception.status, 403)

    def test_folder_link_pointing_outside_is_refused(self):
        """Folder unggahan diganti tautan/junction ke folder lain berisi berkas yang sama: lolos 'utuh', tapi isinya
        di luar penyimpanan - resolve harus menolak."""
        files = [{"path": "App/a.dll", "size": 3}]
        view, _ = rea_upload.create("dir", files=files)
        rea_upload.write_chunk(view["id"], 0, 0, b"abc")
        done = rea_upload.finish(view["id"])
        outside = os.path.join(self.dir, "elsewhere")
        os.makedirs(outside)
        with open(os.path.join(outside, "a.dll"), "wb") as f:
            f.write(b"abc")
        app = os.path.join(self.dir, "rea-uploads", done["id"], "content", "App")
        shutil.rmtree(app)
        self._link(app, outside, True)
        self.addCleanup(lambda: (os.rmdir(app) if os.path.isdir(app) and os.path.islink(app) is False
                                 and not os.listdir(app) else None))
        with self.assertRaises(UploadError) as cm:
            rea_upload.resolve(done["id"])
        self.assertEqual(cm.exception.status, 403)


class IdsAndDeleteTest(Base):
    def test_ids_that_are_not_ours_are_404_not_path_traversal(self):
        for bad in ("../x", "..\\..\\x", "", None, "ab", "g" * 12, "A" * 5, "0" * 40, "../../etc/passwd", 12345):
            for fn in (rea_upload.status, rea_upload.resolve, rea_upload.delete):
                with self.assertRaises(UploadError, msg=f"{fn.__name__}({bad!r})") as cm:
                    fn(bad)
                self.assertEqual(cm.exception.status, 404)
            self.assertIsNone(rea_upload.get(bad))

    def test_delete_removes_everything(self):
        done = self.upload_file("a.exe", b"x" * 100)
        self.assertTrue(rea_upload.delete(done["id"]))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "rea-uploads", done["id"])))
        self.assertIsNone(rea_upload.get(done["id"]))

    def test_delete_refuses_upload_used_by_a_running_job(self):
        done = self.upload_file("a.exe", b"x" * 100)
        with mock.patch.object(rea, "active_upload_ids", return_value={done["id"]}):
            with self.assertRaises(UploadError) as cm:
                rea_upload.delete(done["id"])
        self.assertEqual(cm.exception.status, 409)
        self.assertIsNotNone(rea_upload.get(done["id"]))

    def test_delete_partial_upload(self):
        view, _ = rea_upload.create("file", name="a.bin", size=3000)
        rea_upload.write_chunk(view["id"], 0, 0, b"a" * 1000)
        rea_upload.delete(view["id"])
        with self.assertRaises(UploadError) as cm:
            rea_upload.write_chunk(view["id"], 0, 1000, b"a" * 1000)
        self.assertEqual(cm.exception.status, 404)

    def test_list_uploads_newest_first_with_partial_ones(self):
        a = self.upload_file("a.exe", b"a" * 10)
        time.sleep(0.02)
        view, _ = rea_upload.create("file", name="b.bin", size=100)
        listing = rea_upload.list_uploads()
        self.assertEqual([u["id"] for u in listing], [view["id"], a["id"]])
        self.assertEqual([u["state"] for u in listing], ["uploading", "ready"])


class PruneTest(Base):
    def age(self, uid, seconds, key="last_used"):
        path = os.path.join(self.dir, "rea-uploads", uid, "meta.json")
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        meta[key] = time.time() - seconds
        if key == "last_used":
            meta["updated"] = meta[key]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        rea_upload._META_CACHE.clear()

    def ids(self):
        return {u["id"] for u in rea_upload.list_uploads()}

    def test_count_cap_keeps_the_newest(self):
        made = []
        for i in range(5):
            made.append(self.upload_file(f"f{i}.exe", b"x" * 10)["id"])
            time.sleep(0.01)
        with mock.patch.object(rea_upload, "keep_count", return_value=3):
            rea_upload.prune()
        self.assertEqual(self.ids(), set(made[-3:]))

    def test_size_cap_drops_oldest_but_never_the_newest(self):
        a = self.upload_file("a.bin", b"x" * 600)["id"]
        time.sleep(0.01)
        b = self.upload_file("b.bin", b"x" * 600)["id"]
        time.sleep(0.01)
        with mock.patch.object(rea_upload, "keep_bytes", return_value=700):
            rea_upload.prune()
        self.assertEqual(self.ids(), {b})
        self.assertNotIn(a, self.ids())
        with mock.patch.object(rea_upload, "keep_bytes", return_value=10):   # lebih kecil dari yang terbaru
            rea_upload.prune()
        self.assertEqual(self.ids(), {b})

    def test_expired_ready_upload_is_removed(self):
        old = self.upload_file("old.exe", b"x" * 10)["id"]
        new = self.upload_file("new.exe", b"x" * 10)["id"]
        self.age(old, 8 * 86400)
        rea_upload.prune()
        self.assertEqual(self.ids(), {new})

    def test_stale_partial_upload_is_removed_but_fresh_one_stays(self):
        stale, _ = rea_upload.create("file", name="s.bin", size=100)
        fresh, _ = rea_upload.create("file", name="f.bin", size=100)
        self.age(stale["id"], 2 * 86400, key="updated")
        rea_upload.prune()
        self.assertEqual(self.ids(), {fresh["id"]})

    def test_uploads_used_by_running_jobs_are_never_pruned(self):
        old = self.upload_file("old.exe", b"x" * 10)["id"]
        self.age(old, 30 * 86400)
        with mock.patch.object(rea, "active_upload_ids", return_value={old}):
            rea_upload.prune()
        self.assertEqual(self.ids(), {old})
        rea_upload.prune()
        self.assertEqual(self.ids(), set())

    def test_held_upload_survives_prune_and_delete_until_released(self):
        """Selama pemilik belum menekan 'Ya' (job belum terdaftar), unggahannya tidak boleh dipangkas/dihapus."""
        old = self.upload_file("old.exe", b"x" * 10)["id"]
        self.age(old, 30 * 86400)
        with rea_upload.holding(old):
            rea_upload.prune()
            self.assertEqual(self.ids(), {old})
            with self.assertRaises(UploadError) as cm:
                rea_upload.delete(old)
            self.assertEqual(cm.exception.status, 409)
        rea_upload.prune()
        self.assertEqual(self.ids(), set())

    def test_reusing_a_finished_upload_refreshes_its_last_used(self):
        done = self.upload_file("same.exe", b"x" * 10, fingerprint="fp-reuse")
        self.age(done["id"], 6 * 86400)
        again, reused = rea_upload.create("file", name="same.exe", size=10, fingerprint="fp-reuse")
        self.assertTrue(reused)
        self.assertLess(time.time() - again["last_used"], 5)
        rea_upload.prune()
        self.assertEqual(self.ids(), {done["id"]})

    def test_chunk_that_loses_the_race_with_finish_gets_409_not_500(self):
        data = b"a" * 1024
        view, _ = rea_upload.create("file", name="a.bin", size=2048)
        uid = view["id"]
        rea_upload.write_chunk(uid, 0, 0, data)
        stale = rea_upload._need(uid)                  # keadaan sebelum finish: masih 'uploading'
        rea_upload.write_chunk(uid, 0, 1024, data)
        rea_upload.finish(uid)
        real_need = rea_upload._need
        calls = []

        def need(upload_id):
            calls.append(1)
            return stale if len(calls) == 1 else real_need(upload_id)

        with mock.patch.object(rea_upload, "_need", need):
            with self.assertRaises(UploadError) as cm:
                rea_upload.write_chunk(uid, 0, 2048, b"zz")
        self.assertEqual(cm.exception.status, 409)

    def test_broken_folders_are_cleaned_only_when_old(self):
        root = os.path.join(self.dir, "rea-uploads")
        os.makedirs(os.path.join(root, "aaaaaaaaaaaa"))
        os.makedirs(os.path.join(root, "bbbbbbbbbbbb"))
        os.makedirs(os.path.join(root, "not-an-id"))
        old = time.time() - 3 * 86400
        os.utime(os.path.join(root, "bbbbbbbbbbbb"), (old, old))
        rea_upload.prune()
        self.assertTrue(os.path.isdir(os.path.join(root, "aaaaaaaaaaaa")))     # baru: mungkin sedang dibuat
        self.assertFalse(os.path.isdir(os.path.join(root, "bbbbbbbbbbbb")))
        self.assertTrue(os.path.isdir(os.path.join(root, "not-an-id")))        # bukan milik kita: tak disentuh

    def test_too_many_unfinished_uploads_is_429(self):
        for i in range(rea_upload.MAX_ACTIVE):
            rea_upload.create("file", name=f"p{i}.bin", size=100, fingerprint=f"fp{i}")
        with self.assertRaises(UploadError) as cm:
            rea_upload.create("file", name="one-more.bin", size=100, fingerprint="baru")
        self.assertEqual(cm.exception.status, 429)
        # melanjutkan unggahan yang sudah ada bukan unggahan baru: tidak ditolak
        again, reused = rea_upload.create("file", name="p0.bin", size=100, fingerprint="fp0")
        self.assertFalse(reused)
        self.assertEqual(len(rea_upload.list_uploads()), rea_upload.MAX_ACTIVE)
        # menghapus satu membuka slot lagi
        rea_upload.delete(again["id"])
        rea_upload.create("file", name="one-more.bin", size=100, fingerprint="baru")


class RelabelTest(unittest.TestCase):
    """rea._relabel: path penyimpanan internal tidak boleh tampil di ringkasan - hanya nama asli berkas."""

    def test_internal_path_is_replaced_in_every_spelling(self):
        target = r"C:\Users\x\data\rea-uploads\0123456789ab\content\notepad.exe"
        summary = {"title": "Inventaris berkas: notepad.exe",
                   "facts": [["Berkas", target], ["Alias", target.replace("\\", "/")], ["Panjang", "\\\\?\\" + target]],
                   "notes": [f"dibaca {target} selesai"], "errors": [target]}
        out = rea._relabel(summary, target, "notepad.exe")
        flat = json.dumps(out)
        self.assertNotIn("rea-uploads", flat)
        self.assertNotIn("0123456789ab", flat)
        self.assertEqual(out["facts"][0], ["Berkas", "notepad.exe"])
        self.assertEqual(out["facts"][1], ["Alias", "notepad.exe"])
        self.assertEqual(out["facts"][2], ["Panjang", "notepad.exe"])

    def test_folder_prefix_becomes_folder_name(self):
        target = "/data/rea-uploads/0123456789ab/content/MyApp"
        out = rea._relabel({"title": "t", "facts": [["Berkas", target + "/resources/app.asar"]], "notes": [],
                            "errors": []}, target, "MyApp")
        self.assertEqual(out["facts"][0][1], "MyApp/resources/app.asar")

    def test_noop_without_label_or_for_non_dict(self):
        s = {"title": "t", "facts": [], "notes": [], "errors": []}
        self.assertIs(rea._relabel(s, "/x", None), s)
        self.assertIsNone(rea._relabel(None, "/x", "y"))


if __name__ == "__main__":
    unittest.main()
