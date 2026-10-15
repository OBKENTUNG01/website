"""Alat (tools) yang bisa dipakai It's Me.

Dua jenis:
  1. Alat server Anthropic (web_search, web_fetch) — jalan di sisi Anthropic,
     dipakai It's Me untuk cari & baca info terbaru dari internet.
  2. Alat klien (di bawah) — jalan di komputer/HP tempat bot ini berjalan:
     baca/tulis berkas, jalankan perintah, deploy website lewat Git, memori,
     dan tugas latar (multitasking).

Aksi berisiko (menulis berkas, menjalankan perintah berbahaya, push Git)
minta konfirmasi dulu lewat tombol di Telegram. Begitu kamu tekan "Ya",
langsung dijalankan.
"""

import os
import re
import subprocess

# --- Alat server (internet) -------------------------------------------------
SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]

# --- Definisi alat klien ----------------------------------------------------
CLIENT_TOOLS = [
    {
        "name": "jalankan_perintah",
        "description": (
            "Jalankan satu perintah shell di komputer/HP tempat It's Me "
            "berjalan. Perintah yang berpotensi mengubah/menghapus sesuatu "
            "akan minta konfirmasi dulu. Gunakan untuk git, pengecekan "
            "berkas, menjalankan skrip, dll."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "perintah": {"type": "string", "description": "Perintah shell."},
                "alasan": {
                    "type": "string",
                    "description": "Penjelasan singkat untuk apa perintah ini.",
                },
            },
            "required": ["perintah"],
            "additionalProperties": False,
        },
    },
    {
        "name": "baca_berkas",
        "description": "Baca isi sebuah berkas teks. Path relatif ke folder "
        "website kalau sudah diatur, atau path absolut.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tulis_berkas",
        "description": "Tulis/timpa isi sebuah berkas. Minta konfirmasi dulu.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "isi": {"type": "string"},
            },
            "required": ["path", "isi"],
            "additionalProperties": False,
        },
    },
    {
        "name": "deploy_website",
        "description": (
            "Simpan semua perubahan di folder website ke Git lalu push "
            "(git add -A, commit, push). Hostinger yang diatur auto-deploy "
            "akan menarik perubahan ini. Minta konfirmasi dulu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pesan_commit": {"type": "string"},
            },
            "required": ["pesan_commit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simpan_memori",
        "description": "Simpan sebuah catatan ke memori jangka panjang supaya "
        "It's Me mengingatnya di percakapan berikutnya.",
        "input_schema": {
            "type": "object",
            "properties": {"catatan": {"type": "string"}},
            "required": ["catatan"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tugas_latar",
        "description": (
            "Kerjakan sebuah tugas di latar belakang (paralel) sambil kamu "
            "tetap bisa mengobrol. Hasilnya dikirim terpisah begitu selesai. "
            "Pakai ini kalau ada beberapa tugas yang ingin jalan bersamaan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"instruksi": {"type": "string"}},
            "required": ["instruksi"],
            "additionalProperties": False,
        },
    },
]

ALL_TOOLS = SERVER_TOOLS + CLIENT_TOOLS

# Perintah yang dianggap berisiko → butuh konfirmasi.
_RISKY = re.compile(
    r"\b(rm|rmdir|mv|dd|mkfs|shutdown|reboot|kill|pkill|chmod|chown|"
    r"truncate|git\s+push|git\s+reset|git\s+clean|curl|wget|npm\s+i|"
    r"pip\s+install|apt|sudo|>|>>)\b"
)


def _resolve(ctx, path):
    if os.path.isabs(path):
        return path
    base = ctx.get("website_dir") or os.getcwd()
    return os.path.join(base, path)


def _run_shell(command, cwd, timeout=180):
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "⏱️ Perintah melebihi batas waktu dan dihentikan."
    out = (proc.stdout or "")[-4000:]
    err = (proc.stderr or "")[-2000:]
    parts = [f"exit code: {proc.returncode}"]
    if out.strip():
        parts.append("stdout:\n" + out)
    if err.strip():
        parts.append("stderr:\n" + err)
    return "\n".join(parts)


def handle_tool(name, tool_input, ctx):
    """Jalankan satu alat klien dan kembalikan hasilnya sebagai teks.

    ctx berisi: website_dir, memory, confirm(teks)->bool,
    schedule_background(instruksi)->str.
    """
    try:
        if name == "jalankan_perintah":
            perintah = tool_input.get("perintah", "")
            if not perintah.strip():
                return "Perintah kosong."
            if _RISKY.search(perintah):
                alasan = tool_input.get("alasan", "")
                teks = f"Jalankan perintah ini?\n\n`{perintah}`"
                if alasan:
                    teks += f"\n\nAlasan: {alasan}"
                if not ctx["confirm"](teks):
                    return "Dibatalkan oleh pemilik."
            return _run_shell(perintah, ctx.get("website_dir"))

        if name == "baca_berkas":
            path = _resolve(ctx, tool_input["path"])
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()[:12000]

        if name == "tulis_berkas":
            path = _resolve(ctx, tool_input["path"])
            isi = tool_input.get("isi", "")
            if not ctx["confirm"](f"Tulis/timpa berkas ini?\n\n{path}"):
                return "Dibatalkan oleh pemilik."
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(isi)
            return f"Berhasil ditulis: {path} ({len(isi)} karakter)."

        if name == "deploy_website":
            cwd = ctx.get("website_dir")
            if not cwd:
                return "WEBSITE_REPO_DIR belum diatur, jadi deploy belum bisa."
            pesan = tool_input.get("pesan_commit", "update dari It's Me")
            if not ctx["confirm"](f"Deploy website sekarang?\n\nCommit: {pesan}"):
                return "Dibatalkan oleh pemilik."
            steps = [
                "git add -A",
                f'git commit -m "{pesan}"',
                "git push",
            ]
            log = []
            for step in steps:
                log.append(f"$ {step}\n" + _run_shell(step, cwd))
            return "\n\n".join(log)

        if name == "simpan_memori":
            return ctx["memory"].add(tool_input.get("catatan", ""))

        if name == "tugas_latar":
            return ctx["schedule_background"](tool_input.get("instruksi", ""))

        return f"Alat tidak dikenal: {name}"
    except FileNotFoundError:
        return "Berkas tidak ditemukan."
    except Exception as exc:  # noqa: BLE001 - laporkan error apa pun ke pemakai
        return f"Terjadi error saat menjalankan {name}: {exc}"
