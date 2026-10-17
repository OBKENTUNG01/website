"""Uji ujung-ke-ujung otak /claudepc: Claude Code sungguhan + API Anthropic tiruan.

Tidak memakai akun atau kuota Claude: Claude Code diarahkan ke server tiruan di
komputer ini yang menyuruhnya memakai alat, lalu dicek apakah aturan It's Me
(tombol konfirmasi, penolakan berkas rahasia, lanjutan sesi) benar-benar
berlaku. Butuh claude-agent-sdk. Jalankan dari folder jarvis/:
    python tests/e2e_claudepc.py
Bukan bagian `unittest discover` karena menjalankan program Claude Code asli.
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Variabel dari sesi Claude lain (mis. Claude Code yang menjalankan uji ini)
# tidak boleh ikut ke Claude Code yang diuji.
for _name in [n for n in os.environ if n.startswith(("CLAUDE", "ANTHROPIC"))]:
    del os.environ[_name]

SCENARIO = {"tool": None}  # (nama alat, input) yang diminta "model" tiruan
REQUESTS = []


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _message(blocks, stop):
    start = {"type": "message_start", "message": {
        "id": "msg_x", "type": "message", "role": "assistant", "model": "claude-tiruan",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1}}}
    events = [start]
    for i, block in enumerate(blocks):
        if block["type"] == "text":
            events += [
                {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": block["text"]}},
            ]
        else:
            events += [
                {"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}},
                {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}},
            ]
        events.append({"type": "content_block_stop", "index": i})
    events += [
        {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    return events


class FakeAnthropic(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, b"{}")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if "count_tokens" in self.path:
            return self._send(200, b'{"input_tokens": 10}')
        if "/v1/messages" not in self.path:
            return self._send(200, b"{}")
        tools = [t.get("name") for t in body.get("tools") or []]
        # Claude Code bisa menyisipkan pesan "system" sesudah hasil alat; abaikan.
        convo = [m for m in body.get("messages") or [] if m.get("role") in ("user", "assistant")] or [{}]
        last = convo[-1].get("content")
        results = [b for b in last if isinstance(b, dict) and b.get("type") == "tool_result"] if isinstance(last, list) else []
        REQUESTS.append({"tools": tools, "messages": body.get("messages"), "tool_results": results})
        # Selesai kalau hasil untuk alat yang terakhir kita minta sudah ada.
        answered = {
            b.get("tool_use_id"): b
            for m in convo if isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
        }
        issued = SCENARIO.get("issued")
        if issued and issued in answered:
            content = answered[issued].get("content")
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            blocks, stop = [{"type": "text", "text": "SELESAI: " + text[:300]}], "end_turn"
        elif SCENARIO["tool"] and tools:
            name, inp = SCENARIO["tool"]
            if name == "SHELL":
                name = "Bash" if "Bash" in tools else "PowerShell"
            SCENARIO["issued"] = f"toolu_{len(REQUESTS)}"
            blocks, stop = [{"type": "tool_use", "id": SCENARIO["issued"], "name": name, "input": inp}], "tool_use"
        else:
            blocks, stop = [{"type": "text", "text": "ok"}], "end_turn"
        if body.get("stream"):
            return self._send(200, _sse(_message(blocks, stop)), "text/event-stream")
        message = {"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-tiruan",
                   "content": [dict(b) for b in blocks], "stop_reason": stop, "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 5}}
        self._send(200, json.dumps(message).encode())


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnthropic)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    from itsme import brain_claude_code as cc
    from itsme import config

    site = tempfile.mkdtemp(prefix="itsme-site-")
    with open(os.path.join(site, ".env"), "w") as f:
        f.write("RAHASIA=jangan-dibaca\n")
    data = tempfile.mkdtemp(prefix="itsme-data-")
    confirm = mock.Mock(return_value=False)
    ctx = {"confirm": confirm, "website_dir": site, "memory": mock.Mock(text=lambda: "")}
    failures = []

    def check(label, ok, detail=""):
        print(("[OK]    " if ok else "[GAGAL] ") + label + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(label)
            if os.environ.get("E2E_DEBUG") and REQUESTS:
                for m in REQUESTS[int(os.environ.get("E2E_REQ", "-1"))]["messages"][-int(os.environ.get("E2E_N", "4")):]:
                    content = m.get("content")
                    if isinstance(content, list):
                        content = [{k: (str(v)[:160]) for k, v in b.items() if k in ("type", "content", "is_error", "text", "name")} for b in content]
                    print("      ", m.get("role"), str(content)[:400])

    async def ask(messages):
        # Persis seperti bot: otak dijalankan di thread terpisah.
        return await asyncio.to_thread(cc.run_agent, messages, ctx)

    with mock.patch.object(config, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "e" * 40), \
         mock.patch.object(config, "ANTHROPIC_API_KEY", "sk-ant-api03-tidak-boleh-dipakai"), \
         mock.patch.object(config, "DATA_DIR", data):
        history = [{"role": "user", "content": "cek status git"}]
        SCENARIO["issued"] = None
        SCENARIO["tool"] = ("SHELL", {"command": "git status", "description": "lihat status"})
        reply = asyncio.run(ask(history))
        check("perintah yang hanya melihat langsung jalan tanpa tombol", confirm.call_count == 0 and reply.startswith("SELESAI"), reply[:200])
        check("hasil dicatat dengan id sesi", bool(history[-1].get("session_id")), str(history[-1]))

        history.append({"role": "user", "content": "hapus berkas"})
        SCENARIO["issued"] = None
        SCENARIO["tool"] = ("SHELL", {"command": "Remove-Item penting.txt", "description": "hapus berkas"})
        reply = asyncio.run(ask(history))
        asked = confirm.call_args[0][0] if confirm.call_args else ""
        check("perintah lain menampilkan tombol konfirmasi berisi perintahnya", "Remove-Item penting.txt" in asked, asked)
        check("ditolak pemilik -> tidak dijalankan", "Dibatalkan oleh pemilik" in reply, reply[:200])
        sent = json.dumps(REQUESTS[-2]["messages"], ensure_ascii=False)
        check("giliran kedua melanjutkan sesi yang sama", "cek status git" in sent, sent[:200])

        confirm.reset_mock()
        history.append({"role": "user", "content": "baca .env"})
        SCENARIO["issued"] = None
        SCENARIO["tool"] = ("Read", {"file_path": os.path.join(site, ".env")})
        reply = asyncio.run(ask(history))
        check("berkas rahasia ditolak tanpa bertanya", confirm.call_count == 0 and "rahasia" in reply and "jangan-dibaca" not in reply, reply[:200])

        confirm.return_value = True
        history.append({"role": "user", "content": "tulis berkas"})
        target = os.path.join(site, "halo.txt")
        SCENARIO["issued"] = None
        SCENARIO["tool"] = ("Write", {"file_path": target, "content": "halo dari claude"})
        reply = asyncio.run(ask(history))
        check("tulis berkas setelah tombol Ya", os.path.exists(target) and "halo dari claude" in (confirm.call_args[0][0] if confirm.call_args else ""), reply[:200])

    check("Claude Code memakai folder sesi It's Me sendiri", os.path.isdir(os.path.join(data, "claude")))
    server.shutdown()
    print("\nSEMUA LULUS" if not failures else f"\n{len(failures)} GAGAL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
