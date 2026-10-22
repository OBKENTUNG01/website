"""API key yang dikirim pemilik lewat Telegram (/kunci).

Key diperiksa dulu ke penyedianya, disimpan ke .env, lalu langsung dipakai
tanpa menyalakan ulang It's Me. Key tidak pernah dicatat di log, tidak masuk
riwayat obrolan, dan tidak dikirim ke otak AI; pesan Telegram yang berisi key
dihapus oleh main.py.
"""

import logging
import os
import re

import httpx

from . import config

log = logging.getLogger("itsme")

# Berkas .env yang dibaca config.py (folder jarvis/).
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

# jenis -> (awalan key, nama di .env & config, nama untuk pemilik). Urutan
# penting: token akun (sk-ant-oat…) juga diawali sk-ant-.
KINDS = {
    "claude_code": ("sk-ant-oat", "CLAUDE_CODE_OAUTH_TOKEN", "akun Claude (Claude di komputer)"),
    "claude": ("sk-ant-", "ANTHROPIC_API_KEY", "Claude (Anthropic)"),
    "openrouter": ("sk-or-", "OPENROUTER_API_KEY", "OpenRouter"),
    # Key DeepSeek dari gateway diawali nsk- / nsk_. Key resmi api.deepseek.com
    # diawali sk- (tak bisa dibedakan otomatis), jadi untuk yang itu pakai
    # perintah /deepseek <key>.
    "deepseek": ("nsk", "DEEPSEEK_API_KEY", "DeepSeek"),
}
# Key hanya berisi huruf, angka, - dan _; selain itu bisa merusak .env.
_KEY_CHARS = re.compile(r"[A-Za-z0-9_-]{20,300}")


def looks_like_key(value):
    """Teks ini berbentuk API key (tanpa karakter yang bisa merusak .env)?"""
    return bool(_KEY_CHARS.fullmatch(value or ""))


class KeyRejected(Exception):
    """Penyedia menolak key ini (salah, sudah dicabut, atau tanpa izin)."""


def detect(key):
    """Jenis key ("claude" / "openrouter"), atau None kalau tidak dikenali."""
    if not _KEY_CHARS.fullmatch(key or ""):
        return None
    return next((kind for kind, (prefix, _, _) in KINDS.items() if key.startswith(prefix)), None)


def label(kind):
    return KINDS[kind][2]


def mask(key):
    """Tampilkan awalan dan 4 huruf terakhir saja, mis. sk-ant-…a1b2."""
    kind = detect(key)
    prefix = KINDS[kind][0] if kind else ""
    return f"{prefix}…{key[-4:]}"


def verify(kind, key):
    """Periksa key ke penyedianya tanpa biaya.

    Melempar KeyRejected kalau key ditolak; error lain berarti belum bisa
    diperiksa (mis. internet putus).
    """
    if kind == "claude_code":
        from .brain_claude_code import LOGIN_HINT, check_login, load_sdk

        try:
            load_sdk()
        except ImportError as exc:
            raise KeyRejected("Claude Code belum terpasang di komputer. " + LOGIN_HINT) from exc
        # Satu jawaban pendek ("OK") memakai sedikit kuota akunmu.
        if check_login(key) == "rejected":
            raise KeyRejected("Token akun Claude ditolak (salah atau kedaluwarsa).")
        return
    if kind == "claude":
        import anthropic  # hanya dipakai di sini dan di otak Claude

        client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=20)
        try:
            client.models.list(limit=1)  # daftar model: tidak memanggil model, gratis
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise KeyRejected("Anthropic menolak key ini (salah, sudah dicabut, atau tanpa izin).") from exc
        return
    if kind == "deepseek":
        # Endpoint daftar model (kompatibel OpenAI) — tidak memanggil model, gratis.
        r = httpx.get(
            f"{config.DEEPSEEK_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise KeyRejected(
                "DeepSeek menolak key ini (salah, dicabut, atau alamat API-nya beda). "
                "Kalau key-mu dari gateway lain, isi DEEPSEEK_URL di .env lalu kirim lagi."
            )
        r.raise_for_status()
        return
    r = httpx.get(
        f"{config.OPENROUTER_URL}/key",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    if r.status_code in (401, 403):
        raise KeyRejected("OpenRouter menolak key ini (salah atau sudah dicabut).")
    r.raise_for_status()


def save(kind, key):
    """Simpan key ke .env dan langsung pakai."""
    name = KINDS[kind][1]
    _set_env(ENV_FILE, name, key)
    setattr(config, name, key)
    log.info("API key %s diganti lewat Telegram.", label(kind))


def refresh_from_env(name):
    """Baca ulang satu nilai dari .env (mis. baru disimpan proses lain)."""
    try:
        from dotenv import dotenv_values

        value = dotenv_values(ENV_FILE).get(name)
    except Exception:  # noqa: BLE001 - python-dotenv opsional, .env bisa tidak ada
        return
    if value:
        setattr(config, name, value)


def _set_env(path, name, value):
    """Ganti baris "NAME=" atau "# NAME=" di .env, atau tambahkan di akhir
    (sama seperti skrip setup). Ditulis di tempat supaya izin berkas
    (chmod 600 dari setup.sh) tetap."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    pattern = re.compile(rf"^[#\s]*{re.escape(name)}=")
    result, done = [], False
    for line in lines:
        if pattern.match(line):
            if not done:
                result.append(f"{name}={value}")
                done = True
            continue
        result.append(line)
    if not done:
        result.append(f"{name}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(result) + "\n")
