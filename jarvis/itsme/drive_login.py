"""Hubungkan Google Drive sekali (OAuth) di komputer.

Jalankan dari folder It's Me:
    Windows:      .venv\\Scripts\\python -m itsme.drive_login
    macOS/Linux:  .venv/bin/python -m itsme.drive_login

Butuh berkas kredensial OAuth dari Google Cloud (client_secret). Skrip ini
memasang paket Google bila belum ada, membuka browser untuk login dengan akun
Google-mu, lalu menyimpan token ke data/drive_token.json. Setelah itu It's Me
bisa mengakses Google Drive-mu (dari Telegram atau dashboard).

Tidak pernah berakhir dengan traceback: setiap kegagalan dijelaskan biasa.
"""

import os
import sys

from . import config, drive


def _steps():
    print("Berkas kredensial Google belum ada. Sekali saja:")
    print("  1) Buka https://console.cloud.google.com/ dan buat (atau pilih) sebuah project.")
    print("  2) Di 'APIs & Services' > 'Library', aktifkan 'Google Drive API'.")
    print("  3) Di 'APIs & Services' > 'OAuth consent screen': pilih External, isi nama,")
    print("     lalu tambahkan email Google-mu sebagai 'Test user'.")
    print("  4) Di 'Credentials' > 'Create credentials' > 'OAuth client ID' > jenis")
    print("     'Desktop app'. Unduh berkas JSON-nya.")
    print(f"  5) Simpan berkas JSON itu persis sebagai:\n     {config.DRIVE_CREDENTIALS_FILE}")
    print("  6) Jalankan perintah ini lagi.")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    if not os.path.exists(config.DRIVE_CREDENTIALS_FILE):
        _steps()
        return 1

    try:
        drive.load_libs()
    except ImportError:
        print("Memasang paket Google untuk It's Me (sekali saja)...")
        if not drive.install_libs():
            print("\n[GAGAL] Paket Google gagal dipasang. Cek koneksi internet lalu jalankan lagi.")
            return 1
        try:
            drive.load_libs()
        except ImportError:
            print("\n[GAGAL] Paket sudah dipasang tapi belum bisa dimuat. Tutup jendela ini, buka lagi, lalu ulangi.")
            return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Membuka browser untuk login Google... (pilih akun, lalu izinkan akses Drive)")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(config.DRIVE_CREDENTIALS_FILE, drive.SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as exc:  # noqa: BLE001 - tidak boleh ada traceback untuk pemilik
        print(f"\n[GAGAL] Login Google gagal: {type(exc).__name__}: {exc}")
        print("Pastikan berkas kredensial jenis 'Desktop app' dan emailmu terdaftar sebagai Test user.")
        return 1

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.DRIVE_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    try:
        os.chmod(config.DRIVE_TOKEN_FILE, 0o600)
    except OSError:
        pass

    print("\nTersambung! Menguji akses Drive...")
    try:
        files = drive.list_folder("root", 5)
        contoh = ", ".join(x["name"] for x in files) or "(Drive kosong)"
        print(f"Berhasil. Beberapa isi Drive-mu: {contoh}")
    except Exception as exc:  # noqa: BLE001
        print(f"Token tersimpan, tapi uji akses gagal: {exc}")
        return 1
    print("\nSelesai! Sekarang minta It's Me, mis: 'cari berkas laporan di Google Drive'")
    print("atau 'unggah C:\\...\\laporan.pdf ke Google Drive'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
