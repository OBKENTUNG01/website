"""Bot WhatsApp lewat jembatan Node (itsme/whatsapp/wa.js).

Masuk: tiap pesan pemilik dibaca dari stdout node, lalu dipanggil handler(jid, text)
di thread pembaca. Keluar: send(jid, text) menulis satu baris JSON ke stdin node.
Login pertama: QR muncul di jendela It's Me; sesi disimpan di data/whatsapp/.

Keadaan sambungan dilaporkan jembatan sebagai baris {"state": ...} (lihat wa.js), supaya
/wa bisa bilang yang SEBENARNYA: sudah tersambung (tidak ada QR yang perlu dipindai), menunggu
QR, atau keluar dari "Perangkat tertaut".
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
# Keadaan dari wa.js -> nama keadaan di sini.
_STATES = {"open": "tersambung", "qr": "qr", "close": "terputus", "logged_out": "keluar"}


def prerequisites():
    """(lokasi node.exe atau "", apakah 'npm install' sudah dijalankan) - dipakai
    start() dan perintah /diagnosa."""
    return shutil.which("node") or "", os.path.isdir(os.path.join(DIR, "node_modules"))


class WhatsApp:
    def __init__(self, on_message, on_qr=None, on_giveup=None, on_state=None):
        self._on_message = on_message
        self._on_qr = on_qr
        self._on_giveup = on_giveup
        self._on_state = on_state  # (keadaan, nomor) dari thread pembaca
        self._proc = None
        self._lock = threading.Lock()
        self._stop = False
        self._tries = 0
        self._last = 0.0
        # mulai | qr | tersambung | terputus | keluar  (+ nomor akun yang tertaut kalau tersambung)
        self.state = "mulai"
        self.me = ""
        self._cond = threading.Condition()

    # --- keadaan sambungan -------------------------------------------------------
    def _set_state(self, state, me=""):
        with self._cond:
            self.state = state
            if state == "tersambung":
                self.me = me or self.me
            elif state in ("keluar", "mulai"):
                self.me = ""
            self._cond.notify_all()
        if self._on_state:
            try:
                self._on_state(state, self.me)
            except Exception:  # noqa: BLE001 - pemberitahuan gagal tidak boleh merusak thread pembaca
                log.exception("gagal memproses perubahan keadaan WhatsApp")

    def wait_state(self, timeout=20.0, want=("tersambung", "qr", "keluar")):
        """Tunggu (memblokir; panggil lewat thread) sampai jembatan melapor salah satu
        keadaan di `want`, paling lama `timeout` detik. Kembalikan keadaan SEKARANG."""
        end = time.monotonic() + timeout
        with self._cond:
            while self.state not in want:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            return self.state

    def status_text(self):
        """Keadaan sambungan dalam bahasa manusia (untuk /wa dan /diagnosa)."""
        return {
            "mulai": "sedang menyala, menunggu kabar dari WhatsApp",
            "qr": "menunggu QR dipindai",
            "tersambung": f"tersambung ke akun {self.me}" if self.me else "tersambung",
            "terputus": "terputus, mencoba menyambung lagi",
            "keluar": "keluar dari Perangkat tertaut (QR baru disiapkan)",
        }.get(self.state, self.state)

    def start(self):
        """Nyalakan node. (False, alasan) kalau tidak bisa - alasan "node" (Node.js
        tidak ada di PATH) atau "npm" (belum 'npm install'), supaya pemanggil bisa
        kasih pesan yang tepat, bukan cuma "pastikan ini-itu" yang tidak jelas mana
        yang sebenarnya bermasalah."""
        node, has_modules = prerequisites()
        if not node:
            log.warning("WhatsApp diminta tapi Node.js tidak ada di PATH (pasang dari nodejs.org).")
            return False, "node"
        if not has_modules:
            log.warning("WhatsApp: jalankan dulu 'npm install' di %s", DIR)
            return False, "npm"
        self._set_state("mulai")
        _kill_orphans()
        env = dict(os.environ,
                   WA_OWNER=str(config.WA_OWNER or ""),
                   WA_GUESTS=",".join(config.WA_GUESTS),
                   WA_OPEN="1" if config.WA_OPEN else "0",
                   WA_AUTH=os.path.join(config.DATA_DIR, "whatsapp"),
                   WA_FILES=config.WA_FILES_DIR,
                   WA_MAX_MB=str(config.WA_MAX_MB))
        proc = subprocess.Popen(
            [node, "wa.js"], cwd=DIR, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        # _stop baru dimatikan SETELAH proses baru ada: pembaca proses lama yang masih berakhir
        # tidak boleh mengira jembatan itu mati sendiri lalu menyalakan yang kedua.
        self._stop = False
        self._proc = proc
        threading.Thread(target=self._read, daemon=True).start()
        return True, ""

    def relink(self):
        """Tautkan ulang ke akun WhatsApp: lepas akun yang sekarang dari "Perangkat tertaut" dan
        minta QR baru. Sesi lama TIDAK dihapus, hanya dipindah ke data/whatsapp.lama-<waktu>.
        Kembalikan (ok, alasan) seperti start(); alasan "sesi" = folder sesi lama tak bisa dipindah."""
        if self._proc is not None and self._proc.poll() is None:
            self._set_state("mulai")
            if self._send_obj({"cmd": "logout"}) and self.wait_state(10, want=("qr", "keluar")) in ("qr", "keluar"):
                return True, ""  # wa.js sudah memindahkan sesi lama dan menyiapkan QR baru
            log.warning("WhatsApp: jembatan tidak menjawab perintah logout; sesi dipindah dari sini.")
        self.stop()
        if self._proc is not None:
            try:
                self._proc.wait(timeout=8)
            except (subprocess.TimeoutExpired, OSError):
                pass
        try:
            moved = _set_aside_session()
        except OSError:
            log.exception("WhatsApp: gagal memindahkan sesi lama")
            return False, "sesi"
        if moved:
            log.info("WhatsApp: sesi lama disimpan di %s", moved)
        self._tries = 0
        return self.start()

    def _read(self):
        proc = self._proc
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            # Baris keadaan dari wa.js ({"state": ...}); baris log Baileys (pino) punya "level"
            # dan tidak boleh dikira keadaan.
            if msg.get("state") in _STATES and "level" not in msg:
                self._set_state(_STATES[msg["state"]], str(msg.get("me") or ""))
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
        code = proc.poll()
        log.warning("WhatsApp: jembatan node berhenti (keluar kode %s).", code)
        if self._stop or proc is not self._proc:
            return  # dihentikan sengaja, atau sudah digantikan jembatan yang lebih baru
        self._set_state("terputus")
        # ponytail: nyalakan ulang sendiri, tapi berhenti setelah _RETRY_MAX kali
        # dalam semenit supaya tidak jadi spam. (Akun yang dicabut dari HP ditangani wa.js
        # sendiri: sesi lama dipindah, QR baru dibuat - tidak sampai di sini.)
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


def _set_aside_session():
    """Pindahkan data/whatsapp ke samping (BUKAN hapus) supaya penautan berikutnya mulai dari
    nol dan WhatsApp membuat QR baru. Kembalikan jalur barunya, atau None kalau tak ada sesi."""
    auth = os.path.join(config.DATA_DIR, "whatsapp")
    if not os.path.isdir(auth):
        return None
    target = f"{auth}.lama-{time.strftime('%Y%m%d-%H%M%S')}"
    os.rename(auth, target)
    return target


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
