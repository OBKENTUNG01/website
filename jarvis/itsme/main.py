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
import atexit
import logging
import os
import queue
import re
import sys
import uuid
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

from . import brain, config, updater
from .brain import run_agent
from .memory import Memory
from .telegram import Telegram, confirm_keyboard

log = logging.getLogger("itsme")

HELP = (
    "Halo, It's Me siap. 🫡\n\n"
    "Kirim saja apa yang kamu mau — tanya, minta cari info terbaru, edit & "
    "deploy website, atau beberapa tugas sekaligus.\n\n"
    "Perintah:\n"
    "/help — bantuan ini\n"
    "/otak — lihat otak yang sedang dipakai\n"
    "/cloud — pakai otak cloud (OpenRouter, lebih pintar)\n"
    "/claude — pakai otak Claude (Anthropic API, paling pintar)\n"
    "/lokal — pakai otak lokal (gratis, privat)\n"
    "/memori — lihat isi memori jangka panjang\n"
    "/hapus_memori — kosongkan memori\n"
    "/reset — mulai percakapan baru (lupakan konteks obrolan, memori tetap)\n"
    "/id — tampilkan user id Telegram-mu"
)

# Perintah Telegram untuk mengganti otak.
_BRAIN_COMMANDS = {"/lokal": "ollama", "/cloud": "openrouter", "/claude": "claude"}
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
_HARI = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")


def _stamped(text):
    """Pesan pemilik diawali waktu kirimnya, supaya otak tahu jam & tanggal
    tanpa system prompt yang berubah-ubah (lihat persona.py)."""
    now = datetime.now()
    return f"[{_HARI[now.weekday()]}, {now:%d-%m-%Y %H.%M}] {text}"


