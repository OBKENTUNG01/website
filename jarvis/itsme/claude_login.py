"""Login sekali untuk otak "Claude di komputer" (/claudepc).

Jalankan di komputer, dari folder It's Me:
    Windows:      .venv\\Scripts\\python -m itsme.claude_login
    macOS/Linux:  .venv/bin/python -m itsme.claude_login

Aplikasi Claude desktop menyimpan login-nya sendiri dan tidak bisa dipakai
program lain. Skrip ini memasang Claude Code (paket claude-agent-sdk) bila
belum ada, menjalankan `claude setup-token` (browser terbuka untuk login dengan
akun Claude-mu; tokennya berlaku ±1 tahun), lalu memeriksa dan menyimpan token
itu ke .env. Setelah itu kirim /claudepc ke bot dari HP — It's Me yang sedang
jalan langsung membacanya, tidak perlu dinyalakan ulang.

Skrip ini tidak boleh berakhir dengan traceback: setiap kegagalan dijelaskan
dengan kalimat biasa beserta langkah berikutnya.
"""

import getpass
import os
import shutil
import subprocess
import sys

from . import keys
from .brain_claude_code import load_sdk

# Ditandai saat skrip menjalankan dirinya lagi di proses baru (lihat _sdk).
_RESTARTED = "ITSME_CLAUDE_LOGIN_RESTARTED"


class _Stop(Exception):
    """Berhenti dengan pesan untuk pemilik (tanpa traceback)."""


def _sdk():
    """claude_agent_sdk; dipasang dulu kalau belum ada."""
    try:
        return load_sdk()
    except ImportError:
        pass
    print("Memasang Claude Code untuk It's Me (sekali saja, unduhan sekitar 250 MB)...")
    code = subprocess.call(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "claude-agent-sdk"]
    )
    if code != 0:
        raise _Stop("Gagal memasang claude-agent-sdk. Cek koneksi internet lalu jalankan perintah ini lagi.")
    try:
        return load_sdk()
    except ImportError:
        # Cadangan terakhir: proses Python baru membaca semua paket yang baru dipasang.
        if os.environ.get(_RESTARTED):
            raise _Stop("[CC] sudah terpasang tapi belum bisa dimuat. Tutup jendela ini, "
                        "buka lagi, lalu jalankan perintah ini lagi.") from None
        env = dict(os.environ, **{_RESTARTED: "1"})
        # from None: ImportError tadi bukan penyebabnya, jangan ditumpuk di traceback.
        raise SystemExit(subprocess.call([sys.executable, "-m", "itsme.claude_login"], env=env)) from None


def _cli(sdk):
    """Program Claude Code bawaan paket, atau `claude` yang sudah terpasang."""
    bundled = os.path.join(os.path.dirname(sdk.__file__), "_bundled", "claude.exe" if os.name == "nt" else "claude")
    if os.path.exists(bundled):
        return bundled
    found = shutil.which("claude")
    if found:
        return found
    raise _Stop("Program Claude Code tidak ditemukan. Jalankan: .venv\\Scripts\\python -m pip install --force-reinstall claude-agent-sdk")


def _login():
    cli = _cli(_sdk())
    print()
    print("Langkah 1/2 - login dengan akun Claude-mu")
    print("  Browser akan terbuka. Login (atau klik Authorize kalau sudah login),")
    print("  lalu kembali ke jendela ini: token panjang diawali sk-ant-oat akan muncul.")
    print()
    # Tanpa API key: kalau ada, Claude Code memakainya alih-alih akunmu.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}
    subprocess.call([cli, "setup-token"], env=env)
    print()
    print("Langkah 2/2 - salin token tadi lalu tempel di sini")
    for _ in range(3):
        token = "".join(getpass.getpass("  Token (ketikan tidak tampil, lalu Enter): ").split())
        if not token:
            print("  Dibatalkan.")
            return 1
        if keys.detect(token) != "claude_code":
            print("  Itu bukan token akun Claude (harus diawali sk-ant-oat). Coba lagi.")
            continue
        print("  Memeriksa token (satu jawaban pendek dari Claude)...")
        try:
            keys.verify("claude_code", token)
        except keys.KeyRejected as exc:
            print(f"  {exc} Coba lagi.")
            continue
        except Exception as exc:  # noqa: BLE001 - mis. internet putus
            raise _Stop(f"Token belum bisa diperiksa ({type(exc).__name__}). Cek internet lalu jalankan perintah ini lagi.") from exc
        keys.save("claude_code", token)
        print()
        print(f"Selesai! Token {keys.mask(token).replace(chr(0x2026), '...')} tersimpan di .env.")
        print("Buka Telegram dan kirim /claudepc ke bot.")
        return 0
    return 1


def main():
    # Huruf yang tidak bisa ditampilkan jendela ini diganti, bukan membuat skrip berhenti.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        return _login()
    except _Stop as exc:
        print(f"\n[GAGAL] {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nDibatalkan.")
        return 1
    except Exception as exc:  # noqa: BLE001 - tidak boleh ada traceback untuk pemilik
        print(f"\n[GAGAL] Terjadi kesalahan tak terduga: {type(exc).__name__}: {exc}")
        print("Kirim tangkapan layar pesan ini supaya bisa diperbaiki.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
