"""Pemeriksa kesiapan It's Me.

Jalankan dari folder jarvis/:  python -m itsme.doctor

Mengecek isi .env, token Telegram (langsung ke Telegram), dan otaknya
(Ollama menyala & model sudah diunduh, atau API key Claude ada). Keluar
dengan kode 0 kalau semua beres, 1 kalau ada yang perlu dibetulkan.
Sengaja hanya memakai teks ASCII supaya aman di semua jenis terminal.
"""

import sys

import httpx

from . import config


class _Report:
    def __init__(self):
        self.ok = True

    def line(self, passed, label, hint=""):
        print(("[OK]    " if passed else "[GAGAL] ") + label)
        if not passed:
            self.ok = False
            if hint:
                print("        -> " + hint)

    def warn(self, label, hint=""):
        """Saran saja — tidak membuat pemeriksaan gagal."""
        print("[SARAN] " + label)
        if hint:
            print("        -> " + hint)


# Perbaikan pemanggilan alat untuk qwen3.5 & gemma4 masuk Ollama sekitar
# Maret–April 2026 (seri 0.20). Versi yang lebih lama bisa "menulis" panggilan
# alat sebagai teks alih-alih menjalankannya.
MIN_OLLAMA = (0, 20)


def _version_tuple(text):
    parts = []
    for piece in str(text).split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) if len(parts) == 2 else None


def _check_telegram(report):
    if not config.TELEGRAM_TOKEN:
        return  # sudah dilaporkan oleh config.check()
    url = f"{config.TELEGRAM_API_URL}/bot{config.TELEGRAM_TOKEN}/getMe"
    try:
        data = httpx.get(url, timeout=15).json()
    except (httpx.HTTPError, ValueError) as exc:
        report.line(False, "Tidak bisa menghubungi Telegram", f"Cek internet. ({exc})")
        return
    if data.get("ok"):
        username = data.get("result", {}).get("username", "?")
        report.line(True, f"Token Telegram valid: bot @{username}")
    else:
        report.line(
            False,
            "Token Telegram ditolak",
            "Salin ulang token dari @BotFather ke TELEGRAM_BOT_TOKEN di .env",
        )


def _model_names(tags):
    names = set()
    for model in tags.get("models", []):
        for key in ("name", "model"):
            if model.get(key):
                names.add(model[key])
    return names


def _check_ollama(report, required=True):
    """required=False: otak lokal hanya cadangan, jadi masalahnya cukup saran."""
    try:
        resp = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=10)
        tags = resp.json()
    except (httpx.HTTPError, ValueError):
        label = f"Ollama tidak bisa dihubungi di {config.OLLAMA_URL}"
        hint = "Buka aplikasi Ollama (Windows/Mac) atau jalankan: ollama serve"
        if required:
            report.line(False, label, hint)
        else:
            report.warn(label + " (otak cadangan tidak tersedia)", hint)
        return
    try:
        version = httpx.get(f"{config.OLLAMA_URL}/api/version", timeout=10).json().get("version", "?")
    except (httpx.HTTPError, ValueError, AttributeError):
        version = "?"
    report.line(True, f"Ollama {version} menyala di {config.OLLAMA_URL}")
    parsed = _version_tuple(version)
    if parsed is not None and parsed < MIN_OLLAMA:
        report.warn(
            f"Ollama {version} sudah lama",
            "Perbarui dari https://ollama.com/download supaya pemanggilan alat lancar.",
        )
    wanted = config.OLLAMA_MODEL
    names = _model_names(tags)
    present = wanted in names or (":" not in wanted and f"{wanted}:latest" in names)
    if present or required:
        report.line(present, f"Model {wanted} sudah diunduh", f"Jalankan: ollama pull {wanted}")
    else:
        report.warn(f"Model cadangan {wanted} belum diunduh", f"Jalankan: ollama pull {wanted}")


def _money(value):
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "?"


