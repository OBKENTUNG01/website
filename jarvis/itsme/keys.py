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
    # Token GitHub untuk Pembuat Aplikasi HP (generate APK). Classic (ghp_) atau
    # fine-grained (github_pat_, dikenali khusus di detect()).
    "github": ("ghp_", "GITHUB_TOKEN", "GitHub (Pembuat Aplikasi HP)"),
}
# Key hanya berisi huruf, angka, - dan _; selain itu bisa merusak .env.
_KEY_CHARS = re.compile(r"[A-Za-z0-9_-]{20,300}")


def looks_like_key(value):
    """Teks ini berbentuk API key (tanpa karakter yang bisa merusak .env)?"""
    return bool(_KEY_CHARS.fullmatch(value or ""))


class KeyRejected(Exception):
    """Penyedia menolak key ini (salah, sudah dicabut, atau tanpa izin)."""


def detect(key):
    """Jenis key ("claude" / "openrouter" / ...), atau None kalau tidak dikenali."""
    if not _KEY_CHARS.fullmatch(key or ""):
        return None
    if key.startswith("github_pat_"):  # token GitHub fine-grained
        return "github"
    return next((kind for kind, (prefix, _, _) in KINDS.items() if key.startswith(prefix)), None)


def label(kind):
    return KINDS[kind][2]


def mask(key):
    """Tampilkan awalan dan 4 huruf terakhir saja, mis. sk-ant-…a1b2."""
    kind = detect(key)
    if (key or "").startswith("github_pat_"):
        prefix = "github_pat_"
    else:
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
        # Alamat dipilih dari awalan key: nsk- -> NusaGate, sk- -> DeepSeek resmi.
        r = httpx.get(
            f"{config.deepseek_url(key)}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise KeyRejected("Penyedia DeepSeek menolak key ini (salah, dicabut, atau tanpa izin).")
        r.raise_for_status()
        return
    if kind == "github":
        r = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise KeyRejected("GitHub menolak token ini (salah, dicabut, atau tanpa izin).")
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


def gateway_models(key):
    """ID model yang ditawarkan gateway untuk `key` (GET /models, gratis). Melempar KeyRejected
    kalau key ditolak; error jaringan/HTTP lain dilempar apa adanya."""
    r = httpx.get(
        f"{config.deepseek_url(key)}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    if r.status_code in (401, 403):
        raise KeyRejected("Penyedia menolak key ini (salah, dicabut, atau tanpa izin).")
    r.raise_for_status()
    return _model_ids(r.json())


def _model_ids(data):
    """Nama model dari jawaban GET /models. Bentuk standar OpenAI ({"data": [{"id": ...}]}); bentuk lain yang
    wajar (daftar langsung, "models", nama saja) juga dibaca, karena NusaGate tidak mendokumentasikan bentuknya."""
    items = data
    if isinstance(data, dict):
        items = data.get("data") if data.get("data") is not None else data.get("models")
    names = set()
    for item in items if isinstance(items, list) else []:
        name = item if isinstance(item, str) else (
            next((item[k] for k in ("id", "model", "name") if isinstance(item.get(k), str)), None)
            if isinstance(item, dict) else None)
        if name and name.strip():
            names.add(name.strip())
    return sorted(names)


def save(kind, key):
    """Simpan key ke .env dan langsung pakai. Key DeepSeek/NusaGate DITAMBAHKAN ke kumpulan
    (semua key dipakai bergiliran, lihat keypool.py); jenis lain menggantikan key lama."""
    if kind == "deepseek":
        pool_add(key)
        return
    name = KINDS[kind][1]
    _set_env(ENV_FILE, name, key)
    setattr(config, name, key)
    log.info("API key %s diganti lewat Telegram.", label(kind))


def pool_add(key):
    """Tambahkan key ke kumpulan DeepSeek/NusaGate tanpa menghapus yang lama. True kalau key-nya baru."""
    added, _ = pool_add_many([key])
    return bool(added)


def pool_add_many(new_keys):
    """Tambahkan banyak key sekaligus (satu kali tulis .env). Kembalikan (baru, sudah_ada)."""
    have = set(config.deepseek_keys())
    added, existing = [], []
    for key in new_keys:
        (existing if key in have or key in added else added).append(key)
    if added:
        stored = list(config.DEEPSEEK_API_KEYS) + added
        _set_env(ENV_FILE, "DEEPSEEK_API_KEYS", ",".join(stored))
        config.DEEPSEEK_API_KEYS = stored
        log.info("API key DeepSeek/NusaGate ditambahkan ke kumpulan (+%d, sekarang %d key).",
                 len(added), len(config.deepseek_keys()))
    return added, existing


def pool_remove(key):
    """Keluarkan satu key dari kumpulan (dari DEEPSEEK_API_KEYS dan/atau DEEPSEEK_API_KEY lama)."""
    from . import keypool

    removed = False
    if key in config.DEEPSEEK_API_KEYS:
        stored = [k for k in config.DEEPSEEK_API_KEYS if k != key]
        _set_env(ENV_FILE, "DEEPSEEK_API_KEYS", ",".join(stored))
        config.DEEPSEEK_API_KEYS = stored
        removed = True
    if config.DEEPSEEK_API_KEY == key:
        _set_env(ENV_FILE, "DEEPSEEK_API_KEY", "")
        config.DEEPSEEK_API_KEY = None
        removed = True
    if removed:
        keypool.forget(key)
        log.info("API key DeepSeek/NusaGate dikeluarkan dari kumpulan (sisa %d key).", len(config.deepseek_keys()))
    return removed


def set_models(names):
    """Simpan daftar model NusaGate yang diputar bergantian (kosong = model bawaan)."""
    names = list(dict.fromkeys(names))
    _set_env(ENV_FILE, "ITSME_NUSAGATE_MODELS", ",".join(names))
    config.NUSAGATE_MODELS = names


# --- rahasia tidak boleh lewat WhatsApp ----------------------------------------------------
# Awalan key yang dikenal. Sengaja berbasis awalan (bukan "teks acak panjang") supaya pesan
# biasa tidak ikut terblokir.
_SECRET_RES = [
    re.compile(r"\bnsk[_-][A-Za-z0-9_-]{16,}"),  # NusaGate
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),  # Anthropic, juga token akun sk-ant-oat
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{16,}"),  # OpenRouter
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),  # OpenAI / DeepSeek resmi
    re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}"),  # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),  # Google
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
# Perintah yang MEMASANG key (/kunci <key>, /deepseek <key>): bagian setelah kata perintahnya adalah key.
# (/cloud, /claude, /claudepc hanya mengganti otak dan tidak menerima key.)
_KEY_COMMAND = re.compile(r"^\s*/(?:kunci|deepseek|key)(?:@\w+)?\s+\S", re.IGNORECASE)
_PLACEHOLDER = "[kunci disembunyikan]"


