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


def _check_ollama(report):
    try:
        resp = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=10)
        tags = resp.json()
    except (httpx.HTTPError, ValueError):
        report.line(
            False,
            f"Ollama tidak bisa dihubungi di {config.OLLAMA_URL}",
            "Buka aplikasi Ollama (Windows/Mac) atau jalankan: ollama serve",
        )
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
    report.line(
        present,
        f"Model {wanted} sudah diunduh",
        f"Jalankan: ollama pull {wanted}",
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

    print("\nSemua beres, siap dipakai." if report.ok else "\nAda yang perlu dibetulkan (lihat di atas).")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
