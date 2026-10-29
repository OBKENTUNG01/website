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
import time

from . import config

log = logging.getLogger("itsme")
DIR = os.path.join(os.path.dirname(__file__), "whatsapp")
# Jembatan yang mati dicoba nyalakan lagi, tapi berhenti kalau terus gagal.
_RETRY_WINDOW = 60
_RETRY_MAX = 5

class WhatsApp:
    def __init__(self, on_message, on_qr=None, on_giveup=None):
        self._on_message = on_message
        self._on_qr = on_qr
        self._on_giveup = on_giveup
        self._proc = None
        self._lock = threading.Lock()
        self._stop = False
        self._tries = 0
        self._last = 0.0

    def start(self):
        """Nyalakan node. False kalau tidak bisa (lihat log)."""
        node = shutil.which("node")
        if not node:
            log.warning("WhatsApp diminta tapi Node.js tidak ada di PATH (pasang dari nodejs.org).")
            return False
        if not os.path.isdir(os.path.join(DIR, "node_modules")):
            log.warning("WhatsApp: jalankan dulu 'npm install' di %s", DIR)
            return False
        self._stop = False
        _kill_orphans()
        env = dict(os.environ,
                   WA_OWNER=str(config.WA_OWNER or ""),
                   WA_GUESTS=",".join(config.WA_GUESTS),
                   WA_OPEN="1" if config.WA_OPEN else "0",
                   WA_AUTH=os.path.join(config.DATA_DIR, "whatsapp"),
                   WA_FILES=config.WA_FILES_DIR,
                   WA_MAX_MB=str(config.WA_MAX_MB))
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
                self._on_message(msg.get("jid"), msg.get("text") or "",
                                 bool(msg.get("guest")), msg.get("file"))
            except Exception:  # noqa: BLE001 - satu pesan gagal tidak boleh mematikan pembaca
                log.exception("gagal memproses pesan WhatsApp")
        code = self._proc.poll()
        log.warning("WhatsApp: jembatan node berhenti (keluar kode %s).", code)
        if self._stop:
            return
        # ponytail: nyalakan ulang sendiri, tapi berhenti setelah _RETRY_MAX kali
        # dalam semenit supaya sesi yang dicabut dari HP tidak jadi spam. Pulih:
        # hapus data/whatsapp lalu nyalakan ulang It's Me (QR baru terkirim).
        now = time.time()
        self._tries = self._tries + 1 if now - self._last < _RETRY_WINDOW else 1
        self._last = now
        if self._tries > _RETRY_MAX:
            log.error("WhatsApp: gagal menyambung %dx; bot WA dimatikan.", _RETRY_MAX)
            if self._on_giveup:
                try:
                    self._on_giveup()
                except Exception:  # noqa: BLE001 - pemberitahuan gagal tidak boleh merusak thread pembaca
                    log.exception("gagal memberitahu bahwa jembatan WhatsApp menyerah")
            return
        time.sleep(3)
        self.start()

    def _send_obj(self, obj):
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                log.warning("WhatsApp tidak tersambung; pesan tidak terkirim.")
                return False
            try:
                self._proc.stdin.write(json.dumps(obj) + "\n")
                self._proc.stdin.flush()
                return True
            except (OSError, ValueError):
                log.warning("gagal menulis ke jembatan WhatsApp", exc_info=True)
                return False

    def send(self, jid, text):
        return self._send_obj({"jid": jid, "text": text})

    def send_file(self, jid, path, name=None, caption=None):
        """Kirim satu berkas (sebagai dokumen) ke nomor WhatsApp."""
        obj = {"jid": jid, "file": path, "name": name or os.path.basename(path)}
        if caption:
            obj["caption"] = caption
        return self._send_obj(obj)

    def stop(self):
        self._stop = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def _kill_orphans():
    """Bunuh wa.js sisa dari It's Me yang mati mendadak — dua sesi pada folder
    auth yang sama membuat WhatsApp saling tendang."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
             "Where-Object { $_.CommandLine -like '*wa.js*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("WhatsApp: gagal membersihkan node wa.js lama", exc_info=True)