def _check_openrouter(report):
    key = config.OPENROUTER_API_KEY
    if not key:
        return  # sudah dilaporkan oleh config.check()
    try:
        resp = httpx.get(
            f"{config.OPENROUTER_URL}/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        info = resp.json().get("data") or {}
    except (httpx.HTTPError, ValueError, AttributeError):
        report.line(False, "Tidak bisa menghubungi OpenRouter", "Cek koneksi internet.")
        return
    if resp.status_code != 200:
        report.line(
            False,
            "API key OpenRouter ditolak",
            "Buat key baru di https://openrouter.ai/settings/keys lalu jalankan setup lagi.",
        )
        return
    tier = "akun gratis" if info.get("is_free_tier") else "akun berbayar"
    report.line(True, f"API key OpenRouter valid ({tier})")
    quota = info.get("free_model_daily_requests")
    if config.OPENROUTER_MODEL.endswith(":free") and isinstance(quota, dict):
        try:
            remaining, limit = int(quota["remaining"]), int(quota["limit"])
        except (KeyError, TypeError, ValueError):
            remaining = limit = None
        if remaining is not None and remaining <= 0:
            from .brain_openrouter import reset_time_text

            report.warn(
                f"Kuota model gratis hari ini sudah habis (0 dari {limit})",
                f"Kuota baru mulai pukul {reset_time_text()}. Sementara itu otak lokal yang menjawab.",
            )
        elif remaining is not None:
            report.line(True, f"Kuota model gratis hari ini: sisa {remaining} dari {limit}")
    limit, remaining = info.get("limit"), info.get("limit_remaining")
    if limit is None:
        report.warn(
            "Key OpenRouter ini TANPA batas kredit",
            "Demi keamanan, buat key dengan batas (mis. $5) di https://openrouter.ai/settings/keys",
        )
    else:
        report.line(True, f"Batas key: sisa {_money(remaining)} dari {_money(limit)}")
        try:
            if float(remaining) <= 0:
                report.warn("Batas kredit key sudah habis", "Naikkan batasnya, atau pakai model ':free'.")
        except (TypeError, ValueError):
            pass

    model_id = config.OPENROUTER_MODEL
    try:
        models = httpx.get(f"{config.OPENROUTER_URL}/models", timeout=20).json().get("data") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        report.warn("Daftar model OpenRouter tidak bisa diambil", "Model belum bisa dicek.")
        return
    model = next((m for m in models if m.get("id") == model_id), None)
    if model is None:
        report.line(
            False,
            f"Model cloud '{model_id}' tidak ada di OpenRouter",
            "Cek namanya di https://openrouter.ai/models lalu ubah ITSME_OPENROUTER_MODEL.",
        )
    elif "tools" not in (model.get("supported_parameters") or []):
        report.line(
            False,
            f"Model cloud '{model_id}' tidak mendukung pemanggilan alat",
            "Pilih model lain yang mendukung tools di https://openrouter.ai/models",
        )
    else:
        report.line(True, f"Model cloud {model_id} tersedia")
    for fallback in config.OPENROUTER_FALLBACKS:
        found = next((m for m in models if m.get("id") == fallback), None)
        if found and "tools" in (found.get("supported_parameters") or []):
            report.line(True, f"Model cadangan cloud {fallback} tersedia")
        else:
            report.warn(
                f"Model cadangan cloud '{fallback}' tidak ada atau tidak mendukung alat",
                "Perbaiki ITSME_OPENROUTER_FALLBACKS di .env (isi off untuk mematikan).",
            )
    if model_id.endswith(":free"):
        report.warn(
            "Model gratis dibatasi 50 permintaan/hari (1000/hari kalau pernah isi saldo minimal $10)",
            "Satu pesan bisa memakai beberapa permintaan kalau It's Me memakai alat.",
        )


def main():
    report = _Report()
    print(f"Memeriksa {config.NAME} (otak: {config.BACKEND})\n")

    problems = config.check()
    for problem in problems:
        report.line(False, problem, "Isi di berkas .env (lihat .env.example)")
    if not problems:
        report.line(True, "Isi .env lengkap")

    _check_telegram(report)
    if config.BACKEND == "ollama":
        _check_ollama(report)
    elif config.BACKEND == "openrouter":
        _check_openrouter(report)
        if config.FALLBACK_LOCAL:
            _check_ollama(report, required=False)

    print("\nSemua beres, siap dipakai." if report.ok else "\nAda yang perlu dibetulkan (lihat di atas).")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
