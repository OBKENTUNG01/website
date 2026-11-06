"""Copot (uninstall) Ollama dari komputer ini, tanpa dialog.

Jalur CADANGAN untuk tool `copot_ollama` (tools.py) dan tombol di dashboard: dipakai kalau It's Me
Node tidak ada atau tidak menjawab. Kalau node ada, pasang/copot Ollama dikerjakan per komputer
lewat node (node.ollama_*, yang dijalankan inode/ollama_ctl.py di itsme-node). Mandiri - tidak
mengimpor dari itsme-node, sama seperti brain_ollama.py punya klien Ollama sendiri terpisah dari
inode/llm.py (dua paket terpisah, bisa dipasang di komputer yang berbeda).

Hanya Windows: Ollama di macOS/Linux dicopot dengan cara lain (Homebrew / skrip uninstall resmi
mereka), jadi di sana fungsi ini melaporkan itu apa adanya, bukan menebak caranya.

Dua jebakan nyata uninstaller Inno Setup yang ditangani di sini (logika yang sama dengan
inode/ollama_ctl.py):
  - unins000.exe menyalin dirinya ke TEMP lalu proses aslinya LANGSUNG KELUAR; uninstall
    SESUNGGUHNYA masih berjalan di salinan itu. Jadi hasilnya tidak boleh dinilai dari kode
    keluar atau dari installed() sesaat sesudahnya (dulu itu membuat uninstall yang berhasil
    dilaporkan gagal), melainkan dipantau sampai catatan uninstall di registry dan unins000.exe
    benar-benar hilang.
  - Selama uninstall, aplikasi tray Ollama bisa menyala lagi sendiri dan mengunci berkas: kode
    keluar tetap 0 dan registry tetap terhapus, tapi programnya tersisa. Karena itu proses Ollama
    dimatikan terus tiap detik selama menunggu, dan di akhir dicek bahwa ollama.exe memang sudah
    terhapus - tidak dilaporkan "berhasil" kalau sisanya masih ada.
"""

import os
import subprocess
import sys
import tempfile
import time

IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WIN else 0
# Kunci uninstall Inno Setup milik Ollama (GUID tetap dari pemasang resminya).
PRODUCT_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1"
# Proses milik Ollama: aplikasi tray, server, dan runner model.
_OLLAMA_PROCS = ("ollama app.exe", "ollama.exe", "ollama_llama_server.exe", "llama-server.exe", "ollama-runner.exe")
UNINSTALL_WAIT = 180  # detik: batas menunggu uninstall benar-benar selesai
POLL_EVERY = 1.0      # detik: jeda antar pemeriksaan (sekaligus mematikan Ollama yang hidup lagi)
SETTLE_WAIT = 5       # detik: sisa waktu untuk berkas program setelah uninstaller selesai


# --- deteksi ---------------------------------------------------------------------
def _registry():
    """{"version","path","uninstall"} dari kunci uninstall Inno Setup milik Ollama
    (HKCU, lalu HKLM), atau None kalau tidak ada."""
    if not IS_WIN:
        return None
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, PRODUCT_KEY) as key:
                def read(name):
                    try:
                        return str(winreg.QueryValueEx(key, name)[0])
                    except OSError:
                        return ""

                return {"version": read("DisplayVersion"), "path": read("Inno Setup: App Path"),
                        "uninstall": read("UninstallString").strip('"')}
        except OSError:
            continue
    return None


def _default_dir():
    base = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(base, "Programs", "Ollama") if base else ""


def _install_dir(reg=None):
    """Folder program Ollama: dari registry kalau ada, kalau tidak lokasi bawaan pemasangnya."""
    reg = reg if reg is not None else _registry()
    return (reg or {}).get("path") or _default_dir()


def _uninstaller_path():
    """Lokasi unins000.exe Ollama (dari registry, atau tempat bawaan), atau None kalau berkasnya tidak ada."""
    base = _default_dir()
    for path in ((_registry() or {}).get("uninstall"), os.path.join(base, "unins000.exe") if base else ""):
        if path and os.path.isfile(path):
            return path
    return None


def _remaining():
    """Yang masih tersisa dari pemasangan Ollama dan menandakan uninstall BELUM selesai
    (daftar kosong = bersih): catatan uninstall di registry dan unins000.exe."""
    left = []
    if _registry() is not None:
        left.append("catatan uninstall di registry")
    if _uninstaller_path() is not None:
        left.append("unins000.exe")
    return left


