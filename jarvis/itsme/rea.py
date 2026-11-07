"""Jembatan ke REA ("Reverse Engineer Anything", paket npm rea-agents) lewat CLI-nya.

REA sudah terpasang sebagai server MCP milik Claude (npx rea-agents mcp), tapi otak
lokal/cloud It's Me (brain_ollama.py dkk) tidak bicara protokol MCP - jadi modul ini
memanggil CLI satu-tugas REA ("rea <perintah> <target> --json") sebagai proses anak,
jalur integrasi resmi yang didokumentasikan proyeknya sendiri (docs/cli.md di
github.com/morluto/rea).

PENTING (dari DISCLAIMER.md & SECURITY.md resmi REA): REA BUKAN sandbox - target yang
dibuat khusus untuk mengeksploitasi bug di program analisisnya berpotensi menjalankan kode
di komputer ini. Hanya untuk software yang pemilik punya sendiri atau berwenang
memeriksanya (riset keamanan, CTF, debug aplikasi sendiri). Alat ini hanya bisa dipanggil
pemilik (tamu diblokir di tools.handle_tool), sama seperti jalankan_perintah.

Cara kerja (hasil uji nyata di komputer ini, bukan tebakan):
  - Versi dipatok: rea-agents@6.7.0 (env ITSME_REA_VERSION; "latest" boleh). "latest"
    pernah menarik 6.8.0 yang butuh Node >=22.23.4 sehingga npx mengunduh ulang 45 detik.
  - TANPA shell: argv list [node, npx-cli.js, -y, rea-agents@VER, perintah, target, --json].
    Dulu lewat "cmd.exe /d /c npx ..." sehingga nama berkas berisi '&' memecah perintah.
    Node dicari lewat shutil.which("node") (env ITSME_REA_NODE untuk menimpa), npx-cli.js
    ada di folder npm milik Node itu (env ITSME_REA_NPX_CLI untuk menimpa).
  - Daftar putih COMMANDS dibuat dari `rea --help` 6.7.0 yang nyata; perintah di luarnya
    dan target berawalan '-' ditolak. Argumen tambahan hanya dari tabel internal.
  - Berjalan sebagai JOB di latar (maks. 2 serentak). Keluaran ditulis ke BERKAS:
    <DATA_DIR>/rea-jobs/<id>/{result.json, stderr.log, meta.json}; 20 job terakhir disimpan
    dan yang sudah selesai tetap terbaca setelah It's Me menyala ulang.
  - Batas waktu per perintah (bawaan 15 menit; analyze-javascript-application 45 menit;
    env ITSME_REA_TIMEOUT / ITSME_REA_TIMEOUT_JS dalam menit), bisa dibatalkan, dan seluruh
    pohon proses anak dimatikan (Job Object di Windows, grup proses di Linux/macOS).
  - Hasil bisa SANGAT besar (291 MB nyata): berkas <=30 MB dibaca biasa, yang lebih besar
    dipindai sebagai aliran (scan_json) dengan memori kecil.

  - Berkas/folder yang DIUNGGAH lewat dashboard (rea_upload.py) dianalisis dari folder penyimpanannya; job mencatat
    nama aslinya (`label`) dan ringkasan menampilkan nama itu, bukan path internal. start(..., upload=info).

API publik: REA_VERSION, COMMANDS, ReaError, pick_command, suggest, start, job, list_jobs,
result_path, cancel, analyze, hasil, active_upload_ids, scan_json/Besar.
"""

import atexit
import hashlib
import json
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter

IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WIN else 0

# --- konfigurasi ----------------------------------------------------------------------
_DEFAULT_VERSION = "6.7.0"
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")


def _version_from_env():
    """Versi paket rea-agents dari ITSME_REA_VERSION; isian aneh dibuang (kembali ke bawaan)."""
    value = (os.environ.get("ITSME_REA_VERSION") or "").strip()
    return value if value and _VERSION_RE.fullmatch(value) else _DEFAULT_VERSION


REA_VERSION = _version_from_env()

MAX_RUNNING = 2            # job REA yang boleh berjalan serentak
KEEP_JOBS = 20             # job terakhir yang disimpan di disk
SUMMARY_MAX_CHARS = 3500   # ringkasan teks; batas pesan Telegram 4096
_STALE_AFTER = 90          # detik tanpa denyut sebelum job "running" di disk dianggap terputus
_BEAT_EVERY = 10           # detik antar denyut (progres & waktu terakhir ditulis ke meta.json)


def _minutes_from_env(name, default):
    try:
        value = float((os.environ.get(name) or "").strip().replace(",", "."))
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:   # 'inf'/'nan' lolos float() tapi bukan batas waktu
        return default
    return min(value, 24 * 60.0)                  # paling lama sehari


# Batas waktu per perintah (detik).
_DEFAULT_TIMEOUT = 15 * 60
_TIMEOUTS = {"analyze-javascript-application": 45 * 60}


def _timeout_for(command):
    if command in _TIMEOUTS:
        base = _TIMEOUTS[command]
        return int(_minutes_from_env("ITSME_REA_TIMEOUT_JS", base / 60) * 60)
    return int(_minutes_from_env("ITSME_REA_TIMEOUT", _DEFAULT_TIMEOUT / 60) * 60)


# Argumen tambahan HANYA dari tabel ini (tidak pernah dari input pengguna). Tanpa
# --integrity-policy record-and-continue, analisis JavaScript gagal pada app.asar yang
# biner native-nya ditandatangani ulang sesudah dikemas (kasus nyata: Unity Hub).
_EXTRA_ARGS = {
    "analyze-javascript-application": ("--integrity-policy", "record-and-continue"),
}

# Daftar putih: semua ADA di `rea --help` 6.7.0 (diperiksa langsung), tiap perintah menerima
# tepat satu <path> sebagai argumen wajib dan tidak menjalankan/menulis apa pun di target.
COMMANDS = {
    "inspect-artifact": "Inventaris umum berkas/paket (exe, dll, apk, jar, zip, asar): format, ukuran, "
                        "hash, isi, dan saran pemeriksaan lanjutan. Cepat.",
    "inspect-pe-resources": "Sumber daya program Windows (.exe/.dll): ikon, manifest, blok versi, bahasa, hash.",
    "inspect-managed-artifact": "Cek apakah .exe/.dll adalah program .NET dan baca identitasnya "
                                "(assembly, versi, referensi) tanpa menjalankannya.",
    "inspect-managed-native-boundaries": "Program .NET: daftar pustaka native yang dipanggil (P/Invoke).",
    "inspect-go-binary": "Info build program Go: versi Go, modul utama, dependensi, setelan build, revisi git.",
    "inspect-rust-binary": "Daftar dependensi Rust yang tertanam (cargo-auditable) di dalam program.",
    "inspect-electron-fuses": "Status 'fuse' aplikasi Electron (RunAsNode, integritas ASAR, dsb.) "
                              "tanpa menjalankan aplikasinya.",
    "analyze-javascript-application": "Analisis statis aplikasi JavaScript/Electron (folder atau .asar): "
                                      "jendela, IPC, API terekspos, temuan. LAMBAT (bisa belasan menit) "
                                      "dan hasilnya sangat besar.",
    "identify-flutter-build": "Cek apakah sebuah APK dibuat dengan Flutter dan baca info mesinnya.",
    "inspect-android-package": "Isi APK: paket, versi, izin, komponen manifest. Butuh JADX "
                               "(atur REA_JADX_MCP_JAR).",
    "decode-android-resources": "Dekode manifest dan string APK dengan apktool. Butuh apktool "
                                "(atur REA_APKTOOL_COMMAND).",
}

_LABELS = {
    "inspect-artifact": "Inventaris umum (isi, hash, format)",
    "inspect-pe-resources": "Sumber daya Windows (ikon, manifest, versi)",
    "inspect-managed-artifact": "Cek apakah program .NET",
    "inspect-managed-native-boundaries": "Pustaka native yang dipanggil .NET (P/Invoke)",
    "inspect-go-binary": "Cek program Go (versi, modul, dependensi)",
    "inspect-rust-binary": "Cek dependensi Rust tertanam",
    "inspect-electron-fuses": "Cek fuse Electron (RunAsNode, integritas ASAR)",
    "analyze-javascript-application": "Analisis aplikasi JavaScript/Electron (lambat)",
    "identify-flutter-build": "Cek apakah aplikasi Flutter",
    "inspect-android-package": "Manifest, izin & komponen APK (butuh JADX)",
    "decode-android-resources": "Dekode manifest & string APK (butuh apktool)",
}

_PE_EXT = {".exe", ".dll", ".sys", ".ocx", ".scr", ".cpl", ".efi", ".drv", ".mui"}
_NATIVE_EXT = {".so", ".dylib"}
_PACKAGE_EXT = {".apk", ".aab", ".xapk", ".jar", ".war", ".aar", ".zip", ".msi", ".ipa", ".appx", ".msix",
                ".vsix", ".nupkg", ".crx", ".asar"}
_AUTO_EXT = _PE_EXT | _NATIVE_EXT | _PACKAGE_EXT

_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+://")


class ReaError(Exception):
    """Target, perintah, atau keadaan komputer tidak memungkinkan; pesannya siap dikirim ke pemilik."""


# --- pemilihan perintah ----------------------------------------------------------------
def _ext(target):
    base = str(target or "").strip().rstrip("/\\")
    return os.path.splitext(base)[1].lower()


def pick_command(target):
    """Perintah REA bawaan untuk target, atau None kalau tidak ada yang cocok (pemilik harus
    menyebut rea_command sendiri).

    Dari kenyataan REA 6.7.0: berkas biasa (exe, dll, sys, apk, jar, so, dylib, msi, zip, asar, dll.)
    -> inspect-artifact; FOLDER -> analyze-javascript-application (inspect-artifact menolak folder);
    alamat web (URL) tidak didukung perintah mana pun, jadi None."""
    if not isinstance(target, str) or not target.strip():
        return None
    text = target.strip()
    if _URL_RE.match(text):
        return None
    if os.path.isdir(text):
        return "analyze-javascript-application"
    if _ext(text) in _AUTO_EXT:
        return "inspect-artifact"
    return None


def _jadx_ready():
    return bool((os.environ.get("REA_JADX_MCP_JAR") or "").strip())


def _apktool_ready():
    return bool((os.environ.get("REA_APKTOOL_COMMAND") or "").strip() or shutil.which("apktool"))


def suggest(target, last_command=None):
    """Pemeriksaan lanjutan yang relevan untuk jenis target ini (tanpa mengulang last_command):
    [{"command", "label"}, ...]. Perintah yang butuh JADX/apktool hanya ditawarkan kalau
    komponennya terlihat terpasang (env), supaya tombol tidak mengarah ke galat pasti."""
    if not isinstance(target, str) or not target.strip() or _URL_RE.match(target.strip()):
        return []
    text = target.strip()
    ext = _ext(text)
    if os.path.isdir(text):
        names = ["analyze-javascript-application"]
    elif ext in _PE_EXT:
        names = ["inspect-artifact", "inspect-pe-resources", "inspect-managed-artifact",
                 "inspect-go-binary", "inspect-rust-binary"]
        if ext == ".exe":
            names.append("inspect-electron-fuses")
        names.append("inspect-managed-native-boundaries")
    elif ext == ".apk":
        names = ["inspect-artifact", "identify-flutter-build"]
        if _jadx_ready():
            names.append("inspect-android-package")
        if _apktool_ready():
            names.append("decode-android-resources")
    elif ext == ".asar":
        names = ["inspect-artifact", "analyze-javascript-application"]
    elif ext in _NATIVE_EXT:
        names = ["inspect-artifact", "inspect-go-binary", "inspect-rust-binary"]
    elif ext in _PACKAGE_EXT or os.path.isfile(text):
        names = ["inspect-artifact"]
    else:
        names = []
    return [{"command": n, "label": _LABELS[n]} for n in names if n != last_command]


# --- Node & npx (tanpa shell) ------------------------------------------------------------
def _find_node():
    forced = (os.environ.get("ITSME_REA_NODE") or "").strip().strip('"')
    if forced:
        if not os.path.isfile(forced):
            raise ReaError(f"ITSME_REA_NODE menunjuk ke berkas yang tidak ada: {forced}")
        return os.path.abspath(forced)
    found = shutil.which("node")
    if not found:
        raise ReaError("Node.js tidak ditemukan di komputer ini (REA butuh Node.js). Pasang Node.js, atau isi "
                       "ITSME_REA_NODE dengan path node.exe.")
    if IS_WIN and found.lower().endswith((".cmd", ".bat")):
        raise ReaError(f"'node' di PATH adalah skrip ({found}), bukan node.exe, dan tidak bisa dijalankan tanpa "
                       "shell. Isi ITSME_REA_NODE dengan path node.exe.")
    return found


