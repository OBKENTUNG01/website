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
import json
import logging
import os
import queue
import re
import sys
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from urllib.parse import urlparse

from . import access, brain, brain_guest, config, dashboard, devices, keypool, keys, launch, lock, node, quota, updater
from .brain import run_agent
from .memory import Memory
from .telegram import Telegram, confirm_keyboard
from .whatsapp import WhatsApp
from .whatsapp import prerequisites as wa_prerequisites

log = logging.getLogger("itsme")

HELP = (
    "Halo, It's Me siap. 🫡\n\n"
    "Kirim saja apa yang kamu mau — tanya, minta cari info terbaru, edit & "
    "deploy website, atau beberapa tugas sekaligus.\n\n"
    "Perintah:\n"
    "/help — bantuan ini\n"
    "/otak — lihat otak yang sedang dipakai\n"
    "/alamat — alamat dashboard untuk dibuka dari HP/internet (lewat tunnel)\n"
    "/wa <nomor> — atur nomor WhatsApp pemilik, nyalakan bot WA, dan laporkan statusnya (QR hanya dikirim kalau belum tertaut)\n"
    "/wa_ulang — tautkan ulang WhatsApp: minta QR baru (sesi lama disimpan, bukan dihapus)\n"
    "/cloud — pakai otak cloud (OpenRouter, lebih pintar)\n"
    "/deepseek — pakai otak DeepSeek (murah & pintar); /deepseek <key> untuk simpan key-nya\n"
    "/claudepc — pakai Claude di komputer (akun Claude-mu, tanpa API key)\n"
    "/claude — pakai otak Claude (Anthropic API, paling pintar)\n"
    "/lokal — pakai otak lokal (gratis, privat)\n"
    "/kunci <key> — simpan API key atau token akun Claude (pesannya langsung kuhapus). Key NusaGate boleh "
    "banyak sekaligus (satu per baris): SEMUANYA dipakai bergiliran. Key hanya dipasang lewat Telegram, "
    "tidak pernah lewat WhatsApp\n"
    "/kunci_daftar — lihat semua key NusaGate beserta statusnya (siap / dibatasi / saldo habis / ditolak)\n"
    "/kunci_hapus <no> — keluarkan key dari daftar (nomor dari /kunci_daftar)\n"
    "/kunci_tes — periksa semua key ke penyedianya; /kunci_tes chat = coba menjawab sungguhan (biaya sangat kecil)\n"
    "/model_daftar — model yang ditawarkan NusaGate; /model_pakai <nama> <nama> … — putar beberapa model bergantian\n"
    "/wa_tamu — atur apakah orang yang chat ke nomor WhatsApp-mu dijawab AI (aktif/mati); "
    "/wa_blokir <nomor>, /wa_buka <nomor>, /wa_info <teks>\n"
    "/akses — lihat mode akses (aman/penuh)\n"
    "/akses_penuh — langsung bertindak tanpa tombol (berkas rahasia tetap dijaga)\n"
    "/akses_aman — kembali minta tombol untuk aksi yang mengubah sesuatu\n"
    "/memori — lihat isi memori jangka panjang\n"
    "/hapus_memori — kosongkan memori\n"
    "/reset — mulai percakapan baru (lupakan konteks obrolan, memori tetap)\n"
    "/perangkat — daftar komputer lain yang terhubung\n"
    "/pakai <nama> — pesan berikutnya dikerjakan komputer itu (/pakai saja = kembali ke sini)\n"
    "/pasang — satu baris perintah untuk memasang It's Me di komputer lain (sekali pakai)\n"
    "/pasang_tetap — sama, tapi SATU tautan dipakai ulang untuk beberapa komputer\n"
    "/komputer — buka layar & berkas komputer yang terhubung (seperti remote desktop)\n"
    "/layar <nama> — kirim foto layar komputer itu ke chat\n"
    "/nyalakan <nama> — bangunkan komputer itu dari jarak jauh (Wake-on-LAN)\n"
    "/diagnosa — kesehatan komputer yang membalas ini (Telegram, jaringan, WhatsApp, log)\n"
    "/lock — kunci bot; kata sandi diminta lagi sebelum bisa dipakai\n"
    "/id — tampilkan user id Telegram-mu"
)
# Perintah yang tetap dikerjakan Hub walau ada perangkat tujuan (/pakai).
# Memori & mode akses Hub disalin ke semua perangkat, jadi diatur di sini.
# Perintah yang tetap boleh dipakai tamu (sisanya: pemilik saja).
_GUEST_OK = {"", "/start", "/help", "/id", "/lock"}
_HUB_ONLY = {
    "/start", "/help", "/kunci", "/akses", "/akses_penuh", "/akses_aman",
    "/memori", "/hapus_memori", "/perangkat", "/pakai", "/pasang", "/pasang_tetap", "/id",
    "/lock", "/komputer", "/layar", "/nyalakan", "/wa", "/wa_ulang", "/alamat", "/diagnosa",
    "/kunci_daftar", "/kunci_hapus", "/kunci_tes", "/model_daftar", "/model_pakai",
    "/wa_tamu", "/wa_blokir", "/wa_buka", "/wa_info",
}
# Perintah pengaturan yang hanya jalan lewat Telegram (key dan pengaturan tamu tidak boleh diurus dari
# WhatsApp). Kalau pemilik mengetiknya di WhatsApp, dia diberi tahu ke mana harus pergi.
_WA_TELEGRAM_ONLY = {
    "/kunci", "/deepseek", "/kunci_daftar", "/kunci_hapus", "/kunci_tes", "/model_daftar", "/model_pakai",
    "/wa_tamu", "/wa_blokir", "/wa_buka", "/wa_info",
}
_WA_TELEGRAM_ONLY_NOTE = ("Perintah itu hanya bisa lewat Telegram (key dan pengaturan sengaja tidak diurus dari "
                          "WhatsApp, supaya tidak bocor). Kirim di bot Telegram-mu.")
_WA_SECRET_OWNER = (
    "🔒 Pesan itu berisi API key / kunci rahasia, jadi TIDAK kuproses lewat WhatsApp (dari sini key bisa bocor).\n\n"
    "Pasang key hanya lewat Telegram: kirim ke bot Telegram-mu\n/kunci <key>\n\n"
    "Karena key itu sudah tertulis di WhatsApp, hapus pesannya dari chat ini (hapus untuk semua orang), "
    "dan sebaiknya buat ulang key-nya di penyedianya, lalu pasang yang baru lewat Telegram."
)
_WA_SECRET_GUEST = ("⚠️ Jangan mengirim kunci rahasia, kata sandi, atau API key lewat WhatsApp. "
                    "Pesan itu tidak kubaca; hapus saja dari chat ini.")
_WA_GUEST_BUSY = "Maaf, asisten AI sedang tidak bisa menjawab. Coba lagi nanti ya."
_WA_GUEST_TEXT_ONLY = "Maaf, aku hanya bisa membaca pesan teks. Tulis pertanyaanmu dengan kata-kata ya."
_WA_GUEST_QUOTA = "Kuota harian untuk asisten AI sudah habis. Coba lagi besok ya."
# Paling banyak sekian jawaban tamu diproses bersamaan (sisanya antre), supaya satu keramaian tidak
# menghabiskan thread dan membuat pemilik menunggu. Dan paling banyak sekian riwayat tamu disimpan.
_WA_GUEST_PARALLEL = 4
_WA_GUEST_HISTORIES = 200
_WA_GUEST_TIMEOUT = 60  # detik paling lama tamu menunggu; lewat itu dia menerima permintaan maaf singkat
_POOL_ALERT_WAIT = 3  # detik mengumpulkan kabar "key tidak bisa dipakai" jadi satu pesan
_WA_OPEN_WARNING = (
    "Aktifkan balasan AI untuk SEMUA orang yang chat ke nomor WhatsApp-mu?\n\n"
    "Yang terjadi: setiap pesan teks dari kontak (satu lawan satu) dijawab AI atas nama nomor ini, dan "
    "jawabannya tampil di chat mereka seperti pesanmu sendiri - diawali tulisan bahwa itu asisten AI. "
    "Tamu hanya bisa tanya-jawab: tanpa alat, tanpa memorimu, tanpa berkas atau data pribadimu. Grup, "
    "siaran, dan status tidak dibalas.\n\n"
    "Kamu bisa memblokir nomor tertentu (/wa_blokir) dan mematikannya kapan saja (/wa_tamu mati).\n"
    "Biaya: tiap jawaban memakai key NusaGate-mu (batas harian per kontak berlaku)."
)


_MODEL_ALL_WARNING = (
    "Putar SEMUA {n} model yang ditawarkan NusaGate secara bergantian?\n\n"
    "Tiap jawaban memakai model berikutnya, jadi tidak ada model yang menganggur. Tapi harga dan kemampuan "
    "tiap model BERBEDA: model yang mahal ikut terpakai (saldo lebih cepat habis), dan model yang tidak "
    "mendukung alat akan menjawab tanpa menjalankan alat. Model yang ditolak penyedia otomatis dilewati.\n\n"
    "Mau lebih terkontrol? Pilih sebagian saja: /model_pakai <nama> <nama> …"
)


class _NoMemory:
    """Memori kosong untuk tamu: memori pemilik tidak pernah sampai ke orang lain."""

    def text(self):
        return ""

    def add(self, *args, **kwargs):
        return None

    def clear(self):
        return None
# Paling banyak sekian QR WhatsApp dikirim per permintaan (/wa, /wa_ulang); WhatsApp terus
# membuat QR baru tiap ~20 detik selama belum dipindai, dan chat tak boleh dibanjiri.
_WA_QR_MAX = 3
_WA_QR_GAP = 45  # detik minimal antar-QR
# Perintah ini juga dilayani lewat WhatsApp (lihat _run_wa), tapi tetap khusus
# pemilik — bukan tamu — sama seperti di Telegram.
_WA_OWNER_COMMANDS = {"/kirim", "/komputer", "/nyalakan", "/reset", "/otak", "/memori", "/hapus_memori",
                      "/diagnosa"}
# Paling lama Hub menunggu jawaban dari perangkat (detik).
_DEVICE_REPLY_TIMEOUT = 3600

