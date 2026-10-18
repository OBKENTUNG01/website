"""Akses Google Drive untuk It's Me: cari, unggah, unduh, dan kelola berkas/folder.

Butuh paket Google (dipasang otomatis saat drive_login) dan login sekali di
komputer: `python -m itsme.drive_login`. Token hasil login disimpan di
data/drive_token.json; berkas kredensial Google (client_secret) di
data/drive_credentials.json. Keduanya rahasia (lihat is_secret_path di tools.py).
"""

import importlib
import io
import os
import site
import sys
import sysconfig

from . import config

# Akses penuh ke Drive pemilik (unggah, unduh, kelola). Kalau mau lebih sempit,
# ganti ke "https://www.googleapis.com/auth/drive.file" (hanya berkas buatan app).
SCOPES = ["https://www.googleapis.com/auth/drive"]
_PKGS = ("google", "googleapiclient", "google_auth_oauthlib", "googleapiclient")
_INSTALL = ["google-api-python-client", "google-auth-oauthlib", "google-auth-httplib2"]
_FOLDER_MIME = "application/vnd.google-apps.folder"
# Berkas Google asli (Docs/Sheets/Slides) tidak bisa diunduh langsung; diekspor.
_EXPORT = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}


class DriveError(Exception):
    """Gagal dengan pesan siap dibaca pemilik."""


def login_hint():
    return (
        "Google Drive belum terhubung. Sekali saja, di komputer:\n"
        "1) Buka console.cloud.google.com → buat OAuth client ID jenis 'Desktop app', "
        "aktifkan Google Drive API, lalu unduh berkas JSON-nya.\n"
        f"2) Simpan berkas itu sebagai: {config.DRIVE_CREDENTIALS_FILE}\n"
        "3) Jalankan: .venv\\Scripts\\python -m itsme.drive_login\n"
        "Setelah itu It's Me bisa mengakses Google Drive-mu."
    )


def ready():
    return os.path.exists(config.DRIVE_TOKEN_FILE)


# --- memuat paket Google ----------------------------------------------------------
def _try_import():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    return Credentials, Request, build, MediaFileUpload, MediaIoBaseDownload


def load_libs():
    """Import paket Google; juga kalau baru dipasang di proses yang sama."""
    try:
        return _try_import()
    except ImportError:
        for name in [n for n in list(sys.modules) if n.split(".")[0] in _PKGS]:
            del sys.modules[name]
        for folder in dict.fromkeys((sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"])):
            site.addsitedir(folder)
        importlib.invalidate_caches()
        return _try_import()


def install_libs():
    """Pasang paket Google (dipakai drive_login). True kalau berhasil."""
    import subprocess

    code = subprocess.call(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *_INSTALL]
    )
    return code == 0


# --- layanan Drive -----------------------------------------------------------------
def _service():
    if not ready():
        raise DriveError(login_hint())
    try:
        Credentials, Request, build, *_ = load_libs()
    except ImportError as exc:
        raise DriveError("Paket Google belum terpasang. " + login_hint()) from exc
    creds = Credentials.from_authorized_user_file(config.DRIVE_TOKEN_FILE, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(config.DRIVE_TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            raise DriveError("Login Google Drive sudah tidak berlaku. " + login_hint())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


_FIELDS = "files(id,name,mimeType,size,modifiedTime)"


def _fmt(f):
    return {
        "id": f["id"],
        "name": f.get("name", "?"),
        "folder": f.get("mimeType") == _FOLDER_MIME,
        "mime": f.get("mimeType", ""),
        "size": int(f.get("size") or 0),
        "modified": f.get("modifiedTime", ""),
    }


# --- operasi -----------------------------------------------------------------------
def search(kata, limit=25):
    kata = (kata or "").replace("\\", "\\\\").replace("'", "\\'")
    q = f"name contains '{kata}' and trashed=false"
    res = _service().files().list(q=q, pageSize=limit, fields=_FIELDS, spaces="drive").execute()
    return [_fmt(f) for f in res.get("files", [])]


def list_folder(folder_id="root", limit=100):
    q = f"'{folder_id}' in parents and trashed=false"
    res = _service().files().list(q=q, pageSize=limit, fields=_FIELDS, orderBy="folder,name").execute()
    return [_fmt(f) for f in res.get("files", [])]


def upload(local_path, parent_id=None, name=None):
    if not os.path.isfile(local_path):
        raise DriveError(f"Berkas lokal tidak ditemukan: {local_path}")
    _, _, _, MediaFileUpload, _ = load_libs()
    meta = {"name": name or os.path.basename(local_path)}
    if parent_id:
        meta["parents"] = [parent_id]
    media = MediaFileUpload(local_path, resumable=True)
    return _service().files().create(body=meta, media_body=media, fields="id,name").execute()


def download(file_id, local_path):
    *_, MediaIoBaseDownload = load_libs()
    svc = _service()
    meta = svc.files().get(fileId=file_id, fields="name,mimeType").execute()
    mime = meta.get("mimeType", "")
    if mime in _EXPORT:
        export_mime, ext = _EXPORT[mime]
        if not local_path.lower().endswith(ext):
            local_path += ext
        request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
    elif mime.startswith("application/vnd.google-apps"):
        raise DriveError(f"Berkas Google '{meta.get('name')}' ({mime}) tidak bisa diunduh langsung.")
    else:
        request = svc.files().get_media(fileId=file_id)
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with io.FileIO(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return {"name": meta.get("name"), "path": local_path}


def make_folder(name, parent_id=None):
    meta = {"name": name, "mimeType": _FOLDER_MIME}
    if parent_id:
        meta["parents"] = [parent_id]
    return _service().files().create(body=meta, fields="id,name").execute()


def rename(file_id, new_name):
    return _service().files().update(fileId=file_id, body={"name": new_name}, fields="id,name").execute()


def move(file_id, new_parent_id):
    svc = _service()
    prev = ",".join(svc.files().get(fileId=file_id, fields="parents").execute().get("parents", []))
    return svc.files().update(
        fileId=file_id, addParents=new_parent_id, removeParents=prev, fields="id,name,parents"
    ).execute()


def delete(file_id, permanent=False):
    svc = _service()
    if permanent:
        svc.files().delete(fileId=file_id).execute()
    else:  # pindah ke sampah (bisa dipulihkan)
        svc.files().update(fileId=file_id, body={"trashed": True}).execute()


def name_of(file_id):
    try:
        return _service().files().get(fileId=file_id, fields="name").execute().get("name", file_id)
    except Exception:  # noqa: BLE001
        return file_id
