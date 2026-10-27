"""Entry point It's Me — bot Telegram.

Jalankan: python -m itsme.main   (dari folder jarvis/)

Fitur utama:
- Hanya menjawab pemilik (OWNER_TELEGRAM_ID).
- Multitasking: tiap pesan ditangani sebagai task async; alat tugas_latar
  menjalankan pekerjaan paralel yang hasilnya dikirim terpisah.
- Konfirmasi aksi berisiko lewat tombol inline.
- Tidak pernah diam saja: selama berpikir, status "mengetik" terus tampil, dan
  error apa pun dilaporkan ke pemilik serta dicatat di data/itsme.log.
- Hanya satu It's Me yang boleh jalan sekaligus; dua bot dengan token yang
  sama akan saling berebut pesan dari Telegram.
- Memperbarui diri sendiri: repo GitHub diperiksa berkala; kalau ada commit
  baru, pekerjaan yang sedang jalan diselesaikan dulu, pembaruan dipasang,
  lalu It's Me menyala ulang (lihat updater.py).
"""

import asyncio
import hashlib
import atexit
import base64
import logging
import os
import queue
import re
import sys
import uuid
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

from . import access, brain, config, dashboard, devices, keys, launch, lock, node, quota, updater
from .brain import run_agent
from .memory import Memory
from .telegram import Telegram, confirm_keyboard
from .whatsapp import WhatsApp

log = logging.getLogger("itsme")

HELP = (
    "Halo, It's Me siap. 🫡\n\n"
    "Kirim saja apa yang kamu mau — tanya, minta cari info terbaru, edit & "
    "deploy website, atau beberapa tugas sekaligus.\n\n"
    "Perintah:\n"
    "/help — bantuan ini\n"
    "/otak — lihat otak yang sedang dipakai\n"
    "/alamat — alamat dashboard untuk dibuka dari HP/internet (lewat tunnel)\n"
    "/wa <nomor> — atur nomor WhatsApp pemilik (bot WA langsung dinyalakan)\n"
    "/cloud — pakai otak cloud (OpenRouter, lebih pintar)\n"
    "/deepseek — pakai otak DeepSeek (murah & pintar); /deepseek <key> untuk simpan key-nya\n"
    "/claudepc — pakai Claude di komputer (akun Claude-mu, tanpa API key)\n"
    "/claude — pakai otak Claude (Anthropic API, paling pintar)\n"
    "/lokal — pakai otak lokal (gratis, privat)\n"
    "/kunci <key> — simpan API key atau token akun Claude (pesannya langsung kuhapus)\n"
    "/akses — lihat mode akses (aman/penuh)\n"
    "/akses_penuh — langsung bertindak tanpa tombol (berkas rahasia tetap dijaga)\n"
    "/akses_aman — kembali minta tombol untuk aksi yang mengubah sesuatu\n"
    "/memori — lihat isi memori jangka panjang\n"
    "/hapus_memori — kosongkan memori\n"
    "/reset — mulai percakapan baru (lupakan konteks obrolan, memori tetap)\n"
    "/perangkat — daftar komputer lain yang terhubung\n"
    "/pakai <nama> — pesan berikutnya dikerjakan komputer itu (/pakai saja = kembali ke sini)\n"
    "/pasang — satu baris perintah untuk memasang It's Me di komputer lain\n"
    "/komputer — buka layar & berkas komputer yang terhubung (seperti remote desktop)\n"
    "/layar <nama> — kirim foto layar komputer itu ke chat\n"
    "/lock — kunci bot; kata sandi diminta lagi sebelum bisa dipakai\n"
    "/id — tampilkan user id Telegram-mu"
)
# Perintah yang tetap dikerjakan Hub walau ada perangkat tujuan (/pakai).
# Memori & mode akses Hub disalin ke semua perangkat, jadi diatur di sini.
# Perintah yang tetap boleh dipakai tamu (sisanya: pemilik saja).
_GUEST_OK = {"", "/start", "/help", "/id", "/lock"}
_HUB_ONLY = {
    "/start", "/help", "/kunci", "/akses", "/akses_penuh", "/akses_aman",
    "/memori", "/hapus_memori", "/perangkat", "/pakai", "/pasang", "/id",
    "/lock", "/komputer", "/layar", "/wa",
}
# Paling lama Hub menunggu jawaban dari perangkat (detik).
_DEVICE_REPLY_TIMEOUT = 3600

KUNCI_HELP = (
    "Kirim API key dengan: /kunci <key>\n\n"
    "Key Claude diawali sk-ant-, token akun Claude (dari claude setup-token) "
    "diawali sk-ant-oat, key OpenRouter diawali sk-or-, key DeepSeek (gateway) "
    "diawali nsk-. Pesanmu langsung kuhapus, key diperiksa dulu ke penyedianya, "
    "lalu disimpan ke .env dan langsung aktif. Key tidak pernah dibaca otak AI.\n\n"
    "Key DeepSeek resmi diawali sk- (tak bisa dibedakan otomatis) — untuk yang "
    "itu kirim: /deepseek <key>."
)
ACCESS_WARNING = (
    "Aktifkan akses penuh?\n\n"
    "Semua perintah, baca/tulis berkas, deploy, dan memori langsung jalan tanpa "
    "tombol; aksi yang mengubah sesuatu tetap kukabari di chat.\n\n"
    "Tetap dijaga: berkas rahasia (password, token, kunci, login aplikasi & "
    "browser) selalu ditolak, dan perintah yang menyebutnya tetap ditanyakan.\n\n"
    "Risikonya: siapa pun yang bisa membuka Telegram-mu bisa mengendalikan "
    "komputer ini, dan perintah tersembunyi di halaman web bisa ikut dijalankan. "
    "Matikan kapan saja dengan /akses_aman."
)
# Perintah Telegram untuk mengganti otak, dan perintah yang memakai tiap jenis key.
_BRAIN_COMMANDS = {
    "/lokal": "ollama",
    "/cloud": "openrouter",
    "/deepseek": "deepseek",
    "/claude": "claude",
    "/claudepc": "claude_code",
}
_KEY_COMMANDS = {kind: cmd for cmd, kind in _BRAIN_COMMANDS.items()}
# Pesan yang bukan teks (suara, foto, stiker, ...) belum bisa dibaca otaknya.
_NON_TEXT = ("voice", "audio", "photo", "video", "video_note", "document", "sticker", "location", "contact")
_URL = re.compile(r"https?://[^\s<>\"']+")
# Jawaban otak lokal di komputer tanpa GPU bisa makan beberapa menit; setelah
# sekian detik pemilik diberi tahu bahwa It's Me masih bekerja.
_SLOW_NOTICE_AFTER = 25
_TYPING_EVERY = 4.5
# Exit code saat It's Me lain sudah jalan; skrip penyala berhenti mengulang.
EXIT_ALREADY_RUNNING = 3
# Exit code setelah pembaruan terpasang; skrip penyala langsung menyalakan lagi.
EXIT_UPDATED = 5
# Pemeriksaan pembaruan pertama, beberapa detik setelah menyala (berikutnya
# tiap config.UPDATE_INTERVAL).
_FIRST_UPDATE_CHECK = 30
# Sebelum menyala ulang, pesan & tugas latar yang sedang jalan ditunggu selesai,
# paling lama sekian detik.
_UPDATE_MAX_WAIT = 600
# Jeda menanyakan pemimpin ke It's Me Node (detik).
_NODE_CHECK_EVERY = 5
_HARI = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")


def _stamped(text):
    """Pesan pemilik diawali waktu kirimnya, supaya otak tahu jam & tanggal
    tanpa system prompt yang berubah-ubah (lihat persona.py)."""
    now = datetime.now()
    return f"[{_HARI[now.weekday()]}, {now:%d-%m-%Y %H.%M}] {text}"


_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # batas gambar Claude API


def _attachment(message):
    """(file_id, nama, gambar?) untuk foto atau berkas kiriman pemilik, atau None."""
    if message.get("photo"):
        photo = message["photo"][-1]  # ukuran terbesar
        return photo["file_id"], f"foto_{photo['file_unique_id']}.jpg", True
    doc = message.get("document")
    if doc:
        name = doc.get("file_name") or "berkas"
        is_image = os.path.splitext(name)[1].lower() in _IMAGE_TYPES
        return doc["file_id"], name, is_image
    return None


