"""Copot (uninstall) Ollama dari komputer ini, tanpa dialog.

Dipakai tool `copot_ollama` (tools.py). Mandiri - tidak mengimpor dari itsme-node,
sama seperti brain_ollama.py punya klien Ollama sendiri terpisah dari inode/llm.py
(dua paket terpisah, bisa dipasang di komputer yang berbeda).

Hanya Windows: Ollama di macOS/Linux dicopot dengan cara lain (Homebrew / skrip
uninstall resmi mereka), jadi di sana fungsi ini melaporkan itu apa adanya,
bukan menebak caranya.
"""

import os
import subprocess
import sys
import time

IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WIN else 0
_OLLAMA_PROCS = {"ollama.exe", "ollama_llama_server.exe", "llama-server.exe", "ollama-runner.exe"}


def _uninstaller_path():
    base = os.environ.get("LOCALAPPDATA", "")
    path = os.path.join(base, "Programs", "Ollama", "unins000.exe") if base else ""
    return path if path and os.path.isfile(path) else None


def installed():
    """True kalau Ollama (versi Windows, dipasang lewat installer resminya) ada di komputer ini."""
    return IS_WIN and _uninstaller_path() is not None


def _pids():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                              timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in (out.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].strip('"').lower() in _OLLAMA_PROCS:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def _kill_all():
    for pid in _pids():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                            timeout=10, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass


def uninstall(timeout=60):
    """Copot Ollama lewat uninstaller Inno Setup-nya, tanpa dialog apa pun.

    Kembalikan (ok, pesan). Mematikan dulu proses Ollama yang berjalan - uninstaller
    Inno Setup bisa menolak jalan kalau programnya masih dipakai. Tidak menghapus
    model/data yang sudah diunduh (~/.ollama) - itu di luar yang dihapus installer-nya;
    pemilik bisa hapus foldernya sendiri lewat baca_berkas/jalankan_perintah kalau mau.
    """
    if not IS_WIN:
        return False, (
            "Copot otomatis baru didukung di Windows. Di macOS: `brew uninstall ollama` "
            "(kalau dipasang lewat Homebrew) atau pindahkan Ollama.app ke sampah. Di Linux: "
            "ikuti langkah uninstall resmi di https://github.com/ollama/ollama/blob/main/docs/linux.md#uninstall."
        )
    path = _uninstaller_path()
    if not path:
        return False, "Ollama tidak terpasang di komputer ini (tidak ketemu uninstaller-nya)."
    _kill_all()
    time.sleep(1)  # beri waktu proses benar-benar berhenti sebelum uninstaller jalan
    try:
        subprocess.run([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                        timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        pass  # uninstaller Inno Setup kadang lepas ke proses anak sendiri; timeout bukan berarti gagal
    except OSError as exc:
        return False, f"Gagal menjalankan uninstaller: {exc}"
    if installed():
        return False, "Uninstaller sudah dijalankan tapi Ollama sepertinya masih terpasang. Coba cek manual."
    return True, "Ollama sudah dicopot dari komputer ini. Otak lokal (brain_ollama) tidak akan bisa dipakai sampai dipasang ulang."
