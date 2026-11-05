"""Jembatan ke REA ("Reverse Engineer Anything", paket npm rea-agents) lewat CLI-nya.

REA sudah terpasang sebagai server MCP milik Claude (npx rea-agents mcp), tapi otak
lokal/cloud It's Me (brain_ollama.py dkk) tidak bicara protokol MCP - jadi modul ini
memanggil CLI satu-tugas REA ("rea <perintah> <target> --json") sebagai proses anak,
jalur integrasi resmi yang didokumentasikan proyeknya sendiri untuk pola "terima target
-> jalankan REA -> baca JSON hasilnya" (lihat docs/cli.md di github.com/morluto/rea).

PENTING (dari DISCLAIMER.md & SECURITY.md resmi REA, bukan asumsi kami): REA BUKAN
sandbox - target yang dibuat khusus untuk mengeksploitasi bug di program analisisnya
(Ghidra/dll.) berpotensi menjalankan kode di komputer ini. Hanya untuk software yang
pemilik punya sendiri atau berwenang memeriksanya (riset keamanan, CTF, debug aplikasi
sendiri) - bukan membongkar punya orang lain tanpa izin. Tool ini hanya bisa dipanggil
pemilik lewat bot (mode tamu diblokir di tools.handle_tool, lihat guest check di awal
handle_tool), sama seperti jalankan_perintah sudah mengizinkan pemilik menjalankan
PowerShell apa pun - REA tidak memperluas batas kepercayaan itu, hanya memberinya
jalan pintas yang rapi.

Hanya dua perintah yang dipilih OTOMATIS dari ekstensi berkas, dan keduanya sudah
diverifikasi lewat riset ke repo resminya (bukan tebakan): .exe/.dll -> inspect-pe-resources,
.apk -> inspect-apk-signature. REA punya ratusan command (satu per kapabilitas, mis. untuk
Electron/JS, APK Android, WASM, firmware, dst.) - menebak sisanya berisiko menyesatkan,
jadi target lain WAJIB menyebut rea_command sendiri (`rea --help` atau https://rea.tools/guides).
"""

import os
import subprocess
import sys
import tempfile

IS_WIN = sys.platform == "win32"
TIMEOUT_SECONDS = 180
_MAX_OUTPUT = 6000

# Hanya dua pemetaan yang dipastikan benar lewat riset ke docs/cli.md resmi REA.
_AUTO_COMMAND = {
    ".exe": "inspect-pe-resources",
    ".dll": "inspect-pe-resources",
    ".apk": "inspect-apk-signature",
}


class ReaError(Exception):
    """Target atau permintaan tidak valid; pesannya siap dikirim ke pemilik."""


def pick_command(target):
    """Perintah REA bawaan dari ekstensi berkas, atau None kalau tidak yakin
    (pemilik harus menyebut rea_command sendiri)."""
    ext = os.path.splitext(target)[1].lower()
    return _AUTO_COMMAND.get(ext)


def _argv(command, target):
    base = ["npx", "-y", "rea-agents@latest", command, target, "--json"]
    if IS_WIN:
        # Sama seperti konfigurasi MCP "rea" yang sudah jalan di komputer ini: npx
        # adalah skrip .cmd, dan subprocess tanpa shell tidak bisa menjalankannya
        # langsung di Windows - perlu lewat cmd.exe /c.
        return ["cmd.exe", "/d", "/c"] + base
    return base


def analyze(target, command=None, timeout=TIMEOUT_SECONDS):
    """Jalankan `rea <command> <target> --json` dan kembalikan (ok, teks hasil/error).

    target: path lokal yang sudah divalidasi ADA & bukan berkas rahasia oleh pemanggil
    (lihat tools.py), atau URL http(s) yang pemilik ketik sendiri dan sudah disetujui.
    command: nama perintah REA (kebab-case); kalau None dipilih dari pick_command(target).
    Melempar ReaError kalau tidak ada command yang cocok (otomatis maupun diberi).
    """
    if not command:
        command = pick_command(target)
    if not command:
        raise ReaError(
            "Tidak tahu perintah REA yang cocok untuk ini secara otomatis (hanya .exe/.dll/.apk "
            "yang punya bawaan). Sebut rea_command-nya sendiri, misalnya lewat jalankan_perintah: "
            "`npx -y rea-agents@latest <perintah> <target> --json` (lihat `rea --help`)."
        )
    with tempfile.TemporaryDirectory(prefix="itsme-rea-") as cwd:
        try:
            proc = subprocess.run(
                _argv(command, target), cwd=cwd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "⏱️ REA melebihi batas waktu dan dihentikan."
        except FileNotFoundError:
            return False, "npx tidak ditemukan - Node.js belum terpasang di komputer ini."
    out = (proc.stdout or "").strip()[:_MAX_OUTPUT]
    err = (proc.stderr or "").strip()[:_MAX_OUTPUT]
    if proc.returncode != 0:
        return False, f"REA gagal (exit {proc.returncode}, perintah: {command}).\n{err or out or '(tidak ada keluaran)'}"
    return True, out or "(REA tidak mengembalikan apa pun)"
