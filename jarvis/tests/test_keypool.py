"""Kumpulan key NusaGate: SEMUA key dipakai, merata, dengan cadangan otomatis (itsme/keypool.py).

Permintaan pemilik: punya 10 key atau lebih dari NusaGate, semuanya harus dipakai supaya tidak ada
yang kebanjiran sementara yang lain menganggur. NusaGate membatasi 60 permintaan/menit PER KEY.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from collections import Counter
from unittest import mock

from itsme import config, keypool


def make_keys(n):
    return [f"nsk_{i:02d}" + "k" * 28 + f"{i:04d}" for i in range(n)]


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class PoolBase(unittest.TestCase):
    N = 10

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.clock = Clock()
        self.keys = make_keys(self.N)
        for p in (
            mock.patch.object(config, "DATA_DIR", self.dir),
            mock.patch.object(config, "DEEPSEEK_API_KEY", None),
            mock.patch.object(config, "DEEPSEEK_API_KEYS", list(self.keys)),
            mock.patch.object(config, "NUSAGATE_MODELS", []),
            mock.patch.object(config, "POOL_RPM", 50),
            mock.patch.object(keypool, "_clock", self.clock),
            mock.patch.object(keypool, "_wall", lambda: 5000.0 + self.clock.now),
        ):
            p.start()
            self.addCleanup(p.stop)
        keypool.reset_all()
        self.addCleanup(keypool.reset_all)
        self.addCleanup(keypool.set_alert, None)

    def use(self, n=1, outcome="ok", **kw):
        """Pinjam & kembalikan n kali; kembalikan daftar key yang dipakai."""
        used = []
        for _ in range(n):
            lease = keypool.acquire()
            used.append(lease.key)
            keypool.release(lease, outcome, **kw)
        return used


class EvenUseTest(PoolBase):
    def test_every_key_is_used_and_evenly(self):
        used = Counter(self.use(100))
        self.assertEqual(len(used), 10)  # TIDAK ADA yang menganggur
        self.assertEqual(set(used.values()), {10})  # dan tidak ada yang kebanjiran

    def test_uneven_count_differs_by_at_most_one(self):
        used = Counter(self.use(37))
        self.assertEqual(len(used), 10)
        self.assertLessEqual(max(used.values()) - min(used.values()), 1)

    def test_simultaneous_requests_spread_over_different_keys(self):
        leases = [keypool.acquire() for _ in range(10)]
        self.assertEqual(len({l.key for l in leases}), 10)  # sepuluh permintaan serentak, sepuluh key
        for l in leases:
            keypool.release(l, "ok")

    def test_snapshot_shows_counts_and_never_the_raw_key(self):
        self.use(20)
        rows = keypool.snapshot()
        self.assertEqual([r["uses"] for r in rows], [2] * 10)
        text = json.dumps(rows)
        for key in self.keys:
            self.assertNotIn(key, text)
        self.assertTrue(all(r["mask"].startswith("nsk") and "…" in r["mask"] for r in rows))

    def test_thread_safe_under_concurrency(self):
        def worker():
            for _ in range(50):
                lease = keypool.acquire()
                keypool.release(lease, "ok")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = keypool.snapshot()
        self.assertEqual(sum(r["uses"] for r in rows), 400)
        self.assertEqual({r["inflight"] for r in rows}, {0})  # tidak ada key yang "tersangkut"


class ManyKeysTest(PoolBase):
    """Pemilik: "10 atau mungkin lebih". Tidak ada batas 10 di mana pun - diuji dengan 40 key."""

    N = 40

    def test_forty_keys_are_all_used_and_evenly(self):
        used = Counter(self.use(400))
        self.assertEqual(len(used), 40)  # tidak ada yang menganggur
        self.assertEqual(set(used.values()), {10})  # tidak ada yang kebanjiran

    def test_forty_simultaneous_requests_take_forty_different_keys(self):
        leases = [keypool.acquire() for _ in range(40)]
        self.assertEqual(len({lease.key for lease in leases}), 40)
        for lease in leases:
            keypool.release(lease, "ok")

    def test_when_thirty_keys_run_dry_the_remaining_ten_share_the_load_evenly(self):
        for lease in [keypool.acquire() for _ in range(30)]:
            keypool.release(lease, "balance")
        used = Counter(self.use(100))
        self.assertEqual(len(used), 10)
        self.assertEqual(set(used.values()), {10})

    def test_the_status_list_numbers_every_key_without_showing_one(self):
        rows = keypool.snapshot()
        self.assertEqual([r["no"] for r in rows], list(range(1, 41)))
        shown = json.dumps(rows)
        self.assertFalse(any(key in shown for key in self.keys))

    def test_soft_rate_limit_applies_per_key_not_to_the_whole_pool(self):
        # 40 key x 50/menit = 2000 permintaan per menit sebelum kumpulan "penuh".
        with mock.patch.object(config, "POOL_RPM", 50):
            self.use(2000)
            with self.assertRaises(keypool.PoolExhausted) as cm:
                keypool.acquire()
        self.assertIn("penuh", str(cm.exception))


class FailoverTest(PoolBase):
    N = 3

    def test_rate_limited_key_is_skipped_until_retry_after_passes(self):
        lease = keypool.acquire()
        limited = lease.key
        keypool.release(lease, "limit", retry_after=12)
        for _ in range(10):  # selama dibatasi tidak ada yang memakainya
            self.assertNotIn(limited, self.use(1))
        self.clock.advance(13)
        self.assertIn(limited, self.use(6))  # sudah pulih: ikut lagi

    def test_limit_without_retry_after_uses_a_sensible_default(self):
        lease = keypool.acquire()
        keypool.release(lease, "limit")
        row = next(r for r in keypool.snapshot() if r["id"] == lease.id)
        self.assertEqual(row["status"], "limit")
        self.assertAlmostEqual(row["until"] - (5000.0 + self.clock.now), 30.0, delta=1)

    def test_retry_after_is_bounded(self):
        lease = keypool.acquire()
        keypool.release(lease, "limit", retry_after=99999)
        row = next(r for r in keypool.snapshot() if r["id"] == lease.id)
        self.assertLessEqual(row["until"] - (5000.0 + self.clock.now), 300.5)

    def test_out_of_balance_key_rests_30_minutes(self):
        lease = keypool.acquire()
        keypool.release(lease, "balance")
        self.clock.advance(29 * 60)
        self.assertNotIn(lease.key, self.use(9))
        self.clock.advance(2 * 60)
        self.assertIn(lease.key, self.use(6))

    def test_plan_window_cap_rests_20_minutes(self):
        lease = keypool.acquire()
        keypool.release(lease, "cap")
        self.clock.advance(19 * 60)
        self.assertNotIn(lease.key, self.use(9))
        self.clock.advance(2 * 60)
        self.assertIn(lease.key, self.use(6))

    def test_rejected_key_is_set_aside_then_retried_after_an_hour(self):
        lease = keypool.acquire()
        keypool.release(lease, "invalid")
        self.clock.advance(59 * 60)
        self.assertNotIn(lease.key, self.use(9))
        self.clock.advance(2 * 60)
        self.assertIn(lease.key, self.use(6))  # 401 sesaat tidak mematikan key selamanya

    def test_transient_errors_only_lower_priority(self):
        lease = keypool.acquire()
        keypool.release(lease, "busy")
        # key lain didahulukan...
        self.assertNotIn(lease.key, self.use(2))
        # ...tapi kalau cuma dia yang tersisa, tetap dicoba (galat sesaat, bukan vonis)
        for _ in range(len(self.keys) - 1):  # semua key lain dibatasi penyedia
            keypool.release(keypool.acquire(), "limit", retry_after=100)
        self.assertEqual(self.use(1), [lease.key])

    def test_client_errors_do_not_punish_the_key(self):
        lease = keypool.acquire()
        keypool.release(lease, "client")
        row = next(r for r in keypool.snapshot() if r["id"] == lease.id)
        self.assertEqual((row["status"], row["errors"]), ("siap", 0))

    def test_penalty_expires_by_itself(self):
        lease = keypool.acquire()
        keypool.release(lease, "busy")
        row = next(r for r in keypool.snapshot() if r["id"] == lease.id)
        self.assertEqual(row["status"], "sibuk")  # diturunkan prioritasnya...
        self.clock.advance(21)
        self.assertEqual(next(r for r in keypool.snapshot() if r["id"] == lease.id)["status"], "siap")  # ...lalu pulih

    def test_a_success_on_that_key_clears_its_penalty_at_once(self):
        lease = keypool.acquire()
        keypool.release(lease, "busy")
        keypool.release(keypool.acquire(), "ok")  # key lain
        for _ in range(len(self.keys) - 1):
            keypool.release(keypool.acquire(), "limit", retry_after=100)  # sisakan hanya key yang tadi sibuk
        keypool.release(keypool.acquire(), "ok")  # berhasil dengan key sibuk itu
        self.assertEqual(next(r for r in keypool.snapshot() if r["id"] == lease.id)["status"], "siap")

    def test_exclude_never_hands_out_a_key_already_tried(self):
        tried = set()
        for _ in range(3):
            lease = keypool.acquire(exclude=tried)
            tried.add(lease.id)
            keypool.release(lease, "busy")
        with self.assertRaises(keypool.PoolExhausted) as cm:
            keypool.acquire(exclude=tried)
        self.assertIn("sudah dicoba", str(cm.exception))

    def test_everything_down_gives_a_clear_reason_and_wait(self):
        for _ in range(3):
            keypool.release(keypool.acquire(), "limit", retry_after=20)
        with self.assertRaises(keypool.PoolExhausted) as cm:
            keypool.acquire()
        self.assertIn("3 key dibatasi", str(cm.exception))
        self.assertAlmostEqual(cm.exception.wait, 20, delta=1)

    def test_mixed_reasons_are_all_named(self):
        a, b, c = keypool.acquire(), keypool.acquire(), keypool.acquire()
        keypool.release(a, "balance")
        keypool.release(b, "invalid")
        keypool.release(c, "limit", retry_after=10)
        with self.assertRaises(keypool.PoolExhausted) as cm:
            keypool.acquire()
        text = str(cm.exception)
        for part in ("saldo habis", "ditolak", "dibatasi"):
            self.assertIn(part, text)

    def test_soft_per_minute_limit_prefers_other_keys_then_waits(self):
        with mock.patch.object(config, "POOL_RPM", 2):
            self.use(6)  # tiga key x dua permintaan = semua penuh menit ini
            with self.assertRaises(keypool.PoolExhausted) as cm:
                keypool.acquire()
            self.assertIn("penuh", str(cm.exception))
            self.assertGreater(cm.exception.wait, 0)
            self.clock.advance(61)
            keypool.acquire()  # menit baru: lega lagi

    def test_checked_key_comes_back_at_once(self):
        lease = keypool.acquire()
        keypool.release(lease, "invalid")
        keypool.mark_checked(lease.key, True)
        self.assertIn(lease.key, self.use(6))

    def test_failed_check_sets_the_key_aside(self):
        keypool.mark_checked(self.keys[0], False)
        self.assertNotIn(self.keys[0], self.use(12))

    def test_a_key_the_provider_recognises_is_only_forgiven_for_being_rejected(self):
        # Daftar model terbuka tidak membuktikan key itu bisa menjawab: saldo/batas paket bisa tetap habis.
        a, b, c = keypool.acquire(), keypool.acquire(), keypool.acquire()
        keypool.release(a, "invalid")
        keypool.release(b, "balance")
        keypool.release(c, "limit", retry_after=60)
        for lease in (a, b, c):
            keypool.mark_checked(lease.key, True, full=False)
        status = {r["no"]: r["status"] for r in keypool.snapshot()}
        by_key = {lease.key: status[self.keys.index(lease.key) + 1] for lease in (a, b, c)}
        self.assertEqual(by_key[a.key], "siap")  # tanda 'ditolak' dicabut
        self.assertEqual(by_key[b.key], "balance")  # saldo tetap habis
        self.assertEqual(by_key[c.key], "limit")  # dibatasi tetap dibatasi

    def test_a_key_that_really_answered_is_cleared_of_every_mark(self):
        a, b = keypool.acquire(), keypool.acquire()
        keypool.release(a, "balance")
        keypool.release(b, "cap")
        for lease in (a, b):
            keypool.mark_checked(lease.key, True)
        self.assertEqual({r["status"] for r in keypool.snapshot()}, {"siap"})


class AlertTest(PoolBase):
    N = 2

    def test_owner_is_told_once_when_a_key_dies_and_never_with_the_raw_key(self):
        seen = []
        keypool.set_alert(lambda kind, masked: seen.append((kind, masked)))
        lease = keypool.acquire()
        keypool.release(lease, "invalid")
        self.clock.advance(3601)
        again = keypool.acquire(exclude=set())
        while again.key != lease.key:  # sampai key yang sama dicoba lagi
            keypool.release(again, "ok")
            again = keypool.acquire()
        keypool.release(again, "invalid")  # masih ditolak: tidak diulang-ulang
        self.assertEqual([k for k, _ in seen], ["invalid"])
        self.assertNotIn(lease.key, seen[0][1])

    def test_balance_and_cap_are_announced_too(self):
        seen = []
        keypool.set_alert(lambda kind, masked: seen.append(kind))
        keypool.release(keypool.acquire(), "balance")
        keypool.release(keypool.acquire(), "cap")
        self.assertEqual(seen, ["balance", "cap"])

    def test_a_broken_alert_callback_never_breaks_requests(self):
        def boom(*a):
            raise RuntimeError("x")

        keypool.set_alert(boom)
        with self.assertLogs("itsme", level="ERROR"):
            keypool.release(keypool.acquire(), "invalid")


class KeyListTest(PoolBase):
    N = 0

    def test_no_keys(self):
        with self.assertRaises(keypool.PoolExhausted) as cm:
            keypool.acquire()
        self.assertIn("/kunci", str(cm.exception))
        self.assertFalse(keypool.has_keys())

    def test_legacy_single_key_comes_first_and_duplicates_collapse(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEY", "nsk_" + "a" * 32), \
             mock.patch.object(config, "DEEPSEEK_API_KEYS", ["nsk_" + "b" * 32, "nsk_" + "a" * 32, "nsk_" + "c" * 32]):
            self.assertEqual(config.deepseek_keys(), ["nsk_" + "a" * 32, "nsk_" + "b" * 32, "nsk_" + "c" * 32])

    def test_keys_added_later_join_at_once(self):
        self.assertFalse(keypool.has_keys())
        with mock.patch.object(config, "DEEPSEEK_API_KEYS", make_keys(2)):
            self.assertEqual(len(set(self.use(4))), 2)

    def test_forgetting_a_key_drops_its_state(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEYS", make_keys(1)):
            self.use(3)
            keypool.forget(make_keys(1)[0])
            self.assertEqual(keypool.snapshot()[0]["uses"], 0)


class PersistenceTest(PoolBase):
    N = 3

    def test_counts_survive_a_restart_and_hold_no_raw_keys(self):
        self.use(9)
        keypool.flush()
        path = os.path.join(self.dir, "keypool.json")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        for key in self.keys:
            self.assertNotIn(key, raw)
        keypool.reset_all()  # "menyala ulang"
        self.assertEqual([r["uses"] for r in keypool.snapshot()], [3, 3, 3])

    def test_corrupt_file_is_ignored(self):
        with open(os.path.join(self.dir, "keypool.json"), "w") as f:
            f.write("{rusak")
        self.assertEqual([r["uses"] for r in keypool.snapshot()], [0, 0, 0])

    def test_unwritable_data_dir_never_breaks_requests(self):
        with mock.patch.object(config, "DATA_DIR", os.path.join(self.dir, "berkas-bukan-folder", "x")):
            open(os.path.join(self.dir, "berkas-bukan-folder"), "w").close()
            self.use(3)
            keypool.flush()


class ModelRotationTest(PoolBase):
    N = 1

    def test_no_models_configured_means_each_key_default(self):
        self.assertIsNone(keypool.next_model())
        self.assertEqual(keypool.model_for(self.keys[0], None), "deepseek-v4.1-flash")

    def test_models_take_turns_so_none_sits_idle(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b", "m-c"]):
            picked = []
            for _ in range(9):
                picked.append(keypool.next_model())
                self.clock.advance(1)
        self.assertEqual(Counter(picked), {"m-a": 3, "m-b": 3, "m-c": 3})

    def test_a_rejected_model_is_left_alone_for_30_minutes(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]):
            keypool.model_bad("m-a")
            for _ in range(5):
                self.assertEqual(keypool.next_model(), "m-b")
                self.clock.advance(1)
            self.clock.advance(31 * 60)
            self.assertIn("m-a", {keypool.next_model() for _ in range(2)})

    def test_all_models_cooling_falls_back_to_default(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a"]):
            keypool.model_bad("m-a")
            self.assertIsNone(keypool.next_model())

    def test_exclude_skips_models_already_tried(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]):
            self.assertEqual(keypool.next_model(exclude={"m-a"}), "m-b")

    def test_run_model_only_applies_to_gateway_keys(self):
        self.assertEqual(keypool.model_for("nsk_" + "x" * 32, "m-a"), "m-a")
        with mock.patch.object(config, "DEEPSEEK_URL", ""):
            self.assertEqual(keypool.model_for("sk-" + "x" * 40, "m-a"), "deepseek-chat")  # key resmi tetap miliknya

    def test_model_rows(self):
        with mock.patch.object(config, "NUSAGATE_MODELS", ["m-a", "m-b"]):
            keypool.model_ok("m-a")
            keypool.model_bad("m-b")
            rows = {r["name"]: r for r in keypool.model_rows()}
        self.assertEqual((rows["m-a"]["uses"], rows["m-a"]["cooling"]), (1, False))
        self.assertTrue(rows["m-b"]["cooling"])


if __name__ == "__main__":
    unittest.main()