def contains_secret(text):
    """Teks ini kelihatan berisi API key / token / kunci rahasia (atau mencoba memasangnya)?
    Dipakai WhatsApp: key TIDAK BOLEH lewat WhatsApp (bisa bocor dan terbaca AI), hanya Telegram."""
    text = text or ""
    if _KEY_COMMAND.match(text):
        return True
    if any(rx.search(text) for rx in _SECRET_RES):
        return True
    whole = text.strip()
    # Seluruh pesan = satu deret acak panjang bercampur huruf besar, kecil, dan angka: key tanpa
    # awalan yang dikenal. (Nomor resi huruf besar+angka atau hash heksadesimal tidak ikut terjaring.)
    return (looks_like_key(whole) and len(whole) >= 32
            and any(c.isdigit() for c in whole)
            and any(c.islower() for c in whole) and any(c.isupper() for c in whole))


def scrub(text):
    """Ganti apa pun yang mirip key/token rahasia dengan penanda. Untuk teks dari luar (pesan galat
    penyedia) sebelum ditampilkan atau dicatat: penyedia kadang mengulang key dalam pesan galatnya."""
    out = str(text or "")
    for rx in _SECRET_RES:
        out = rx.sub(_PLACEHOLDER, out)
    return out


_TOKEN_SPLIT = re.compile(r"[\s,;]+")
_TOKEN_EDGES = "`'\"()[]<>{}"


def parse_tokens(text):
    """Calon key dalam sebuah pesan: dipisah spasi, baris baru, koma, atau titik koma. Kata perintah di
    depan (/kunci) dibuang; tanda kutip/kurung dan awalan "NAMA=" ikut dibuang. Urutan tetap, tanpa
    duplikat. Hasilnya BELUM tentu key - periksa dengan detect() / looks_like_key()."""
    tokens = [t for t in _TOKEN_SPLIT.split(text or "") if t]
    if tokens and tokens[0].startswith("/"):
        tokens = tokens[1:]
    out = []
    for token in tokens:
        token = token.strip(_TOKEN_EDGES)
        if "=" in token:
            token = token.rsplit("=", 1)[1].strip(_TOKEN_EDGES)
        if token and token not in out:
            out.append(token)
    return out


def only_keys(text):
    """Pesan ini isinya HANYA key (satu atau banyak, tanpa kata lain), dengan atau tanpa kata perintah di
    depannya? Key yang ditempel tanpa /kunci - atau dengan perintah yang salah ketik (/kunc, /kunci-) -
    tetap tidak boleh sampai ke otak AI atau tersimpan di riwayat obrolan."""
    tokens = parse_tokens(text)
    return (bool(tokens) and all(looks_like_key(t) for t in tokens)
            and any(detect(t) or contains_secret(t) for t in tokens))


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