KUNCI_HELP = (
    "Kirim API key dengan: /kunci <key>\n\n"
    "Key NusaGate (diawali nsk_) boleh BANYAK sekaligus — satu per baris, atau dipisah spasi: semuanya "
    "diperiksa lalu dipakai bergiliran supaya beban terbagi rata. Key baru DITAMBAHKAN ke yang sudah ada, "
    "tidak menggantikannya (lihat /kunci_daftar).\n\n"
    "Key Claude diawali sk-ant-, token akun Claude (dari claude setup-token) "
    "diawali sk-ant-oat, key OpenRouter diawali sk-or-. Pesanmu langsung kuhapus, key diperiksa "
    "dulu ke penyedianya, lalu disimpan ke .env dan langsung aktif. Key tidak pernah dibaca otak AI.\n\n"
    "Key DeepSeek resmi diawali sk- (tak bisa dibedakan otomatis) — untuk yang "
    "itu kirim: /deepseek <key>.\n\n"
    "Pasang key hanya lewat Telegram, jangan lewat WhatsApp."
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


def _wa_mb(n):
    n = int(n or 0)
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def _wa_start_problem(why):
    """Pesan persis sesuai alasan WhatsApp.start() gagal ("node"/"npm", lihat
    whatsapp.py) - supaya pemiliknya tahu PASTI mana yang kurang, bukan disuruh
    mengecek dua kemungkinan sekaligus tanpa tahu mana yang sebenarnya salah."""
    whatsapp_dir = os.path.join(os.path.dirname(__file__), "whatsapp")
    # Nama komputer selalu disebut: bisa ada beberapa komputer yang menjalankan It's Me
    # dengan token bot yang sama, dan pemilik harus tahu KOMPUTER MANA yang perlu dibenahi.
    host = f"Komputer yang membalas ini: {config.DEVICE_NAME}\n"
    if why == "node":
        return host + ("Sebabnya: Node.js belum terpasang / tidak ketemu di PATH. Pasang dari "
                       "https://nodejs.org (pilih versi LTS), lalu nyalakan ulang It's Me dan kirim /wa lagi.")
    if why == "npm":
        return host + ("Sebabnya: Node.js sudah ada, tapi paket WhatsApp-nya belum dipasang. "
                       "Jalankan sekali di PowerShell:\n"
                       f'cd "{whatsapp_dir}"\n'
                       "npm install --allow-git=all\n\n"
                       "Lalu kirim /wa lagi.")
    return host + "Sebabnya tidak dikenal - lihat logs\\itsme.log di komputer itu."


def _wa_notice_due(why):
    """Sekali sehari per alasan: pemberitahuan 'WhatsApp gagal menyala' saat It's Me
    baru menyala tidak boleh jadi spam tiap pembaruan otomatis."""
    path = os.path.join(config.DATA_DIR, "wa_warned.json")
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(path, encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    if last.get("why") == why and last.get("day") == today:
        return False
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"why": why, "day": today}, f)
    except OSError:
        pass
    return True


# Berkas teks kecil ikut dibacakan ke otak supaya bisa langsung dibahas.
_WA_TEKS = (".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".html", ".xml",
            ".ini", ".yml", ".yaml", ".sh", ".ps1", ".kt", ".java", ".sql")


