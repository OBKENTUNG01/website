"""Entry point It's Me — bot Telegram.

Jalankan: python -m itsme.main   (dari folder jarvis/)

Fitur utama:
- Hanya menjawab pemilik (OWNER_TELEGRAM_ID).
- Multitasking: tiap pesan ditangani sebagai task async; alat tugas_latar
  menjalankan pekerjaan paralel yang hasilnya dikirim terpisah.
- Konfirmasi aksi berisiko lewat tombol inline.
"""

import asyncio
import uuid

from . import config
from .brain import run_agent
from .memory import Memory
from .telegram import Telegram, confirm_keyboard

HELP = (
    "Halo, It's Me siap. 🫡\n\n"
    "Kirim saja apa yang kamu mau — tanya, minta cari info terbaru, edit & "
    "deploy website, atau beberapa tugas sekaligus.\n\n"
    "Perintah:\n"
    "/help — bantuan ini\n"
    "/reset — mulai percakapan baru (lupakan konteks obrolan, memori tetap)\n"
    "/id — tampilkan user id Telegram-mu"
)


class App:
    def __init__(self):
        self.tg = Telegram(config.TELEGRAM_TOKEN)
        self.memory = Memory(config.MEMORY_FILE)
        self.loop = None
        self.histories = {}  # chat_id -> list[message]
        self.chat_locks = {}  # chat_id -> asyncio.Lock (jaga konsistensi riwayat)
        self.pending = {}  # confirm id -> asyncio.Future

    # --- util ---------------------------------------------------------------
    def _lock(self, chat_id):
        if chat_id not in self.chat_locks:
            self.chat_locks[chat_id] = asyncio.Lock()
        return self.chat_locks[chat_id]

    def _ctx_for(self, chat_id):
        return {
            "website_dir": config.WEBSITE_REPO_DIR or None,
            "memory": self.memory,
            "confirm": lambda text: self._confirm_blocking(chat_id, text),
            "schedule_background": lambda instr: self._schedule_background(
                chat_id, instr
            ),
        }

    # --- konfirmasi (dari thread worker ke event loop) ---------------------
    def _confirm_blocking(self, chat_id, text):
        fut = asyncio.run_coroutine_threadsafe(
            self._ask_confirm(chat_id, text), self.loop
        )
        try:
            return fut.result()
        except Exception:
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
        asyncio.run_coroutine_threadsafe(
            self._run_background(chat_id, instruksi), self.loop
        )
        return f"Oke, aku kerjakan di latar: {instruksi}"

    async def _run_background(self, chat_id, instruksi):
        await self.tg.send(chat_id, f"⏳ Mulai di latar: {instruksi}")
        messages = [{"role": "user", "content": instruksi}]
        ctx = self._ctx_for(chat_id)
        reply = await asyncio.to_thread(run_agent, messages, ctx)
        await self.tg.send(chat_id, f"✅ Selesai: {instruksi}\n\n{reply}")

    # --- penanganan pesan ---------------------------------------------------
    async def _handle_message(self, message):
        chat_id = message["chat"]["id"]
        user_id = message.get("from", {}).get("id")
        text = message.get("text", "")

        if user_id != config.OWNER_ID:
            # Bukan pemilik — abaikan diam-diam (keamanan).
            return

        if text.startswith("/start") or text.startswith("/help"):
            await self.tg.send(chat_id, HELP)
            return
        if text.startswith("/id"):
            await self.tg.send(chat_id, f"User id kamu: {user_id}")
            return
        if text.startswith("/reset"):
            self.histories.pop(chat_id, None)
            await self.tg.send(chat_id, "Oke, konteks obrolan direset. Memori tetap.")
            return
        if not text.strip():
            return

        async with self._lock(chat_id):
            history = self.histories.setdefault(chat_id, [])
            history.append({"role": "user", "content": text})
            await self.tg.send_chat_action(chat_id, "typing")
            ctx = self._ctx_for(chat_id)
            reply = await asyncio.to_thread(run_agent, history, ctx)
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
        if future and not future.done():
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
        except Exception as exc:  # noqa: BLE001
            print(f"[itsme] error dispatch: {exc}")

    # --- loop utama ---------------------------------------------------------
    async def run(self):
        self.loop = asyncio.get_running_loop()
        print(f"[itsme] {config.NAME} jalan. Model={config.MODEL} effort={config.EFFORT}")
        try:
            while True:
                updates = await self.tg.get_updates()
                for update in updates:
                    asyncio.create_task(self._dispatch(update))
        finally:
            await self.tg.close()


def main():
    problems = config.check()
    if problems:
        print("Konfigurasi belum lengkap:")
        for p in problems:
            print("  - " + p)
        print("\nSalin .env.example menjadi .env lalu isi nilainya.")
        return
    try:
        asyncio.run(App().run())
    except KeyboardInterrupt:
        print("\n[itsme] berhenti.")


if __name__ == "__main__":
    main()