def installed():
    """True kalau Ollama (versi Windows, dipasang lewat installer resminya) masih tercatat di
    komputer ini: ada catatan uninstall-nya di registry atau berkas unins000.exe."""
    return IS_WIN and bool(_remaining())


def _models_dir():
    return os.path.join(os.path.expanduser("~"), ".ollama", "models")


# --- copot ------------------------------------------------------------------------
def _kill_all():
    """Matikan semua proses Ollama yang sedang hidup (beserta anak-anaknya). Tidak melempar."""
    args = ["taskkill", "/F", "/T"]
    for name in _OLLAMA_PROCS:
        args += ["/IM", name]
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def _poll(done, seconds):
    """Tiap POLL_EVERY detik: matikan Ollama yang hidup lagi lalu periksa done(), paling lama
    `seconds` detik. Kembalikan True begitu done() terpenuhi, False kalau waktunya habis."""
    end = time.monotonic() + seconds
    while True:
        _kill_all()  # aplikasi tray bisa menyala lagi sendiri di tengah uninstall
        if done():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(POLL_EVERY)


def uninstall(timeout=UNINSTALL_WAIT):
    """Copot Ollama lewat uninstaller Inno Setup-nya, tanpa dialog apa pun.

    Kembalikan (ok, pesan). Mematikan dulu proses Ollama yang berjalan - uninstaller Inno Setup
    bisa menolak jalan kalau programnya masih dipakai - lalu MENUNGGU sampai `timeout` detik
    sampai uninstall benar-benar selesai (lihat catatan di atas modul). Tidak menghapus
    model/data yang sudah diunduh (~/.ollama) - itu di luar yang dihapus installer-nya; pemilik
    bisa hapus foldernya sendiri lewat baca_berkas/jalankan_perintah kalau mau.
    """
    if not IS_WIN:
        return False, (
            "Copot otomatis baru didukung di Windows. Di macOS: `brew uninstall ollama` "
            "(kalau dipasang lewat Homebrew) atau pindahkan Ollama.app ke sampah. Di Linux: "
            "ikuti langkah uninstall resmi di https://github.com/ollama/ollama/blob/main/docs/linux.md#uninstall."
        )
    path = _uninstaller_path()
    if not path:
        if _registry() is not None:
            return False, ("Ollama tercatat terpasang di registry tapi uninstaller-nya (unins000.exe) tidak "
                           "ketemu. Copot lewat Pengaturan > Aplikasi.")
        return False, "Ollama tidak terpasang di komputer ini (tidak ketemu uninstaller-nya)."
    folder = _install_dir()  # dibaca SEKARANG: sesudah uninstall catatan registry-nya hilang
    exe = os.path.join(folder, "ollama.exe") if folder else ""
    log_path = os.path.join(tempfile.gettempdir(), "itsme-ollama-uninstall.log")
    try:
        os.remove(log_path)
    except OSError:
        pass
    _kill_all()
    time.sleep(1)  # beri waktu proses benar-benar berhenti sebelum uninstaller jalan
    try:
        proc = subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log_path}"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=_NO_WINDOW, close_fds=True)
    except OSError as exc:
        return False, f"Gagal menjalankan uninstaller: {exc}"
    # Proses yang kita jalankan biasanya sudah keluar dalam hitungan detik sementara uninstall
    # sebenarnya baru mulai: yang dipantau hasilnya, bukan prosesnya.
    if not _poll(lambda: not _remaining(), timeout):
        code = proc.poll()
        detail = f" Uninstaller keluar dengan kode {code}." if code not in (None, 0) else ""
        return False, (f"Uninstall belum tuntas setelah {timeout} detik: masih ada {', '.join(_remaining())}."
                       f"{detail} Coba cek lewat Pengaturan > Aplikasi, atau jalankan lagi. Log: {log_path}")
    # Uninstaller menghapus program lebih dulu, baru dirinya sendiri: kalau ollama.exe masih ada
    # padahal uninstaller sudah hilang, berkasnya terkunci dan tidak akan dihapus lagi.
    if exe and not _poll(lambda: not os.path.isfile(exe), SETTLE_WAIT):
        return False, (f"Uninstaller sudah selesai tapi {exe} masih ada (mungkin masih dipakai program lain). "
                       f"Tutup Ollama lalu hapus foldernya: {folder}")
    note = (" Model yang sudah diunduh tetap tersimpan di ~/.ollama." if os.path.isdir(_models_dir()) else "")
    return True, ("Ollama sudah dicopot dari komputer ini." + note +
                  " Otak lokal (brain_ollama) tidak akan bisa dipakai sampai dipasang ulang.")