def _wa_file_note(file):
    """Keterangan berkas WhatsApp untuk otak (isi disertakan kalau teks & kecil)."""
    nama = file.get("name") or "berkas"
    jalur = file.get("path") or ""
    nota = (f"[Pengirim melampirkan {file.get('kind') or 'berkas'}: {nama} "
            f"({_wa_mb(file.get('size'))}), tersimpan di {jalur}]")
    try:
        if jalur and os.path.getsize(jalur) <= 40000 and nama.lower().endswith(_WA_TEKS):
            with open(jalur, encoding="utf-8", errors="replace") as f:
                return nota + "\nIsi berkas:\n" + f.read(40000)
    except OSError:
        pass
    return nota


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
        self._qr_at = None  # kapan QR WhatsApp terakhir dikirim (None = belum)
        self._qr_sent = 0  # berapa QR yang sudah dikirim sejak /wa atau /wa_ulang terakhir
        self._wa_relinking = False  # /wa_ulang sedang jalan: "keluar" bukan kejutan bagi pemilik
        self._wa_last_in = None  # kapan pesan WhatsApp terakhir masuk (bukti jalur pesan hidup)
        self.wa = None  # jembatan WhatsApp (itsme/whatsapp.py)
        # Tamu WhatsApp (mode terbuka): penjaga banjir per kontak, kabar yang cukup sekali sehari, urutan
        # riwayat tamu terbaru, dan batas jumlah jawaban tamu yang berjalan bersamaan.
        self._wa_pace = {}
        self._wa_noted = {}
        self._wa_guest_seen = OrderedDict()
        self._wa_guest_gate = asyncio.Semaphore(_WA_GUEST_PARALLEL)
        # Kabar "key NusaGate tidak bisa dipakai" dikumpulkan beberapa detik jadi satu pesan.
        self._pool_alerts = {}
        self._pool_alert_timer = None
        # Webhook Telegram sedang terpasang & sehat (diurus _keep_webhook). Selama False,
        # _poll memakai getUpdates supaya bot tidak tuli.
        self._webhook_ok = False
        self._webhook_task = None

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
        if keypool.has_keys():
            lines.append(f"/deepseek — {brain.describe('deepseek')}")
            rows = keypool.snapshot()
            siap = sum(1 for r in rows if r["status"] in ("siap", "sibuk"))
            lines.append(f"   Kumpulan key: {len(rows)} key, {siap} siap sekarang (/kunci_daftar)")
        else:
            lines.append("/deepseek — belum aktif (kirim /kunci <key NusaGate>, boleh banyak sekaligus)")
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
    async def _verify_many(self, picked):
        """Periksa banyak key bersamaan (paling banyak 6 sekaligus). `picked` = [(jenis, key)].
        Kembalikan [(status, keterangan)] searah dengan `picked`; status: ok | rejected | error."""
        gate = asyncio.Semaphore(6)

        async def one(kind, key):
            async with gate:
                try:
                    await asyncio.to_thread(keys.verify, kind, key)
                    return "ok", ""
                except keys.KeyRejected as exc:
                    return "rejected", str(exc)
                except Exception as exc:  # noqa: BLE001 - mis. internet putus
                    # Hanya nama error-nya: pesan error lengkap tidak boleh membawa key ke log.
                    log.warning("API key %s belum bisa diperiksa: %s", keys.label(kind), type(exc).__name__)
                    return "error", type(exc).__name__

        return await asyncio.gather(*(one(kind, key) for kind, key in picked))

    async def _save_key(self, chat_id, message, force_kind=None, then_use=False):
        """Simpan API key dari chat tanpa membiarkannya terbaca: pesannya
        langsung dihapus, key diperiksa, disimpan ke .env, dan hanya
        ditampilkan sebagian. Tidak pernah masuk riwayat atau log.

        Boleh BANYAK key sekaligus (satu per baris, atau dipisah spasi/koma): semuanya diperiksa
        bersamaan. Key NusaGate/DeepSeek DITAMBAHKAN ke kumpulan yang dipakai bergiliran (keypool.py);
        jenis lain menggantikan key lama.

        force_kind memaksa jenis key (mis. "deepseek" untuk key resmi sk- yang
        tak bisa dibedakan otomatis). then_use langsung memakai otaknya."""
        tokens = keys.parse_tokens(message.get("text") or "")
        if not tokens:
            await self.tg.send(
                chat_id,
                "Kirim: /deepseek <key>  (key diawali nsk- atau sk-). Boleh banyak, satu per baris."
                if force_kind else KUNCI_HELP,
            )
            return
        deleted = await self.tg.delete_message(chat_id, message.get("message_id"))
        note = "" if deleted else "\n\n⚠️ Pesanmu yang berisi key belum bisa kuhapus — hapus sendiri, ya."
        picked, unknown = [], 0
        for token in tokens:
            kind = (force_kind if keys.looks_like_key(token) else None) if force_kind else keys.detect(token)
            if kind:
                picked.append((kind, token))
            else:
                unknown += 1
        if not picked:
            await self.tg.send(
                chat_id,
                ("Itu tidak seperti API key (hanya huruf, angka, - dan _)." if force_kind else
                 "Key tidak dikenali. Key Claude diawali sk-ant-, key OpenRouter diawali "
                 "sk-or-, key DeepSeek diawali nsk-. Contoh: /kunci sk-ant-…") + note,
            )
            return
        results = await self._verify_many(picked)

        if len(tokens) == 1:  # satu key: jawaban ringkas
            kind, key = picked[0]
            status, detail = results[0]
            name = keys.label(kind)
            if status == "rejected":
                await self.tg.send(chat_id, f"❌ {detail} Key tidak disimpan." + note)
                return
            if status == "error":
                await self.tg.send(
                    chat_id,
                    f"⚠️ Key {name} belum bisa diperiksa ({detail}). Cek "
                    "internet komputer lalu kirim lagi. Key tidak disimpan." + note,
                )
                return
            added = True
            try:
                if kind == "deepseek":  # ditambahkan ke kumpulan, bukan menggantikan
                    added, _ = await asyncio.to_thread(keys.pool_add_many, [key])
                else:
                    await asyncio.to_thread(keys.save, kind, key)
            except OSError as exc:
                await self.tg.send(chat_id, f"⚠️ Key {name} gagal disimpan ke .env: {exc.strerror or exc}" + note)
                return
            usage = await self._key_usage(chat_id, kind, then_use)
            if kind == "deepseek":
                total = len(keypool.all_keys())
                head = (f"✅ Key {name} ({keys.mask(key)}) sudah diperiksa dan ditambahkan; sekarang {total} key "
                        "dipakai bergiliran (hanya tersimpan di komputer ini)." if added else
                        f"↩️ Key {name} ({keys.mask(key)}) sudah ada di daftar (tidak dobel); total {total} key.")
            else:
                head = f"✅ Key {name} ({keys.mask(key)}) sudah diperiksa, disimpan, dan langsung aktif."
            await self.tg.send(chat_id, f"{head} {usage}" + note)
            return

        accepted, rejected, unchecked = [], [], []
        for index, (kind, key) in enumerate(picked):
            status = results[index][0]
            if status == "ok":
                accepted.append((kind, key))
            elif status == "rejected":
                rejected.append(key)
            else:
                unchecked.append(key)
        added, existing, ignored, singles = [], [], [], {}
        try:
            pool_keys = [key for kind, key in accepted if kind == "deepseek"]
            if pool_keys:
                added, existing = await asyncio.to_thread(keys.pool_add_many, pool_keys)
            for kind, key in accepted:
                if kind == "deepseek":
                    continue
                if kind in singles:
                    ignored.append(key)  # jenis selain NusaGate/DeepSeek hanya punya satu tempat
                else:
                    singles[kind] = key
            for kind, key in singles.items():
                await asyncio.to_thread(keys.save, kind, key)
        except OSError as exc:
            await self.tg.send(chat_id, f"⚠️ Key gagal disimpan ke .env: {exc.strerror or exc}" + note)
            return

        lines = []
        if added:
            lines.append(f"✅ {len(added)} key NusaGate/DeepSeek baru ditambahkan: "
                         f"{', '.join(keys.mask(k) for k in added)}")
        if existing:
            lines.append(f"↩️ {len(existing)} key sudah ada sebelumnya (tidak dobel): "
                         f"{', '.join(keys.mask(k) for k in existing)}")
        for kind, key in singles.items():
            lines.append(f"✅ Key {keys.label(kind)} ({keys.mask(key)}) disimpan dan langsung aktif.")
        if ignored:
            lines.append(f"ℹ️ {len(ignored)} key lain dari jenis yang hanya punya satu tempat tidak dipakai "
                         f"(diabaikan): {', '.join(keys.mask(k) for k in ignored)}")
        if rejected:
            lines.append(f"❌ {len(rejected)} key DITOLAK penyedia dan tidak disimpan: "
                         f"{', '.join(keys.mask(k) for k in rejected)}")
        if unchecked:
            lines.append(f"⚠️ {len(unchecked)} key belum bisa diperiksa (internet?) dan tidak disimpan: "
                         f"{', '.join(keys.mask(k) for k in unchecked)} — kirim lagi nanti.")
        if unknown:
            lines.append(f"❓ {unknown} potongan teks tidak dikenali sebagai key (diabaikan).")
        pool_used = any(kind == "deepseek" for kind, _ in accepted)
        if pool_used:
            lines.append(f"\n🔑 Sekarang ada {len(keypool.all_keys())} key NusaGate/DeepSeek; semuanya dipakai "
                         "bergiliran, dan hanya tersimpan di komputer ini (tidak disalin ke komputer lain). "
                         "Daftar & status: /kunci_daftar")
        for kind in list(singles) + (["deepseek"] if pool_used else []):
            lines.append(await self._key_usage(chat_id, kind, then_use and kind == force_kind))
        await self.tg.send(chat_id, "\n".join(lines) + note)

    async def _key_usage(self, chat_id, kind, then_use):
        """Kalimat 'apa selanjutnya' setelah key jenis `kind` tersimpan (dan, kalau diminta, pakai otaknya)."""
        name = keys.label(kind)
        if then_use:
            try:
                await asyncio.to_thread(brain.set_backend, kind)
                self.histories.pop(chat_id, None)  # format riwayat tiap otak berbeda
                return f"Otak {name} sekarang aktif. Konteks obrolan direset, memori tetap."
            except ValueError as exc:
                return f"Tersimpan, tapi belum bisa dipakai sekarang: {exc}"
        if kind == "github":
            return "Sekarang kamu bisa generate APK aplikasi HP dari dashboard (tab HP)."
        if brain.current_backend() == kind:
            return f"Otak {name} sekarang memakai key ini."
        if kind == "deepseek":
            return (f"Kirim {_KEY_COMMANDS[kind]} supaya jadi otak utama. Tamu WhatsApp (kalau /wa_tamu aktif) "
                    "dan otak DeepSeek otomatis memakai semua key ini.")
        return f"Kirim {_KEY_COMMANDS[kind]} untuk memakainya."

    # --- kumpulan key NusaGate (Telegram saja) -----------------------------------------------
    @staticmethod
    def _pool_status(row, now):
        """Satu baris status key untuk pemilik (bahasa manusia)."""
        def left(seconds):
            seconds = max(1, int(seconds))
            return f"±{seconds} dtk" if seconds < 90 else f"±{round(seconds / 60)} mnt"

        until = (row["until"] - now) if row["until"] else 0
        status = row["status"]
        if status == "siap":
            return f"siap · dipakai {row['uses']}x" + (" · sedang bekerja" if row["inflight"] else "")
        if status == "sibuk":
            return f"sempat error, diturunkan sebentar · dipakai {row['uses']}x"
        if status == "penuh":
            return f"penuh menit ini (batas lunak {config.POOL_RPM}/menit), lega lagi {left(until)}"
        if status == "limit":
            return f"dibatasi penyedia (60 permintaan/menit per key), pulih {left(until)}"
        if status == "cap":
            return f"batas paket tercapai, dicoba lagi {left(until)}"
        if status == "balance":
            return f"saldo habis, dicoba lagi {left(until)} — isi ulang di nusagate.com"
        return f"ditolak/dicabut penyedia — periksa: /kunci_tes · keluarkan: /kunci_hapus {row['no']}"

    def _pool_text(self):
        rows = keypool.snapshot()
        if not rows:
            return ("Belum ada key NusaGate/DeepSeek.\n\nPasang lewat Telegram ini (jangan lewat WhatsApp): "
                    "/kunci <key>. Boleh banyak key sekaligus, satu per baris; semuanya dipakai bergiliran.")
        ready = sum(1 for r in rows if r["status"] in ("siap", "sibuk"))
        now = time.time()
        lines = [f"🔑 {len(rows)} key NusaGate/DeepSeek dipakai bergiliran — {ready} siap sekarang.", ""]
        lines += [f"{r['no']}. {r['mask']} — {self._pool_status(r, now)}" for r in rows]
        total = sum(r["uses"] for r in rows)
        lines += ["", f"Total permintaan terlayani: {total}"
                      + (f" (sejak {datetime.fromtimestamp(keypool.since()):%d-%m-%Y})" if keypool.since() else "")]
        models = keypool.model_rows()
        if models:
            lines.append("Model bergantian: " + ", ".join(
                f"{m['name']} ({m['uses']}x{', istirahat' if m['cooling'] else ''})" for m in models))
        else:
            first = (keypool.all_keys() or [None])[0]
            lines.append(f"Model: {config.deepseek_model(first)} (bawaan). Atur putaran model: /model_pakai")
        lines += ["", "Tambah: /kunci <key> · Keluarkan: /kunci_hapus <no> · Periksa semua: /kunci_tes"]
        return "\n".join(lines)

    async def _key_list(self, chat_id, arg=""):
        await self.tg.send(chat_id, self._pool_text())

    async def _key_remove(self, chat_id, arg=""):
        pool = keypool.all_keys()
        if not pool:
            await self.tg.send(chat_id, "Belum ada key yang bisa dikeluarkan. /kunci_daftar")
            return
        numbers = sorted({int(n) for n in re.findall(r"\d+", arg or "")})
        if not numbers:
            await self.tg.send(chat_id, "Pakai: /kunci_hapus <nomor> — nomornya dari /kunci_daftar. "
                                        "Boleh beberapa: /kunci_hapus 2 5")
            return
        bad = [n for n in numbers if not 1 <= n <= len(pool)]
        if bad:
            await self.tg.send(chat_id, f"Tidak ada key bernomor {', '.join(map(str, bad))}. "
                                        f"Nomor yang ada 1–{len(pool)} (lihat /kunci_daftar).")
            return
        targets = [pool[n - 1] for n in numbers]
        removed = []
        for key in targets:
            masked = keys.mask(key)
            if await asyncio.to_thread(keys.pool_remove, key):
                removed.append(masked)
        left = len(keypool.all_keys())
        text = f"🗑️ Dikeluarkan: {', '.join(removed) or '(tidak ada)'}. Sisa {left} key."
        if not left:
            text += (" Kumpulan kosong: tamu WhatsApp dijawab otak cadangan (OpenRouter atau otak lokal) "
                     "sampai kamu memasang key lagi.")
        else:
            text += " Nomor key bisa bergeser, lihat /kunci_daftar sebelum menghapus lagi."
        await self.tg.send(chat_id, text)

    async def _key_test(self, chat_id, arg=""):
        """/kunci_tes: periksa SEMUA key (gratis, lewat daftar model). '/kunci_tes chat' juga meminta tiap
        key menjawab satu kata - bukti paling jujur bahwa key itu benar-benar bisa dipakai."""
        pool = keypool.all_keys()
        if not pool:
            await self.tg.send(chat_id, "Belum ada key. Kirim /kunci <key> dulu.")
            return
        chat = arg.strip().lower() in ("chat", "jawab", "penuh")
        await self.tg.send(chat_id, f"Memeriksa {len(pool)} key{' dan meminta tiap key menjawab' if chat else ''}… "
                                    "tunggu sebentar.")
        from . import brain_deepseek

        gate = asyncio.Semaphore(6)

        async def one(number, key):
            label = f"{number}. {keys.mask(key)}"
            async with gate:
                try:
                    names = await asyncio.to_thread(keys.gateway_models, key)
                except keys.KeyRejected:
                    keypool.mark_checked(key, False)
                    return label + " ❌ ditolak penyedia (salah atau dicabut)", False, []
                except Exception as exc:  # noqa: BLE001 - mis. internet putus
                    log.warning("pemeriksaan key %d gagal: %s", number, type(exc).__name__)
                    return label + f" ⚠️ belum terjangkau ({type(exc).__name__})", False, []
                if not chat:
                    keypool.mark_checked(key, True, full=False)
                    return label + f" ✅ dikenali penyedia ({len(names)} model)", True, names
                ok, info, took = await asyncio.to_thread(brain_deepseek.ping, key)
                if ok:
                    return label + f" ✅ bisa menjawab (model {info}, {took:.1f} dtk)", True, names
                return label + f" ❌ tidak bisa menjawab: {info}", False, names

        results = await asyncio.gather(*(one(i, k) for i, k in enumerate(pool, 1)))
        good = sum(1 for _, ok, _ in results if ok)
        lines = [f"🔎 Hasil pemeriksaan {len(pool)} key: {good} baik"
                 f"{'' if good == len(pool) else f', {len(pool) - good} bermasalah'}.", ""]
        lines += [text for text, _, _ in results]
        if not chat:
            lines += ["", "Ini baru memastikan key dikenali. Untuk membuktikan tiap key benar-benar bisa menjawab "
                          "(biaya hanya beberapa token per key): /kunci_tes chat"]
        bad = [i for i, (_, ok, _) in enumerate(results, 1) if not ok]
        if bad:
            lines.append(f"Keluarkan yang bermasalah: /kunci_hapus {' '.join(map(str, bad))}")
        await self.tg.send(chat_id, "\n".join(lines))

    async def _model_list(self, chat_id, arg=""):
        pool = keypool.all_keys()
        key = next((k for k in pool if keypool.is_gateway(k)), None)
        if key is None:
            await self.tg.send(chat_id, "Belum ada key NusaGate. Kirim /kunci <key> dulu.")
            return
        try:
            names = await asyncio.to_thread(keys.gateway_models, key)
        except keys.KeyRejected:
            await self.tg.send(chat_id, "Key pertama ditolak penyedia, jadi daftar model belum bisa diambil. "
                                        "Cek dengan /kunci_tes.")
            return
        except Exception as exc:  # noqa: BLE001 - mis. internet putus
            await self.tg.send(chat_id, f"Daftar model belum bisa diambil ({type(exc).__name__}). Coba lagi nanti.")
            return
        using = config.NUSAGATE_MODELS
        first = (pool or [None])[0]
        lines = [f"Model yang ditawarkan penyedia ({len(names)}):", ""]
        lines += [("• " + n + ("  ← dipakai" if n in using else "")) for n in names[:80]]
        if len(names) > 80:
            lines.append(f"… dan {len(names) - 80} lainnya")
        lines += ["", ("Sekarang diputar bergantian: " + ", ".join(using)) if using
                  else f"Sekarang satu model saja: {config.deepseek_model(first)} (bawaan).",
                  "Putar beberapa model bergantian: /model_pakai <nama> <nama> … · semuanya: /model_pakai semua · "
                  "kembali ke bawaan: /model_pakai bawaan",
                  "Tiap jawaban memakai model berikutnya, jadi tidak ada model yang kebanjiran atau menganggur."]
        await self.tg.send(chat_id, "\n".join(lines))

    async def _model_use(self, chat_id, arg=""):
        wanted = [w for w in re.split(r"[\s,;]+", arg or "") if w]
        if not wanted:
            await self.tg.send(chat_id, "Pakai: /model_pakai <nama> <nama> … (nama dari /model_daftar), "
                                        "/model_pakai semua untuk memutar SEMUA model yang ditawarkan, atau "
                                        "/model_pakai bawaan untuk kembali ke satu model bawaan.")
            return
        if len(wanted) == 1 and wanted[0].lower() in ("bawaan", "default", "reset", "kosong"):
            await asyncio.to_thread(keys.set_models, [])
            await self.tg.send(chat_id, f"Kembali ke satu model bawaan: {config.deepseek_model((keypool.all_keys() or [None])[0])}.")
            return
        every = len(wanted) == 1 and wanted[0].lower() in ("semua", "all")
        warning = ""
        pool = keypool.all_keys()
        gateway_key = next((k for k in pool if keypool.is_gateway(k)), None)
        if gateway_key is not None:
            try:
                names = await asyncio.to_thread(keys.gateway_models, gateway_key)
            except Exception as exc:  # noqa: BLE001 - tidak bisa memeriksa
                names = None
                if every:
                    await self.tg.send(chat_id, f"Daftar model belum bisa diambil ({type(exc).__name__}), jadi "
                                                "\"semua\" belum bisa dijalankan. Coba lagi nanti, atau sebutkan "
                                                "nama modelnya satu per satu.")
                    return
                warning = (f"\n⚠️ Daftar model penyedia belum bisa diperiksa ({type(exc).__name__}); nama yang salah "
                           "akan dilewati otomatis, tapi cek dengan /model_daftar.")
            if every:
                if not names:
                    await self.tg.send(chat_id, "Penyedia tidak mengirim daftar model, jadi \"semua\" belum bisa "
                                                "dijalankan. Sebutkan nama modelnya satu per satu.")
                    return
                if not await self._ask_confirm(chat_id, _MODEL_ALL_WARNING.format(n=len(names))):
                    await self.tg.send(chat_id, "Oke, model tidak diubah.")
                    return
                wanted = list(names)
            elif names is not None:
                unknown = [w for w in wanted if w not in names]
                if unknown:
                    await self.tg.send(chat_id, f"Model tidak ada di penyedia: {', '.join(unknown)}. Tidak disimpan. "
                                                "Lihat nama yang benar di /model_daftar.")
                    return
        elif every:
            await self.tg.send(chat_id, "Belum ada key NusaGate, jadi daftar model belum bisa diambil. "
                                        "Kirim /kunci <key> dulu.")
            return
        else:
            warning = "\nℹ️ Belum ada key NusaGate; model ini baru dipakai setelah kamu memasang key (/kunci)."
        await asyncio.to_thread(keys.set_models, wanted)
        how = (f"Tiap jawaban memakai model berikutnya secara bergilir ({len(config.NUSAGATE_MODELS)} model)."
               if len(config.NUSAGATE_MODELS) > 1 else "Semua jawaban memakai model itu.")
        await self.tg.send(chat_id, f"✅ Model dipakai: {', '.join(config.NUSAGATE_MODELS)}. {how} Model yang "
                                    "ditolak penyedia otomatis dilewati." + warning)

    def _new_wa(self):
        return WhatsApp(self._wa_inbox, self._wa_qr, on_giveup=self._wa_gave_up, on_state=self._wa_state)

    def _wa_status_text(self):
        nyala = "menyala" if self.wa else ("mati" if not config.WHATSAPP else "gagal menyala")
        baris = [f"Nomor WhatsApp pemilik: {config.WA_OWNER or '(belum diisi)'}", f"Bot WhatsApp: {nyala}"]
        if self.wa:
            baris.append(f"Sambungan: {self.wa.status_text()}")
        baris += ["", "Ubah nomor: /wa 6281234567890",
                  "Tautkan ulang / minta QR baru: /wa_ulang"]
        return "\n".join(baris)

    def _wa_link_report(self, nomor, state):
        """Laporan JUJUR hasil /wa dan /wa_ulang. QR hanya ada untuk akun yang BELUM tertaut;
        kalau sesi tersimpan masih berlaku, WhatsApp langsung tersambung dan tidak membuat QR -
        jadi jangan pernah berjanji 'QR kukirim' sebelum jembatan benar-benar melapor 'qr'."""
        me = self.wa.me if self.wa else ""
        if state == "tersambung":
            if me and me != nomor:
                return (f"⚠️ WhatsApp tersambung, tetapi ke akun {me}, bukan {nomor}.\n"
                        f"Bot melayani chat dari {nomor} dan chat \"Pesan ke diri sendiri\" milik akun {me}.\n"
                        f"Untuk menautkan akun {nomor}: kirim /wa_ulang lalu pindai QR-nya.")
            return (f"✅ WhatsApp sudah TERSAMBUNG ke nomor {me or nomor} memakai sesi yang tersimpan, "
                    "jadi memang tidak ada QR yang perlu dipindai (QR hanya muncul untuk akun yang "
                    "belum ditautkan).\n\n"
                    "Coba sekarang: di WhatsApp HP buka chat \"Pesan ke diri sendiri\", lalu kirim \"halo\".\n"
                    "Mau menautkan ulang atau ganti akun? Kirim /wa_ulang.")
        if state == "qr":
            return ("📷 WhatsApp belum tertaut, jadi QR sudah kukirim ke chat ini (berupa foto). Buka "
                    "WhatsApp HP > Perangkat tertaut > Tautkan perangkat, lalu pindai QR itu. QR berlaku "
                    "±1 menit; kalau habis, kirim /wa_ulang untuk QR baru.\n"
                    "Catatan: kalau Telegram ini dibuka di HP yang sama dengan WhatsApp-nya, buka dulu "
                    "Telegram di komputer atau tablet — QR tidak bisa dipindai dari layar HP yang sama.")
        if state == "keluar":
            return ("⚠️ WhatsApp keluar dari Perangkat tertaut. Sesi lama kusimpan dan QR baru sedang "
                    "disiapkan; akan kukirim ke chat ini.")
        return ("Bot WhatsApp sudah menyala, tetapi belum ada kabar dari WhatsApp dalam beberapa detik "
                "(internet lambat?). Kalau QR diperlukan, akan kukirim otomatis ke chat ini. Cek juga /diagnosa.")

    async def _set_wa_owner(self, chat_id, arg):
        """Atur nomor WhatsApp pemilik dari Telegram (/wa 62812…); tanpa arg = status."""
        if not arg:
            await self.tg.send(chat_id, self._wa_status_text())
            return
        nomor = re.sub(r"\D", "", arg)
        if nomor.startswith("0"):
            nomor = "62" + nomor[1:]
        if not 8 <= len(nomor) <= 15:
            await self.tg.send(chat_id, "Nomor tidak valid. Contoh: /wa 6281234567890")
            return
        # Nomor sama & jembatan sudah tersambung: tak perlu memutus-nyambung lagi, cukup lapor.
        sama = bool(self.wa) and config.WA_OWNER == nomor and self.wa.state == "tersambung"
        keys._set_env(keys.ENV_FILE, "ITSME_WA_OWNER", nomor)
        keys._set_env(keys.ENV_FILE, "ITSME_WHATSAPP", "1")
        config.WA_OWNER, config.WHATSAPP = nomor, True
        if sama:
            await self.tg.send(chat_id, f"Nomor WhatsApp pemilik: {nomor} (tidak berubah).\n\n"
                                        + self._wa_link_report(nomor, "tersambung"))
            return
        if self.wa:
            self.wa.stop()
        self._qr_at, self._qr_sent = None, 0
        self.wa = self._new_wa()
        ok, why = self.wa.start()
        if not ok:
            self.wa = None
            await self.tg.send(chat_id, f"Nomor {nomor} disimpan, tapi bot WhatsApp belum bisa menyala.\n\n"
                                        + _wa_start_problem(why))
            return
        await self.tg.send(chat_id, f"Nomor WhatsApp pemilik disimpan: {nomor}\n"
                                    "Menyalakan bot WhatsApp… (paling lama 20 detik)")
        state = await asyncio.to_thread(self.wa.wait_state, 20)
        await self.tg.send(chat_id, self._wa_link_report(nomor, state))

    async def _wa_relink(self, chat_id, arg=""):
        """/wa_ulang: tautkan ulang WhatsApp (QR baru). Sesi lama DISIMPAN, bukan dihapus. Kalau
        sekarang sedang tersambung, minta konfirmasi dulu: ini melepas akun yang bekerja."""
        if not config.WA_OWNER:
            await self.tg.send(chat_id, "Isi nomornya dulu: /wa 6281234567890")
            return
        if self.wa and self.wa.state == "tersambung" and arg.strip().lower() not in ("ya", "y", "ok"):
            await self.tg.send(chat_id, f"WhatsApp sekarang TERSAMBUNG ke akun {self.wa.me or config.WA_OWNER}. "
                                        "Menautkan ulang akan melepasnya dari \"Perangkat tertaut\" dan "
                                        "menggantinya dengan QR baru yang harus kamu pindai.\n\n"
                                        "Kalau yakin, kirim: /wa_ulang ya")
            return
        await self.tg.send(chat_id, "Menautkan ulang WhatsApp… sesi lama kusimpan (tidak dihapus). "
                                    "QR baru menyusul, paling lama 30 detik.")
        self._qr_at, self._qr_sent = None, 0
        self._wa_relinking = True
        try:
            if self.wa is None:
                config.WHATSAPP = True
                self.wa = self._new_wa()
                ok, why = await asyncio.to_thread(self.wa.start)
            else:
                ok, why = await asyncio.to_thread(self.wa.relink)
            if not ok:
                self.wa = None
                pesan = ("Sesi WhatsApp yang lama tidak bisa dipindahkan (mungkin masih dipakai proses lain). "
                         "Coba lagi beberapa menit lagi." if why == "sesi" else _wa_start_problem(why))
                await self.tg.send(chat_id, "Gagal menautkan ulang WhatsApp.\n\n" + pesan)
                return
            state = await asyncio.to_thread(self.wa.wait_state, 30, ("qr", "tersambung"))
        finally:
            self._wa_relinking = False
        await self.tg.send(chat_id, self._wa_link_report(config.WA_OWNER, state))

    # --- tamu WhatsApp (mode terbuka): diatur dari Telegram ----------------------------------------
    def _wa_guest_text(self):
        on = config.WA_OPEN
        owner = config.WA_OWNER or "(belum diisi: /wa <nomor>)"
        if self.wa:
            bridge = f"menyala, {self.wa.status_text()}"
        else:
            bridge = "BELUM menyala" if config.WHATSAPP else "dimatikan (nyalakan dengan /wa <nomor>)"
        brains = []
        if keypool.has_keys():
            n = len(keypool.all_keys())
            brains.append(f"{n} key NusaGate/DeepSeek bergiliran")
        if config.OPENROUTER_API_KEY:
            brains.append("OpenRouter")
        if config.WA_GUEST_LOCAL:
            brains.append("otak lokal (Ollama)")
        lines = [
            f"👥 Mode tamu WhatsApp: {'AKTIF ✅' if on else 'MATI ⛔'}",
            "",
            f"Nomor: {owner} · bot WhatsApp: {bridge}",
            "",
            "Kalau AKTIF: orang yang chat ke nomor ini (satu lawan satu) dijawab AI. Mereka hanya bisa "
            "tanya-jawab: tanpa alat, tanpa memorimu, tanpa berkas atau data pribadimu. Grup, siaran, dan "
            "status tidak pernah dibalas, lampiran dari tamu tidak diunduh. Balasan pertama ke tiap kontak "
            "per hari diawali pemberitahuan bahwa itu asisten AI.",
            "",
            "Otak untuk tamu: " + " → ".join(brains) + " (yang pertama siap dipakai)",
            f"Batas per kontak: {config.WA_GUEST_PER_MIN} pesan/menit, "
            f"{config.GUEST_DAILY_MAX if config.GUEST_DAILY_MAX > 0 else 'tanpa batas'} pertanyaan/hari · "
            f"semua tamu digabung: "
            f"{config.WA_GUEST_TOTAL_MAX if config.WA_GUEST_TOTAL_MAX > 0 else 'tanpa batas'} jawaban/hari",
            f"Diblokir: {len(config.WA_BLOCK)} nomor · info untuk tamu: {'ada' if brain_guest.read_info() else 'belum ada'}",
        ]
        if on and not keypool.has_keys() and not config.OPENROUTER_API_KEY:
            lines += ["", "⚠️ Belum ada key NusaGate/OpenRouter: tamu hanya menerima permintaan maaf"
                          + (" (atau jawaban otak lokal, kalau Ollama menyala)" if config.WA_GUEST_LOCAL else "")
                          + ". Pasang key lewat /kunci <key>."]
        lines += ["", "Atur: /wa_tamu aktif · /wa_tamu mati · /wa_blokir <nomor> · /wa_buka <nomor> · /wa_info <teks>"]
        return "\n".join(lines)

    def _wa_push_settings(self):
        """Terapkan pengaturan tamu ke jembatan yang sedang jalan (tanpa memutus WhatsApp)."""
        if self.wa:
            return bool(self.wa.update_settings())
        return False

    async def _wa_guest_mode(self, chat_id, arg=""):
        """/wa_tamu [aktif|mati]: balas AI untuk orang yang chat ke nomor ini."""
        arg = (arg or "").strip().lower()
        if arg in ("aktif", "on", "nyala", "nyalakan", "ya"):
            if config.WA_OPEN:
                await self.tg.send(chat_id, "Mode tamu sudah AKTIF.\n\n" + self._wa_guest_text())
                return
            if not await self._ask_confirm(chat_id, _WA_OPEN_WARNING):
                await self.tg.send(chat_id, "Oke, mode tamu tetap MATI.")
                return
            keys._set_env(keys.ENV_FILE, "ITSME_WA_OPEN", "1")
            config.WA_OPEN = True
            live = self._wa_push_settings()
            await self.tg.send(chat_id, "✅ Mode tamu AKTIF. " + (
                "Mulai sekarang orang yang chat ke nomor ini dijawab AI." if live else
                "Tersimpan, tapi bot WhatsApp belum menyala — aktif begitu menyala (/wa)."
            ) + " Matikan kapan saja: /wa_tamu mati")
            return
        if arg in ("mati", "off", "matikan", "tidak"):
            keys._set_env(keys.ENV_FILE, "ITSME_WA_OPEN", "0")
            config.WA_OPEN = False
            self._wa_push_settings()
            await self.tg.send(chat_id, "⛔ Mode tamu MATI. Tidak ada lagi balasan AI ke orang lain; "
                                        "hanya chat \"Pesan ke diri sendiri\" yang dilayani.")
            return
        await self.tg.send(chat_id, self._wa_guest_text())

    @staticmethod
    def _wa_number(arg):
        nomor = re.sub(r"\D", "", arg or "")
        if nomor.startswith("0"):
            nomor = "62" + nomor[1:]
        return nomor if 8 <= len(nomor) <= 15 else ""

    async def _wa_block(self, chat_id, arg=""):
        """/wa_blokir <nomor>: nomor itu tidak pernah dijawab AI (walau mode tamu aktif)."""
        if not (arg or "").strip():
            shown = ", ".join(sorted(config.WA_BLOCK)) or "(belum ada)"
            await self.tg.send(chat_id, f"Nomor yang diblokir: {shown}\n\nBlokir: /wa_blokir 6281234567890 · "
                                        "buka lagi: /wa_buka 6281234567890")
            return
        nomor = self._wa_number(arg)
        if not nomor:
            await self.tg.send(chat_id, "Nomor tidak valid. Contoh: /wa_blokir 6281234567890")
            return
        if nomor == re.sub(r"\D", "", config.WA_OWNER or ""):
            await self.tg.send(chat_id, "Itu nomor pemilik sendiri; tidak perlu diblokir.")
            return
        if nomor in config.WA_BLOCK:
            await self.tg.send(chat_id, f"Nomor {nomor} sudah diblokir.")
            return
        blocked = sorted(config.WA_BLOCK | {nomor})
        keys._set_env(keys.ENV_FILE, "ITSME_WA_BLOCK", ",".join(blocked))
        config.WA_BLOCK = set(blocked)
        self._wa_push_settings()
        self.histories.pop("wa:" + nomor + "@s.whatsapp.net", None)
        await self.tg.send(chat_id, f"🚫 Nomor {nomor} diblokir: tidak akan dijawab AI lagi. "
                                    f"Total diblokir: {len(blocked)}. Buka lagi: /wa_buka {nomor}")

    async def _wa_unblock(self, chat_id, arg=""):
        """/wa_buka <nomor>: nomor itu boleh dijawab AI lagi."""
        nomor = self._wa_number(arg)
        if not nomor:
            await self.tg.send(chat_id, "Pakai: /wa_buka 6281234567890 (lihat yang diblokir: /wa_blokir)")
            return
        if nomor not in config.WA_BLOCK:
            await self.tg.send(chat_id, f"Nomor {nomor} tidak ada di daftar blokir.")
            return
        blocked = sorted(config.WA_BLOCK - {nomor})
        keys._set_env(keys.ENV_FILE, "ITSME_WA_BLOCK", ",".join(blocked))
        config.WA_BLOCK = set(blocked)
        self._wa_push_settings()
        await self.tg.send(chat_id, f"✅ Nomor {nomor} dibuka lagi. Sisa diblokir: {len(blocked)}.")

    async def _wa_info(self, chat_id, arg=""):
        """/wa_info [teks|hapus]: keterangan yang BOLEH dibagikan AI kepada tamu (jam kerja, alamat, dsb)."""
        path = brain_guest.info_file()
        arg = (arg or "").strip()
        if arg.lower() in ("hapus", "kosong", "reset"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                await self.tg.send(chat_id, f"Info gagal dihapus: {exc.strerror or exc}")
                return
            await self.tg.send(chat_id, "Info untuk tamu dikosongkan.")
            return
        if not arg:
            current = brain_guest.read_info()
            await self.tg.send(chat_id, (f"Info yang boleh dibagikan AI kepada tamu:\n\n{current}\n\n" if current else
                                         "Belum ada info untuk tamu.\n\n")
                               + "Tulis/ganti: /wa_info <teks> · kosongkan: /wa_info hapus\n"
                                 "Isinya bisa dibaca SEMUA tamu - jangan menulis hal pribadi atau rahasia.")
            return
        if len(arg) > 2000:
            await self.tg.send(chat_id, f"Terlalu panjang ({len(arg)} huruf, maksimal 2000). Ringkas dulu.")
            return
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(arg)
        except OSError as exc:
            await self.tg.send(chat_id, f"Info gagal disimpan: {exc.strerror or exc}")
            return
        await self.tg.send(chat_id, "✅ Info untuk tamu tersimpan. AI hanya akan membagikan isi ini (dan "
                                    "pengetahuan umumnya) kepada tamu.")

    async def _wa_say(self, jid, text):
        """Kirim teks ke chat WhatsApp (di thread). Diam kalau jembatan sedang tidak ada."""
        wa = self.wa
        if wa is None:
            return False
        return await asyncio.to_thread(wa.send, jid, text)

    def _wa_guest_pace(self, jid):
        """Penjaga banjir: satu kontak paling banyak WA_GUEST_PER_MIN pesan per menit yang dilayani
        (juga memutus balas-balasan dengan bot lain). Lebihnya diabaikan diam-diam."""
        now = time.monotonic()
        stamps = self._wa_pace.setdefault(jid, deque())
        while stamps and now - stamps[0] > 60:
            stamps.popleft()
        if len(stamps) >= config.WA_GUEST_PER_MIN:
            return False
        stamps.append(now)
        if len(self._wa_pace) > 500:  # jangan tumbuh tanpa batas
            for old in [j for j, s in self._wa_pace.items() if not s or now - s[-1] > 120]:
                del self._wa_pace[old]
        return True

    def _wa_once_a_day(self, kind, jid):
        """True sekali saja per hari untuk (jenis, kontak): perkenalan dan kabar kuota tidak diulang-ulang."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._wa_noted.get((kind, jid)) == today:
            return False
        if len(self._wa_noted) > 2000:
            self._wa_noted = {k: v for k, v in self._wa_noted.items() if v == today}
        self._wa_noted[(kind, jid)] = today
        return True

    async def _wa_guest_answer(self, key, text):
        """Jawaban AI untuk seorang tamu: (teks, berhasil). Riwayat tamu itu terpisah dari semua orang dan
        dari pemilik; jawaban baru masuk riwayat hanya kalau berhasil (kegagalan tidak meninggalkan
        setengah percakapan, dan tamu hanya menerima permintaan maaf singkat, tidak pernah isi galatnya)."""
        async with self._wa_guest_gate:
            async with self._lock(key):
                history = list(self.histories.get(key, []))
                history.append({"role": "user", "content": _stamped(text)})
                try:
                    reply = await asyncio.wait_for(
                        asyncio.to_thread(run_agent, history, self._guest_ctx(key)), _WA_GUEST_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 - tamu tidak pernah melihat isi galat
                    log.warning("tamu WhatsApp: AI tidak bisa menjawab (%s)", type(exc).__name__)
                    return _WA_GUEST_BUSY, False
                self.histories[key] = history
                self._wa_guest_seen[key] = None
                self._wa_guest_seen.move_to_end(key)
                while len(self._wa_guest_seen) > _WA_GUEST_HISTORIES:  # simpan riwayat tamu terbaru saja
                    old, _ = self._wa_guest_seen.popitem(last=False)
                    self.histories.pop(old, None)
                return reply, True

    @staticmethod
    def _guest_ctx(key):
        """Konteks minimal untuk tamu: tanpa memori pemilik, tombol konfirmasi, atau tugas latar."""
        return {"guest": True, "memory": _NoMemory(), "chat_id": key, "full_access": False, "in_background": False}

    async def _run_wa_guest(self, jid, text, media=None):
        """Tamu: orang lain yang chat ke nomor ini (mode terbuka). Hanya tanya-jawab lewat brain_guest -
        tanpa alat, tanpa memori pemilik, tanpa lampiran. Dijaga: penjaga banjir, kuota harian, dan batas
        jumlah jawaban yang diproses bersamaan."""
        if not self._wa_guest_pace(jid):
            return
        text = (text or "").strip()
        first = text.split(maxsplit=1)[0].lower() if text.startswith("/") else ""
        if first in _WA_OWNER_COMMANDS:
            # Sama seperti di Telegram: perintah ini khusus pemilik, bukan tamu.
            await self._wa_say(jid, "Mode tamu: perintah itu hanya untuk pemilik.")
            return
        if not text:  # lampiran saja (gambar, suara, dokumen): tidak dibaca
            await self._wa_say(jid, _WA_GUEST_TEXT_ONLY)
            return
        if not quota.allow("wa:" + jid):
            if self._wa_once_a_day("kuota", jid):  # setelah itu diam, supaya tidak jadi balas-balasan tanpa akhir
                await self._wa_say(jid, _WA_GUEST_QUOTA)
            return
        # Pagar biaya untuk SEMUA tamu digabung (dicek setelah batas per tamu, supaya satu tamu yang berisik
        # tidak menghabiskan jatah orang lain). Habis: diam (bukan balasan), pemilik dikabari sekali sehari.
        if not quota.allow("wa-total", config.WA_GUEST_TOTAL_MAX):
            if self._wa_once_a_day("total", "*") and self.tg is not None:
                self._spawn(self.tg.send(
                    config.OWNER_ID,
                    f"⚠️ Batas harian GABUNGAN tamu WhatsApp ({config.WA_GUEST_TOTAL_MAX} jawaban) tercapai. "
                    "Tamu tidak dijawab lagi sampai besok. Naikkan batasnya dengan ITSME_WA_GUEST_TOTAL_MAX di "
                    ".env, atau matikan mode tamu: /wa_tamu mati"))
            return
        reply, answered = await self._wa_guest_answer("wa:" + jid, text[:2000])
        # Perkenalan ("ini asisten AI") menyertai jawaban PERTAMA yang berhasil per kontak per hari;
        # permintaan maaf karena AI sedang gagal tidak memakai jatahnya.
        if answered and config.WA_GUEST_INTRO and self._wa_once_a_day("perkenalan", jid):
            reply = f"{config.WA_GUEST_INTRO}\n\n{reply}"
        await self._wa_say(jid, reply)

    def _pool_alert(self, kind, masked):
        """Dari thread mana pun: sebuah key NusaGate tidak bisa dipakai lagi. Pemilik dikabari (key lain tetap
        bekerja). Kabar dikumpulkan beberapa detik supaya banyak key yang gagal bersamaan jadi satu pesan."""
        if self.tg is None or self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._queue_pool_alert, kind, masked)

    def _queue_pool_alert(self, kind, masked):
        self._pool_alerts.setdefault(kind, []).append(masked)
        if not self._pool_alert_timer:
            self._pool_alert_timer = self.loop.call_later(_POOL_ALERT_WAIT, self._send_pool_alerts)

    def _send_pool_alerts(self):
        alerts, self._pool_alerts, self._pool_alert_timer = self._pool_alerts, {}, None
        what = {
            "balance": "saldonya habis (dicoba lagi tiap 30 menit). Isi ulang di nusagate.com",
            "cap": "batas paketnya tercapai (dicoba lagi tiap 20 menit)",
            "invalid": "ditolak penyedia (salah atau dicabut; dicoba lagi tiap jam). Keluarkan lewat /kunci_hapus",
        }
        left = len([r for r in keypool.snapshot() if r["status"] in ("siap", "sibuk")])
        for kind, masks in alerts.items():
            if kind in what:
                self._spawn(self.tg.send(
                    config.OWNER_ID,
                    f"⚠️ {len(masks)} key NusaGate tidak bisa dipakai sementara — {what[kind]}:\n"
                    + "\n".join(f"• {m}" for m in masks)
                    + f"\n\nKey lain tetap bekerja ({left} siap sekarang). Daftar: /kunci_daftar"))

    def _wa_qr(self, png):
        """QR login dari thread node: kirim sebagai foto ke Telegram. Jarak antar-QR minimal
        _WA_QR_GAP detik dan paling banyak _WA_QR_MAX per permintaan (WhatsApp terus membuat QR baru
        selama belum dipindai - jangan membanjiri chat)."""
        now = self.loop.time()
        if self._qr_at is not None and now - self._qr_at < _WA_QR_GAP:
            return
        if self._qr_sent >= _WA_QR_MAX:
            return
        self._qr_at, self._qr_sent = now, self._qr_sent + 1
        self.loop.call_soon_threadsafe(self._spawn, self.tg.send_photo(
            config.OWNER_ID, png, "whatsapp-qr.png",
            "WhatsApp: buka WhatsApp HP > Perangkat tertaut > Tautkan perangkat, lalu pindai QR ini "
            "(berlaku ±1 menit; kalau habis kirim /wa_ulang). Kalau Telegram ini dibuka di HP yang sama "
            "dengan WhatsApp-nya, buka dulu di komputer atau tablet."))

    def _wa_state(self, state, me):
        """Dari thread jembatan: keadaan sambungan WhatsApp berubah. Pemilik hanya dikabari untuk
        yang penting: dicabut dari HP (QR baru menyusul) dan 'QR sudah dipindai, tersambung'."""
        # Jejak di log (nomor disamarkan): bukti keadaan sambungan tanpa perlu menebak.
        log.info("WhatsApp: keadaan sambungan -> %s%s", state, f" (akun ...{me[-4:]})" if me else "")
        if self.tg is None or self.loop is None:
            return
        if state == "keluar":
            self._qr_at, self._qr_sent = None, 0  # QR baru akan datang: boleh dikirim lagi
            if self._wa_relinking:
                return  # pemilik sendiri yang memintanya lewat /wa_ulang
            pesan = ("⚠️ WhatsApp keluar dari \"Perangkat tertaut\" (dicabut dari HP atau diputus WhatsApp). "
                     "Sesi lama kusimpan dan QR baru kukirim ke sini.")
        elif state == "tersambung" and self._qr_sent > 0:
            self._qr_sent = 0
            pesan = (f"✅ QR terpindai: WhatsApp tersambung ke nomor {me}. "
                     "Coba kirim \"halo\" ke chat \"Pesan ke diri sendiri\".")
        else:
            return
        self.loop.call_soon_threadsafe(self._spawn, self.tg.send(config.OWNER_ID, pesan))

    def _wa_gave_up(self):
        """Dipanggil dari thread jembatan WhatsApp kalau sudah menyerah (gagal
        menyambung berkali-kali) — tanpa ini, status /wa terus bilang "menyala"
        dan pemiliknya tidak pernah tahu WA berhenti selain membaca log mentah."""
        self.wa = None
        if self.tg is None:
            return
        self.loop.call_soon_threadsafe(self._spawn, self.tg.send(
            config.OWNER_ID,
            "⚠️ Bot WhatsApp berhenti total setelah gagal menyambung berkali-kali. "
            "Kirim /wa untuk melihat statusnya, atau /wa <nomor> untuk menyalakannya lagi.",
        ))

    # --- WhatsApp -------------------------------------------------------------
    def _wa_inbox(self, jid, text, guest=False, file=None, media=None):
        """Dipanggil dari thread pembaca WhatsApp; serahkan ke event loop."""
        if text.strip() or file or media:
            # Hanya jejak "ada pesan masuk" (tanpa isi & tanpa nomor): bukti jalur pesan hidup,
            # dipakai /diagnosa.
            self._wa_last_in = datetime.now()
            log.info("WhatsApp: pesan masuk (%s, %d karakter%s).", "tamu" if guest else "pemilik",
                     len(text), ", + berkas" if file else (", + lampiran tak dibaca" if media else ""))
            self.loop.call_soon_threadsafe(self._spawn, self._run_wa(jid, text, guest, file, media))

    async def _run_wa(self, jid, text, guest=False, file=None, media=None):
        # ponytail: riwayat dipisah per nomor WhatsApp, tapi konfirmasi aksi &
        # tugas latar tetap lewat chat Telegram pemilik (tombol).
        if keys.contains_secret(text):
            # API key / kata sandi TIDAK BOLEH lewat WhatsApp: bisa bocor, dan jawaban AI bisa mengulangnya.
            # Tidak diproses, tidak masuk riwayat, tidak sampai ke AI, isinya tidak dicatat di log.
            log.warning("WhatsApp: pesan berisi kunci rahasia ditolak (%s; isinya tidak dicatat).",
                        "tamu" if guest else "pemilik")
            if guest and not self._wa_guest_pace(jid):
                return  # kuncinya tetap tidak diproses; hanya balasan penolakannya yang tidak diulang-ulang
            await self._wa_say(jid, _WA_SECRET_GUEST if guest else _WA_SECRET_OWNER)
            return
        if guest:
            await self._run_wa_guest(jid, text, media)
            return
        first = text.strip().split(maxsplit=1)[0].lower() if text.strip().startswith("/") else ""
        if first in _WA_TELEGRAM_ONLY:
            await self._wa_say(jid, _WA_TELEGRAM_ONLY_NOTE)
            return
        if first in _WA_OWNER_COMMANDS:
            # Sama seperti di Telegram: perintah ini khusus pemilik, bukan tamu.
            if first == "/kirim":
                await self._wa_kirim(jid, text.strip()[len("/kirim"):].strip())
                return
            if first == "/komputer":
                await asyncio.to_thread(self.wa.send, jid, await asyncio.to_thread(self._computers_text))
                return
            if first == "/diagnosa":
                await asyncio.to_thread(self.wa.send, jid, await self._diagnosa())
                return
            if first == "/nyalakan":
                name = text.strip().split(maxsplit=1)[1].strip() if " " in text.strip() else ""
                await self._wake_computer(lambda t: asyncio.to_thread(self.wa.send, jid, t), name)
                return
            if first == "/reset":
                self.histories.pop("wa:" + jid, None)
                await asyncio.to_thread(self.wa.send, jid, "Oke, konteks obrolan direset. Memori tetap.")
                return
            if first == "/otak":
                await asyncio.to_thread(self.wa.send, jid, await asyncio.to_thread(self._brain_status))
                return
            if first == "/memori":
                isi = self.memory.text() or "(memori masih kosong)"
                if len(isi) > 3800:
                    isi = "…" + isi[-3800:]
                await asyncio.to_thread(self.wa.send, jid, f"{isi}\n\n/hapus_memori — kosongkan memori")
                return
            # first == "/hapus_memori": konfirmasi tetap lewat tombol Telegram
            # pemilik (tak ada tombol di WA) — WA dikabari dulu supaya tak diam.
            await asyncio.to_thread(
                self.wa.send, jid,
                "Menghapus seluruh memori jangka panjang perlu konfirmasi pemilik — kutanya lewat Telegram dulu.",
            )
            if await self._ask_confirm(
                config.OWNER_ID, "Kosongkan seluruh memori jangka panjang? (diminta lewat WhatsApp)"
            ):
                self.memory.clear()
                await asyncio.to_thread(self.wa.send, jid, "Memori sudah dikosongkan.")
            else:
                await asyncio.to_thread(self.wa.send, jid, "Oke, memori tidak dihapus.")
            return
        if file:
            text = (text.strip() + "\n\n" if text.strip() else "") + _wa_file_note(file)
        key = "wa:" + jid
        async with self._lock(key):
            history = self.histories.setdefault(key, [])
            history.append({"role": "user", "content": _stamped(text)})
            ctx = self._ctx_for(config.OWNER_ID, guest=guest)
            ctx["confirm"] = lambda t: self._confirm_blocking_wa(jid, t)
            reply = await self._think(config.OWNER_ID, history, ctx)
        await asyncio.to_thread(self.wa.send, jid, reply)

    def _confirm_blocking_wa(self, jid, text):
        """Sama seperti _confirm_blocking (tombol Ya/Tidak tetap lewat Telegram
        pemilik), tapi chat WhatsApp diberi kabar dulu — supaya tidak kelihatan
        diam sampai 5 menit sementara pemiliknya belum buka Telegram."""
        fut = asyncio.run_coroutine_threadsafe(
            asyncio.to_thread(
                self.wa.send, jid,
                "⏳ Ada aksi yang perlu dikonfirmasi pemilik dulu — kutanya lewat "
                "Telegram, tunggu sebentar ya.",
            ),
            self.loop,
        )
        try:
            fut.result(timeout=10)
        except Exception:  # noqa: BLE001 - kabar gagal tidak boleh menghentikan konfirmasi
            log.exception("kabar tunggu konfirmasi WhatsApp gagal dikirim")
        return self._confirm_blocking(config.OWNER_ID, text)

    async def _wa_kirim(self, jid, path):
        """Kirim berkas dari komputer ini ke WhatsApp (hanya pemilik)."""
        from .tools import is_hardlinked, is_secret_path

        say = lambda t: asyncio.to_thread(self.wa.send, jid, t)  # noqa: E731
        path = os.path.expanduser((path or "").strip().strip('"').strip("'"))
        if not path:
            await say("Pakai: /kirim <path berkas>")
            return
        if is_secret_path(path):
            await say("Ditolak: berkas itu biasanya berisi rahasia (token, password, kunci).")
            return
        if is_hardlinked(path):
            await say("Ditolak: berkas ini hardlink, namanya bisa menyembunyikan berkas rahasia.")
            return
        if not os.path.isfile(path):
            await say(f"Berkas tidak ditemukan: {path}")
            return
        size = os.path.getsize(path)
        if size > config.WA_MAX_MB * 1024 * 1024:
            await say(f"Berkas {_wa_mb(size)} melebihi batas {config.WA_MAX_MB} MB.")
            return
        if not await asyncio.to_thread(self.wa.send_file, jid, path):
            await say("Gagal mengirim berkas (WhatsApp tidak tersambung).")

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
        if text.strip().split("@")[0].lower() == "/lock":
            # Sampai sini berarti lock.active() salah: belum ada kata sandi diatur.
            await self.tg.send(
                chat_id,
                "Kunci belum diatur, jadi belum bisa dipakai. Pemilik: isi "
                "ITSME_PASSPHRASE di .env lalu nyalakan ulang It's Me.",
            )
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
        if first == "/pasang_tetap":
            await self._invite(chat_id, reusable=True)
            return
        if first == "/komputer":
            await self.tg.send(chat_id, await asyncio.to_thread(self._computers_text))
            return
        if first == "/diagnosa":
            await self.tg.send(chat_id, await self._diagnosa())
            return
        if first == "/layar":
            await self._send_screen(chat_id, text.split(maxsplit=1)[1].strip() if " " in text.strip() else "")
            return
        if first == "/nyalakan":
            name = text.split(maxsplit=1)[1].strip() if " " in text.strip() else ""
            await self._wake_computer(lambda t: self.tg.send(chat_id, t), name)
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
        if not guest and keys.only_keys(text):
            # Pesan yang isinya HANYA key (ditempel tanpa /kunci): tetap dipasang dan pesannya dihapus,
            # bukan dikirim ke otak AI, riwayat obrolan, atau komputer lain.
            await self._save_key(chat_id, message)
            return
        handler = {
            "/kunci_daftar": self._key_list, "/kunci_hapus": self._key_remove, "/kunci_tes": self._key_test,
            "/model_daftar": self._model_list, "/model_pakai": self._model_use,
            "/wa_tamu": self._wa_guest_mode, "/wa_blokir": self._wa_block, "/wa_buka": self._wa_unblock,
            "/wa_info": self._wa_info,
        }.get(first)
        if handler:  # pengaturan Hub: kumpulan key, model, dan tamu WhatsApp
            parts = text.split(maxsplit=1)
            await handler(chat_id, parts[1].strip() if len(parts) > 1 else "")
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

        if first == "/kunci":
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
        if command == "/wa_ulang":
            await self._wa_relink(chat_id, text.partition(" ")[2].strip())
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
            "/nyalakan <nama> — komputer itu mati? coba bangunkan dari jarak jauh (Wake-on-LAN).",
            f"Layar langsung + kendali + telusur berkas: buka dashboard It's Me "
            f"(http://localhost:{config.DASHBOARD_PORT}) di komputer ini, lalu tab 🖥️ Komputer "
            "(dari HP lewat Tailscale/Cloudflare — lihat PANDUAN_AKSES_JARAK_JAUH).",
        ]
        return "\n".join(lines)

    async def _wake_computer(self, send, name):
        # /nyalakan: coba bangunkan komputer yang sedang MATI lewat Wake-on-LAN.
        # Cuma bisa kalau ADA komputer It's Me lain yang masih menyala satu jaringan
        # fisik dengan name dan pernah melihatnya langsung (lihat node.py:wake,
        # itsme-node inode/wol.py). Batas fisik, bukan batas kode: komputer sendirian
        # tanpa teman sejaringan tidak bisa dibangunkan murni lewat web. send mengirim
        # satu baris balasan - Telegram atau WhatsApp, siapa pun yang memanggil.
        if not name:
            await send("Format: /nyalakan <nama komputer>. /komputer untuk lihat namanya.")
            return
        if not node.installed():
            await send("It's Me Node belum terpasang di komputer ini.")
            return
        res = await asyncio.to_thread(node.wake, name)
        if res is None:
            await send("Node di komputer ini belum menjawab (mungkin baru menyala).")
            return
        if res.get("ok"):
            await send(
                f"📡 Sinyal nyala terkirim ke {name} (MAC {res.get('mac', '?')}) lewat "
                f"{res.get('via', 'komputer jembatan')}. Tunggu sebentar lalu cek /komputer — "
                "siaran WoL tidak punya balasan, jadi ini belum membuktikan komputernya benar-benar menyala."
            )
            return
        await send(
            f"Tidak ada komputer It's Me yang menyala yang pernah melihat {name!r} langsung di "
            "jaringan fisiknya. Wake-on-LAN cuma bisa lewat komputer It's Me LAIN yang masih "
            "menyala satu LAN dengan komputer itu — tanpa itu, membangunkannya murni lewat web "
            "tidak mungkin secara fisik (perlu alat tambahan seperti colokan pintar)."
        )

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

    async def _invite(self, chat_id, reusable=False):
        """/pasang: satu baris PowerShell berisi kode undangan sekali pakai. Komputer
        baru menukar kodenya di dashboard Hub (lewat domain/tunnel sendiri), mengambil
        kode gabung + isi .env bot, lalu menyambung KELUAR ke sini. Tanpa Tailscale.

        reusable=True (/pasang_tetap): satu tautan yang SAMA dipakai untuk beberapa
        komputer (tidak hangus setelah satu kali), berlaku sampai 30 hari atau sampai
        /pasang_tetap dikirim lagi (kode lama langsung tak berlaku saat itu)."""
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
        ttl = node.REUSABLE_INVITE_TTL if reusable else node.INVITE_TTL
        ok = await asyncio.to_thread(node.create_invite, code, env, ttl, 5, reusable)
        if not ok:
            await self.tg.send(
                chat_id,
                f"Node di komputer ini belum menjawab (mungkin baru menyala atau sedang "
                f"diperbarui). Coba {'/pasang_tetap' if reusable else '/pasang'} lagi sebentar lagi.",
            )
            return
        note = (
            "\n\n⚠️ Alamat ini dari link instan (Quick Tunnel) yang BERUBAH tiap It's Me "
            "nyala ulang. Komputer baru tersambung sekarang, tapi kalau Hub nyala ulang, "
            "undang lagi (/pasang) — atau pakai domain tetap (ITSME_PUBLIC_URL) agar permanen."
            if ephemeral else ""
        )
        if reusable:
            hari = node.REUSABLE_INVITE_TTL // 86400
            pasang_lagi = (
                f"Tautan ini bisa dipakai BERKALI-KALI untuk komputer mana pun sampai {hari} "
                "hari, atau sampai kamu kirim /pasang_tetap lagi (tautan lama langsung tak "
                "berlaku begitu yang baru dibuat — cuma satu tautan tetap yang aktif sekaligus). "
                "⚠️ Siapa pun yang punya tautan ini bisa ikut gabung ke jaringanmu selama masih "
                "berlaku — kirim hanya ke komputer yang kamu percaya, atau kirim /pasang_tetap "
                "lagi kapan saja untuk mencabut yang lama."
            )
        else:
            pasang_lagi = (f"Kode hanya bisa dipakai sekali dan hangus dalam "
                           f"{node.INVITE_TTL // 60} menit. Untuk komputer berikutnya, kirim /pasang lagi.")
        await self.tg.send(
            chat_id,
            "Pasang di komputer lain (TANPA Tailscale):\n"
            "1. Buka PowerShell di komputer itu.\n"
            "2. Tempel satu baris di pesan berikut, lalu Enter.\n"
            "3. Klik Ya saat Windows meminta izin Administrator.\n\n"
            "Komputer itu menyambung ke Hub lewat alamat publiknya. Token bot, pemilik, "
            "kunci otak, memori, dan kode jaringan disalin sendiri dari komputer ini. "
            "Tidak ada yang perlu diketik atau login.\n\n"
            f"{pasang_lagi}" + note,
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
            if config.TG_WEBHOOK and self._webhook_ok:
                # Pesan datang lewat webhook dashboard (/tg/<rahasia>), bukan getUpdates.
                # Ini juga mematikan poller lain yang memakai token yang sama.
                await asyncio.sleep(10)
                continue
            # Mode polling biasa - atau mode webhook yang BELUM/tidak terpasang: dulu bot
            # di sini tidur selamanya dan tuli sampai nyala ulang; sekarang tetap
            # mengambil pesan sampai _keep_webhook berhasil memasang webhooknya.
            updates = await self.tg.get_updates()
            for update in updates:
                self._spawn(self._dispatch(update))

    async def _keep_webhook(self, url):
        """Pasang webhook Telegram ke `url` (alamat tunnel) dan JAGA tetap terpasang.

        1. Tunggu DNS publik mengenal hostname baru.
        2. Pasang dengan IP tetap hasil DNS publik itu (_install_webhook): resolver
           Telegram sering belum mengenal alamat tunnel baru sampai beberapa menit padahal
           DNS publik sudah, dan setWebhook yang gagal malah MENGHAPUS webhook yang ada
           (29 Okt 2026: 6 dari 9 restart gagal total, bot tuli lebih dari 10 jam).
        3. Gagal pun jangan menyerah: coba lagi dengan jeda makin panjang. Selama belum
           terpasang, _poll memakai getUpdates, jadi bot tetap mendengar.
        4. Setelah terpasang, periksa berkala; pasang ulang kalau hilang/berganti atau
           Telegram gagal mengantar."""
        secret = tg_webhook_secret()
        self._webhook_ok = False
        await self._wait_dns_visible(url)
        jeda = (5, 10, 20, 30, 60, 120, 300)
        gagal = 0
        while True:
            if await self._install_webhook(url, secret):
                self._webhook_ok = True
                await self._watch_webhook(url, secret)
                self._webhook_ok = False
                gagal = 0
                continue
            await asyncio.sleep(jeda[min(gagal, len(jeda) - 1)])
            gagal += 1

    async def _install_webhook(self, url, secret):
        """Satu putaran pemasangan webhook; True kalau Telegram menerima.

        Utamakan IP tetap dari DNS publik kita sendiri (setWebhook ip_address), sehingga
        keberhasilannya tak tergantung resolver Telegram. Kalau itu gagal, atau DNS publik
        belum punya alamatnya, baru biarkan Telegram me-resolve sendiri (cara lama)."""
        ips = await self.tg.resolve_ipv4(urlparse(url).hostname)
        if ips and await self.tg.set_webhook(url, secret, jeda=(3, 6), ip_address=ips[0]):
            return True
        return await self.tg.set_webhook(url, secret)

    async def _wait_dns_visible(self, url, limit=90.0):
        host = urlparse(url).hostname
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if await self.tg.host_resolves(host):
                return True
            await asyncio.sleep(3)
        log.warning("DNS publik belum mengenal %s setelah %ds; webhook tetap dicoba", host, limit)
        return False

    async def _watch_webhook(self, url, secret, every=300):
        """Selesai (artinya: pasang ulang) kalau webhook hilang, berganti alamat, atau
        Telegram baru-baru ini gagal mengantar pesan ke alamat kita."""
        want = f"{url.rstrip('/')}/tg/{secret}"
        while True:
            await asyncio.sleep(every)
            info = await self.tg.get_webhook_info()
            if info is None:
                continue  # jaringan goyah; periksa lagi nanti
            if info.get("url") != want:
                log.warning("Webhook Telegram hilang/berganti (sekarang: %s); dipasang ulang",
                            urlparse(info.get("url") or "").hostname or "kosong")
                return
            err_at = info.get("last_error_date")
            if err_at and time.time() - err_at < 2 * every:
                log.warning("Telegram gagal mengantar ke webhook (%s); dipasang ulang",
                            info.get("last_error_message"))
                return

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
        keypool.set_alert(self._pool_alert)  # key NusaGate yang mati dikabarkan ke pemilik (satu pesan)
        dashboard.start(self)
        self._start_quick_tunnel()
        if node.installed():
            # Bukan lewat _spawn: tugas ini TIDAK PERNAH selesai (loop selamanya),
            # dan _spawn menandainya tugas yang harus ditunggu _wait_idle sebelum
            # memasang pembaruan - itu akan membuat setiap pembaruan menunggu
            # sampai _UPDATE_MAX_WAIT habis, setiap kali, sia-sia.
            self._seed_retrofit_task = asyncio.create_task(self._retrofit_bridge_seeds())
        if config.WHATSAPP:
            self.wa = self._new_wa()
            ok, why = self.wa.start()
            if not ok:
                self.wa = None
                if _wa_notice_due(why):  # sekali sehari per alasan, bukan tiap pembaruan
                    await self.tg.send(config.OWNER_ID,
                                       "⚠️ Bot WhatsApp diminta nyala (ITSME_WHATSAPP=1) tapi belum bisa "
                                       "menyala saat It's Me baru dinyalakan.\n\n" + _wa_start_problem(why))
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
            keypool.flush()  # hitungan pemakaian tiap key tersimpan walau menyala ulang
            if self.wa:
                self.wa.stop()
            if getattr(self, "quick_tunnel", None):
                self.quick_tunnel.stop()
            await self.tg.close()

    async def _diagnosa(self):
        """/diagnosa: laporan kesehatan KOMPUTER YANG MEMBALAS ini (bisa ada beberapa
        komputer yang menjalankan It's Me dengan token bot yang sama, jadi nama
        komputernya selalu disebut) - siapa, versi, mode Telegram, jaringan komputer,
        WhatsApp, dan peringatan terakhir di log (token disamarkan)."""
        lines = [f"🩺 Diagnosa - komputer: {config.DEVICE_NAME}"]
        sha = updater.installed_version()
        up = ""
        if self.started:
            menit = int((datetime.now() - self.started).total_seconds() // 60)
            up = f", menyala {menit // 60} jam {menit % 60} menit"
        lines.append(f"Versi It's Me: {sha[:7] if sha else '(tidak diketahui)'}{up}")
        lines.append(f"Otak: {brain.describe()}")

        lines += ["", "Telegram:"]
        if config.TG_WEBHOOK:
            lines.append("- Mode: webhook AKTIF" if self._webhook_ok else
                         "- Mode: webhook BELUM terpasang (sementara memakai polling)")
            info = await self.tg.get_webhook_info()
            if info is not None:
                host = urlparse(info.get("url") or "").hostname or "(kosong)"
                lines.append(f"- Webhook di Telegram: {host}, antrean {info.get('pending_update_count', 0)}")
                if info.get("last_error_message"):
                    lines.append(f"- Galat pengantaran terakhir: {info['last_error_message']}")
        else:
            lines.append("- Mode: polling (getUpdates)")

        lines += ["", "Jaringan komputer (It's Me Node):"]
        if not node.installed():
            lines.append("- belum terpasang")
        else:
            st = await asyncio.to_thread(node.status)
            if st is None:
                lines.append("- node tidak menjawab (mungkin baru menyala)")
            else:
                nodes = st.get("nodes") or []
                itu_saya = st.get("leader") == st.get("me")
                lines.append(f"- {len(nodes)} komputer hidup; pemimpin: {node.leader_name(st) or '?'}"
                             f"{' (INI)' if itu_saya else ''}")
                for n in nodes:
                    lines.append(f"  - {n.get('name')} @ {n.get('addr')}{' [pemimpin]' if n.get('leader') else ''}")
                if len(nodes) <= 1:
                    lines.append("  Hanya komputer ini yang terlihat. Kalau ada komputer lain yang "
                                 "menjalankan It's Me, mereka belum saling kenal.")

        lines += ["", "WhatsApp:"]
        node_path, has_modules = wa_prerequisites()
        lines.append(f"- Bot: menyala - {self.wa.status_text()}" if self.wa else
                     ("- Bot: GAGAL menyala" if config.WHATSAPP else "- Bot: dimatikan (/wa <nomor> untuk menyalakan)"))
        if self.wa:
            if self._wa_last_in:
                menit = int((datetime.now() - self._wa_last_in).total_seconds() // 60)
                lines.append(f"- Pesan WhatsApp terakhir masuk: {self._wa_last_in:%H:%M} ({menit} menit lalu)")
            else:
                lines.append("- Pesan WhatsApp masuk: belum ada sejak It's Me menyala "
                             "(tes: kirim \"halo\" ke chat \"Pesan ke diri sendiri\")")
        lines.append(f"- Node.js: {node_path or 'TIDAK ditemukan'}")
        lines.append(f"- Paket WhatsApp (npm install): {'terpasang' if has_modules else 'BELUM'}")
        lines.append(f"- Mode tamu (orang lain dijawab AI): {'AKTIF' if config.WA_OPEN else 'mati'}"
                     f" · diblokir {len(config.WA_BLOCK)} nomor (/wa_tamu)")
        rows = keypool.snapshot()
        if rows:
            siap = sum(1 for r in rows if r["status"] in ("siap", "sibuk"))
            lines.append(f"- Key NusaGate/DeepSeek: {len(rows)} key, {siap} siap (/kunci_daftar)")

        warns = [x for x in dashboard._logs(400).splitlines() if " WARNING " in x or " ERROR " in x]
        lines += ["", "Peringatan/galat terakhir di log:"]
        lines += [f"- {x[:170]}" for x in warns[-5:]] or ["- (tidak ada)"]
        return "\n".join(lines)

    async def _retrofit_bridge_seeds(self):
        """Komputer yang bergabung SEBELUM perbaikan "seed" (lihat node.py) tidak
        punya titik awal gosip sama sekali, jadi diam-diam tidak ikut gagal-pindah-
        pemimpin/sinkron memori. Titipkan alamat node ini ke tiap komputer jembatan
        secara berkala, supaya ikut diperbaiki tanpa pemiliknya harus /pasang ulang."""
        while True:
            try:
                await asyncio.to_thread(node.push_seed_to_bridges)
            except Exception:  # noqa: BLE001 - latar belakang, jangan ganggu bot utama
                log.exception("gagal menitipkan seed ke komputer jembatan")
            await asyncio.sleep(30)

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
                def keep():
                    # Bukan lewat _spawn: tugas ini tak pernah selesai (menjaga webhook
                    # selamanya) dan _wait_idle menunggu self._tasks kosong sebelum pembaruan.
                    if self._webhook_task:
                        self._webhook_task.cancel()
                    self._webhook_task = asyncio.ensure_future(self._keep_webhook(url))

                try:
                    self.loop.call_soon_threadsafe(keep)
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