def _find_npx_cli(node):
    """Path npx-cli.js milik npm yang menyertai `node` (Windows: <folder node>/node_modules/npm/bin;
    Linux/macOS: <prefix>/lib/node_modules/npm/bin)."""
    forced = (os.environ.get("ITSME_REA_NPX_CLI") or "").strip().strip('"')
    if forced:
        if not os.path.isfile(forced):
            raise ReaError(f"ITSME_REA_NPX_CLI menunjuk ke berkas yang tidak ada: {forced}")
        return os.path.abspath(forced)
    rel = os.path.join("node_modules", "npm", "bin", "npx-cli.js")
    seen = []
    for base in (os.path.dirname(node), os.path.dirname(os.path.realpath(node))):
        for cand in (os.path.join(base, rel),
                     os.path.join(base, "..", "lib", rel),
                     os.path.join(base, "..", rel)):
            cand = os.path.normpath(cand)
            if cand not in seen:
                seen.append(cand)
    for cand in seen:
        if os.path.isfile(cand):
            return cand
    return None


def _launcher():
    """Awal argv untuk memanggil npx tanpa shell: [node, npx-cli.js] (atau biner npx di POSIX)."""
    node = _find_node()
    cli = _find_npx_cli(node)
    if cli:
        return [node, cli]
    if not IS_WIN:
        npx = shutil.which("npx")
        if npx:
            return [npx]
    raise ReaError("npx tidak ditemukan: npx-cli.js tidak ada di folder npm milik Node ini. Pasang npm "
                   "bersama Node.js, atau isi ITSME_REA_NPX_CLI dengan path npx-cli.js.")


def _argv(command, target):
    extra = list(_EXTRA_ARGS.get(command, ()))
    return _launcher() + ["-y", f"rea-agents@{REA_VERSION}", command, target] + extra + ["--json"]


_SECRET_NAME = re.compile(r"(TOKEN|API_?KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_?KEY)", re.I)


