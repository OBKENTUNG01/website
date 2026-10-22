"""Pembuat Aplikasi HP: memicu & memantau build APK It's Me HP lewat GitHub
Actions, lalu memberi tautan unduh APK terbaru.

Dashboard (tab HP) memanggil ini. Build berjalan di GitHub (runner Ubuntu dengan
Android SDK) karena komputer biasa tidak punya perkakas Android; dashboard hanya
mengirim pilihan (nama aplikasi, warna ikon, dll) ke workflow dan menampilkan
hasilnya. Butuh token GitHub (ITSME_GITHUB_TOKEN) dengan izin Actions: write.
"""

import logging

import httpx

from . import config

log = logging.getLogger("itsme")

API = "https://api.github.com"
WORKFLOW = "build.yml"
# Input yang diterima workflow (lihat itsme-hp/.github/workflows/build.yml).
FIELDS = ("app_name", "accent", "app_id", "version_name")


class BuildError(Exception):
    """Gagal memicu/memeriksa build; pesannya siap dibaca pemilik."""


def configured():
    return bool(config.GITHUB_TOKEN and config.HP_REPO)


def _headers():
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _clean_inputs(inputs):
    """Hanya field yang dikenal & berupa teks; sisanya dibuang."""
    out = {}
    for k in FIELDS:
        v = (inputs or {}).get(k)
        if v not in (None, ""):
            out[k] = str(v)[:100]
    return out


def trigger(inputs):
    """Jalankan workflow build dengan pilihan pemakai. Lempar BuildError kalau gagal."""
    if not configured():
        raise BuildError("Belum ada token GitHub. Kirim /kunci <token GitHub> ke bot dulu.")
    try:
        r = httpx.post(
            f"{API}/repos/{config.HP_REPO}/actions/workflows/{WORKFLOW}/dispatches",
            headers=_headers(),
            json={"ref": "main", "inputs": _clean_inputs(inputs)},
            timeout=25,
        )
    except httpx.HTTPError as exc:
        raise BuildError(f"Tidak bisa menghubungi GitHub ({type(exc).__name__}).") from exc
    if r.status_code == 204:
        log.info("Build APK HP dipicu di %s.", config.HP_REPO)
        return True
    if r.status_code in (401, 403):
        raise BuildError("GitHub menolak token (perlu izin Actions: write pada repo HP).")
    if r.status_code == 404:
        raise BuildError(f"Repo atau workflow tidak ditemukan ({config.HP_REPO} / {WORKFLOW}).")
    raise BuildError(f"GitHub mengembalikan error {r.status_code}.")


def latest_apk():
    """URL unduh APK rilis terbaru, atau None."""
    if not configured():
        return None
    try:
        r = httpx.get(f"{API}/repos/{config.HP_REPO}/releases/tags/latest",
                      headers=_headers(), timeout=20)
        if r.status_code != 200:
            return None
        for asset in r.json().get("assets") or []:
            if str(asset.get("name") or "").lower().endswith(".apk"):
                return asset.get("browser_download_url")
    except (httpx.HTTPError, ValueError):
        return None
    return None


def status():
    """Keadaan Pembuat Aplikasi: terkonfigurasi?, build terakhir, tautan APK."""
    if not configured():
        return {"configured": False, "repo": config.HP_REPO}
    out = {"configured": True, "repo": config.HP_REPO}
    try:
        r = httpx.get(
            f"{API}/repos/{config.HP_REPO}/actions/workflows/{WORKFLOW}/runs?per_page=1",
            headers=_headers(), timeout=20,
        )
        if r.status_code == 200:
            runs = r.json().get("workflow_runs") or []
            if runs:
                run = runs[0]
                out["run"] = {
                    "status": run.get("status"),            # queued|in_progress|completed
                    "conclusion": run.get("conclusion"),    # success|failure|...
                    "url": run.get("html_url"),
                    "created_at": run.get("created_at"),
                }
        elif r.status_code in (401, 403):
            out["error"] = "token GitHub ditolak (perlu izin Actions)"
    except (httpx.HTTPError, ValueError) as exc:
        out["error"] = type(exc).__name__
    out["apk"] = latest_apk()
    return out