def _with_image(text, image_path):
    """Isi pesan pemilik; berisi gambar (format Anthropic) kalau ada."""
    if not image_path or os.path.getsize(image_path) > _MAX_IMAGE_BYTES:
        return text
    media = _IMAGE_TYPES[os.path.splitext(image_path)[1].lower()]
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return [
        {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
        {"type": "text", "text": text},
    ]


class App:
    def __init__(self):
        # Mode perangkat (agent): tanpa Telegram, hanya dashboard + daftar ke Hub.
        self.tg = None if config.AGENT_MODE else Telegram(config.TELEGRAM_TOKEN, config.TELEGRAM_API_URL)
        # Di perangkat, memori adalah salinan memori Hub (catatan baru ikut dikirim ke Hub).
        self.memory = (devices.HubMemory if config.AGENT_MODE else Memory)(config.MEMORY_FILE)
        self.loop = None
        self.histories = {}  # chat_id -> list[message]
        self.chat_locks = {}  # chat_id -> asyncio.Lock (jaga konsistensi riwayat)
        self.pending = {}  # confirm id -> asyncio.Future
        # Alamat web yang boleh dibuka tanpa konfirmasi: hasil cari_web dan
        # alamat yang pemilik kirim sendiri.
        self.seen_urls = {}  # chat_id -> set[str]
        # Referensi ke task yang sedang jalan; tanpa ini asyncio boleh
        # membuangnya (garbage collect) di tengah pekerjaan.
        self._tasks = set()
        self._failed_updates = set()  # commit yang gagal dipasang; tidak dicoba lagi
        self._env_changed = False  # .env dari Hub berubah -> perlu nyala ulang
        self._update_warning = None  # masalah pembaruan yang sudah dilaporkan
        self.started = None  # waktu It's Me menyala (diisi di run())
        self._qr_at = 0.0  # kapan QR WhatsApp terakhir dikirim
        self.wa = None  # jembatan WhatsApp (itsme/whatsapp.py)

    # --- util ---------------------------------------------------------------
    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _say(self, chat_id, text):
        """Kirim pesan ke pemilik: lewat Telegram di Hub, lewat Hub di perangkat."""
        if self.tg is not None:
            await self.tg.send(chat_id, text)
        else:
            await asyncio.to_thread(devices.send_message, text)

    def _brain_status(self):
        lines = [f"Otak sekarang: {brain.describe()}", ""]
        lines.append(f"/lokal — {brain.describe('ollama')}")
        if config.OPENROUTER_API_KEY:
            lines.append(f"/cloud — {brain.describe('openrouter')}")
        else:
            lines.append("/cloud — belum aktif (kirim /kunci <key OpenRouter>)")
        if config.DEEPSEEK_API_KEY:
            lines.append(f"/deepseek — {brain.describe('deepseek')}")
        else:
            lines.append("/deepseek — belum aktif (kirim /deepseek <key>)")
        if config.ANTHROPIC_API_KEY:
            lines.append(f"/claude — {brain.describe('claude')}")
        else:
            lines.append("/claude — belum aktif (kirim /kunci <key Claude>)")
        if brain.claude_code_ready():
            lines.append(f"/claudepc — {brain.describe('claude_code')}")
        else:
            lines.append("/claudepc — belum login (kirim /claudepc untuk caranya)")
        lines += ["", self._access_status()]
        if brain.current_backend() == "openrouter" and config.FALLBACK_LOCAL:
            lines += ["", "Kalau otak cloud tidak bisa dipakai, otak lokal otomatis menjawab."]
        if config.OPENROUTER_API_KEY and config.OPENROUTER_MODEL.endswith(":free"):
            from .brain_openrouter import free_quota, reset_time_text

            quota = free_quota()
            if quota:
                lines += [
                    "",
                    f"Kuota model gratis hari ini: sisa {quota['remaining']} dari {quota['limit']} "
                    f"(baru lagi pukul {reset_time_text()}).",
                ]
        return "\n".join(lines)

    @staticmethod
    def _access_status():
        if access.full():
            return (
                "Akses: 🔓 penuh — langsung bertindak tanpa tombol, berkas rahasia "
                "tetap dijaga. /akses_aman untuk kembali minta tombol."
            )
        return (
            "Akses: 🔒 aman — aksi yang mengubah sesuatu dan membaca di luar folder "
            "website minta tombol Ya. /akses_penuh untuk langsung bertindak."
        )

    def _lock(self, chat_id):
        if chat_id not in self.chat_locks:
            self.chat_locks[chat_id] = asyncio.Lock()
        return self.chat_locks[chat_id]

    def _seen(self, chat_id):
        seen = self.seen_urls.setdefault(chat_id, set())
        if len(seen) > 1000:  # jangan tumbuh tanpa batas
            seen.clear()
        return seen

    def _ctx_for(self, chat_id, in_background=False, guest=False):
        return {
            "website_dir": config.WEBSITE_REPO_DIR or None,
            "memory": self.memory,
            "confirm": lambda text: self._confirm_blocking(chat_id, text),
            "notify": lambda text: self._notify_blocking(chat_id, text),
            "full_access": access.full(),
            "schedule_background": lambda instr: self._schedule_background(
                chat_id, instr
            ),
            "seen_urls": self._seen(chat_id),
            "in_background": in_background,
            "guest": guest,
        }

    async def _keep_typing(self, chat_id):
        """Tampilkan "mengetik…" terus selama otak bekerja (Telegram
        menghapusnya sendiri setelah ±5 detik), dan beri tahu pemilik sekali
        kalau jawabannya lama."""
        waited = 0.0
        noticed = False
        while True:
            await self.tg.send_chat_action(chat_id, "typing")
            await asyncio.sleep(_TYPING_EVERY)
            waited += _TYPING_EVERY
            if not noticed and waited >= _SLOW_NOTICE_AFTER:
                noticed = True
                note = "⏳ Masih kukerjakan…"
                if brain.current_backend() == "ollama":
                    note += " Otak lokal di komputer ini bisa butuh beberapa menit per jawaban."
                await self.tg.send(chat_id, note)

    async def _think(self, chat_id, messages, ctx):
        """Jalankan otak di thread terpisah. Error apa pun jadi pesan yang jelas."""
        # Di perangkat tidak ada Telegram; status "mengetik" ditampilkan Hub.
        typing = asyncio.create_task(self._keep_typing(chat_id)) if self.tg is not None else None
        try:
            return await asyncio.to_thread(run_agent, messages, ctx)
        except Exception as exc:  # noqa: BLE001 - pemilik harus selalu dapat jawaban
            log.exception("otak gagal menjawab")
            return (
                f"⚠️ Maaf, terjadi error saat memproses pesanmu: {type(exc).__name__}: {exc}\n"
                f"(Detailnya tercatat di {config.LOG_FILE})"
            )
        finally:
            if typing:
                typing.cancel()

    # --- konfirmasi (dari thread worker ke event loop) ---------------------
    def _confirm_blocking(self, chat_id, text):
        if self.tg is None:
            # Mode perangkat: tombol Ya/Batal ditampilkan Hub di Telegram pemilik.
            return devices.ask_confirm(text)
        fut = asyncio.run_coroutine_threadsafe(
            self._ask_confirm(chat_id, text), self.loop
        )
        try:
            return fut.result()
        except Exception:  # noqa: BLE001
            log.exception("konfirmasi gagal")
            return False

    def _notify_blocking(self, chat_id, text):
        """Kabari pemilik tentang aksi yang langsung dijalankan (akses penuh)."""
        detail = text.split("\n\n", 1)[-1]  # tanpa baris pertanyaannya
        if self.tg is None:  # mode perangkat: dikabarkan lewat Hub
            devices.send_message(f"🔓 Langsung kujalankan (akses penuh):\n\n{detail}")
            return
        fut = asyncio.run_coroutine_threadsafe(
            self.tg.send(chat_id, f"🔓 Langsung kujalankan (akses penuh):\n\n{detail}"), self.loop
        )
        try:
            fut.result(timeout=30)
        except Exception:  # noqa: BLE001 - kabar gagal tidak boleh menghentikan pekerjaan
            log.exception("kabar akses penuh gagal dikirim")

    async def _ask_confirm(self, chat_id, text):
        cid = uuid.uuid4().hex[:12]
        future = self.loop.create_future()
        self.pending[cid] = future
        sent = await self.tg.send(
            chat_id, "⚠️ " + text, reply_markup=confirm_keyboard(cid)
        )
        message_id = None
        if sent and sent.get("ok"):
            message_id = sent["result"]["message_id"]
        try:
            return await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            await self.tg.send(chat_id, "⏱️ Konfirmasi habis waktu, dianggap batal.")
            return False
        finally:
            self.pending.pop(cid, None)
            if message_id is not None:
                await self.tg.edit_markup(chat_id, message_id, None)

    # --- tugas latar (multitasking) ----------------------------------------
    def _schedule_background(self, chat_id, instruksi):
        instruksi = (instruksi or "").strip()
        if not instruksi:
            return "Instruksi tugas latar kosong."
        # Dipanggil dari thread worker: serahkan ke event loop untuk dibuat task.
        self.loop.call_soon_threadsafe(
            self._spawn, self._run_background(chat_id, instruksi)
        )
        return f"Oke, aku kerjakan di latar: {instruksi}"

    async def _run_background(self, chat_id, instruksi):
        await self._say(chat_id, f"⏳ Mulai di latar: {instruksi}")
        messages = [{"role": "user", "content": _stamped(instruksi)}]
        ctx = self._ctx_for(chat_id, in_background=True)
        reply = await self._think(chat_id, messages, ctx)
        await self._say(chat_id, f"✅ Tugas latar selesai: {instruksi}\n\n{reply}")

    # --- API key lewat chat (/kunci) ----------------------------------------
    async def _save_key(self, chat_id, message, force_kind=None, then_use=False):
        """Simpan API key dari chat tanpa membiarkannya terbaca: pesannya
        langsung dihapus, key diperiksa, disimpan ke .env, dan hanya
        ditampilkan sebagian. Tidak pernah masuk riwayat atau log.

        force_kind memaksa jenis key (mis. "deepseek" untuk key resmi sk- yang
        tak bisa dibedakan otomatis). then_use langsung memakai otaknya."""
        parts = (message.get("text") or "").split()[1:]
        if not parts:
            await self.tg.send(
                chat_id,
                "Kirim: /deepseek <key>  (key diawali nsk- atau sk-)." if force_kind else KUNCI_HELP,
            )
            return
        deleted = await self.tg.delete_message(chat_id, message.get("message_id"))
        note = "" if deleted else "\n\n⚠️ Pesanmu yang berisi key belum bisa kuhapus — hapus sendiri, ya."
        if force_kind:
            key = next((p for p in parts if keys.looks_like_key(p)), None)
        else:
            key = next((p for p in parts if keys.detect(p)), None)
        if key is None:
            await self.tg.send(
                chat_id,
                ("Itu tidak seperti API key (hanya huruf, angka, - dan _)." if force_kind else
                 "Key tidak dikenali. Key Claude diawali sk-ant-, key OpenRouter diawali "
                 "sk-or-, key DeepSeek diawali nsk-. Contoh: /kunci sk-ant-…") + note,
            )
            return
        kind = force_kind or keys.detect(key)
        name = keys.label(kind)
        try:
            await asyncio.to_thread(keys.verify, kind, key)
        except keys.KeyRejected as exc:
            await self.tg.send(chat_id, f"❌ {exc} Key tidak disimpan." + note)
            return
        except Exception as exc:  # noqa: BLE001 - mis. internet putus
            # Hanya nama error-nya: pesan error lengkap tidak boleh membawa key ke log.
            log.warning("API key %s belum bisa diperiksa: %s", name, type(exc).__name__)
            await self.tg.send(
                chat_id,
                f"⚠️ Key {name} belum bisa diperiksa ({type(exc).__name__}). Cek "
                "internet komputer lalu kirim lagi. Key tidak disimpan." + note,
            )
            return
        try:
            await asyncio.to_thread(keys.save, kind, key)
        except OSError as exc:
            await self.tg.send(chat_id, f"⚠️ Key {name} gagal disimpan ke .env: {exc.strerror or exc}" + note)
            return
        if then_use:
            try:
                await asyncio.to_thread(brain.set_backend, kind)
                self.histories.pop(chat_id, None)  # format riwayat tiap otak berbeda
                usage = f"Otak {name} sekarang aktif. Konteks obrolan direset, memori tetap."
            except ValueError as exc:
                usage = f"Tersimpan, tapi belum bisa dipakai sekarang: {exc}"
        elif kind == "github":
            usage = "Sekarang kamu bisa generate APK aplikasi HP dari dashboard (tab HP)."
        elif brain.current_backend() == kind:
            usage = f"Otak {name} sekarang memakai key ini."
        else:
            usage = f"Kirim {_KEY_COMMANDS[kind]} untuk memakainya."
        await self.tg.send(
            chat_id,
            f"✅ Key {name} ({keys.mask(key)}) sudah diperiksa, disimpan, dan langsung aktif. {usage}" + note,
        )

    async def _set_wa_owner(self, chat_id, arg):
        """Atur nomor WhatsApp pemilik dari Telegram (/wa 62812…); tanpa arg = status."""
        if not arg:
            nyala = "menyala" if self.wa else ("mati" if not config.WHATSAPP else "gagal menyala")
            await self.tg.send(
                chat_id,
                f"Nomor WhatsApp pemilik: {config.WA_OWNER or '(belum diisi)'}\n"
                f"Bot WhatsApp: {nyala}\n\nUbah: /wa 6281234567890",
            )
            return
        nomor = re.sub(r"\D", "", arg)
        if nomor.startswith("0"):
            nomor = "62" + nomor[1:]
        if not 8 <= len(nomor) <= 15:
            await self.tg.send(chat_id, "Nomor tidak valid. Contoh: /wa 6281234567890")
            return
        keys._set_env(keys.ENV_FILE, "ITSME_WA_OWNER", nomor)
        keys._set_env(keys.ENV_FILE, "ITSME_WHATSAPP", "1")
        config.WA_OWNER, config.WHATSAPP = nomor, True
        if self.wa:
            self.wa.stop()
        self.wa = WhatsApp(self._wa_inbox, self._wa_qr)
        if not self.wa.start():
            self.wa = None
            await self.tg.send(
                chat_id,
                f"Nomor {nomor} disimpan, tapi bot WhatsApp belum bisa menyala. Pastikan "
                "Node.js terpasang dan 'npm install' sudah jalan di itsme\\whatsapp "
                "(alasannya tertulis di jendela It's Me).",
            )
            return
        await self.tg.send(
            chat_id,
            f"Nomor WhatsApp pemilik disimpan: {nomor}\n"
            "Bot WA menyala — QR-nya kukirim ke chat ini; pindai dari WhatsApp HP > "
            "Perangkat tertaut.",
        )
    def _wa_qr(self, png):
        """QR login dari thread node: kirim sebagai foto ke Telegram (maks. 1x/45 dtk)."""
        if self.loop.time() - self._qr_at < 45:
            return
        self._qr_at = self.loop.time()
        self.loop.call_soon_threadsafe(self._spawn, self.tg.send_photo(
            config.OWNER_ID, png, "whatsapp-qr.png",
            "WhatsApp: buka WhatsApp HP > Perangkat tertaut > Tautkan perangkat, "
            "lalu pindai QR ini."))

    # --- WhatsApp -------------------------------------------------------------
    def _wa_inbox(self, jid, text, guest=False):
        """Dipanggil dari thread pembaca WhatsApp; serahkan ke event loop."""
        if text.strip():
            self.loop.call_soon_threadsafe(self._spawn, self._run_wa(jid, text, guest))

    async def _run_wa(self, jid, text, guest=False):
        # ponytail: riwayat dipisah per nomor WhatsApp, tapi konfirmasi aksi &
        # tugas latar tetap lewat chat Telegram pemilik (tombol).
        if guest and not quota.allow("wa:" + jid):
            await asyncio.to_thread(self.wa.send, jid, "Kuota harian tamu sudah habis. Coba lagi besok.")
            return
        key = "wa:" + jid
        async with self._lock(key):
            history = self.histories.setdefault(key, [])
            history.append({"role": "user", "content": _stamped(text)})
            ctx = self._ctx_for(config.OWNER_ID, guest=guest)
            reply = await self._think(config.OWNER_ID, history, ctx)
        await asyncio.to_thread(self.wa.send, jid, reply)

    # --- penanganan pesan ---------------------------------------------------
    async def _handle_message(self, message):
        chat_id = message["chat"]["id"]
        user_id = message.get("from", {}).get("id")
        text = message.get("text") or ""

        if text.startswith("/id"):
            # Aman dijawab ke siapa pun: hanya memberi tahu id pengirim sendiri.
            await self.tg.send(chat_id, f"User id kamu: {user_id}")
            return

        guest = user_id != config.OWNER_ID
        if guest and user_id not in config.GUEST_IDS:
            # Bukan pemilik dan bukan tamu — abaikan diam-diam (keamanan).
            return

        # Lapisan kedua: kata sandi. Selama terkunci, apa pun selain kata sandi yang
        # benar dijawab dengan pengecoh. Pesannya dihapus supaya tebakan/kata sandi
        # tidak tertinggal di chat.
        if lock.active() and not lock.is_unlocked(chat_id):
            await self.tg.delete_message(chat_id, message.get("message_id"))
            if lock.try_unlock(chat_id, text):
                await self.tg.send(chat_id, lock.WELCOME)
            else:
                await self.tg.send(chat_id, lock.DECOY)
            return
        if lock.active() and text.strip().split("@")[0].lower() == "/lock":
            lock.lock(chat_id)
            await self.tg.send(chat_id, lock.LOCKED_NOTE)
            return

        first = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
        if guest and not quota.allow("tg:%s" % user_id):
            await self.tg.send(
                chat_id,
                "Kuota harian tamu sudah habis. Coba lagi besok, atau minta pemilik "
                "menambah ITSME_GUEST_DAILY_MAX.",
            )
            return
        if guest and first not in _GUEST_OK:
            await self.tg.send(
                chat_id,
                "Mode tamu: aku bisa menjawab pertanyaan, tapi perintah dan alat "
                "(berkas, shell, komputer lain) hanya untuk pemilik.",
            )
            return
        if first == "/perangkat":
            await self.tg.send(chat_id, self._devices_status())
            return
        if first == "/pasang":
            await self._invite(chat_id)
            return
        if first == "/komputer":
            await self.tg.send(chat_id, await asyncio.to_thread(self._computers_text))
            return
        if first == "/layar":
            await self._send_screen(chat_id, text.split(maxsplit=1)[1].strip() if " " in text.strip() else "")
            return
        if first == "/pakai":
            await self.tg.send(chat_id, self._use_device(text.split(maxsplit=1)[1] if " " in text.strip() else ""))
            return
        if first == "/deepseek" and len(text.split()) > 1:
            # /deepseek <key> = simpan key DeepSeek lalu langsung pakai. Ditangani
            # di Hub sebelum diteruskan ke perangkat atau otak AI, supaya key tidak
            # ikut terkirim ke mana-mana. (/deepseek tanpa key = ganti otak biasa.)
            await self._save_key(chat_id, message, force_kind="deepseek", then_use=True)
            return
        device = devices.get_target()
        if device and first not in _HUB_ONLY:
            if _attachment(message) or not text.strip():
                await self.tg.send(
                    chat_id,
                    f"Berkas, foto, dan suara belum bisa diteruskan ke {device}. Kirim teks saja, "
                    "atau /pakai untuk kembali ke komputer ini.",
                )
                return
            await self._forward(chat_id, device, text)
            return

        image_path = None
        attachment = _attachment(message)
        if attachment:
            file_id, name, is_image = attachment
            path = await self.tg.download_file(file_id, config.INBOX_DIR, name)
            if not path:
                await self.tg.send(
                    chat_id,
                    "Maaf, berkasnya gagal kuunduh (batas bot Telegram 20 MB, atau internet bermasalah).",
                )
                return
            caption = (message.get("caption") or "").strip()
            text = (
                f"{caption or 'Tolong lihat dan analisis berkas ini.'}\n\n"
                f"[Pemilik mengirim {'gambar' if is_image else 'berkas'}, tersimpan di: {path}]"
            )
            if is_image and brain.current_backend() == "claude":
                image_path = path  # dikirim langsung ke model sebagai gambar
            elif is_image and brain.current_backend() in ("ollama", "openrouter"):
                text += "\n(Otak ini tidak bisa melihat gambar; sarankan /claudepc atau /claude.)"
        elif not text.strip():
            if any(kind in message for kind in _NON_TEXT):
                await self.tg.send(
                    chat_id,
                    "Maaf, suara, video, stiker, dan sejenisnya belum bisa kubaca. "
                    "Foto dan berkas (PDF, dokumen, teks, kode) bisa — kirim saja, ya.",
                )
            return

        if text.startswith("/kunci"):
            # Ditangani di sini, sebelum riwayat obrolan & otak AI: key tidak
            # boleh sampai ke model mana pun.
            await self._save_key(chat_id, message)
            return
        # Kata perintahnya utuh: "/claudepc" bukan "/claude", "/akses_penuh" bukan "/akses".
        command = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
        if command == "/akses":
            await self.tg.send(chat_id, self._access_status())
            return
        if command == "/akses_penuh":
            if access.full():
                await self.tg.send(chat_id, "Akses penuh sudah aktif. /akses_aman untuk mematikannya.")
            elif await self._ask_confirm(chat_id, ACCESS_WARNING):
                access.set_full(True)
                await self.tg.send(
                    chat_id,
                    "🔓 Akses penuh aktif. Aku langsung bertindak tanpa tombol dan "
                    "mengabarimu setiap kali mengubah sesuatu. /akses_aman untuk mematikan.",
                )
            else:
                await self.tg.send(chat_id, "Oke, tetap di akses aman.")
            return
        if command == "/akses_aman":
            access.set_full(False)
            await self.tg.send(chat_id, "🔒 Kembali ke akses aman: aksi yang mengubah sesuatu minta tombol Ya lagi.")
            return
        if text.startswith("/start") or text.startswith("/help"):
            await self.tg.send(chat_id, HELP)
            return
        if text.startswith("/reset"):
            self.histories.pop(chat_id, None)
            await self.tg.send(chat_id, "Oke, konteks obrolan direset. Memori tetap.")
            return
        if text.startswith("/otak"):
            # Sisa kuota ditanyakan ke OpenRouter; jangan tahan bot selama menunggu.
            await self.tg.send(chat_id, await asyncio.to_thread(self._brain_status))
            return
        if command == "/wa":
            # Nomor WA pemilik diatur dari sini supaya .env tak perlu disunting manual.
            await self._set_wa_owner(chat_id, text.partition(" ")[2].strip())
            return
        if text.startswith("/alamat"):
            await self.tg.send(chat_id, self._tunnel_address())
            return
        if text.startswith("/memori"):
            isi = self.memory.text() or "(memori masih kosong)"
            if len(isi) > 3800:
                isi = "…" + isi[-3800:]
            await self.tg.send(chat_id, f"{isi}\n\n/hapus_memori — kosongkan memori")
            return
        if text.startswith("/hapus_memori"):
            if await self._ask_confirm(chat_id, "Kosongkan seluruh memori jangka panjang?"):
                self.memory.clear()
                await self.tg.send(chat_id, "Memori sudah dikosongkan.")
            return
        target = _BRAIN_COMMANDS.get(command)
        if target:
            try:
                brain.set_backend(target)
            except ValueError as exc:
                await self.tg.send(chat_id, f"Belum bisa ganti otak: {exc}")
                return
            # Format riwayat tiap otak berbeda, jadi obrolan dimulai baru.
            self.histories.pop(chat_id, None)
            await self.tg.send(
                chat_id,
                f"Otak sekarang: {brain.describe()}.\nKonteks obrolan direset, memori tetap.",
            )
            return

        # Alamat yang pemilik kirim sendiri boleh langsung dibuka.
        seen = self._seen(chat_id)
        for url in _URL.findall(text):
            seen.add(url.rstrip(".,;:!?)]}'\""))

        async with self._lock(chat_id):
            history = self.histories.setdefault(chat_id, [])
            history.append({"role": "user", "content": _with_image(_stamped(text), image_path)})
            ctx = self._ctx_for(chat_id, guest=guest)
            reply = await self._think(chat_id, history, ctx)
            await self.tg.send(chat_id, reply)

    # --- banyak perangkat (sisi Hub) ------------------------------------------
    @staticmethod
    def _devices_status():
        items = devices.listing()
        target = devices.get_target()
        if not items:
            return (
                "Belum ada komputer lain yang terhubung.\n\n"
                "Cara menambah: pasang It's Me di komputer itu, lalu isi .env-nya dengan "
                "ITSME_HUB_URL (alamat dashboard komputer ini, mis. lewat Tailscale) dan "
                "ITSME_HUB_SECRET (sama persis dengan di sini)."
            )
        lines = ["Komputer yang terhubung:"]
        for d in items:
            mark = " 🎯" if target and d["name"].lower() == target.lower() else ""
            lines.append(f"{'🟢' if d['online'] else '⚪'} {d['name']}{mark}")
        lines.append("")
        lines.append(f"Tujuan pesan sekarang: {target or 'komputer ini (Hub)'}")
        lines.append("/pakai <nama> — kerjakan di komputer itu · /pakai — kembali ke sini")
        return "\n".join(lines)

    # --- akses komputer lain (layar & berkas) ------------------------------
    def _computers_text(self):
        if not node.installed():
            return ("It's Me Node belum terpasang, jadi belum ada komputer lain yang terhubung. "
                    "Pasang dulu di komputer ini, lalu kirim /pasang untuk menambah komputer lain.")
        nodes = node.computers()
        if not nodes:
            return "Node belum menjawab (mungkin baru menyala). Coba /komputer lagi sebentar lagi."
        lines = ["Komputer yang terhubung:"]
        for n in nodes:
            tag = " 👑 pemimpin" if n.get("leader") else ""
            bot = "" if n.get("bot") else " (tanpa It's Me)"
            lines.append(f"• {n.get('name', '?')}{tag}{bot}")
        lines += [
            "",
            "/layar <nama> — kirim foto layar komputer itu ke sini (mis. /layar Laptop).",
            f"Layar langsung + kendali + telusur berkas: buka dashboard It's Me "
            f"(http://localhost:{config.DASHBOARD_PORT}) di komputer ini, lalu tab 🖥️ Komputer "
            "(dari HP lewat Tailscale/Cloudflare — lihat PANDUAN_AKSES_JARAK_JAUH).",
        ]
        return "\n".join(lines)

    async def _send_screen(self, chat_id, name):
        if not node.installed():
            await self.tg.send(chat_id, "It's Me Node belum terpasang, jadi belum ada komputer untuk dilihat.")
            return
        if name:
            target = await asyncio.to_thread(node.resolve, name)
            if not target:
                await self.tg.send(chat_id, f"Komputer “{name}” tidak ketemu. /komputer untuk daftar namanya.")
                return
            node_id, label = target["id"], target.get("name", name)
        else:
            node_id, label = None, "komputer ini"
        await self.tg.send_chat_action(chat_id, "upload_photo")
        png = await asyncio.to_thread(node.screenshot, node_id)
        if not png:
            await self.tg.send(
                chat_id,
                f"Layar {label} tidak bisa diambil (komputer mati, bukan Windows, atau tidak ada sesi layar).",
            )
            return
        if not await self.tg.send_photo(chat_id, png, "layar.png", f"🖥️ {label}"):
            await self.tg.send(chat_id, f"Foto layar {label} gagal dikirim.")

    async def _invite(self, chat_id):
        """/pasang: satu baris PowerShell berisi kode undangan sekali pakai. Komputer
        baru menukar kodenya di dashboard Hub (lewat domain/tunnel sendiri), mengambil
        kode gabung + isi .env bot, lalu menyambung KELUAR ke sini. Tanpa Tailscale."""
        if not node.installed():
            await self.tg.send(
                chat_id,
                "It's Me Node belum terpasang di komputer ini, jadi belum bisa mengundang "
                "komputer lain. Pasang dulu di komputer ini (PowerShell):\n\n"
                f"irm {node.INSTALL_URL} | iex\n\nSetelah itu kirim /pasang lagi.",
            )
            return
        # Domain tetap (ITSME_PUBLIC_URL) kalau ada; kalau tidak, URL Quick Tunnel
        # yang sedang hidup pun alamat publik yang sah untuk mengundang komputer lain.
        hub_url = node.hub_public_url()
        ephemeral = False
        if not hub_url:
            qt = getattr(self, "quick_tunnel", None)
            if qt and qt.url:
                hub_url = qt.url.rstrip("/")
                ephemeral = True
        if not hub_url:
            await self.tg.send(
                chat_id,
                "Hub ini belum punya alamat publik, jadi komputer lain belum bisa "
                "menyambung.\n\n"
                "Cara tercepat (tanpa domain): nyalakan link instan dengan "
                "quick-tunnel.ps1, lalu kirim /pasang lagi.\n"
                "Atau pakai domain sendiri: isi ITSME_PUBLIC_URL=https://domainmu di "
                ".env (lihat PANDUAN_AKSES_JARAK_JAUH.md) lalu nyalakan ulang It's Me.",
            )
            return
        code = node.new_invite_code()
        env = await asyncio.to_thread(node.invite_env, keys.ENV_FILE)
        if not await asyncio.to_thread(node.create_invite, code, env):
            await self.tg.send(
                chat_id,
                "Node di komputer ini belum menjawab (mungkin baru menyala atau sedang "
                "diperbarui). Coba /pasang lagi sebentar lagi.",
            )
            return
        note = (
            "\n\n⚠️ Alamat ini dari link instan (Quick Tunnel) yang BERUBAH tiap It's Me "
            "nyala ulang. Komputer baru tersambung sekarang, tapi kalau Hub nyala ulang, "
            "undang lagi (/pasang) — atau pakai domain tetap (ITSME_PUBLIC_URL) agar permanen."
            if ephemeral else ""
        )
        await self.tg.send(
            chat_id,
            "Pasang di komputer lain (TANPA Tailscale):\n"
            "1. Buka PowerShell di komputer itu.\n"
            "2. Tempel satu baris di pesan berikut, lalu Enter.\n"
            "3. Klik Ya saat Windows meminta izin Administrator.\n\n"
            "Komputer itu menyambung ke Hub lewat alamat publiknya. Token bot, pemilik, "
            "kunci otak, memori, dan kode jaringan disalin sendiri dari komputer ini. "
            "Tidak ada yang perlu diketik atau login.\n\n"
            f"Kode hanya bisa dipakai sekali dan hangus dalam {node.INVITE_TTL // 60} menit. "
            "Untuk komputer berikutnya, kirim /pasang lagi." + note,
        )
        await self.tg.send(chat_id, node.install_command(code, hub_url))

    @staticmethod
    def _use_device(name):
        name = (name or "").strip()
        if not name or name.lower() in ("hub", "sini", "ini", "utama"):
            devices.set_target(None)
            return "Oke, pesan berikutnya kukerjakan di komputer ini (Hub)."
        dev = devices.find(name)
        if dev is None:
            return f"Perangkat '{name}' tidak ditemukan.\n\n{App._devices_status()}"
        devices.set_target(dev["name"])
        note = "" if dev["online"] else "\n⚠️ Perangkat ini sedang offline; pesan akan menunggu sampai ia menyala."
        return (
            f"🎯 Oke, pesan berikutnya dikerjakan {dev['name']} — otak, memori, dan mode aksesnya "
            f"sama dengan komputer ini. /pakai untuk kembali.{note}"
        )

    async def _forward(self, chat_id, name, text):
        """Titipkan pesan pemilik ke perangkat lalu tunggu jawabannya
        (status "mengetik" tetap tampil selama perangkat bekerja)."""
        dev = devices.find(name)
        if dev is None:
            devices.set_target(None)
            await self.tg.send(chat_id, f"Perangkat {name} sudah tidak terdaftar; kembali ke komputer ini.")
            return
        if not dev["online"]:
            await self.tg.send(chat_id, f"⚪ {dev['name']} sedang offline. Pesanmu kutitipkan sampai ia menyala.")
        task_id, future = devices.enqueue(dev["name"], text)
        typing = asyncio.create_task(self._keep_typing(chat_id))
        try:
            reply = await asyncio.wait_for(asyncio.wrap_future(future), timeout=_DEVICE_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            devices.stop_waiting(task_id)
            reply = "⏱️ Belum ada jawaban setelah lama menunggu; kalau nanti selesai, jawabannya tetap kukirim."
        finally:
            typing.cancel()
        await self.tg.send(chat_id, f"🖥️ {dev['name']}:\n{reply}")

    # --- banyak perangkat (sisi perangkat) ------------------------------------
    async def _handle_remote(self, task):
        """Kerjakan pesan pemilik yang dititipkan Hub, lalu kirim jawabannya balik."""
        chat_id = config.OWNER_ID or 0
        text = (task.get("text") or "").strip()
        command = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
        try:
            if command == "/reset":
                self.histories.pop(chat_id, None)
                reply = "Oke, konteks obrolan di perangkat ini direset. Memori tetap."
            elif command == "/otak":
                reply = await asyncio.to_thread(self._brain_status)
            elif command in _BRAIN_COMMANDS:
                try:
                    brain.set_backend(_BRAIN_COMMANDS[command])
                    self.histories.pop(chat_id, None)
                    reply = f"Otak perangkat ini sekarang: {brain.describe()}."
                except ValueError as exc:
                    reply = f"Belum bisa ganti otak di perangkat ini: {exc}"
            else:
                seen = self._seen(chat_id)
                for url in _URL.findall(text):
                    seen.add(url.rstrip(".,;:!?)]}'\""))
                async with self._lock(chat_id):
                    history = self.histories.setdefault(chat_id, [])
                    history.append({"role": "user", "content": _stamped(text)})
                    reply = await self._think(chat_id, history, self._ctx_for(chat_id))
        except Exception as exc:  # noqa: BLE001 - pemilik harus selalu dapat jawaban
            log.exception("tugas dari Hub gagal")
            reply = f"⚠️ Error di perangkat: {type(exc).__name__}: {exc}"
        await asyncio.to_thread(devices.send_result, task.get("id", ""), reply)

    async def _sync_from_hub(self):
        """Samakan memori, otak, dan mode akses dengan Hub. Kembalikan versinya."""
        data = await asyncio.to_thread(devices.fetch_sync)
        if not data:
            return None
        if hasattr(self.memory, "replace_from_hub") and data.get("memory") is not None:
            self.memory.replace_from_hub(data["memory"])
        access.set_full(data.get("full_access"))
        wanted = data.get("brain")
        if wanted and wanted != brain.current_backend():
            try:
                brain.set_backend(wanted)
                self.histories.clear()  # format riwayat tiap otak berbeda
                log.info("Otak disamakan dengan Hub: %s", brain.describe())
            except ValueError as exc:
                log.warning("Otak Hub (%s) belum bisa dipakai di perangkat ini: %s", wanted, exc)
                await asyncio.to_thread(
                    devices.send_message,
                    f"⚠️ Otak Hub ({wanted}) belum bisa dipakai di sini, tetap {brain.describe()}: {exc}",
                )
        changed = self._apply_hub_env(data.get("env"), data.get("env_managed"))
        if changed:
            log.info("Env dari Hub berubah (%s); perangkat akan menyala ulang.", ", ".join(changed))
            self._env_changed = True
        return data.get("version")

    @staticmethod
    def _apply_hub_env(env, managed=None):
        """Tulis isi .env dari Hub ke .env perangkat ini. Kembalikan kunci yang berubah.

        Dipakai supaya pembaruan di Hub (token bot, pemilik, kata sandi, daftar tamu,
        kunci otak) tidak menuntut pemasangan ulang di komputer lain.

        `managed` = semua kunci yang dikelola Hub (termasuk yang dikosongkan). Tanpa
        itu, menghapus ITSME_PASSPHRASE di Hub tidak pernah sampai ke perangkat,
        karena nilai kosong tersaring di sisi Hub."""
        if not isinstance(env, dict):
            return []
        from dotenv import dotenv_values
        from . import keys as keys_mod

        current = {k: v for k, v in dotenv_values(keys_mod.ENV_FILE).items() if v}
        changed = []
        for k, v in env.items():
            v = str(v or "")
            if not v or "\n" in v or current.get(k) == v:
                continue
            try:
                keys_mod._set_env(keys_mod.ENV_FILE, str(k), v)
            except OSError:
                log.warning("gagal menulis %s ke .env", k)
                continue
            changed.append(str(k))
        # Dikosongkan di Hub -> kosongkan juga di sini (jangan pertahankan nilai lama).
        for k in managed or ():
            k = str(k)
            if k in env or k not in current:
                continue
            try:
                keys_mod._set_env(keys_mod.ENV_FILE, k, "")
            except OSError:
                log.warning("gagal mengosongkan %s di .env", k)
                continue
            changed.append(k)
        return changed

    def owner_ctx(self):
        """ctx alat untuk aksi dari dashboard: konfirmasi muncul sebagai tombol
        di Telegram pemilik (saat akses aman), dan akses penuh dihormati."""
        return self._ctx_for(config.OWNER_ID)

    async def ask(self, text):
        """Jalankan satu giliran untuk pemilik dari dashboard; kembalikan
        jawabannya. Berbagi riwayat & konteks dengan Telegram — konfirmasi aksi
        (saat akses aman) tetap muncul sebagai tombol di Telegram."""
        chat_id = config.OWNER_ID
        async with self._lock(chat_id):
            history = self.histories.setdefault(chat_id, [])
            history.append({"role": "user", "content": _stamped(text)})
            ctx = self._ctx_for(chat_id)
            try:
                return await asyncio.to_thread(run_agent, history, ctx)
            except Exception as exc:  # noqa: BLE001 - pemilik harus selalu dapat jawaban
                log.exception("otak gagal menjawab (dashboard)")
                return f"⚠️ Maaf, terjadi error: {type(exc).__name__}: {exc}"

    async def _handle_callback(self, cb):
        data = cb.get("data", "")
        user_id = cb.get("from", {}).get("id")
        if user_id != config.OWNER_ID:
            await self.tg.answer_callback(cb["id"])
            return
        decision = data.startswith("ok:")
        cid = data.split(":", 1)[1] if ":" in data else ""
        future = self.pending.get(cid)
        if future is None or future.done():
            # Tombol lama (mis. dari sebelum It's Me dinyalakan ulang).
            await self.tg.answer_callback(cb["id"], "Konfirmasi ini sudah tidak berlaku.")
            msg = cb.get("message") or {}
            if msg.get("message_id") and msg.get("chat"):
                await self.tg.edit_markup(msg["chat"]["id"], msg["message_id"], None)
            return
        future.set_result(decision)
        await self.tg.answer_callback(
            cb["id"], "Dijalankan ✅" if decision else "Dibatalkan ❌"
        )

    async def _dispatch(self, update):
        try:
            if "message" in update:
                await self._handle_message(update["message"])
            elif "callback_query" in update:
                await self._handle_callback(update["callback_query"])
        except Exception:  # noqa: BLE001
            log.exception("error saat menangani update Telegram")

    # --- pembaruan otomatis dari GitHub --------------------------------------
    async def _watch_updates(self):
        """Periksa GitHub berkala; kembalikan pembaruan pertama yang siap dipasang."""
        delay = _FIRST_UPDATE_CHECK
        while True:
            await asyncio.sleep(delay)
            delay = config.UPDATE_INTERVAL
            try:
                update = await asyncio.to_thread(updater.check, self._failed_updates)
            except updater.Blocked as exc:
                if str(exc) != self._update_warning:  # cukup sekali per masalah
                    self._update_warning = str(exc)
                    log.warning("Pembaruan dilewati: %s", exc)
                    await self.tg.send(
                        config.OWNER_ID,
                        f"⚠️ Ada pembaruan di GitHub, tapi belum bisa dipasang otomatis: {exc}",
                    )
                continue
            except Exception as exc:  # noqa: BLE001 - internet/GitHub; coba lagi nanti
                log.warning("Pemeriksaan pembaruan gagal: %s", exc)
                continue
            self._update_warning = None
            if update:
                return update

    async def _wait_idle(self, limit):
        """Tunggu sampai tidak ada pesan atau tugas latar yang sedang dikerjakan."""
        deadline = self.loop.time() + limit
        while self._tasks and self.loop.time() < deadline:
            await asyncio.sleep(1)

    async def _install(self, update):
        """Pasang pembaruan; True kalau It's Me perlu dinyalakan ulang."""
        await self.tg.ack_updates()
        await self.tg.send(
            config.OWNER_ID,
            f"🔄 Ada pembaruan dari GitHub: {update.describe()}\nMemasang lalu menyala ulang…",
        )
        try:
            await asyncio.to_thread(updater.apply, update)
        except Exception as exc:  # noqa: BLE001 - pemilik harus tahu, bot tetap jalan
            log.exception("pembaruan %s gagal dipasang", update.short)
            self._failed_updates.add(update.sha)
            await self.tg.send(
                config.OWNER_ID,
                f"⚠️ Pembaruan {update.short} gagal dipasang: {exc}\n"
                "It's Me tetap jalan dengan versi lama.",
            )
            return False
        updater.save_notice(f"✅ It's Me sudah diperbarui ke {update.describe()} dan menyala lagi.")
        log.info("Pembaruan %s terpasang, menyala ulang.", update.short)
        return True

    # --- loop utama ---------------------------------------------------------
    async def _poll(self):
        while True:
            if config.TG_WEBHOOK:
                # Pesan datang lewat webhook dashboard (/tg/<rahasia>), bukan getUpdates.
                # Ini juga mematikan poller lain yang memakai token yang sama.
                await asyncio.sleep(3600)
                continue
            updates = await self.tg.get_updates()
            for update in updates:
                self._spawn(self._dispatch(update))

    async def _watch_leader(self, follower):
        """Selesai kalau It's Me Node memilih komputer lain sebagai pemimpin."""
        while True:
            await asyncio.sleep(_NODE_CHECK_EVERY)
            if await asyncio.to_thread(follower.decide, node.ACTIVE) == node.STANDBY:
                return

    async def _serve_until_update(self, follower=None):
        """Layani Telegram sampai ada pembaruan dari GitHub (kembalikan pembaruan
        itu setelah pekerjaan yang sedang jalan selesai), atau sampai komputer
        lain menjadi pemimpin It's Me Node (kembalikan None)."""
        poll = asyncio.create_task(self._poll())
        watch = asyncio.create_task(self._watch_updates()) if config.AUTO_UPDATE else None
        lead = asyncio.create_task(self._watch_leader(follower)) if follower and config.FOLLOW_NODE else None
        tasks = {t for t in (poll, watch, lead) if t}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if poll.done():
                poll.result()  # error saat mengambil pesan diteruskan
            if lead is not None and lead.done():
                # Berhenti mengambil pesan sekarang juga (bot di pemimpin baru yang
                # mengambilnya), tandai yang sudah terambil supaya tidak dijawab
                # dua kali, lalu selesaikan pekerjaan yang sedang jalan.
                poll.cancel()
                await asyncio.gather(poll, return_exceptions=True)
                await self.tg.ack_updates()
                await self._wait_idle(_UPDATE_MAX_WAIT)
                return None
            update = watch.result()
            log.info("Pembaruan ditemukan: %s", update.describe())
            await self._wait_idle(_UPDATE_MAX_WAIT)
            # Tidak ada await antara pemeriksaan terakhir di atas dan
            # poll.cancel() di bawah, jadi tidak ada pesan baru yang terambil
            # lalu terputus di tengah jalan; pesan yang belum diambil dikirim
            # ulang Telegram setelah It's Me menyala lagi.
            return update
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_agent(self):
        """Mode perangkat: tanpa Telegram (biar tidak berebut token dengan Hub).
        Menunggu pesan pemilik dari Hub, menyamakan memori/otak/akses dengan
        Hub, melayani dashboard, dan tetap diperbarui dari GitHub."""
        self.loop = asyncio.get_running_loop()
        self.started = datetime.now()
        brain.restore()
        dashboard.start(self)
        log.info("%s (mode perangkat '%s') jalan. Hub: %s", config.NAME, config.DEVICE_NAME, config.HUB_URL)
        synced = await self._sync_from_hub()
        notice = updater.pop_notice()
        if notice:
            await asyncio.to_thread(devices.send_message, notice)
        if self._env_changed:
            # .env baru hanya terbaca saat proses mulai; mulai ulang sekarang
            # (tidak ada tugas yang sedang jalan di titik ini).
            return EXIT_UPDATED
        next_update = self.loop.time() + _FIRST_UPDATE_CHECK
        while True:
            reply = await asyncio.to_thread(devices.poll_hub)  # tunggu tugas + heartbeat
            if reply is None:
                await asyncio.sleep(min(config.HUB_HEARTBEAT, 15))  # Hub belum terjangkau
            else:
                if reply.get("task"):
                    self._spawn(self._handle_remote(reply["task"]))
                if reply.get("sync") and reply["sync"] != synced:
                    synced = await self._sync_from_hub() or synced
                    if self._env_changed:
                        await self._wait_idle(_UPDATE_MAX_WAIT)
                        log.info("Env Hub dipakai; perangkat menyala ulang.")
                        return EXIT_UPDATED
            if config.AUTO_UPDATE and self.loop.time() >= next_update:
                next_update = self.loop.time() + config.UPDATE_INTERVAL
                try:
                    update = await asyncio.to_thread(updater.check, self._failed_updates)
                except Exception:  # noqa: BLE001 - internet/GitHub; coba lagi nanti
                    update = None
                if update:
                    log.info("Pembaruan ditemukan: %s", update.describe())
                    await self._wait_idle(_UPDATE_MAX_WAIT)
                    try:
                        await asyncio.to_thread(updater.apply, update)
                        updater.save_notice(f"✅ Perangkat {config.DEVICE_NAME} diperbarui ke {update.describe()}.")
                        return EXIT_UPDATED
                    except Exception:  # noqa: BLE001
                        log.exception("pembaruan %s gagal dipasang", update.short)
                        self._failed_updates.add(update.sha)

    async def run_standby(self, follower):
        """It's Me Node memilih komputer lain sebagai pemimpin: jangan ambil pesan
        Telegram (bot di komputer pemimpin yang menjawab). Tunggu sampai komputer
        ini terpilih, lalu menyala ulang sebagai bot aktif. Memori, otak, dan
        mode akses disalin node dari pemimpin selama siaga."""
        log.info(
            "%s siaga: bot Telegram sedang aktif di %s (pemimpin It's Me Node).",
            config.NAME,
            follower.leader or "komputer lain",
        )
        next_update = self.loop.time() + _FIRST_UPDATE_CHECK
        while True:
            await asyncio.sleep(_NODE_CHECK_EVERY)
            if await asyncio.to_thread(follower.decide, node.STANDBY) == node.ACTIVE:
                log.info("Komputer ini sekarang pemimpin, It's Me aktif.")
                updater.save_notice(f"🔁 It's Me sekarang aktif di komputer {config.DEVICE_NAME}.")
                return EXIT_UPDATED
            if config.AUTO_UPDATE and self.loop.time() >= next_update:
                next_update = self.loop.time() + config.UPDATE_INTERVAL
                try:
                    update = await asyncio.to_thread(updater.check, self._failed_updates)
                except Exception:  # noqa: BLE001 - internet/GitHub; coba lagi nanti
                    update = None
                if update:
                    try:
                        await asyncio.to_thread(updater.apply, update)
                    except Exception:  # noqa: BLE001
                        log.exception("pembaruan %s gagal dipasang", update.short)
                        self._failed_updates.add(update.sha)
                    else:
                        log.info("Pembaruan %s terpasang, menyala ulang.", update.short)
                        return EXIT_UPDATED

    async def run(self):
        """Layani Telegram. Kembalikan EXIT_UPDATED setelah pembaruan terpasang,
        atau saat It's Me Node memindahkan bot ke komputer lain / ke sini."""
        if config.AGENT_MODE:
            return await self.run_agent()
        self.loop = asyncio.get_running_loop()
        self.started = datetime.now()
        follower = node.Follower()
        if follower.enabled and await asyncio.to_thread(follower.first_role) == node.STANDBY:
            try:
                return await self.run_standby(follower)
            finally:
                await self.tg.close()
        brain.restore()
        dashboard.start(self)
        self._start_quick_tunnel()
        if config.WHATSAPP:
            self.wa = WhatsApp(self._wa_inbox, self._wa_qr)
            if not self.wa.start():
                self.wa = None
        log.info("%s jalan. Otak: %s", config.NAME, brain.describe())
        try:
            notice = updater.pop_notice()
            if notice:
                await self.tg.send(config.OWNER_ID, notice)
            while True:
                update = await self._serve_until_update(follower)
                if update is None:
                    log.info(
                        "Pemimpin It's Me Node pindah ke %s; It's Me di sini siaga.",
                        follower.leader or "komputer lain",
                    )
                    return EXIT_UPDATED
                if await self._install(update):
                    return EXIT_UPDATED
        finally:
            if self.wa:
                self.wa.stop()
            if getattr(self, "quick_tunnel", None):
                self.quick_tunnel.stop()
            await self.tg.close()

    def _start_quick_tunnel(self):
        """Nyalakan Cloudflare Quick Tunnel (alamat publik instan) kalau diminta,
        lalu kirim alamatnya ke pemilik lewat Telegram tiap kali berubah."""
        self.quick_tunnel = None
        if not config.QUICK_TUNNEL or self.tg is None:
            return
        from . import tunnel

        def announce(url):
            previous = self._remember_public_url(url)
            if config.TG_WEBHOOK:
                secret = tg_webhook_secret()
                try:
                    asyncio.run_coroutine_threadsafe(self.tg.set_webhook(url, secret), self.loop)
                except Exception:  # noqa: BLE001 - jangan gagalkan tunnel
                    log.exception("gagal memasang webhook Telegram")
            msg = (f"🌐 Dashboard It's Me bisa dibuka dari HP / mana saja:\n{url}\n\n"
                   "Login pakai berkas data/dashboard.key. Alamat ini sementara (berubah "
                   "tiap It's Me nyala ulang) — ketik /alamat untuk yang terbaru.")
            if previous and previous != url:
                # Alamat berubah (link instan berganti setelah nyala ulang/pembaruan).
                # Komputer lain tidak perlu DIPASANG ULANG, cuma diarahkan ke alamat baru.
                msg += ("\n\nℹ️ Alamat berubah dari sebelumnya. Komputer yang sudah tersambung "
                        "tidak perlu dipasang ulang — cukup jalankan satu baris ini di komputer "
                        "itu (data dan pengaturan tetap):\n"
                        f"$env:ITSME_HUB='{url}'; irm {node.INSTALL_URL} | iex")
            try:
                asyncio.run_coroutine_threadsafe(self.tg.send(config.OWNER_ID, msg), self.loop)
            except Exception:  # noqa: BLE001 - tunnel tetap jalan walau kirim gagal
                log.exception("gagal mengirim alamat tunnel")

        self.quick_tunnel = tunnel.QuickTunnel(config.DASHBOARD_PORT, announce)
        self.quick_tunnel.start()
        log.info("Quick tunnel diminta; menunggu alamat dari cloudflared.")

    @staticmethod
    def _remember_public_url(url):
        """Simpan alamat publik terakhir; kembalikan yang lama (atau None).

        Dipakai untuk memberi tahu pemilik saat alamat berganti, supaya komputer lain
        cukup diarahkan ulang satu baris — bukan dipasang ulang dari nol.
        """
        path = os.path.join(config.DATA_DIR, "hub_url.txt")
        previous = None
        try:
            with open(path, encoding="utf-8") as f:
                previous = f.read().strip() or None
        except OSError:
            pass
        if url and url != previous:
            try:
                os.makedirs(config.DATA_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(url + "\n")
            except OSError:
                log.warning("gagal menyimpan alamat publik ke %s", path)
        return previous

    def _tunnel_address(self):
        if config.PUBLIC_URL:
            return f"Alamat dashboard: {config.PUBLIC_URL}"
        qt = getattr(self, "quick_tunnel", None)
        if not config.QUICK_TUNNEL:
            return ("Alamat publik belum dinyalakan. Jalankan quick-tunnel.ps1 untuk link instan, "
                    "atau tunnel-setup.ps1 untuk pakai domain sendiri.")
        if qt and qt.url:
            return f"🌐 Alamat dashboard sekarang:\n{qt.url}\n\n(sementara; berubah tiap nyala ulang)"
        return "Tunnel sedang disiapkan... coba /alamat lagi beberapa detik lagi."


def tg_webhook_secret():
    """Rahasia webhook: turunan token bot, 32 aksara, dipakai di URL & header Telegram."""
    return hashlib.sha256(("itsme-tg-webhook:" + (config.TELEGRAM_TOKEN or "")).encode()).hexdigest()[:32]


def _disable_quick_edit():
    """Matikan QuickEdit di jendela cmd Windows tempat It's Me berjalan.

    Dengan QuickEdit menyala, satu klik di jendela itu memulai "seleksi teks",
    dan Windows menahan semua tulisan ke jendela sampai Enter/Esc ditekan —
    bot ikut berhenti dan tidak menjawab. Teks di jendela tetap bisa disalin
    lewat menu jendela (klik kanan judul → Edit → Mark).
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # tidak berjalan di jendela konsol
        enable_quick_edit, enable_extended_flags = 0x0040, 0x0080
        kernel32.SetConsoleMode(handle, (mode.value & ~enable_quick_edit) | enable_extended_flags)
    except Exception:  # noqa: BLE001 - tanpa ini bot tetap jalan
        pass


def _setup_logging():
    """Log ke jendela dan ke data/itsme.log lewat thread terpisah.

    Menulis ke jendela konsol bisa tertahan (mis. saat ada teks yang sedang
    dipilih). Karena semua tulisan log diserahkan ke antrean, yang tertahan
    hanya thread pencatat — bot tetap menerima dan menjawab pesan.
    """
    log.setLevel(logging.INFO)
    if log.handlers:
        return None
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[itsme] %(message)s"))
    handlers = [console]
    try:
        os.makedirs(os.path.dirname(config.LOG_FILE) or ".", exist_ok=True)
        handler = RotatingFileHandler(
            config.LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handlers.append(handler)
    except OSError as exc:
        print(f"[itsme] Log tidak bisa ditulis ke {config.LOG_FILE}: {exc}")
    pending = queue.SimpleQueue()
    log.addHandler(QueueHandler(pending))
    listener = QueueListener(pending, *handlers, respect_handler_level=True)
    listener.start()
    atexit.register(listener.stop)
    return listener


def _single_instance_lock():
    """Kunci berkas; None kalau It's Me lain sudah memegangnya."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    handle = open(os.path.join(config.DATA_DIR, "itsme.lock"), "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle  # tetap terbuka (dan terkunci) selama proses hidup


def _stop_listener(listener):
    """Hentikan listener log sekali saja — stop() dua kali meledak di Python 3.12 (gh-114706)."""
    if listener:
        atexit.unregister(listener.stop)
        listener.stop()


def _restart(lock, listener):
    """Nyalakan ulang It's Me dengan kode yang baru dipasang.

    Di macOS/Linux proses ini langsung diganti (PID tetap sama, jadi systemd,
    launchd, dan itsme.pid tetap cocok). Di Windows exec tidak mengganti
    proses: It's Me keluar dengan EXIT_UPDATED dan start-itsme.cmd langsung
    menyalakannya lagi.
    """
    if os.name == "nt":
        print("[itsme] Menyala ulang...")
        return
    _stop_listener(listener)  # atexit tidak dijalankan oleh exec
    lock.close()
    os.chdir(updater.APP_DIR)
    os.execv(sys.executable, [sys.executable, "-m", "itsme.main"])


def main():
    _disable_quick_edit()
    listener = _setup_logging()
    try:
        written = launch.ensure(updater.APP_DIR)
        if written:
            log.info("Skrip peluncur disegarkan: %s", ", ".join(written))
    except Exception:  # noqa: BLE001 - peluncur rusak tidak boleh mematikan bot
        log.exception("gagal menyegarkan skrip peluncur")
    problems = config.check()
    if problems:
        print("Konfigurasi belum lengkap:")
        for p in problems:
            print("  - " + p)
        print("\nSalin .env.example menjadi .env lalu isi nilainya.")
        return 2
    lock = _single_instance_lock()
    if lock is None:
        print(
            "It's Me sudah jalan di jendela atau proses lain. Tutup yang lama dulu "
            "kalau mau menyalakan ulang."
        )
        return EXIT_ALREADY_RUNNING
    try:
        code = asyncio.run(App().run())
    except KeyboardInterrupt:
        print("\n[itsme] berhenti.")
        return 0
    if code == EXIT_UPDATED:
        _restart(lock, listener)
    return code


if __name__ == "__main__":
    sys.exit(main())