def _child_env():
    """Lingkungan proses anak TANPA rahasia It's Me (token bot, kunci API, kata sandi): REA bukan sandbox
    dan `npx -y` menjalankan skrip pemasangan paket npm - proses itu tak boleh bisa membaca token milik bot."""
    env = {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
    env.setdefault("NO_COLOR", "1")
    env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    env["NPM_CONFIG_FUND"] = "false"
    env["NPM_CONFIG_AUDIT"] = "false"
    return env


# --- validasi target & perintah -------------------------------------------------------------
def _clean_target(target):
    """Target yang aman dipakai sebagai satu argumen argv. Menolak teks kosong, karakter kontrol,
    awalan '-' (bisa terbaca sebagai opsi), dan URL (REA 6.7.0 hanya memeriksa berkas/folder lokal)."""
    if not isinstance(target, str):
        raise ReaError("Target harus berupa teks (path berkas atau folder).")
    text = target.strip()
    if not text:
        raise ReaError("Target kosong. Sebut path berkas atau folder yang mau diperiksa.")
    if len(text) > 4096 or any(ord(ch) < 32 for ch in text):
        raise ReaError("Target tidak valid (terlalu panjang atau mengandung karakter kontrol).")
    if text.startswith("-"):
        raise ReaError("Target tidak boleh diawali tanda minus ('-'): bisa terbaca sebagai opsi, bukan path. "
                       "Tulis path lengkapnya (mis. C:\\folder\\berkas.exe).")
    if _URL_RE.match(text):
        raise ReaError("REA hanya memeriksa berkas atau folder di komputer ini, bukan alamat web. Unduh dulu "
                       "berkasnya, lalu sebutkan path-nya.")
    if not os.path.isabs(text) and os.path.exists(text):
        text = os.path.abspath(text)  # job berjalan di foldernya sendiri, jadi path relatif harus dipastikan
    return text


def _resolve_command(command, target):
    if command is None or not str(command).strip():
        picked = pick_command(target)
        if not picked:
            raise ReaError(
                "Tidak tahu perintah REA yang cocok untuk ini secara otomatis (bawaan hanya untuk berkas "
                "exe/dll/sys/apk/jar/zip/asar/so/dylib/msi dan folder aplikasi). Sebut perintahnya sendiri; "
                "yang tersedia: " + ", ".join(COMMANDS) + ".")
        return picked
    name = str(command).strip().lower().replace("_", "-")
    if name not in COMMANDS:
        raise ReaError(f"Perintah REA '{str(command).strip()[:60]}' tidak dikenal atau tidak diizinkan. "
                       "Yang tersedia: " + ", ".join(COMMANDS) + ".")
    return name


# --- Job Object Windows & mematikan pohon proses -----------------------------------------------
_K32 = None


def _kernel32():
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k
    return _K32


def _winjob_new(pid):
    """Job Object Windows berlabel KILL_ON_JOB_CLOSE yang berisi proses `pid`: seluruh anak-cucunya
    ikut di dalamnya, bisa dimatikan sekaligus, dan otomatis mati kalau It's Me berhenti/menyala
    ulang (os.execv di Windows meninggalkan proses anak yatim). None kalau gagal (ada cadangan taskkill)."""
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k = _kernel32()
        handle = k.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k.CloseHandle(handle)
            return None
        proc = k.OpenProcess(0x0101, False, int(pid))  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not proc:
            k.CloseHandle(handle)
            return None
        ok = k.AssignProcessToJobObject(handle, proc)
        k.CloseHandle(proc)
        if not ok:
            k.CloseHandle(handle)
            return None
        return handle
    except Exception:  # noqa: BLE001 - apa pun yang gagal di sini cukup berarti "tanpa Job Object"
        return None


def _winjob_close(handle):
    """Tutup handle Job Object (KILL_ON_JOB_CLOSE membunuh sisa proses yang masih ada di dalamnya)."""
    if handle:
        try:
            _kernel32().CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def _pid_alive(pid):
    """Apakah proses `pid` masih hidup (lintas platform, tanpa psutil). Ragu -> anggap hidup."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            k = _kernel32()
            handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5  # akses ditolak = prosesnya ada
            code = wintypes.DWORD()
            ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
            k.CloseHandle(handle)
            return (not ok) or code.value == 259  # STILL_ACTIVE
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _taskkill(pid, force):
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
    args = [exe, "/PID", str(int(pid)), "/T"] + (["/F"] if force else [])
    try:
        subprocess.run(args, capture_output=True, timeout=15, creationflags=_NO_WINDOW, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _killpg_leftovers(proc):
    """POSIX: sisa grup proses (cucu yang mengabaikan SIGTERM atau macet) tetap dibunuh walau pemimpinnya
    sudah keluar. Di Windows Job Object yang menutup/menghentikan seluruhnya."""
    if IS_WIN:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def _kill_tree(proc, winjob=None):
    """Matikan proses REA beserta SELURUH anak-cucunya (npx -> cmd -> node -> pekerja). Hanya
    menyentuh proses yang kita mulai sendiri (lewat Popen/Job Object milik kita)."""
    if IS_WIN:
        if winjob:
            try:
                _kernel32().TerminateJobObject(winjob, 1)
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is None:
            _taskkill(proc.pid, force=False)  # tanpa /F dulu: kalau mau berhenti baik-baik
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _taskkill(proc.pid, force=True)
        if proc.poll() is None:
            proc.kill()
    else:
        for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
            if proc.poll() is not None:
                break
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                proc.send_signal(sig)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        _killpg_leftovers(proc)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# --- format teks ------------------------------------------------------------------------------
def _fmt_n(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def _fmt_size(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            text = f"{n:.1f}".replace(".", ",")
            return (text[:-2] if text.endswith(",0") else text) + f" {unit}"
        n /= 1024
    return str(value)


def _fmt_size_full(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return _fmt_size(n) if n < 1024 else f"{_fmt_size(n)} ({_fmt_n(n)} byte)"


def _fmt_duration(seconds):
    s = int(max(0, seconds))
    if s < 60:
        return f"{s} dtk"
    if s < 3600:
        return f"{s // 60} mnt {s % 60} dtk"
    return f"{s // 3600} jam {(s % 3600) // 60} mnt"


def _short(text, limit=300):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# --- pemindai aliran untuk JSON besar ----------------------------------------------------------
_SCAN_CHUNK = 1 << 20      # byte dibaca per putaran
_SCAN_WINDOW = 4 << 20     # nilai yang muat di jendela ini di-decode sekaligus (cepat, C); yang lebih besar dibongkar
_SCAN_SMALL = 200_000      # nilai sampai ukuran ini (serialisasi) disimpan utuh
_SCAN_MAXKEYS = 200        # paling banyak kunci per objek yang nilainya disimpan
_SCAN_BUDGET = 16 << 20    # total teks nilai yang boleh disimpan sepanjang pemindaian
_SCAN_DEPTH = 64           # batas kedalaman pembongkaran

_DECODER = json.JSONDecoder()
_WS_RE = re.compile(r"[ \t\r\n]*")
_SCALAR_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|NaN|-?Infinity")


class Besar:
    """Penanda untuk nilai JSON yang terlalu besar untuk disimpan: hanya jenis ("objek", "larik",
    "teks"), ukuran dalam byte, dan jumlah isinya. Untuk objek yang dibongkar, `isi` memuat
    nilai-nilai anaknya (kecil = nilai asli, besar = Besar lagi); `kunci` memuat nama kuncinya."""

    __slots__ = ("jenis", "bytes", "jumlah", "isi", "kunci")

    def __init__(self, jenis, nbytes, jumlah=None, isi=None, kunci=None):
        self.jenis = jenis
        self.bytes = nbytes
        self.jumlah = jumlah
        self.isi = isi
        self.kunci = kunci

    def __repr__(self):
        return f"Besar({self.jenis}, {self.bytes} byte, jumlah={self.jumlah})"


def _string_end(txt, i):
    """Indeks tanda kutip penutup string yang isinya mulai di `i`, atau -1 kalau belum ada di `txt`.
    Tanda kutip yang didahului backslash ganjil adalah bagian isi (escape)."""
    while True:
        q = txt.find('"', i)
        if q < 0:
            return -1
        b = q - 1
        while b >= i and txt[b] == "\\":
            b -= 1
        if (q - 1 - b) % 2 == 0:
            return q
        i = q + 1


class _Scanner:
    """Membaca satu nilai JSON dari berkas biner secara bertahap. Teks di-decode latin-1 (1 byte = 1
    karakter) supaya posisi == byte dan potongan multibyte UTF-8 tidak jadi masalah; hanya nilai yang
    disimpan di-decode ulang sebagai UTF-8."""

    def __init__(self, f, chunk, window, small, max_keys, budget):
        self.f = f
        self.chunk = max(1, int(chunk))
        self.window = max(1, int(window))
        self.small = small
        self.max_keys = max_keys
        self.left = budget
        self.txt = ""
        self.pos = 0
        self.base = 0
        self.eof = False

    # -- penyangga --
    def _more(self):
        data = self.f.read(self.chunk)
        if not data:
            self.eof = True
            return False
        if self.pos:
            self.base += self.pos
            self.txt = self.txt[self.pos:]
            self.pos = 0
        self.txt += data.decode("latin-1")
        return True

    def _ensure(self, n):
        while len(self.txt) - self.pos < n and not self.eof:
            self._more()

    def _ws(self):
        while True:
            self.pos = _WS_RE.match(self.txt, self.pos).end()
            if self.pos < len(self.txt) or not self._more():
                return

    def _peek(self):
        if self.pos >= len(self.txt) and not self._more():
            return ""
        return self.txt[self.pos]

    def _at(self):
        return self.base + self.pos

    def _keep(self, size, force=False):
        if size > self.small:
            return False
        if not force and size > self.left:
            return False
        self.left -= size
        return True

    @staticmethod
    def _decode(segment):
        return json.loads(segment.encode("latin-1").decode("utf-8", "replace"))

    # -- string --
    def _string(self, keep, force=False):
        """pos di tanda kutip pembuka. Mengembalikan (nilai | Besar | None, ukuran)."""
        start = self._at()
        while True:
            q = _string_end(self.txt, self.pos + 1)
            if q >= 0:
                a, b = self.pos, q + 1
                self.pos = b
                size = b - a
                if not keep:
                    return None, size
                if self._keep(size, force):
                    return self._decode(self.txt[a:b]), size
                return Besar("teks", size), size
            if len(self.txt) - self.pos > self.window or not self._more():
                break
        if self.eof and len(self.txt) - self.pos <= self.window:
            raise ValueError("JSON terpotong di dalam string")
        self._skip_string()
        size = self._at() - start
        return (Besar("teks", size) if keep else None), size

    def _skip_string(self):
        """Lompati string raksasa per potongan tanpa menyimpannya (pos tepat di tanda kutip pembuka)."""
        self.pos += 1
        esc = False
        while True:
            txt = self.txt
            i = self.pos + (1 if esc else 0)
            esc = False
            q = _string_end(txt, i) if i <= len(txt) else -1
            if q >= 0:
                self.pos = q + 1
                return
            n = len(txt)
            b = n - 1
            while b >= i and txt[b] == "\\":
                b -= 1
            esc = i <= n and (n - 1 - b) % 2 == 1
            self.pos = n
            if not self._more():
                raise ValueError("JSON terpotong di dalam string")

    # -- nilai --
    def value(self, keep, level=0):
        """Baca satu nilai. Mengembalikan (nilai | Besar | None, ukuran dalam byte); nilai None kalau tidak
        diminta disimpan (keep=False)."""
        self._ws()
        c = self._peek()
        if not c:
            raise ValueError("JSON berakhir tiba-tiba")
        if c == '"':
            return self._string(keep)
        if c in "{[":
            return self._container(keep, level)
        while True:
            m = _SCALAR_RE.match(self.txt, self.pos)
            # Angka bisa terbelah di batas potongan ("1." / "1e" / "1e+"): dianggap selesai hanya kalau
            # karakter sesudahnya jelas BUKAN lanjutan angka (atau berkas sudah habis).
            if m and (self.eof or (m.end() < len(self.txt) and self.txt[m.end()] not in "0123456789.eE+-")):
                break
            if not m and len(self.txt) - self.pos >= 12:
                raise ValueError("JSON rusak: nilai tidak dikenal")
            if not self._more():
                if m:
                    break
                raise ValueError("JSON rusak: nilai tidak dikenal atau terpotong")
        self.pos = m.end()
        text = m.group()
        return (json.loads(text) if keep else None), len(text)

    def _container(self, keep, level):
        start = self._at()
        self._ensure(self.window)
        p0 = self.pos
        opener = self.txt[p0]
        try:
            obj, end = _DECODER.raw_decode(self.txt, p0)
        except json.JSONDecodeError as exc:
            # Gagal karena jendela habis di tengah nilai (bukan karena JSON rusak) -> bongkar per anak.
            if not (exc.pos >= len(self.txt) - 64 or exc.msg.startswith("Unterminated")):
                raise ValueError(f"JSON rusak ({exc.msg}) di sekitar byte {self.base + exc.pos}") from None
            return self._descend(keep, level, start)
        except RecursionError:
            raise ValueError("JSON terlalu dalam bersarang") from None
        self.pos = end
        size = end - p0
        if not keep:
            return None, size
        if self._keep(size):
            segment = self.txt[p0:end]
            return (obj if segment.isascii() else self._decode(segment)), size
        if opener == "{":
            return Besar("objek", size, len(obj), kunci=list(obj)[:100]), size
        return Besar("larik", size, len(obj)), size

    def _descend(self, keep, level, start):
        """Nilai terlalu besar untuk jendela: telusuri anak-anaknya satu per satu."""
        if level >= _SCAN_DEPTH:
            raise ValueError("JSON terlalu dalam bersarang")
        opener = self.txt[self.pos]
        closer = "}" if opener == "{" else "]"
        self.pos += 1
        count = 0
        children = {}
        names = []
        while True:
            self._ws()
            c = self._peek()
            if c == closer and count == 0:
                self.pos += 1
                break
            if opener == "{":
                if c != '"':
                    raise ValueError("JSON rusak: kunci objek diharapkan")
                key, _ = self._string(True, force=True)
                if not isinstance(key, str):
                    raise ValueError("JSON rusak: kunci objek terlalu besar")
                self._ws()
                if self._peek() != ":":
                    raise ValueError("JSON rusak: ':' diharapkan")
                self.pos += 1
                keep_child = keep and count < self.max_keys
                value, _ = self.value(keep_child, level + 1)
                if keep_child:
                    children[key] = value
                if len(names) < 100:
                    names.append(key)
            else:
                self.value(False, level + 1)
            count += 1
            self._ws()
            c = self._peek()
            if c == ",":
                self.pos += 1
            elif c == closer:
                self.pos += 1
                break
            else:
                raise ValueError("JSON rusak: ',' atau penutup diharapkan")
        size = self._at() - start
        if not keep:
            return None, size
        if opener == "{":
            return Besar("objek", size, count, isi=children, kunci=names), size
        return Besar("larik", size, count), size


def scan_json(path, small_limit=_SCAN_SMALL, window=_SCAN_WINDOW, chunk=_SCAN_CHUNK, max_keys=_SCAN_MAXKEYS,
              budget=_SCAN_BUDGET):
    """Baca berkas JSON BESAR tanpa memuat semuanya ke memori. Nilai kecil (<= small_limit byte hasil
    serialisasi) dikembalikan utuh; nilai yang lebih besar diganti objek Besar (jenis, ukuran byte,
    jumlah isi) dan objek besar dibongkar sampai nilai kecil di dalamnya ikut terambil. Memori
    puncaknya kira-kira window x beberapa kali lipat + budget, tidak bergantung pada ukuran berkas.
    Melempar ValueError kalau JSON rusak/terpotong."""
    with open(path, "rb") as f:
        if f.read(3) != b"\xef\xbb\xbf":
            f.seek(0)
        scanner = _Scanner(f, chunk, window, small_limit, max_keys, budget)
        result, _ = scanner.value(True)
        scanner._ws()
        if scanner._peek():
            raise ValueError("JSON rusak: ada teks sisa setelah nilai utama")
        return result


_SMALL_FILE = 8 * 1024 * 1024


def _load_result(path):
    """Isi berkas hasil REA: json.load biasa untuk berkas <= 8 MB (memori ~8x ukurannya), pemindai aliran
    untuk yang lebih besar."""
    if os.path.getsize(path) <= _SMALL_FILE:
        with open(path, "rb") as f:
            return json.load(f)
    return scan_json(path)


# --- akses data hasil (dict biasa ATAU kerangka dari pemindai) -----------------------------------
_MISSING = object()


def _g(obj, *path, default=None):
    """obj[path[0]][path[1]]... dari dict biasa atau Besar objek; default kalau ada yang tidak ada."""
    for key in path:
        if isinstance(obj, Besar):
            obj = obj.isi or {}
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key, _MISSING)
        if obj is _MISSING:
            return default
    return obj


def _list(obj):
    return obj if isinstance(obj, list) else []


def _count(obj):
    if isinstance(obj, Besar):
        return obj.jumlah
    if isinstance(obj, (list, dict)):
        return len(obj)
    return None


def _dict(obj):
    if isinstance(obj, Besar):
        return obj.isi or {}
    return obj if isinstance(obj, dict) else {}


# --- info versi di dalam berkas PE (blok RT_VERSION) ----------------------------------------------
def _align4(n):
    return (n + 3) & ~3


def _version_block(buf, off, limit):
    """Satu blok VS_VERSIONINFO/StringTable/String: (panjang_nilai, tipe, kunci, awal_nilai, akhir)."""
    if off < 0 or off + 6 > limit:
        raise ValueError("blok versi terpotong")
    length, value_len, kind = struct.unpack_from("<HHH", buf, off)
    end = off + length
    if length < 6 or end > limit:
        raise ValueError("panjang blok versi tidak sah")
    q = off + 6
    while q + 2 <= end and buf[q:q + 2] != b"\x00\x00":
        q += 2
    key = buf[off + 6:q].decode("utf-16-le", "replace")
    return value_len, kind, key, _align4(q + 2), end


def _parse_version_info(blob):
    """Uraikan blok RT_VERSION (VS_VERSIONINFO) -> {"FileVersion": ..., "ProductName": ..., ...}
    plus "_numeric" (versi berkas dari VS_FIXEDFILEINFO). Melempar ValueError kalau bentuknya salah."""
    out = {}
    value_len, _, key, start, end = _version_block(blob, 0, len(blob))
    if key != "VS_VERSION_INFO":
        raise ValueError("bukan VS_VERSION_INFO")
    if value_len >= 52 and start + 16 <= end:
        sig, _, ms, ls = struct.unpack_from("<IIII", blob, start)
        if sig == 0xFEEF04BD:
            out["_numeric"] = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    pos = _align4(start + value_len)
    while pos + 6 <= end:
        _, _, name, content, block_end = _version_block(blob, pos, end)
        if name == "StringFileInfo":
            table = content
            while table + 6 <= block_end:
                _, _, _, entry, table_end = _version_block(blob, table, block_end)
                while entry + 6 <= table_end:
                    s_len, _, skey, sval, s_end = _version_block(blob, entry, table_end)
                    if s_len:
                        text = blob[sval:sval + 2 * s_len].decode("utf-16-le", "replace").rstrip("\x00")
                        out.setdefault(skey, text)
                    entry = _align4(s_end)
                table = _align4(table_end)
        pos = _align4(block_end)
    return out


def _read_version_info(nr):
    """Info versi dari blok RT_VERSION berkas PE, di lokasi yang DILAPORKAN REA (offset & hash
    payload-nya dicocokkan dulu). REA 6.7.0 sendiri tidak menguraikan isi blok ini. {} kalau gagal."""
    try:
        path = _g(nr, "artifact", "path")
        if not isinstance(path, str):
            return {}
        for res in _list(_g(nr, "resources")):
            rtype = res.get("type") if isinstance(res, dict) else None
            if not (isinstance(rtype, dict) and rtype.get("kind") == "id" and rtype.get("id") == 16):
                continue
            payload = res.get("payload") or {}
            loc = payload.get("location") or {}
            off, size = loc.get("offset"), loc.get("bytes")
            if not (isinstance(off, int) and isinstance(size, int) and 0 < size <= 262144):
                continue
            with open(path, "rb") as f:
                f.seek(off)
                blob = f.read(size)
            if len(blob) != size:
                return {}
            digest = payload.get("sha256")
            if digest and hashlib.sha256(blob).hexdigest() != digest:
                return {}  # berkas sudah berubah sejak dianalisis REA
            return _parse_version_info(blob)
    except (OSError, ValueError, struct.error, AttributeError, TypeError):
        return {}
    return {}


# --- ringkasan hasil ---------------------------------------------------------------------------
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]+")


def _oneline(text):
    """Satu baris tanpa karakter kontrol: teks dari berkas yang dianalisis (tak tepercaya) tidak boleh
    memalsukan baris/bagian ringkasan ("Catatan:", "Galat:") atau menyelipkan perintah palsu."""
    return _CTRL_RE.sub(" ", str(text)).strip()


def _clean_summary(summary):
    """Salinan ringkasan dengan semua teksnya satu baris (lihat _oneline)."""
    if not isinstance(summary, dict):
        return summary
    return {
        "title": _oneline(summary.get("title") or ""),
        "facts": [[_oneline(k), _oneline(v)] for k, v in summary.get("facts") or []],
        "notes": [_oneline(n) for n in summary.get("notes") or []],
        "errors": [_oneline(e) for e in summary.get("errors") or []],
    }


def _path_variants(path):
    """Penulisan lain dari path yang sama yang bisa tercetak di keluaran REA (garis miring maju/mundur, awalan
    \\\\?\\ Windows), yang terpanjang dulu supaya penggantian tidak meninggalkan sisa."""
    text = str(path)
    found = {text, text.replace("\\", "/"), text.replace("/", "\\")}
    for item in list(found):
        found.add(item[4:] if item.startswith("\\\\?\\") else "\\\\?\\" + item)
    return sorted((v for v in found if v), key=len, reverse=True)


def _relabel_text(text, target, label):
    """Ganti path penyimpanan internal `target` di `text` dengan nama tampilan `label` (nama asli berkas yang
    diunggah): pemilik tidak perlu melihat path folder data It's Me."""
    if not isinstance(text, str) or not target or not label:
        return text
    for variant in _path_variants(target):
        if variant in text:
            text = text.replace(variant, label)
    return text


def _relabel(summary, target, label):
    """Salinan ringkasan dengan path internal diganti nama tampilan (lihat _relabel_text)."""
    if not isinstance(summary, dict) or not target or not label:
        return summary

    def swap(value):
        return _relabel_text(value, target, label)

    return {
        "title": swap(summary.get("title") or ""),
        "facts": [[swap(k), swap(v)] for k, v in summary.get("facts") or []],
        "notes": [swap(n) for n in summary.get("notes") or []],
        "errors": [swap(e) for e in summary.get("errors") or []],
    }


class _Sum:
    """Penyusun ringkasan: judul + fakta [label, nilai] + catatan + galat."""

    def __init__(self, title):
        self.title = _oneline(title)
        self.facts = []
        self.notes = []
        self.errors = []

    def fact(self, label, value):
        if value is None or value == "":
            return
        self.facts.append([_oneline(label), _oneline(_short(value, 400))])

    def note(self, text):
        if text:
            self.notes.append(_oneline(_short(text, 400)))

    def err(self, text):
        if text:
            self.errors.append(_oneline(_short(text, 600)))

    def as_dict(self):
        return {"title": self.title, "facts": self.facts, "notes": self.notes, "errors": self.errors}


def _render(summary, limit=SUMMARY_MAX_CHARS):
    """Teks ringkasan untuk Telegram/dashboard, dipotong rapi per baris kalau melebihi `limit`."""
    lines = [str(summary.get("title") or "Hasil REA")]
    for label, value in summary.get("facts") or []:
        lines.append(f"- {label}: {value}")
    if summary.get("notes"):
        lines += ["", "Catatan:"] + [f"- {n}" for n in summary["notes"]]
    if summary.get("errors"):
        lines += ["", "Galat:"] + [f"- {e}" for e in summary["errors"]]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    cut = text[:limit - 70]
    cut = cut[:cut.rfind("\n")] if "\n" in cut else cut
    return cut + "\n... (dipotong; hasil lengkap ada di dashboard > Pengaturan > REA)"


_FORMAT_NAMES = {
    "pe": "PE (program/pustaka Windows)", "apk": "APK (aplikasi Android)", "zip": "ZIP",
    "asar": "ASAR (paket aplikasi Electron)", "jar": "JAR", "elf": "ELF (Linux)", "macho": "Mach-O (macOS)",
    "file": "berkas biasa",
}
_MACHINES = {0x14c: "x86 (32-bit)", 0x8664: "x86-64", 0x1c0: "ARM", 0x1c4: "ARM Thumb-2", 0xaa64: "ARM64"}
_RT_NAMES = {1: "RT_CURSOR", 2: "RT_BITMAP", 3: "RT_ICON", 4: "RT_MENU", 5: "RT_DIALOG", 6: "RT_STRING",
             7: "RT_FONTDIR", 8: "RT_FONT", 9: "RT_ACCELERATOR", 10: "RT_RCDATA", 11: "RT_MESSAGETABLE",
             12: "RT_GROUP_CURSOR", 14: "RT_GROUP_ICON", 16: "RT_VERSION", 17: "RT_DLGINCLUDE", 19: "RT_PLUGPLAY",
             20: "RT_VXD", 21: "RT_ANICURSOR", 22: "RT_ANIICON", 23: "RT_HTML", 24: "RT_MANIFEST"}
_LANGS = {0: "netral", 1033: "en-US", 2057: "en-GB", 1057: "id-ID", 1031: "de-DE", 1036: "fr-FR", 1034: "es-ES",
          3082: "es-ES", 1040: "it-IT", 1041: "ja-JP", 1042: "ko-KR", 2052: "zh-CN", 1028: "zh-TW",
          1049: "ru-RU", 1046: "pt-BR", 2070: "pt-PT", 1055: "tr-TR", 1045: "pl-PL", 1043: "nl-NL"}


def _subject(data):
    subject = _g(data, "subject")
    return subject if isinstance(subject, dict) else {}


def _base(path):
    text = str(path or "").rstrip("/\\")
    return re.split(r"[\\/]", text)[-1] if text else "berkas"


def _sum_artifact(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    fmt = subj.get("format") or _g(nr, "subject", "root_format")
    s = _Sum(f"Inventaris berkas: {name}")
    s.fact("Berkas", subj.get("local_path") or ctx.get("target"))
    s.fact("Format", _FORMAT_NAMES.get(fmt, fmt))
    s.fact("Arsitektur", subj.get("architecture"))
    s.fact("SHA-256", _g(subj, "digest", "sha256") or _g(nr, "subject", "root_sha256"))
    inv = None
    for step in _list(_g(nr, "substeps")):
        if isinstance(step, dict) and step.get("operation") == "inventory_artifact":
            inv = _g(step, "evidence", "normalized_result")
            break
    manifest = _g(inv, "manifest") if inv else None
    if not manifest:
        for obs in _list(_g(nr, "observations")):
            if isinstance(obs, dict) and obs.get("kind") == "root-manifest":
                manifest = obs.get("value")
                break
    manifest = _dict(manifest)
    occurrences = [o for o in _list(_g(inv, "occurrences")) if isinstance(o, dict)] if inv else []
    nodes = [n for n in _list(_g(inv, "nodes")) if isinstance(n, dict)] if inv else []
    root_id = manifest.get("root_artifact_id")
    root = next((n for n in nodes if n.get("artifact_id") == root_id), None)
    if root:
        s.fact("Ukuran", _fmt_size_full(root.get("size")))
    entries = manifest.get("occurrence_count")
    if isinstance(entries, int):
        if entries <= 1:
            s.fact("Isi", "satu berkas biasa (bukan wadah; tidak ada berkas di dalamnya)")
        else:
            total = sum(o.get("declared_size") or 0 for o in occurrences if o.get("parent_occurrence_id"))
            extra = f", total terurai {_fmt_size(total)}" if total else ""
            s.fact("Isi", f"{_fmt_n(entries - 1)} entri di dalam wadah{extra}")
    children = [str(o.get("logical_path")) for o in occurrences if o.get("parent_occurrence_id")]
    if fmt == "apk" and children:
        _apk_facts(s, children)
    elif children:
        tops = Counter(p.split("/")[0] for p in children).most_common(6)
        s.fact("Isi teratas", ", ".join(f"{name_} ({_fmt_n(c)})" for name_, c in tops))
    cov = _g(nr, "coverage", "status")
    if cov and cov != "complete-within-substeps":
        s.fact("Cakupan", cov)
    probes = {str(p.get("operation")) for p in _list(_g(nr, "next_probes")) if isinstance(p, dict)}
    if "inspect_managed_artifact" in probes:
        s.note("Dari inventaris saja belum bisa dibedakan apakah program ini .NET atau native: coba "
               "inspect-managed-artifact.")
    if "open_binary" in probes:
        s.note("Analisis kode native yang dalam (open_binary) butuh mesin seperti Ghidra/Hopper/IDA yang diatur "
               "terpisah di REA (cek `rea doctor`).")
    if fmt == "apk":
        s.note("Manifest, izin, dan sertifikat APK tidak diuraikan perintah ini; inspect-android-package butuh "
               "JADX dan decode-android-resources butuh apktool. Blok tanda tangan APK v2+ tidak dibaca REA.")
    return s.as_dict()


def _apk_facts(s, paths):
    dex = [p for p in paths if re.fullmatch(r"classes\d*\.dex", p)]
    abis = sorted({p.split("/")[1] for p in paths if p.startswith("lib/") and p.count("/") >= 2})
    libs = [p for p in paths if p.startswith("lib/") and p.endswith(".so")]
    versions = sorted(p[len("META-INF/"):-len(".version")].replace("_", ":", 1) for p in paths
                      if p.startswith("META-INF/") and p.endswith(".version") and p.count("/") == 1)
    sig_v1 = [p for p in paths if re.fullmatch(r"META-INF/[^/]+\.(RSA|DSA|EC)", p, re.IGNORECASE)]
    s.fact("AndroidManifest.xml", "ada" if "AndroidManifest.xml" in paths else "tidak ada")
    s.fact("Kode (DEX)", f"{len(dex)} berkas" if dex else "tidak ada classes.dex")
    s.fact("resources.arsc", "ada" if "resources.arsc" in paths else "tidak ada")
    s.fact("Sumber daya (res/)", f"{_fmt_n(sum(1 for p in paths if p.startswith('res/')))} berkas")
    s.fact("Aset (assets/)", f"{_fmt_n(sum(1 for p in paths if p.startswith('assets/')))} berkas")
    s.fact("Pustaka native", f"{len(libs)} berkas .so, ABI: {', '.join(abis)}" if libs else "tidak ada (tanpa lib/*.so)")
    if versions:
        shown = ", ".join(versions[:6]) + (f", +{len(versions) - 6} lagi" if len(versions) > 6 else "")
        s.fact("Pustaka AndroidX/Jetpack", f"{len(versions)} (dari META-INF/*.version): {shown}")
    s.fact("Tanda tangan v1 (META-INF)", ", ".join(sig_v1) if sig_v1 else
           "tidak ada berkas .RSA/.DSA/.EC (mungkin hanya bertanda tangan v2+, yang tidak dibaca REA)")
    if any(p.startswith("kotlin/") or p == "kotlin-tooling-metadata.json" for p in paths):
        s.fact("Kotlin", "terdeteksi (berkas metadata/builtins Kotlin)")


def _sum_pe_resources(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Sumber daya PE: {name}")
    art = _dict(_g(nr, "artifact"))
    fmt = _g(nr, "format")
    s.fact("Format", {"pe32-plus": "PE32+ (64-bit)", "pe32": "PE32 (32-bit)"}.get(fmt, fmt))
    machine = _g(nr, "machine")
    if isinstance(machine, int):
        s.fact("Mesin (CPU)", f"{_MACHINES.get(machine, 'tidak dikenal')} (0x{machine:04x})")
    s.fact("Ukuran", _fmt_size_full(art.get("bytes")))
    s.fact("SHA-256", art.get("sha256"))
    resources = [r for r in _list(_g(nr, "resources")) if isinstance(r, dict)]
    cov = _dict(_g(nr, "coverage"))
    total = cov.get("resources", len(resources))
    s.fact("Sumber daya", f"{_fmt_n(total)} (dari {_fmt_n(cov.get('examined_entries', 0))} entri direktori diperiksa)")
    types = Counter()
    langs = set()
    for res in resources:
        rtype = _dict(res.get("type"))
        types[_RT_NAMES.get(rtype.get("id"), f"RT_{rtype.get('id')}") if rtype.get("kind") == "id"
              else str(rtype.get("name"))] += 1
        lang = _dict(res.get("language"))
        if lang.get("kind") == "id":
            langs.add(lang.get("id"))
    if types:
        s.fact("Jenis sumber daya", ", ".join(f"{k} x{v}" for k, v in types.most_common(10)))
    if langs:
        s.fact("Bahasa", ", ".join(f"{_LANGS.get(i, i)}" for i in sorted(langs, key=str)))
    groups = [g for g in _list(_g(nr, "icon_groups")) if isinstance(g, dict)]
    if groups:
        sizes = [max(w or 256, h or 256) for g in groups for im in _list(g.get("images"))
                 if isinstance(im, dict) for w, h in [(im.get("width"), im.get("height"))]]
        extra = f", gambar terbesar {max(sizes)}x{max(sizes)}" if sizes else ""
        s.fact("Ikon", f"{len(groups)} grup{extra}")
    if total == 0:
        s.note("Tidak ada sumber daya PE (tanpa ikon, manifest, atau info versi).")
    info = _read_version_info(nr)
    if info:
        s.fact("Produk", info.get("ProductName"))
        s.fact("Perusahaan", info.get("CompanyName"))
        s.fact("Deskripsi", info.get("FileDescription"))
        s.fact("Versi berkas", info.get("FileVersion") or info.get("_numeric"))
        if info.get("ProductVersion") and info.get("ProductVersion") != info.get("FileVersion"):
            s.fact("Versi produk", info.get("ProductVersion"))
        s.fact("Nama asli", info.get("OriginalFilename"))
        s.fact("Hak cipta", info.get("LegalCopyright"))
        s.note("Info versi (produk, perusahaan, dst.) dibaca langsung dari blok RT_VERSION di lokasi yang "
               "dilaporkan REA; REA 6.7.0 sendiri tidak menguraikannya.")
    s.note("Subsistem, waktu build, tabel import, dan tanda tangan digital PE tidak dilaporkan perintah ini.")
    return s.as_dict()


def _sum_go(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Program Go: {name}")
    art = _dict(_g(nr, "artifact"))
    build = _dict(_g(nr, "build_info"))
    module = _dict(build.get("module"))
    main = _dict(module.get("main"))
    s.fact("Versi Go", build.get("go_version"))
    s.fact("Modul utama", " ".join(x for x in (main.get("path") or module.get("path"), main.get("version")) if x))
    deps = [d for d in _list(module.get("dependencies")) if isinstance(d, dict)]
    s.fact("Dependensi", f"{_fmt_n(len(deps))} modul" + (": " + ", ".join(str(d.get("path")) for d in deps[:5])
                                                         + (", ..." if len(deps) > 5 else "") if deps else ""))
    settings = {str(x.get("key")): str(x.get("value")) for x in _list(module.get("settings")) if isinstance(x, dict)}
    goos, goarch = settings.get("GOOS"), settings.get("GOARCH")
    if goos or goarch:
        s.fact("Target build", f"{goos or '?'}/{goarch or '?'}")
    if "CGO_ENABLED" in settings:
        s.fact("CGO", "aktif" if settings["CGO_ENABLED"] == "1" else "mati")
    flags = [f"{k.lstrip('-')}={v}" for k, v in settings.items() if k.startswith("-")]
    s.fact("Opsi build", ", ".join(flags))
    if settings.get("vcs.revision"):
        when = f" ({settings['vcs.time']})" if settings.get("vcs.time") else ""
        dirty = {"true": ", ada perubahan lokal", "false": ", tanpa perubahan lokal"}.get(settings.get("vcs.modified"), "")
        s.fact("Revisi VCS", f"{settings.get('vcs', 'vcs')} {settings['vcs.revision'][:12]}{when}{dirty}")
    arch = _g(nr, "architecture")
    bits = _g(nr, "bits")
    s.fact("Biner", " ".join(str(x) for x in (_g(nr, "format"), arch, f"{bits}-bit" if bits else None) if x))
    s.fact("Ukuran", _fmt_size_full(art.get("bytes")))
    s.fact("SHA-256", art.get("sha256"))
    if module.get("complete") is False:
        s.note("Daftar modul/setelan build tidak lengkap (REA melaporkan sebagian).")
    return s.as_dict()


_FUSE_NOTES = {
    ("node_cli_inspect_arguments", "not_honored"): "Opsi --inspect/--inspect-brk diabaikan (debugger Node tidak bisa ditempel).",
    ("node_cli_inspect_arguments", "honored"): "Opsi --inspect/--inspect-brk DITERIMA (debugger Node bisa ditempel).",
    ("electron_run_as_node", "honored"): "ELECTRON_RUN_AS_NODE dihormati: biner bisa dijalankan sebagai Node biasa.",
    ("electron_run_as_node", "not_honored"): "ELECTRON_RUN_AS_NODE diabaikan.",
    ("embedded_asar_integrity_validation", "enforced"): "Integritas ASAR tertanam DIPAKSA.",
    ("embedded_asar_integrity_validation", "not_enforced"): "Integritas ASAR tertanam tidak dipaksa.",
    ("only_load_app_from_asar", "restricted_to_app_asar"): "Kode aplikasi hanya dimuat dari app.asar.",
    ("only_load_app_from_asar", "unrestricted"): "Kode aplikasi boleh dimuat dari luar app.asar.",
}
_FUSE_STATES = {"enabled": "AKTIF", "disabled": "mati", "removed": "dihapus", "unknown": "tidak diketahui"}


def _sum_fuses(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Fuse Electron: {name}")
    art = _dict(_g(nr, "artifact"))
    s.fact("Berkas", art.get("resolved_path") or art.get("selected_path"))
    s.fact("Format", " ".join(str(x) for x in (art.get("format"), subj.get("architecture")) if x))
    s.fact("Ukuran", _fmt_size_full(art.get("bytes")))
    sentinels = [(sl, st) for sl in _list(_g(nr, "slices")) if isinstance(sl, dict)
                 for st in _list(sl.get("sentinels")) if isinstance(st, dict)]
    if not sentinels:
        s.fact("Sentinel fuse", "tidak ditemukan")
        s.note("Tanpa sentinel, status fuse TIDAK DIKETAHUI (bukan berarti semuanya mati); mungkin ini bukan "
               "biner utama Electron.")
        return s.as_dict()
    sl, st = sentinels[0]
    fuses = [f for f in _list(st.get("fuses")) if isinstance(f, dict)]
    s.fact("Sentinel fuse", f"{len(sentinels)} (wire v{st.get('wire_version')}, {len(fuses)} fuse"
                            + ("" if st.get("complete", True) else ", TERPOTONG") + ")")
    groups = {}
    for f in fuses:
        label = _FUSE_STATES.get(str(f.get("state")), str(f.get("state")))
        groups.setdefault(label, []).append(str(f.get("name") or f"fuse#{f.get('index')}"))
    for label in ("AKTIF", "mati", "dihapus", "tidak diketahui"):
        if label in groups:
            s.fact(f"Fuse {label}", ", ".join(groups[label]))
    for imp in _list(_g(nr, "runtime_implications")):
        if isinstance(imp, dict):
            key = (str(imp.get("subject")), str(imp.get("outcome")))
            s.note(_FUSE_NOTES.get(key) or f"{key[0]}: {key[1]}")
    if len(sentinels) > 1:
        s.note(f"Ada {len(sentinels)} sentinel; yang dirangkum di sini hanya yang pertama (lihat hasil lengkap).")
    s.note("Status fuse tidak membuktikan aplikasi tidak membuka Inspector lewat cara lain (mis. app.commandLine).")
    return s.as_dict()


_JS_SUMMARY_LABELS = {
    "browser_windows": "Jendela browser (BrowserWindow)",
    "explicit_web_preferences": "Preferensi web eksplisit",
    "preload_entrypoints": "Titik masuk preload",
    "context_bridge_apis": "API jembatan konteks (contextBridge)",
    "exposed_api_members": "Anggota API terekspos",
    "sender_validation_observations": "Pengamatan validasi pengirim IPC",
    "utility_processes": "Proses utilitas",
    "resolved_utility_entrypoints": "Titik masuk utilitas terselesaikan",
    "native_addon_bindings": "Pengikatan addon native",
    "resolved_native_addon_bindings": "Pengikatan addon native terselesaikan",
}
_JS_IPC_LABELS = {
    "operations": "operasi", "literal_channels": "kanal literal", "dynamic_channel_operations": "operasi kanal dinamis",
    "renderer_transmissions": "pengiriman renderer", "renderer_listeners": "pendengar renderer",
    "main_handlers": "penangan proses utama", "paired_renderer_transmissions": "berpasangan",
    "ambiguous_renderer_transmissions": "ambigu", "unpaired_literal_renderer_transmissions": "literal tanpa pasangan",
}


def _sum_js(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Analisis aplikasi JavaScript: {name}")
    s.fact("Berkas", _g(nr, "input_path") or ctx.get("target"))
    s.fact("Format", _FORMAT_NAMES.get(_g(nr, "format"), _g(nr, "format")))
    stats = _dict(_g(nr, "statistics"))
    if stats:
        s.fact("Berkas relevan", _fmt_n(stats.get("relevant_files")))
        parsed = _fmt_n(stats.get("parsed_javascript_files"))
        s.fact("Berkas JavaScript diurai", f"{parsed} (dilewati {_fmt_n(stats.get('skipped_javascript_files', 0))}, "
                                           f"gagal {_fmt_n(stats.get('parse_failures', 0))})")
        if stats.get("text_bytes_read") is not None:
            s.fact("Teks dibaca", _fmt_size(stats.get("text_bytes_read")))
        if stats.get("visited_ast_nodes") is not None:
            s.fact("Simpul AST dikunjungi", _fmt_n(stats.get("visited_ast_nodes")))
        s.fact("Temuan", _fmt_n(stats.get("findings")))
        if stats.get("modules"):
            s.fact("Modul", _fmt_n(stats.get("modules")))
        if stats.get("nested_asar_containers"):
            s.fact("ASAR bersarang", _fmt_n(stats.get("nested_asar_containers")))
    summary = _dict(_g(nr, "summary"))
    for key, label in _JS_SUMMARY_LABELS.items():
        if key in summary and not isinstance(summary[key], (dict, list)):
            s.fact(label, _fmt_n(summary[key]))
    ipc = _dict(summary.get("ipc"))
    if ipc:
        parts = [f"{_fmt_n(ipc[k])} {label}" for k, label in _JS_IPC_LABELS.items() if k in ipc]
        s.fact("IPC", ", ".join(parts))
    for key, value in summary.items():
        if key not in _JS_SUMMARY_LABELS and key != "ipc" and not isinstance(value, (dict, list)):
            s.fact(key.replace("_", " ").capitalize(), _fmt_n(value))
    nodes, edges = _count(_g(nr, "graph", "nodes")), _count(_g(nr, "graph", "edges"))
    if nodes is not None or edges is not None:
        s.fact("Graf aplikasi", f"{_fmt_n(nodes)} simpul, {_fmt_n(edges)} sisi")
    sem_nodes, sem_rel = _count(_g(nr, "semantic_graph", "nodes")), _count(_g(nr, "semantic_graph", "relations"))
    if sem_nodes is not None or sem_rel is not None:
        unknown = _count(_g(nr, "semantic_graph", "unknowns"))
        s.fact("Graf semantik", f"{_fmt_n(sem_nodes)} simpul, {_fmt_n(sem_rel)} relasi"
                                + (f", {_fmt_n(unknown)} tak-terselesaikan" if unknown is not None else ""))
    contradictions = [c for c in _list(_g(nr, "integrity_contradictions")) if isinstance(c, dict)]
    if contradictions:
        shown = ", ".join(str(c.get("logical_path")) for c in contradictions[:3])
        s.fact("Integritas ASAR", f"{len(contradictions)} berkas tidak cocok dengan hash yang dideklarasikan: {shown}"
                                  + (", ..." if len(contradictions) > 3 else ""))
        s.note("Ketidakcocokan integritas dicatat dan analisis dilanjutkan (--integrity-policy record-and-continue); "
               "biasanya biner native ditandatangani ulang setelah dikemas.")
    coverage = _g(nr, "graph", "coverage", "status")
    if coverage and coverage != "complete":
        s.fact("Cakupan graf", f"{coverage} (hasil dibatasi anggaran REA; lihat hasil lengkap)")
    size = ctx.get("result_bytes")
    if isinstance(size, int) and size > _SMALL_FILE:
        s.note(f"Hasil lengkap {_fmt_size(size)}: ringkasan ini dibuat dengan pemindai aliran. Berkasnya bisa "
               "diunduh dari dashboard.")
    return s.as_dict()


def _sum_managed(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Cek .NET: {name}")
    art = _dict(_g(nr, "artifact"))
    pe = _dict(_g(nr, "pe"))
    cls = _dict(_g(nr, "classification"))
    status = cls.get("status")
    s.fact("Jenis", {"managed": ".NET (kode CIL terkelola)", "not-managed": "BUKAN program .NET (tanpa header CLI)"}
           .get(status, status))
    machine = " ".join(str(x) for x in (pe.get("architecture"), pe.get("optional_header")) if x)
    s.fact("PE", machine + (f", {pe.get('section_count')} section" if pe.get("section_count") else ""))
    s.fact("Ukuran", _fmt_size_full(art.get("byte_length")))
    s.fact("SHA-256", art.get("sha256"))
    if status == "managed":
        asm = _dict(_g(nr, "assembly"))
        if asm.get("name"):
            token = _g(asm, "public_key", "token")
            s.fact("Assembly", f"{asm.get('name')} {asm.get('version') or ''}".strip()
                   + (f" (public key token {token})" if token else ""))
        s.fact("Arsitektur terkelola", cls.get("managed_architecture"))
        s.fact("Implementasi", cls.get("implementation"))
        s.fact("Metadata CLR", _g(nr, "metadata", "version"))
        flags = _list(_g(pe, "cli", "flag_names"))
        s.fact("Flag CLI", ", ".join(map(str, flags)))
        frameworks = [str(f) if not isinstance(f, dict) else str(f.get("name") or f) for f in _list(_g(nr, "target_frameworks"))]
        s.fact("Target framework", ", ".join(frameworks))
        rows = _dict(_g(nr, "metadata", "table_row_counts"))
        if rows:
            s.fact("Isi metadata", ", ".join(f"{label} {_fmt_n(rows[k])}" for k, label in
                                             (("TypeDef", "tipe"), ("MethodDef", "metode"), ("Field", "field"),
                                              ("Property", "properti")) if k in rows))
        refs = [r for r in _list(_g(nr, "references")) if isinstance(r, dict)]
        if refs:
            shown = ", ".join(f"{r.get('name')} {r.get('version') or ''}".strip() for r in refs[:8])
            s.fact("Referensi assembly", f"{len(refs)}: {shown}" + (", ..." if len(refs) > 8 else ""))
        else:
            count = _count(_g(nr, "references"))
            if count:
                s.fact("Referensi assembly", _fmt_n(count))
        res = _count(_g(nr, "resources"))
        if res:
            s.fact("Sumber daya tertanam", _fmt_n(res))
        attrs = _count(_g(nr, "attributes"))
        if attrs:
            s.fact("Custom attribute", _fmt_n(attrs))
    else:
        for ev in _list(cls.get("evidence")):
            if isinstance(ev, dict) and ev.get("detail"):
                s.note(ev["detail"])
                break
    return s.as_dict()


def _sum_boundaries(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Batas native .NET: {name}")
    summary = _dict(_g(nr, "summary"))
    s.fact("Deklarasi P/Invoke", _fmt_n(summary.get("pinvoke_import_count")))
    s.fact("Pustaka native dirujuk", _fmt_n(summary.get("module_ref_count")))
    s.fact("Anggota berimplementasi native", _fmt_n(summary.get("native_implementation_count")))
    mods = [m.get("name") for m in _list(_g(nr, "module_refs")) if isinstance(m, dict)]
    if mods:
        s.fact("Pustaka", ", ".join(map(str, mods[:12])) + (", ..." if len(mods) > 12 else ""))
    if "ready_to_run" in summary:
        s.fact("ReadyToRun", "ya" if summary.get("ready_to_run") else "tidak")
    if "mixed_mode_or_native_header" in summary:
        s.fact("Mode campuran/header native", "ya" if summary.get("mixed_mode_or_native_header") else "tidak")
    s.note("P/Invoke hanya membuktikan deklarasi impor; REA tidak memeriksa apakah pustaka/fungsinya benar-benar ada.")
    return s.as_dict()


def _sum_rust(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    name = subj.get("name") or _base(ctx.get("target"))
    s = _Sum(f"Program Rust: {name}")
    art = _dict(_g(nr, "artifact"))
    s.fact("Biner", " ".join(str(x) for x in (_g(nr, "format"), _g(nr, "architecture"),
                                              f"{_g(nr, 'bits')}-bit" if _g(nr, "bits") else None) if x))
    s.fact("Ukuran", _fmt_size_full(art.get("bytes")))
    s.fact("SHA-256", art.get("sha256"))
    meta = _g(nr, "auditable_metadata")
    if meta is None:
        s.fact("Dependensi Rust tertanam", "tidak ada (.dep-v0 tidak ditemukan)")
        s.note("Tidak adanya .dep-v0 tidak berarti program ini tanpa dependensi.")
    else:
        pkgs = [p for p in _list(_g(meta, "packages")) if isinstance(p, dict)]
        roots = [p for p in pkgs if p.get("root")]
        if roots:
            s.fact("Paket utama", f"{roots[0].get('name')} {roots[0].get('version')}")
        s.fact("Dependensi Rust tertanam", f"{_fmt_n(len(pkgs))} paket")
        s.fact("Contoh paket", ", ".join(f"{p.get('name')} {p.get('version')}" for p in pkgs[:6]))
    commit = _dict(_g(nr, "rustc_commit")).get("commit") if isinstance(_g(nr, "rustc_commit"), dict) else None
    s.fact("Commit rustc", commit)
    return s.as_dict()


def _sum_flutter(data, ctx):
    nr = _g(data, "normalized_result")
    s = _Sum(f"Cek Flutter: {_base(ctx.get('target'))}")
    target = _dict(_g(nr, "target"))
    s.fact("Ukuran", _fmt_size_full(target.get("bytes")))
    s.fact("SHA-256", target.get("sha256"))
    if _g(nr, "flutter_detected"):
        s.fact("Flutter", "terdeteksi")
        s.fact("ABI", ", ".join(map(str, _list(_g(nr, "abis")))))
        for key, value in _dict(nr).items():
            if key not in ("target", "flutter_detected", "abis", "coverage") and not isinstance(value, (dict, list)):
                s.fact(key.replace("_", " ").capitalize(), value)
    else:
        s.fact("Flutter", "tidak terdeteksi (tidak ada lib/*/libapp.so)")
    s.fact("Cakupan", _g(nr, "coverage"))
    return s.as_dict()


def _sum_android(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    s = _Sum(f"Paket Android: {subj.get('name') or _base(ctx.get('target'))}")
    s.fact("Paket", _g(nr, "package_name"))
    version = " ".join(str(x) for x in (_g(nr, "version_name"), f"(kode {_g(nr, 'version_code')})"
                                        if _g(nr, "version_code") else None) if x)
    s.fact("Versi", version)
    s.fact("SDK minimum / target", " / ".join(str(_g(nr, k)) for k in ("min_sdk", "target_sdk") if _g(nr, k)))
    perms = [str(p) for p in _list(_g(nr, "permissions"))]
    if perms:
        short = [p.replace("android.permission.", "") for p in perms]
        s.fact("Izin", f"{len(perms)}: " + ", ".join(short[:15]) + (", ..." if len(short) > 15 else ""))
    else:
        s.fact("Izin", "tidak ada")
    comps = [c for c in _list(_g(nr, "components", "items")) if isinstance(c, dict)]
    if comps:
        kinds = Counter(str(c.get("kind")) for c in comps)
        s.fact("Komponen", ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
        exported = [c for c in comps if c.get("exported") is True]
        if exported:
            s.fact("Diekspor (exported=true)", f"{len(exported)}: " + ", ".join(str(c.get("name")) for c in exported[:6])
                   + (", ..." if len(exported) > 6 else ""))
    s.fact("Kelas / sumber daya", f"{_fmt_n(_g(nr, 'class_count'))} / {_fmt_n(_g(nr, 'resource_count'))}")
    s.fact("Mesin analisis", " ".join(str(x) for x in (_g(nr, "engine", "name"), _g(nr, "engine", "version")) if x))
    if _g(nr, "signature_verification") == "not_performed":
        s.note("Tanda tangan/sertifikat APK TIDAK diverifikasi oleh perintah ini.")
    status = _g(nr, "components", "status")
    if status and status != "complete":
        s.note(f"Inventaris komponen berstatus '{status}' (sebagian).")
    return s.as_dict()


def _sum_generic(data, ctx):
    nr = _g(data, "normalized_result")
    subj = _subject(data)
    op = _g(data, "operation")
    s = _Sum(f"Hasil REA: {ctx.get('command') or op or 'perintah'}")
    s.fact("Berkas", subj.get("local_path") or ctx.get("target"))
    s.fact("Operasi", op)
    s.fact("Format", subj.get("format"))
    s.fact("Arsitektur", subj.get("architecture"))
    s.fact("SHA-256", _g(subj, "digest", "sha256"))
    shown = 0
    for key, value in _dict(nr).items():
        if shown >= 14:
            break
        if isinstance(value, Besar):
            s.fact(key, f"{value.jenis} besar ({_fmt_size(value.bytes)}, {_fmt_n(value.jumlah)} isi)")
        elif isinstance(value, (list, dict)):
            s.fact(key, f"{_fmt_n(len(value))} {'butir' if isinstance(value, list) else 'kunci'}")
        elif value is not None:
            s.fact(key, value)
        else:
            continue
        shown += 1
    s.note("Perintah ini belum punya ringkasan khusus; unduh hasil lengkapnya dari dashboard untuk detailnya.")
    return s.as_dict()


_SUMMARIZERS = {
    "inspect_artifact": _sum_artifact,
    "inspect_pe_resources": _sum_pe_resources,
    "inspect_go_binary": _sum_go,
    "inspect_electron_fuses": _sum_fuses,
    "analyze_javascript_application": _sum_js,
    "inspect_managed_artifact": _sum_managed,
    "inspect_managed_native_boundaries": _sum_boundaries,
    "inspect_rust_binary": _sum_rust,
    "identify_flutter_build": _sum_flutter,
    "inspect_android_package": _sum_android,
}


def _summarize(data, ctx):
    """Ringkasan {"title","facts","notes","errors"} dari hasil sukses REA. Tidak pernah melempar."""
    op = str(_g(data, "operation") or str(ctx.get("command") or "").replace("-", "_"))
    func = _SUMMARIZERS.get(op, _sum_generic)
    try:
        return func(data, ctx)
    except Exception as exc:  # noqa: BLE001 - ringkasan tidak boleh menggagalkan job
        try:
            out = _sum_generic(data, ctx)
        except Exception:  # noqa: BLE001
            out = _Sum(f"Hasil REA: {ctx.get('command')}").as_dict()
        out["errors"] = [f"Ringkasan khusus gagal dibuat ({type(exc).__name__}: {_short(exc, 120)}); "
                         "ini ringkasan umum."]
        return out


# --- galat REA -> pesan ramah ---------------------------------------------------------------
def _is_error_json(data):
    """Bentuk galat REA: {code, message, ...} tanpa normalized_result (galat terstruktur maupun galat CLI)."""
    return (isinstance(data, dict) and isinstance(data.get("code"), str) and isinstance(data.get("message"), str)
            and "normalized_result" not in data)


_PROVIDER_HINTS = {
    "jadx": "Perintah ini membutuhkan JADX (jadx-headless-mcp 0.7.1 JAR) yang belum diatur. Setel variabel "
            "lingkungan REA_JADX_MCP_JAR ke path JAR-nya, lalu jalankan lagi.",
    "apktool": "Perintah ini membutuhkan apktool yang belum ditemukan. Pasang apktool, lalu setel "
               "REA_APKTOOL_COMMAND (atau taruh di PATH).",
    "native-macos": "Perintah ini hanya berjalan di macOS.",
    "pwntools-elf": "Perintah ini hanya mendukung ELF Linux x64 pada REA ini.",
}


def _explain_error(err, command):
    """(judul ringkas, [baris detail]) berbahasa Indonesia dari galat REA terstruktur."""
    code = str(err.get("code") or "")
    category = str(err.get("category") or "")
    message = str(err.get("message") or "")
    remediation = err.get("remediation")
    advice = str(remediation.get("action") or "") if isinstance(remediation, dict) else ""
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    provider = str(details.get("provider_id") or "")
    lines = []
    if code == "COMMAND_NOT_FOUND":
        title = f"Perintah '{command}' tidak dikenal oleh REA {REA_VERSION}."
    elif code == "UNKNOWN":
        title = "REA menolak argumen yang dikirim."
    elif details.get("constraint") == "directory_requires_file":
        title = ("Perintah ini butuh berkas, bukan folder. Untuk folder aplikasi JavaScript/Electron pakai "
                 "analyze-javascript-application.")
    elif details.get("constraint") == "file_format_rejected":
        # Ditemukan lewat uji nyata: REA membuka berkasnya TAPI menolak formatnya (mis. ".exe" yang bukan PE sah,
        # unduhan terpotong). Dulu jatuh ke "tidak ditemukan/tidak bisa dibuka", yang menyesatkan.
        reason = str(details.get("reason") or "")
        title = ("REA menolak berkas ini karena formatnya tidak valid atau tidak sesuai jenisnya"
                 + (f" ({_short(reason, 120)})" if reason else "")
                 + ". Biasanya berkasnya rusak/terpotong (unduhan belum lengkap), ekstensinya tidak sesuai "
                 "isinya, atau bukan program dari jenis itu.")
    elif code == "target_unavailable" or category == "unavailable":
        where = details.get("path") or ""
        title = "Berkas atau folder target tidak ditemukan atau tidak bisa dibuka" + (f": {where}" if where else ".")
    elif code == "capability_unavailable" or category == "unsupported_provider":
        title = next((hint for key, hint in _PROVIDER_HINTS.items() if key in provider.lower()), None)
        if not title:
            title = ("Perintah ini tidak tersedia untuk target atau lingkungan ini"
                     + (f" (butuh komponen '{provider}' yang belum terpasang/diatur; cek `rea doctor`)." if provider else "."))
    elif code == "artifact_integrity_mismatch" or category == "integrity_mismatch":
        title = ("Integritas paket tidak cocok dengan yang dideklarasikannya (biasanya biner di dalam ASAR "
                 "ditandatangani ulang setelah dikemas). analyze-javascript-application tetap melanjutkan dengan "
                 "mencatat ketidakcocokan; atau pakai salinan berkas yang segar.")
    elif code == "unsupported_target" or category == "unsupported_target":
        title = "Jenis target ini tidak didukung perintah tersebut. Coba inspect-artifact."
    elif code == "resource_constraint" or category == "resource_constraint":
        title = "Melebihi batas sumber daya internal REA (ukuran, jumlah entri, atau memori)."
    elif code == "provider_timeout" or category == "timeout":
        title = "Penyedia analisis REA melebihi batas waktunya sendiri."
    elif code == "access_denied":
        title = "Akses ke berkas ditolak (izin)."
    elif code == "artifact_changed":
        title = "Berkas berubah selama dianalisis; jalankan lagi."
    elif code == "cancelled" or category == "cancelled":
        title = "Analisis dibatalkan di sisi REA."
    elif code == "truncated" or category == "truncated":
        title = "Hasil REA terpotong."
    elif code == "invalid_request" or category == "invalid_input":
        title = "REA menganggap permintaan tidak valid (periksa target dan perintahnya)."
    else:
        title = f"REA melaporkan galat (kode: {code or 'tidak ada'})."
    if message:
        lines.append("Pesan REA: " + _short(message, 400))
    if advice and advice != message:
        lines.append("Saran REA: " + _short(advice, 400))
    suggestions = []
    cta = err.get("cta")
    if isinstance(cta, dict):
        for item in _list(cta.get("commands")):
            cmd = str(item.get("command") or "") if isinstance(item, dict) else ""
            m = re.match(r"rea (\S+)", cmd)
            if m and m.group(1) in COMMANDS:
                suggestions.append(m.group(1))
    if suggestions:
        lines.append("REA menyarankan: " + ", ".join(suggestions))
    return title, lines


def _explain_stderr(text, returncode):
    """Judul ramah dari keluaran galat (stderr) saat REA tidak menghasilkan JSON sama sekali."""
    low = text.lower()
    if "ebadengine" in low:
        return (f"Versi Node di komputer ini tidak memenuhi syarat rea-agents@{REA_VERSION} (peringatan "
                "EBADENGINE). Perbarui Node.js, atau pilih versi REA yang lebih lama lewat ITSME_REA_VERSION.")
    if "e404" in low or "404 not found" in low or "no matching version" in low:
        return f"Paket rea-agents@{REA_VERSION} tidak ditemukan di npm. Periksa nilai ITSME_REA_VERSION."
    if any(k in low for k in ("enotfound", "eai_again", "etimedout", "econnrefused", "econnreset", "fetch failed",
                              "network")):
        return (f"npx gagal mengunduh paket rea-agents@{REA_VERSION} dari npm (koneksi internet?). Unduhan hanya "
                "diperlukan saat pertama kali memakai versi ini.")
    if "eacces" in low or "eperm" in low:
        return "npx tidak punya izin menulis ke cache npm (EACCES/EPERM)."
    return f"REA berhenti dengan kode {returncode} tanpa menghasilkan JSON."


# --- progres dari stderr REA -------------------------------------------------------------------
_PHASES = {
    "analyze_javascript_application": "Mendata dan membaca isi aplikasi",
    "read_javascript_artifacts": "Membaca sumber JavaScript",
    "parse_javascript_sources": "Menyiapkan penguraian berkas",
    "parse_javascript_source": "Mengurai berkas",
    "process_javascript_artifact_metadata": "Memproses metadata berkas",
    "build_javascript_application_graph": "Membangun graf aplikasi dan batas Electron",
    "seal_javascript_application_graph": "Menyegel graf aplikasi",
    "build_javascript_semantic_graph": "Membangun graf semantik",
    "seal_javascript_semantic_graph": "Menyegel graf semantik",
    "validate_javascript_application_result": "Memvalidasi hasil",
    "create_javascript_application_evidence": "Membuat bukti (evidence) hasil",
    "seal_javascript_application_result": "Menyegel hasil lengkap",
    "validate_javascript_application_evidence": "Memvalidasi bukti",
    "hash_javascript_application_evidence": "Menghitung hash bukti",
}
_LATE_PHASES = ["build_javascript_application_graph", "seal_javascript_application_graph",
                "build_javascript_semantic_graph", "seal_javascript_semantic_graph",
                "validate_javascript_application_result", "create_javascript_application_evidence",
                "seal_javascript_application_result", "validate_javascript_application_evidence",
                "hash_javascript_application_evidence"]
_COUNTER_RE = re.compile(r"^(?P<what>.*?)\s*\((?P<i>\d+)/(?P<n>\d+)\)\s*$")


def _parse_progress(event):
    """Satu kejadian {"rea_progress": {...}} dari stderr REA -> {"line", "pct"} (pct 0-100 atau None)."""
    ev = event.get("rea_progress") if isinstance(event, dict) else None
    if not isinstance(ev, dict):
        return None
    phase = str(ev.get("phase") or "")
    message = str(ev.get("message") or "")
    if ev.get("terminal") and ev.get("completed") == ev.get("total"):
        return {"line": "Selesai", "pct": 100}
    label = _PHASES.get(phase)
    m = _COUNTER_RE.match(message)
    pct = None
    if m and int(m.group("n")) > 0:
        i, n = int(m.group("i")), int(m.group("n"))
        pct = min(95, int(i * 95 / n))
        line = f"{label or 'Memproses'} ({_fmt_n(i)}/{_fmt_n(n)}): {_short(m.group('what').split(' ', 2)[-1], 80)}"
        if phase == "parse_javascript_source":
            line = f"{label} ({_fmt_n(i)}/{_fmt_n(n)}): {_short(m.group('what').replace('Parsing and projecting ', ''), 80)}"
        elif phase == "process_javascript_artifact_metadata":
            line = f"{label} ({_fmt_n(i)}/{_fmt_n(n)}): {_short(m.group('what').replace('Processing metadata for ', ''), 80)}"
    else:
        total = ev.get("total")
        done = ev.get("completed")
        if isinstance(total, int) and total > 1 and isinstance(done, int):
            pct = min(95, int(done * 95 / total))
        if phase in _LATE_PHASES:
            pct = 95 + int(4 * (_LATE_PHASES.index(phase) + 1) / len(_LATE_PHASES))
        line = label or _short(message, 120) or "Berjalan"
        nm = re.search(r"Processing (\d+) selected application files", message)
        if nm:
            line = f"Memproses {_fmt_n(nm.group(1))} berkas aplikasi"
    return {"line": line, "pct": pct}


def _read_tail(path, size=65536):
    """Byte terakhir sebuah berkas (untuk membaca baris progres terbaru tanpa membaca seluruhnya)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - size))
            return f.read()
    except OSError:
        return b""


def _stderr_progress(jdir):
    path = os.path.join(jdir, "stderr.log")
    tail = _read_tail(path)
    last = None
    for raw in reversed(tail.splitlines()):
        if raw.startswith(b'{"rea_progress"'):
            try:
                last = _parse_progress(json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                last = None
            if last:
                break
    if last:
        return last
    head = _read_tail(path, 8192) if os.path.getsize(path) <= 8192 else b""
    started = b"npm notice run rea" in (head or tail)
    return {"line": "Menjalankan analisis REA…" if started else
            "Menyiapkan paket REA lewat npx (pertama kali bisa lama)…", "pct": None}


# --- penyimpanan job ---------------------------------------------------------------------------
_ID_RE = re.compile(r"[0-9a-f]{8}")
_LOCK = threading.RLock()
_JOBS = {}   # id -> meta (dict yang disimpan juga di meta.json); hanya job milik proses ini
_RUN = {}    # id -> _Run (proses & penguasa yang masih berjalan)


class _Run:
    __slots__ = ("proc", "winjob", "thread", "cancel", "timed_out")

    def __init__(self, proc, winjob):
        self.proc = proc
        self.winjob = winjob
        self.thread = None
        self.cancel = threading.Event()
        self.timed_out = False


def _jobs_root():
    from . import config

    return os.path.join(config.DATA_DIR, "rea-jobs")


def _job_dir(jid):
    return os.path.join(_jobs_root(), jid)


def _save_meta(meta):
    path = os.path.join(_job_dir(meta["id"]), "meta.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _read_meta(jid):
    try:
        with open(os.path.join(_job_dir(jid), "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("id") == jid else None


def _norm_id(job_id):
    jid = str(job_id or "").strip().lower()
    return jid if _ID_RE.fullmatch(jid) else None


def _load_meta(jid):
    """Meta sebuah job: dari memori (milik proses ini) atau dari disk. Job 'running' di disk yang bukan
    milik proses ini dan pemiliknya sudah mati/lama tak berdenyut ditandai gagal (terputus)."""
    with _LOCK:
        if jid in _JOBS:
            return dict(_JOBS[jid])
    meta = _read_meta(jid)
    if meta and meta.get("state") == "running":
        now = time.time()
        beat = float(meta.get("updated") or meta.get("started") or 0)
        owner = meta.get("owner_pid")
        # Job milik proses ini selalu ada di _JOBS (dicek di atas). Yang tidak ada di sana tapi owner_pid
        # SAMA adalah sisa proses sebelumnya yang PID-nya sama (os.execv di Linux/macOS tidak mengganti PID).
        if owner == os.getpid() or not _pid_alive(owner) or now - beat > _STALE_AFTER:
            meta.update(
                state="failed", ended=now,
                error="Terputus: It's Me berhenti atau menyala ulang saat analisis REA ini masih berjalan. "
                      "Jalankan lagi.",
                progress={"line": "Terputus", "pct": None})
            meta["summary"] = {"title": "REA terputus", "facts": [["Perintah", meta.get("command")],
                                                                  ["Target", meta.get("label") or meta.get("target")]],
                               "notes": [], "errors": [meta["error"]]}
            meta["summary_text"] = _render(meta["summary"])
            _save_meta(meta)
    return meta


def _update(jid, **changes):
    with _LOCK:
        meta = _JOBS.get(jid)
        if meta is None:
            return
        meta.update(changes)
        meta["updated"] = time.time()
        snapshot = dict(meta)
    _save_meta(snapshot)


def _view(meta):
    """Bentuk publik sebuah job (lihat job())."""
    now = time.time()
    started = meta.get("started") or now
    ended = meta.get("ended")
    progress = meta.get("progress") or {"line": "", "pct": None}
    if meta.get("state") == "running":
        try:
            progress = _stderr_progress(_job_dir(meta["id"]))
        except OSError:
            pass
    return {
        "id": meta["id"], "state": meta.get("state"), "command": meta.get("command"), "target": meta.get("target"),
        "label": meta.get("label"), "upload": meta.get("upload"),
        "started": started, "ended": ended, "elapsed": round((ended or now) - started, 1),
        "progress": {"line": progress.get("line", ""), "pct": progress.get("pct")},
        "summary": meta.get("summary"), "summary_text": meta.get("summary_text"), "error": meta.get("error"),
        "result_bytes": meta.get("result_bytes"), "suggest": suggest(meta.get("target"), meta.get("command")),
    }


_MAX_TOTAL_BYTES = 2 << 30   # 2 GB: hasil JSON tiap job bisa ratusan MB


def _prune(keep=KEEP_JOBS, max_bytes=_MAX_TOTAL_BYTES):
    """Hapus job lama: sisakan `keep` terbaru DAN total ukuran <= max_bytes (job yang masih berjalan dan
    job terbaru tidak pernah dihapus)."""
    root = _jobs_root()
    try:
        names = [n for n in os.listdir(root) if _ID_RE.fullmatch(n) and os.path.isdir(os.path.join(root, n))]
    except OSError:
        return
    stamped = []
    for name in names:
        with _LOCK:
            meta = _JOBS.get(name)
        meta = meta or _read_meta(name) or {}
        try:
            fallback = os.path.getmtime(os.path.join(root, name))
        except OSError:
            fallback = 0
        stamped.append((float(meta.get("started") or fallback), name))
    stamped.sort(reverse=True)
    for _, name in stamped[keep:]:
        with _LOCK:
            if name in _RUN:
                continue
            _JOBS.pop(name, None)
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    sizes = []
    for _, name in stamped[:keep]:
        try:
            jdir = os.path.join(root, name)
            sizes.append((name, sum(os.path.getsize(os.path.join(jdir, f)) for f in os.listdir(jdir))))
        except OSError:
            sizes.append((name, 0))
    total = sum(size for _, size in sizes)
    for name, size in reversed(sizes[1:]):   # tertua dulu; yang paling baru selalu disimpan
        if total <= max_bytes:
            break
        with _LOCK:
            if name in _RUN:
                continue
            _JOBS.pop(name, None)
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        total -= size


# --- menjalankan & mengawasi job ---------------------------------------------------------------
def _watch(jid):
    run = _RUN[jid]
    proc = run.proc
    deadline = time.monotonic() + _JOBS[jid]["timeout_s"]
    last_beat = time.monotonic()
    while True:
        try:
            returncode = proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if not run.cancel.is_set() and not run.timed_out and now > deadline:
            run.timed_out = True
            _kill_tree(proc, run.winjob)
        if now - last_beat >= _BEAT_EVERY:
            last_beat = now
            try:
                _update(jid, progress=_stderr_progress(_job_dir(jid)))
            except OSError:
                _update(jid)
    try:
        _finish(jid, run, returncode)
    except Exception as exc:  # noqa: BLE001 - jangan biarkan job menggantung di "running"
        _final(jid, "failed", error=f"Galat internal saat menyelesaikan job REA: {type(exc).__name__}: "
                                    f"{_short(exc, 200)}")
    finally:
        _killpg_leftovers(run.proc)   # anak-cucu yang tersisa sesudah selesai normal (POSIX)
        _winjob_close(run.winjob)
        with _LOCK:
            _RUN.pop(jid, None)


def _final(jid, state, error=None, summary=None, progress=None, **extra):
    if summary is None and error:
        meta = _JOBS.get(jid) or {}
        summary = {"title": {"cancelled": "REA dibatalkan", "timeout": "REA melebihi batas waktu"}
                   .get(state, "REA gagal"),
                   "facts": [["Perintah", meta.get("command")], ["Target", meta.get("label") or meta.get("target")]],
                   "notes": [], "errors": [error]}
    meta = _JOBS.get(jid) or {}
    summary = _relabel(_clean_summary(summary), meta.get("target"), meta.get("label"))
    if error and meta.get("label") and meta.get("target"):
        error = _relabel_text(error, meta["target"], meta["label"])
    _update(jid, state=state, ended=time.time(), error=error, summary=summary,
            summary_text=_render(summary) if summary else None,
            progress=progress or {"line": {"done": "Selesai", "cancelled": "Dibatalkan",
                                           "timeout": "Melebihi batas waktu"}.get(state, "Gagal"),
                                  "pct": 100 if state == "done" else None}, **extra)


def _finish(jid, run, returncode):
    meta = _JOBS[jid]
    jdir = _job_dir(jid)
    command, target = meta["command"], meta["target"]
    result = os.path.join(jdir, "result.json")
    if run.cancel.is_set() or run.timed_out:
        try:
            os.remove(result)  # keluaran setengah jadi tidak berguna (dan bisa raksasa)
        except OSError:
            pass
        if run.cancel.is_set():
            _final(jid, "cancelled", error="Dibatalkan; proses REA beserta anak-anaknya dihentikan.",
                   exit_code=returncode, result_bytes=None)
        else:
            minutes = int(meta["timeout_s"] // 60)
            _final(jid, "timeout", error=f"REA melebihi batas waktu {minutes} menit untuk '{command}' dan "
                                         "dihentikan (naikkan lewat ITSME_REA_TIMEOUT / ITSME_REA_TIMEOUT_JS, dalam menit).",
                   exit_code=returncode, result_bytes=None)
        return
    _update(jid, progress={"line": "Merangkum hasil…", "pct": 99}, exit_code=returncode)
    size = os.path.getsize(result) if os.path.exists(result) else 0
    stderr_text = _read_tail(os.path.join(jdir, "stderr.log"), 16384).decode("utf-8", "replace")
    _trim_log(os.path.join(jdir, "stderr.log"))
    facts = [["Perintah", command], ["Target", meta.get("label") or target]]
    if size == 0:
        headline = _explain_stderr(stderr_text, returncode)
        errors = [headline]
        clean = [ln for ln in stderr_text.splitlines() if ln.strip() and not ln.startswith("npm notice")]
        if clean:
            errors.append("Keluaran galat: " + _short(" | ".join(clean[-4:]), 400))
        _final(jid, "failed", error=headline, result_bytes=0,
               summary={"title": "REA gagal", "facts": facts, "notes": [], "errors": errors})
        return
    try:
        data = _load_result(result)
    except (ValueError, OSError, RecursionError) as exc:
        with open(result, "rb") as f:
            head = f.read(300).decode("utf-8", "replace").strip()
        headline = _explain_stderr(stderr_text, returncode) if returncode else \
            "Keluaran REA bukan JSON yang valid atau terpotong."
        _final(jid, "failed", error=headline, result_bytes=size,
               summary={"title": "REA gagal", "facts": facts, "notes": [],
                        "errors": [headline, f"Detail: {_short(exc, 160)}", f"Awal keluaran: {_short(head, 200)}"]})
        return
    if _is_error_json(data):
        headline, lines = _explain_error(data, command)
        facts += [["Kode galat REA", f"{data.get('code')}" + (f" ({data.get('category')})" if data.get("category") else "")]]
        _final(jid, "failed", error=headline, result_bytes=size,
               summary={"title": "REA gagal", "facts": facts, "notes": [], "errors": [headline] + lines})
        return
    summary = _summarize(data, {"command": command, "target": target, "result_bytes": size, "path": result})
    state = "done"
    if returncode:
        summary.setdefault("notes", []).append(f"REA keluar dengan kode {returncode} walau hasilnya ada.")
    _final(jid, state, summary=summary, result_bytes=size)


def _trim_log(path, keep=262144):
    """Pangkas stderr.log yang membengkak (progres ribuan berkas) menjadi ekornya saja."""
    try:
        if os.path.getsize(path) > 4 * keep:
            tail = _read_tail(path, keep)
            with open(path, "wb") as f:
                f.write(b"...(dipangkas)...\n" + tail)
    except OSError:
        pass


def _upload_meta(upload):
    """Bagian info unggahan (rea_upload.resolve) yang disimpan di meta job: {'id','name','kind','size','sha256',
    'file','rel','label'} dengan semua nilai dipaksa ke tipe sederhana, atau None."""
    if not isinstance(upload, dict) or not isinstance(upload.get("id"), str):
        return None
    out = {}
    for key in ("id", "name", "kind", "sha256", "rel", "label"):
        value = upload.get(key)
        out[key] = _oneline(value)[:300] if isinstance(value, str) else None
    for key in ("size", "file"):
        value = upload.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return out


def start(target, command=None, upload=None):
    """Mulai analisis REA di latar belakang dan kembalikan job() awalnya.

    ReaError kalau: target kosong/berawalan '-'/URL, perintah tidak ada di COMMANDS, Node/npx tidak ada,
    atau sudah ada MAX_RUNNING job yang berjalan. Argumen tambahan hanya dari tabel internal (_EXTRA_ARGS).

    `upload` (opsional): info berkas yang diunggah lewat dashboard (rea_upload.resolve()['upload'] ditambah
    'label'). Job lalu memakai nama aslinya di tampilan/ringkasan (bukan path penyimpanan internal) dan
    unggahannya tidak dipangkas selama job berjalan."""
    clean = _clean_target(target)
    up = _upload_meta(upload)
    name = _resolve_command(command, clean)
    argv = _argv(name, clean)
    timeout_s = _timeout_for(name)   # dihitung SEBELUM proses dijalankan (isian env aneh tak boleh meninggalkan yatim)
    with _LOCK:
        running = sum(1 for m in _JOBS.values() if m.get("state") == "running")
        if running >= MAX_RUNNING:
            raise ReaError(f"Sudah ada {running} analisis REA yang berjalan (maksimal {MAX_RUNNING} serentak). "
                           "Tunggu salah satunya selesai, atau batalkan lewat dashboard > Pengaturan > REA.")
        root = _jobs_root()
        os.makedirs(root, exist_ok=True)
        jid = uuid.uuid4().hex[:8]
        while os.path.exists(os.path.join(root, jid)):
            jid = uuid.uuid4().hex[:8]
        jdir = os.path.join(root, jid)
        os.makedirs(jdir)
        kwargs = {"stdin": subprocess.DEVNULL, "cwd": jdir, "env": _child_env()}
        if IS_WIN:
            kwargs["creationflags"] = _NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            with open(os.path.join(jdir, "result.json"), "wb") as out, \
                    open(os.path.join(jdir, "stderr.log"), "wb") as err:
                proc = subprocess.Popen(argv, stdout=out, stderr=err, **kwargs)
        except OSError as exc:
            shutil.rmtree(jdir, ignore_errors=True)
            raise ReaError(f"Tidak bisa menjalankan Node/npx untuk REA: {exc}") from exc
        now = time.time()
        meta = {"id": jid, "state": "running", "command": name, "target": clean, "started": now, "ended": None,
                "updated": now, "pid": proc.pid, "owner_pid": os.getpid(), "exit_code": None,
                "rea_version": REA_VERSION, "extra_args": list(_EXTRA_ARGS.get(name, ())),
                "timeout_s": timeout_s, "error": None, "summary": None, "summary_text": None,
                "result_bytes": None, "progress": {"line": "Memulai…", "pct": None},
                "upload": up, "label": (up or {}).get("label")}
        run = _Run(proc, _winjob_new(proc.pid))
        _JOBS[jid] = meta
        _RUN[jid] = run
        _save_meta(meta)
        run.thread = threading.Thread(target=_watch, args=(jid,), name=f"rea-job-{jid}", daemon=True)
        run.thread.start()
    try:
        _prune()
    except OSError:
        pass
    return job(jid)


def active_upload_ids():
    """Id unggahan (rea_upload) yang dipakai job REA yang masih berjalan: tidak boleh dihapus atau dipangkas."""
    with _LOCK:
        return {m["upload"]["id"] for m in _JOBS.values()
                if m.get("state") == "running" and isinstance(m.get("upload"), dict) and m["upload"].get("id")}


def job(job_id):
    """Status sebuah job: {"id","state","command","target","started","ended","elapsed","progress":
    {"line","pct"},"summary","summary_text","error","result_bytes","suggest"} atau None kalau tidak ada.
    state: running | done | failed | cancelled | timeout."""
    jid = _norm_id(job_id)
    meta = _load_meta(jid) if jid else None
    return _view(meta) if meta else None


def list_jobs(limit=10):
    """Job terbaru dulu (bentuk tiap butir sama dengan job())."""
    root = _jobs_root()
    try:
        names = [n for n in os.listdir(root) if _ID_RE.fullmatch(n)]
    except OSError:
        return []
    metas = [m for m in (_load_meta(n) for n in names) if m]
    metas.sort(key=lambda m: m.get("started") or 0, reverse=True)
    return [_view(m) for m in metas[:max(0, int(limit))]]


def result_path(job_id):
    """Path berkas JSON hasil lengkap sebuah job yang sudah selesai (untuk diunduh), atau None."""
    jid = _norm_id(job_id)
    meta = _load_meta(jid) if jid else None
    if not meta or meta.get("state") not in ("done", "failed"):
        return None
    path = os.path.join(_job_dir(jid), "result.json")
    return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None


def cancel(job_id):
    """Batalkan job yang berjalan: seluruh pohon proses anaknya dimatikan. True kalau job itu memang
    sedang berjalan dan sudah dihentikan, False kalau tidak ada atau sudah selesai."""
    jid = _norm_id(job_id)
    with _LOCK:
        run = _RUN.get(jid) if jid else None
        meta = _JOBS.get(jid) if jid else None
        if run is None or meta is None or meta.get("state") != "running":
            return False
        run.cancel.set()
    _kill_tree(run.proc, run.winjob)
    if run.thread is not None:
        run.thread.join(timeout=15)
    return True


def _shutdown():
    """Saat It's Me berhenti normal: hentikan semua REA yang masih jalan (tidak meninggalkan yatim)."""
    with _LOCK:
        runs = list(_RUN.items())
    for jid, run in runs:
        run.cancel.set()
        try:
            _kill_tree(run.proc, run.winjob)
        except Exception:  # noqa: BLE001
            pass
        meta = _JOBS.get(jid)
        if meta and meta.get("state") == "running":
            meta.update(state="cancelled", ended=time.time(),
                        error="Dihentikan karena It's Me berhenti.", progress={"line": "Dihentikan", "pct": None})
            _save_meta(meta)


atexit.register(_shutdown)


# --- API sinkron untuk tool Telegram ----------------------------------------------------------
def _outcome(view):
    """(ok, teks) dari job yang sudah tidak berjalan."""
    text = view.get("summary_text") or view.get("error") or "(REA tidak mengembalikan apa pun)"
    return view.get("state") == "done", text


def analyze(target, command=None, timeout=90):
    """Mulai analisis lalu tunggu sampai `timeout` detik. Selesai -> (True, ringkasan teks); gagal ->
    (False, penjelasan); belum selesai -> (True, "REA masih berjalan (id ...)") dan job tetap jalan di
    latar. ReaError dilempar seperti start()."""
    view = start(target, command)
    jid = view["id"]
    end = time.monotonic() + max(0.0, float(timeout))
    while True:
        view = job(jid) or view
        if view["state"] != "running":
            return _outcome(view)
        if time.monotonic() >= end:
            return True, (f"REA masih berjalan (id {jid}, perintah {view['command']}, sudah "
                          f"{_fmt_duration(view['elapsed'])}; {view['progress']['line']}). Hasil lengkap nanti di "
                          f"dashboard > Pengaturan > REA, atau minta 'hasil_rea {jid}'.")
        time.sleep(0.25)


def hasil(job_id):
    """(ok, teks) ringkasan sebuah job (untuk tool 'hasil_rea')."""
    view = job(job_id)
    if view is None:
        recent = ", ".join(v["id"] for v in list_jobs(5))
        return False, f"Tidak ada analisis REA dengan id '{str(job_id).strip()[:40]}'." + \
            (f" Id terbaru: {recent}." if recent else " Belum ada analisis REA yang tercatat.")
    if view["state"] == "running":
        pct = f", {view['progress']['pct']}%" if view["progress"]["pct"] is not None else ""
        return True, (f"REA masih berjalan (id {view['id']}, perintah {view['command']}, "
                      f"{_fmt_duration(view['elapsed'])}{pct}): {view['progress']['line']}")
    return _outcome(view)
