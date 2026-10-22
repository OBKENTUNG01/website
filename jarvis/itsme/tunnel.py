"""Cloudflare Quick Tunnel: alamat publik INSTAN untuk dashboard, tanpa domain.

Menjalankan `cloudflared tunnel --url http://127.0.0.1:<port>`, menangkap alamat
`*.trycloudflare.com` dari keluarannya, lalu memanggil callback saat alamat muncul
atau berubah (dipakai main.py untuk mengirimnya ke Telegram). Jalan di latar belakang
dan menyalakan cloudflared lagi kalau mati. Aktif hanya kalau ITSME_QUICK_TUNNEL=1.

Alamatnya sementara (berubah tiap It's Me / tunnel menyala ulang). Untuk alamat
TETAP lewat domain sendiri, lihat tunnel-setup.ps1 (tunnel bernama + ITSME_PUBLIC_URL).
"""

import logging
import os
import re
import shutil
import subprocess
import threading

from . import config

log = logging.getLogger("itsme")
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: tanpa jendela


def cloudflared_path():
    """Lokasi cloudflared, atau None. ITSME_TUNNEL_BIN, lalu PATH, lalu folder It's Me."""
    if config.TUNNEL_BIN and os.path.exists(config.TUNNEL_BIN):
        return config.TUNNEL_BIN
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ItsMe", "cloudflared.exe")
    return local if local and os.path.exists(local) else None


def find_url(text):
    """Alamat trycloudflare pertama di `text`, atau None."""
    m = _URL.search(text or "")
    return m.group(0) if m else None


class QuickTunnel:
    """Mengelola satu proses cloudflared quick tunnel di latar belakang."""

    def __init__(self, port, on_url):
        self.port = int(port)
        self.on_url = on_url      # callback(url): dipanggil saat alamat baru muncul
        self.url = None
        self._stop = threading.Event()
        self._proc = None
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="itsme-tunnel", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def _run(self):
        cf = cloudflared_path()
        if not cf:
            log.warning("Quick tunnel: cloudflared belum ada (jalankan quick-tunnel.ps1). Dilewati.")
            return
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    [cf, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                )
            except OSError as exc:
                log.warning("Quick tunnel gagal dijalankan: %s", exc)
                self._stop.wait(15)
                continue
            for line in self._proc.stdout or ():
                if self._stop.is_set():
                    break
                url = find_url(line)
                if url and url != self.url:
                    self.url = url
                    log.info("Quick tunnel aktif: %s", url)
                    try:
                        self.on_url(url)
                    except Exception:  # noqa: BLE001 - jangan jatuhkan thread tunnel
                        log.exception("gagal mengabari alamat tunnel")
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.url = None
            if not self._stop.is_set():
                log.info("Quick tunnel putus; menyalakan lagi sebentar lagi.")
                self._stop.wait(5)
