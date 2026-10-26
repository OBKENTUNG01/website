"""Klien Telegram kecil (async) dengan httpx.

Cukup untuk It's Me: long-polling getUpdates, kirim pesan (dipotong otomatis
kalau panjang), tombol konfirmasi inline, dan menjawab callback.
"""

import asyncio
import json
import logging
import os
import re
import time

import httpx

log = logging.getLogger("itsme")

API = "https://api.telegram.org"
MAX_LEN = 4000  # batas aman di bawah 4096 milik Telegram
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # dilarang di nama berkas Windows


def _safe_name(name):
    """Nama berkas kiriman yang aman disimpan: tanpa folder, tanpa karakter
    terlarang Windows, dan tidak terlalu panjang (jalur Windows maks. 260)."""
    stem, ext = os.path.splitext(_BAD_NAME_CHARS.sub("_", os.path.basename(name)))
    return (stem[:100] + ext[:10]) or "berkas"


class Telegram:
    def __init__(self, token, api=API):
        self._base = f"{api.rstrip('/')}/bot{token}"
        self._file_base = f"{api.rstrip('/')}/file/bot{token}"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(70.0))
        self._offset = None
        # Hanya satu permintaan getUpdates boleh berjalan; dua sekaligus membuat
        # Telegram memutus yang lama ("Conflict: terminated by other getUpdates").
        self._poll_lock = asyncio.Lock()
        self._conflict_logged_at = 0.0
        self._conflicts = 0

    async def close(self):
        await self._client.aclose()

    async def get_updates(self):
        params = {
            "timeout": 50,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if self._offset is not None:
            params["offset"] = self._offset
        async with self._poll_lock:
            try:
                r = await self._client.get(f"{self._base}/getUpdates", params=params)
                data = r.json()
            except (httpx.HTTPError, ValueError):
                # jaringan goyah — jeda sebentar lalu coba lagi di loop pemanggil
                await asyncio.sleep(3)
                return []
        if not data.get("ok"):
            desc = str(data.get("description", data))
            if "Conflict" in desc:
                # Ada poller lain memakai token ini (perangkat/jendela lain).
                # Dicatat tiap 5 menit saja supaya log tidak tenggelam.
                self._conflicts += 1
                now = time.monotonic()
                if now - self._conflict_logged_at >= 300:
                    self._conflict_logged_at = now
                    log.warning(
                        "Telegram menolak: %s (kejadian ke-%d). Token bot ini dipakai "
                        "proses/perangkat LAIN: tutup It's Me di sana, atau pakai token "
                        "bot berbeda.", desc, self._conflicts,
                    )
                await asyncio.sleep(15)
                return []
            # mis. token salah — beri jeda supaya tidak membanjiri API
            log.warning("Telegram menolak: %s", desc)
            await asyncio.sleep(5)
            return []
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    async def ack_updates(self):
        """Tandai update yang sudah diambil sebagai selesai di Telegram, tanpa
        mengambil yang baru — yang baru dikirim ulang setelah menyala lagi."""
        if self._offset is None:
            return
        async with self._poll_lock:  # jangan menabrak long-poll yang sedang jalan
            try:
                await self._client.get(
                    f"{self._base}/getUpdates",
                    params={"offset": self._offset, "timeout": 0, "limit": 1},
                )
            except httpx.HTTPError:
                pass

    async def send(self, chat_id, text, reply_markup=None):
        text = text or "…"
        chunks = [text[i : i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]
        last = None
        for idx, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            # tombol hanya ditempel di potongan terakhir
            if reply_markup and idx == len(chunks) - 1:
                payload["reply_markup"] = json.dumps(reply_markup)
            try:
                r = await self._client.post(f"{self._base}/sendMessage", json=payload)
                last = r.json()
            except httpx.HTTPError:
                last = None
        return last

    async def send_photo(self, chat_id, data, filename="layar.png", caption=None):
        """Kirim gambar (bytes) sebagai foto. True kalau berhasil."""
        form = {"chat_id": str(chat_id)}
        if caption:
            form["caption"] = caption[:1024]
        try:
            r = await self._client.post(
                f"{self._base}/sendPhoto", data=form,
                files={"photo": (filename, data, "image/png")},
            )
            return bool(r.json().get("ok"))
        except (httpx.HTTPError, ValueError):
            return False

    async def download_file(self, file_id, dest_dir, name=None):
        """Unduh berkas kiriman pemilik ke dest_dir; kembalikan jalurnya, atau
        None kalau gagal (mis. lebih dari 20 MB, batas bot Telegram)."""
        try:
            r = await self._client.get(f"{self._base}/getFile", params={"file_id": file_id})
            info = r.json()
            if not info.get("ok"):
                return None
            remote = info["result"]["file_path"]
            r = await self._client.get(f"{self._file_base}/{remote}")
            if r.status_code != 200:
                return None
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        path = os.path.join(dest_dir, f"{int(time.time())}_{_safe_name(name or remote)}")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(r.content)
        except OSError:
            log.warning("Berkas kiriman gagal disimpan ke %s", path, exc_info=True)
            return None
        return path

    async def send_chat_action(self, chat_id, action="typing"):
        try:
            await self._client.post(
                f"{self._base}/sendChatAction",
                json={"chat_id": chat_id, "action": action},
            )
        except httpx.HTTPError:
            pass

    async def answer_callback(self, callback_id, text=None):
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            await self._client.post(f"{self._base}/answerCallbackQuery", json=payload)
        except httpx.HTTPError:
            pass

    async def delete_message(self, chat_id, message_id):
        """Hapus pesan; True kalau berhasil. Di chat pribadi bot boleh menghapus
        pesan dari lawan bicaranya (selama umurnya belum 48 jam)."""
        try:
            r = await self._client.post(
                f"{self._base}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message_id},
            )
            return bool(r.json().get("ok"))
        except (httpx.HTTPError, ValueError):
            return False

    async def edit_markup(self, chat_id, message_id, reply_markup=None):
        payload = {"chat_id": chat_id, "message_id": message_id}
        payload["reply_markup"] = json.dumps(reply_markup or {"inline_keyboard": []})
        try:
            await self._client.post(
                f"{self._base}/editMessageReplyMarkup", json=payload
            )
        except httpx.HTTPError:
            pass


def confirm_keyboard(cid):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Ya, lakukan", "callback_data": f"ok:{cid}"},
                {"text": "❌ Batal", "callback_data": f"no:{cid}"},
            ]
        ]
    }