class App:
    def __init__(self):
        self.tg = Telegram(config.TELEGRAM_TOKEN, config.TELEGRAM_API_URL)
        self.memory = Memory(config.MEMORY_FILE)
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
        self._update_warning = None  # masalah pembaruan yang sudah dilaporkan

    # --- util ---------------------------------------------------------------
    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _brain_status(self):
        lines = [f"Otak sekarang: {brain.describe()}", ""]
        lines.append(f"/lokal — {brain.describe('ollama')}")
        if config.OPENROUTER_API_KEY:
            lines.append(f"/cloud — {brain.describe('openrouter')}")
        else:
            lines.append("/cloud — belum aktif (jalankan setup lagi dan isi API key OpenRouter)")
        if config.ANTHROPIC_API_KEY:
            lines.append(f"/claude — {brain.describe('claude')}")
        else:
            lines.append("/claude — belum aktif (isi ANTHROPIC_API_KEY di .env lalu nyalakan ulang)")
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

    def _lock(self, chat_id):
        if chat_id not in self.chat_locks:
            self.chat_locks[chat_id] = asyncio.Lock()
        return self.chat_locks[chat_id]

    def _seen(self, chat_id):
        seen = self.seen_urls.setdefault(chat_id, set())
        if len(seen) > 1000:  # jangan tumbuh tanpa batas
            seen.clear()
        return seen

    def _ctx_for(self, chat_id, in_background=False):
        return {
            "website_dir": config.WEBSITE_REPO_DIR or None,
            "memory": self.memory,
            "confirm": lambda text: self._confirm_blocking(chat_id, text),
            "schedule_background": lambda instr: self._schedule_background(
                chat_id, instr
            ),
            "seen_urls": self._seen(chat_id),
            "in_background": in_background,
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
        typing = asyncio.create_task(self._keep_typing(chat_id))
        try:
            return await asyncio.to_thread(run_agent, messages, ctx)
        except Exception as exc:  # noqa: BLE001 - pemilik harus selalu dapat jawaban
            log.exception("otak gagal menjawab")
            return (
                f"⚠️ Maaf, terjadi error saat memproses pesanmu: {type(exc).__name__}: {exc}\n"
                f"(Detailnya tercatat di {config.LOG_FILE})"
            )
        finally:
            typing.cancel()

    # --- konfirmasi (dari thread worker ke event loop) ---------------------
    def _confirm_blocking(self, chat_id, text):
        fut = asyncio.run_coroutine_threadsafe(
            self._ask_confirm(chat_id, text), self.loop
        )
        try:
            return fut.result()
        except Exception:  # noqa: BLE001
            log.exception("konfirmasi gagal")
            return False

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
        await self.tg.send(chat_id, f"⏳ Mulai di latar: {instruksi}")
        messages = [{"role": "user", "content": _stamped(instruksi)}]
        ctx = self._ctx_for(chat_id, in_background=True)
        reply = await self._think(chat_id, messages, ctx)
        await self.tg.send(chat_id, f"✅ Tugas latar selesai: {instruksi}\n\n{reply}")

    # --- penanganan pesan ---------------------------------------------------
    async def _handle_message(self, message):
        chat_id = message["chat"]["id"]
        user_id = message.get("from", {}).get("id")
        text = message.get("text") or ""

        if text.startswith("/id"):
            # Aman dijawab ke siapa pun: hanya memberi tahu id pengirim sendiri.
            await self.tg.send(chat_id, f"User id kamu: {user_id}")
            return

        if user_id != config.OWNER_ID:
            # Bukan pemilik — abaikan diam-diam (keamanan).
            return

        if not text.strip():
            if any(kind in message for kind in _NON_TEXT):
                await self.tg.send(
                    chat_id,
                    "Maaf, untuk sekarang aku hanya bisa membaca pesan teks "
                    "(belum bisa mendengar suara atau melihat gambar). Ketik saja pesanmu, ya.",
                )
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
        target = next((b for cmd, b in _BRAIN_COMMANDS.items() if text.startswith(cmd)), None)
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
            history.append({"role": "user", "content": _stamped(text)})
            ctx = self._ctx_for(chat_id)
            reply = await self._think(chat_id, history, ctx)
            await self.tg.send(chat_id, reply)

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
            updates = await self.tg.get_updates()
            for update in updates:
                self._spawn(self._dispatch(update))

    async def _serve_until_update(self):
        """Layani Telegram sampai ada pembaruan dari GitHub, lalu kembalikan
        pembaruan itu setelah pekerjaan yang sedang jalan selesai."""
        poll = asyncio.create_task(self._poll())
        watch = asyncio.create_task(self._watch_updates()) if config.AUTO_UPDATE else None
        try:
            await asyncio.wait({t for t in (poll, watch) if t}, return_when=asyncio.FIRST_COMPLETED)
            if poll.done():
                poll.result()  # error saat mengambil pesan diteruskan
            update = watch.result()
            log.info("Pembaruan ditemukan: %s", update.describe())
            await self._wait_idle(_UPDATE_MAX_WAIT)
            # Tidak ada await antara pemeriksaan terakhir di atas dan
            # poll.cancel() di bawah, jadi tidak ada pesan baru yang terambil
            # lalu terputus di tengah jalan; pesan yang belum diambil dikirim
            # ulang Telegram setelah It's Me menyala lagi.
            return update
        finally:
            poll.cancel()
            if watch:
                watch.cancel()
            await asyncio.gather(*(t for t in (poll, watch) if t), return_exceptions=True)

    async def run(self):
        """Layani Telegram. Kembalikan EXIT_UPDATED setelah pembaruan terpasang."""
        self.loop = asyncio.get_running_loop()
        log.info("%s jalan. Otak: %s", config.NAME, brain.describe())
        try:
            notice = updater.pop_notice()
            if notice:
                await self.tg.send(config.OWNER_ID, notice)
            while True:
                update = await self._serve_until_update()
                if await self._install(update):
                    return EXIT_UPDATED
        finally:
            await self.tg.close()


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


def _restart(lock, listener):
    """Nyalakan ulang It's Me dengan kode yang baru dipasang.

    Di macOS/Linux proses ini langsung diganti (PID tetap sama, jadi systemd,
    launchd, dan itsme.pid tetap cocok). Di Windows exec tidak mengganti
    proses: It's Me keluar dengan EXIT_UPDATED dan start-itsme.cmd langsung
    menyalakannya lagi.
    """
    if os.name == "nt":
        print("[itsme] Pembaruan terpasang, menyala ulang...")
        return
    if listener:
        listener.stop()  # atexit tidak dijalankan oleh exec
    lock.close()
    os.chdir(updater.APP_DIR)
    os.execv(sys.executable, [sys.executable, "-m", "itsme.main"])


def main():
    _disable_quick_edit()
    listener = _setup_logging()
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
