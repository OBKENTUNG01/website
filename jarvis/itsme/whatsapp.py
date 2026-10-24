"""Bot WhatsApp lewat jembatan Node (itsme/whatsapp/wa.js).

Masuk: tiap pesan pemilik dibaca dari stdout node, lalu dipanggil handler(jid, text)
di thread pembaca. Keluar: send(jid, text) menulis satu baris JSON ke stdin node.
Login pertama: QR muncul di jendela It's Me; sesi disimpan di data/whatsapp/.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import threading

from . import config

log = logging.getLogger("itsme")
DIR = os.path.join(os.path.dirname(__file__), "whatsapp")

class WhatsApp:
    def __init__(self, on_message, on_qr=None):
        self._on_message = on_message
        self._on_qr = on_qr
        self._proc = None
        self._lock = threading.Lock()

    def start(self):
        """Nyalakan node. False kalau tidak bisa (lihat log)."""
        node = shutil.which("node")
        if not node:
            log.warning("WhatsApp diminta tapi Node.js tidak ada di PATH (pasang dari nodejs.org).")
            return False
        if not os.path.isdir(os.path.join(DIR, "node_modules")):
            log.warning("WhatsApp: jalankan dulu 'npm install' di %s", DIR)
            return False
        env = dict(os.environ,
                   WA_OWNER=str(config.WA_OWNER or ""),
                   WA_AUTH=os.path.join(config.DATA_DIR, "whatsapp"))
        # ponytail: node dianggap hidup sampai keluar; tanpa auto-restart dari Python
        # (wa.js sudah menyambung ulang sendiri). Perlu restart dari Python kalau
        # prosesnya mati total — tambahkan loop di sini kalau itu sering terjadi.
        self._proc = subprocess.Popen(
            [node, "wa.js"], cwd=DIR, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def _read(self):
        for line in self._proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("qr"):
                if self._on_qr:
                    try:
                        self._on_qr(base64.b64decode(msg["qr"]))
                    except Exception:  # noqa: BLE001 - QR rusak tidak boleh mematikan pembaca
                        log.exception("gagal memproses QR WhatsApp")
                continue
            try:
                self._on_message(msg.get("jid"), msg.get("text") or "")
            except Exception:  # noqa: BLE001 - satu pesan gagal tidak boleh mematikan pembaca
                log.exception("gagal memproses pesan WhatsApp")
        log.warning("WhatsApp: jembatan node berhenti (keluar kode %s).", self._proc.poll())

    def send(self, jid, text):
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                log.warning("WhatsApp tidak tersambung; pesan tidak terkirim.")
                return False
            try:
                self._proc.stdin.write(json.dumps({"jid": jid, "text": text}) + "\n")
                self._proc.stdin.flush()
                return True
            except (OSError, ValueError):
                log.warning("gagal menulis ke jembatan WhatsApp", exc_info=True)
                return False

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
