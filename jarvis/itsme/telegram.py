"""Klien Telegram kecil (async) dengan httpx.

Cukup untuk It's Me: long-polling getUpdates, kirim pesan (dipotong otomatis
kalau panjang), tombol konfirmasi inline, dan menjawab callback.
"""

import asyncio
import json
import logging

import httpx

log = logging.getLogger("itsme")

API = "https://api.telegram.org"
MAX_LEN = 4000  # batas aman di bawah 4096 milik Telegram


class Telegram:
    def __init__(self, token, api=API):
        self._base = f"{api.rstrip('/')}/bot{token}"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(70.0))
        self._offset = None

    async def close(self):
        await self._client.aclose()

    async def get_updates(self):
        params = {
            "timeout": 50,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            r = await self._client.get(f"{self._base}/getUpdates", params=params)
            data = r.json()
        except (httpx.HTTPError, ValueError):
            # jaringan goyah — jeda sebentar lalu coba lagi di loop pemanggil
            await asyncio.sleep(3)
            return []
        if not data.get("ok"):
            # mis. token salah — beri jeda supaya tidak membanjiri API
            log.warning("Telegram menolak: %s", data.get("description", data))
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
