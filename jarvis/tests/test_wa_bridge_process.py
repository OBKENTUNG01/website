"""Jembatan WhatsApp (wa.js) dijalankan SUNGGUHAN sebagai proses Node dengan Baileys palsu
(tests/wa_fake_modules), lalu dikirimi pesan masuk dan perintah dari Python seperti saat dipakai.

Yang dibuktikan: siapa yang dilayani (pemilik / tamu / diabaikan), perubahan pengaturan langsung
({"cmd":"set"}), lampiran tamu tidak diunduh, pesan basi & ganda dilewati, dan balasan untuk tamu
tidak pernah dialihkan ke chat pemilik. Tanpa WhatsApp sungguhan, tanpa jaringan. Dilewati kalau Node.js
tidak ada.
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from itsme import whatsapp as wa_module

FAKE_MODULES = os.path.join(os.path.dirname(__file__), "wa_fake_modules", "node_modules")
SELF = "6283125645146"
SELF_LID = "99887766554433"
OWNER_JID = f"{SELF}@s.whatsapp.net"
OWNER_LID_JID = f"{SELF_LID}@lid"
GUEST = "6281234567890@s.whatsapp.net"
GUEST2 = "6289876543210@s.whatsapp.net"
GUEST_LID = "55512345@lid"


@unittest.skipUnless(shutil.which("node"), "Node.js tidak terpasang")
class BridgeProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wa_bridge_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = os.path.join(self.tmp, "app")
        os.makedirs(self.app)
        for name in ("wa.js", "wa_core.js"):
            shutil.copy(os.path.join(wa_module.DIR, name), self.app)
        # Hanya modul palsu yang terlihat: node_modules asli (kalau ada) tidak ikut tersalin.
        shutil.copytree(FAKE_MODULES, os.path.join(self.app, "node_modules"))
        self.ctrl = os.path.join(self.tmp, "ctrl")
        os.makedirs(self.ctrl)
        self.events = queue.Queue()
        self.proc = None
        self.msg_n = 0

    def start(self, **env):
        base = dict(os.environ, FAKE_WA_DIR=self.ctrl, WA_OWNER=SELF, WA_AUTH=os.path.join(self.tmp, "auth"),
                    WA_FILES=os.path.join(self.tmp, "files"), WA_OPEN="0", WA_GUESTS="", WA_BLOCK="",
                    WA_MAX_MB="5")
        base.update(env)
        err = open(os.path.join(self.tmp, "stderr.txt"), "wb")
        self.proc = subprocess.Popen(
            [shutil.which("node"), "wa.js"], cwd=self.app, env=base, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8")
        self.addCleanup(err.close)
        self.addCleanup(self.stop)  # LIFO: proses dihentikan dulu, baru berkas galatnya ditutup

        def reader(proc):
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        self.events.put(json.loads(line))
                    except ValueError:
                        pass

        threading.Thread(target=reader, args=(self.proc,), daemon=True).start()
        self.wait_for(lambda e: e.get("state") == "open")

    def stop(self):
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    # --- bantuan ------------------------------------------------------------------------
    def wait_for(self, match, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if match(event):
                return event
        self.fail("kejadian yang ditunggu tidak datang; stderr:\n" + self.stderr())

    def stderr(self):
        try:
            with open(os.path.join(self.tmp, "stderr.txt"), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def inject(self, jid, text=None, from_me=False, mid=None, ts=None, message=None, alt=None):
        self.msg_n += 1
        key = {"remoteJid": jid, "fromMe": from_me, "id": mid or f"M{self.msg_n}"}
        if alt:
            key["remoteJidAlt"] = alt
        payload = {"type": "notify", "messages": [{
            "key": key, "messageTimestamp": ts if ts is not None else int(time.time()),
            "message": message if message is not None else {"conversation": text},
        }]}
        with open(os.path.join(self.ctrl, "inject.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def sentinel(self):
        """Pesan pemilik yang PASTI dilayani. Kejadian yang datang sebelumnya = pesan yang dilayani."""
        self.msg_n += 1
        tag = f"sentinel-{self.msg_n}"
        self.inject(OWNER_JID, tag, from_me=True)
        return tag

    def drain_until_sentinel(self):
        """Kumpulkan semua pesan yang dilayani sampai sentinel; tanpa tidur: urutan pasti (satu antrean)."""
        tag = self.sentinel()
        seen = []
        end = time.time() + 15
        while time.time() < end:
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if "jid" not in event:
                continue
            if event.get("text") == tag:
                return seen
            seen.append(event)
        self.fail("sentinel tidak datang; stderr:\n" + self.stderr())

    def command(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def sent(self):
        path = os.path.join(self.ctrl, "sent.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def wait_sent(self, count, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.sent()) >= count:
                return self.sent()
            time.sleep(0.05)
        self.fail(f"pesan keluar ke-{count} tidak tercatat: {self.sent()}\n{self.stderr()}")

    def downloads(self):
        path = os.path.join(self.ctrl, "download.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()

    # --- pemilik ---------------------------------------------------------------------------
    def test_owner_self_chat_is_served_as_the_owner(self):
        self.start()
        self.inject(OWNER_JID, "halo", from_me=True)
        event = self.wait_for(lambda e: e.get("text") == "halo")
        self.assertFalse(event["guest"])
        self.assertEqual(event["jid"], OWNER_JID)

    def test_owner_self_chat_through_the_lid_address_is_served_too(self):
        self.start()
        self.inject(OWNER_LID_JID, "lewat lid", from_me=True)
        event = self.wait_for(lambda e: e.get("text") == "lewat lid")
        self.assertFalse(event["guest"])

    def test_what_the_owner_types_to_someone_else_is_not_answered(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, "pesan pemilik untuk temannya", from_me=True)
        self.assertEqual(self.drain_until_sentinel(), [])

    def test_the_bridges_own_replies_are_not_answered_again(self):
        self.start()
        self.command({"jid": OWNER_JID, "text": "balasan bot"})
        sent = self.wait_sent(1)
        self.inject(OWNER_JID, "balasan bot", from_me=True, mid="OUT1")  # id yang dikirim bridge sendiri
        self.assertEqual(self.drain_until_sentinel(), [])
        self.assertEqual(sent[0]["text"], "balasan bot")

    def test_owner_attachment_is_still_downloaded(self):
        self.start()
        self.inject(OWNER_JID, from_me=True, message={"documentMessage": {"fileName": "catatan.txt", "fileLength": 16}})
        event = self.wait_for(lambda e: e.get("file"))
        self.assertFalse(event["guest"])
        self.assertEqual(event["file"]["name"], "catatan.txt")
        self.assertTrue(os.path.isfile(event["file"]["path"]))
        self.assertEqual(len(self.downloads()), 1)

    # --- tamu ---------------------------------------------------------------------------------
    def test_strangers_are_ignored_while_open_mode_is_off(self):
        self.start()
        self.inject(GUEST, "halo, ini siapa?")
        self.assertEqual(self.drain_until_sentinel(), [])

    def test_open_mode_serves_strangers_as_guests(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, "halo, ini siapa?")
        event = self.wait_for(lambda e: e.get("text") == "halo, ini siapa?")
        self.assertTrue(event["guest"])
        self.assertEqual(event["jid"], GUEST)
        self.assertIsNone(event["file"])

    def test_open_mode_can_be_switched_on_and_off_while_running(self):
        self.start()
        self.inject(GUEST, "sebelum")
        self.assertEqual(self.drain_until_sentinel(), [])
        self.command({"cmd": "set", "open": True, "block": [], "guests": []})
        self.inject(GUEST, "sesudah aktif")
        served = self.drain_until_sentinel()
        self.assertEqual([e["text"] for e in served], ["sesudah aktif"])
        self.command({"cmd": "set", "open": False, "block": [], "guests": []})
        self.inject(GUEST, "sesudah mati")
        self.assertEqual(self.drain_until_sentinel(), [])

    def test_registered_guests_are_served_even_when_open_mode_is_off(self):
        self.start(WA_GUESTS="6281234567890")
        self.inject(GUEST, "halo")
        self.inject(GUEST2, "halo juga")
        served = self.drain_until_sentinel()
        self.assertEqual([(e["jid"], e["guest"]) for e in served], [(GUEST, True)])

    def test_blocked_numbers_are_never_answered_and_the_list_updates_live(self):
        self.start(WA_OPEN="1", WA_BLOCK="6281234567890")
        self.inject(GUEST, "dari yang diblokir")
        self.inject(GUEST2, "dari yang boleh")
        self.assertEqual([e["jid"] for e in self.drain_until_sentinel()], [GUEST2])
        self.command({"cmd": "set", "open": True, "block": ["6289876543210"], "guests": []})
        self.inject(GUEST, "sudah dibuka")
        self.inject(GUEST2, "sekarang diblokir")
        self.assertEqual([e["jid"] for e in self.drain_until_sentinel()], [GUEST])

    def test_a_blocked_contact_is_recognised_through_the_phone_number_alias(self):
        self.start(WA_OPEN="1", WA_BLOCK="6281234567890")
        self.inject(GUEST_LID, "lewat lid", alt=GUEST)
        self.assertEqual(self.drain_until_sentinel(), [])

    def test_groups_broadcasts_status_and_channels_are_never_answered(self):
        self.start(WA_OPEN="1", WA_GUESTS="6281234567890")
        for jid in ("120363025246125486@g.us", "status@broadcast", "123456@broadcast", "120363111@newsletter"):
            self.inject(jid, "halo semua")
            self.inject(jid, "pemilik di grup", from_me=True)
        self.assertEqual(self.drain_until_sentinel(), [])

    def test_guest_attachments_are_not_downloaded_only_noted(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, message={"imageMessage": {"fileLength": 1000}})
        self.inject(GUEST, message={"audioMessage": {"ptt": True}})
        self.inject(GUEST, message={"documentMessage": {"fileName": "x.exe", "fileLength": 10}})
        served = self.drain_until_sentinel()
        self.assertEqual([e["media"] for e in served], ["gambar", "audio", "dokumen"])
        self.assertTrue(all(e["file"] is None and e["guest"] for e in served))
        self.assertEqual(self.downloads(), [])
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "files")))  # tidak ada yang tersimpan di disk

    def test_a_guest_caption_is_answered_as_text_without_downloading(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, message={"imageMessage": {"caption": "ini apa ya?", "fileLength": 10}})
        served = self.drain_until_sentinel()
        self.assertEqual(served[0]["text"], "ini apa ya?")
        self.assertEqual(served[0]["media"], "gambar")
        self.assertEqual(self.downloads(), [])

    def test_stickers_and_empty_events_are_ignored(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, message={"stickerMessage": {}})
        self.inject(GUEST, message={"reactionMessage": {"text": "👍"}})
        self.inject(GUEST, message={})
        self.assertEqual(self.drain_until_sentinel(), [])

    def test_old_guest_messages_from_while_it_was_off_are_skipped(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, "tadi pagi", ts=int(time.time()) - 3 * 3600)
        self.inject(GUEST, "baru saja", ts=int(time.time()) - 20)
        self.assertEqual([e["text"] for e in self.drain_until_sentinel()], ["baru saja"])

    def test_the_same_guest_message_is_answered_once(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, "satu kali saja", mid="SAMA")
        self.inject(GUEST, "satu kali saja", mid="SAMA")
        self.inject(GUEST, "pesan lain", mid="LAIN")
        self.assertEqual([e["text"] for e in self.drain_until_sentinel()], ["satu kali saja", "pesan lain"])

    def test_text_inside_a_disappearing_messages_wrapper_is_read(self):
        self.start(WA_OPEN="1")
        self.inject(GUEST, message={"ephemeralMessage": {"message": {"extendedTextMessage": {"text": "pesan sementara"}}}})
        self.assertEqual([e["text"] for e in self.drain_until_sentinel()], ["pesan sementara"])

    def test_no_message_of_a_guest_is_ever_marked_as_the_owner(self):
        self.start(WA_OPEN="1")
        for n in range(5):
            self.inject(GUEST, f"tamu {n}")
        self.assertTrue(all(e["guest"] for e in self.drain_until_sentinel()))

    # --- mengirim -------------------------------------------------------------------------
    def test_replies_go_to_the_address_they_were_given(self):
        self.start(WA_OPEN="1")
        self.command({"jid": GUEST, "text": "balasan untuk tamu"})
        sent = self.wait_sent(1)
        self.assertEqual(sent, [{"jid": GUEST, "text": "balasan untuk tamu", "document": False}])

    def test_a_rejected_guest_address_is_never_redirected_to_the_owners_own_chat(self):
        self.start(WA_OPEN="1", FAKE_WA_FAIL=f"{GUEST_LID},{OWNER_LID_JID}")
        self.command({"jid": GUEST_LID, "text": "RAHASIA PERCAKAPAN TAMU"})
        self.command({"jid": GUEST, "text": "penanda"})
        sent = self.wait_sent(1)
        self.assertEqual([s["jid"] for s in sent], [GUEST])  # hanya penanda; balasan tamu tidak bocor ke pemilik
        time.sleep(0.3)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("gagal kirim", self.stderr())

    def test_the_owners_own_lid_address_still_falls_back_to_the_phone_number(self):
        self.start(FAKE_WA_FAIL=OWNER_LID_JID)
        self.command({"jid": OWNER_LID_JID, "text": "balasan untuk pemilik"})
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["jid"], OWNER_JID)
        self.assertEqual(sent[0]["text"], "balasan untuk pemilik")

    def test_unknown_commands_do_not_break_the_bridge(self):
        self.start(WA_OPEN="1")
        self.command({"cmd": "tidak-ada"})
        self.command({"cmd": "set", "open": "ya", "block": "bukan-daftar"})  # salah tipe: diabaikan
        self.proc.stdin.write("bukan json\n")
        self.proc.stdin.flush()
        self.inject(GUEST, "masih hidup")
        self.assertEqual([e["text"] for e in self.drain_until_sentinel()], ["masih hidup"])
        self.assertIsNone(self.proc.poll())


if __name__ == "__main__":
    unittest.main()
