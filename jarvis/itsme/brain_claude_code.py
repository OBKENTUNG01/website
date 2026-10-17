"""Otak "Claude di komputer" (/claudepc): Claude Code lewat Claude Agent SDK,
masuk dengan akun Claude-mu sendiri. Tanpa API key — pemakaiannya dihitung
dari kuota paket Claude-mu (Pro/Max).

Login aplikasi Claude desktop terkunci untuk aplikasi itu dan tidak bisa
dipakai program lain, jadi It's Me butuh token akunnya sendiri. Sekali saja,
di komputer: `python -m itsme.claude_login` (browser terbuka untuk login, token
disimpan ke .env sebagai CLAUDE_CODE_OAUTH_TOKEN, berlaku ±1 tahun). Setelah
itu cukup kirim /claudepc dari HP.

Alat Claude Code dijaga aturan yang sama dengan otak lain lewat hook
PreToolUse: perintah yang mengubah sesuatu dan menulis berkas minta konfirmasi
lewat tombol Telegram, membaca di luar folder website minta konfirmasi, berkas
rahasia selalu ditolak.
"""

import asyncio
import importlib
import json
import os
import site
import sys
import sysconfig

from . import config, tools
from .persona import claude_code_prompt
from .updater import APP_DIR

if os.name == "nt":
    _LOGIN_COMMAND = f'cd "{APP_DIR}"; .venv\\Scripts\\python -m itsme.claude_login'
else:
    _LOGIN_COMMAND = f'cd "{APP_DIR}" && .venv/bin/python -m itsme.claude_login'

LOGIN_HINT = (
    "Claude di komputer belum login. Sekali saja, di komputer buka "
    f"{'PowerShell' if os.name == 'nt' else 'Terminal'} lalu jalankan:\n\n"
    f"{_LOGIN_COMMAND}\n\n"
    "Browser terbuka untuk login dengan akun Claude-mu (berlaku ±1 tahun). "
    "Setelah selesai, kirim /claudepc lagi."
)

# Alat yang hanya melihat, mencari, atau mengatur kerja Claude sendiri.
_FREE = {
    "Glob", "LS", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent",
    "ToolSearch", "BashOutput", "KillShell", "KillBash", "ExitPlanMode",
}
_SHELL = {"Bash", "PowerShell"}
_READ = {"Read", "NotebookRead", "Grep"}
_WRITE = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# Konfirmasi lewat Telegram ditunggu paling lama 300 detik (main.py).
_HOOK_TIMEOUT = 330
# Paket Python murni yang bisa gagal setengah jalan saat pywin32 belum terbaca.
_SDK_PACKAGES = ("claude_agent_sdk", "mcp")


def load_sdk():
    """Import claude_agent_sdk, juga kalau paketnya baru dipasang saat proses
    ini sudah jalan (claude_login, atau It's Me yang sudah menyala).

    Di Windows SDK butuh pywin32, yang jalurnya didaftarkan lewat berkas .pth —
    dan .pth hanya dibaca saat Python menyala. Kalau import gagal, .pth di
    folder paket dibaca ulang lalu dicoba sekali lagi. ImportError berarti
    paketnya memang belum terpasang.
    """
    try:
        import claude_agent_sdk
    except ImportError:
        # Buang modul setengah jadi dari percobaan tadi (mis. paket mcp tanpa
        # atribut submodulnya), supaya percobaan kedua mengimpor ulang utuh.
        for name in [n for n in sys.modules if n.split(".")[0] in _SDK_PACKAGES]:
            del sys.modules[name]
        paths = sysconfig.get_paths()
        for folder in dict.fromkeys((paths["purelib"], paths["platlib"])):
            site.addsitedir(folder)
        importlib.invalidate_caches()
        import claude_agent_sdk
    return claude_agent_sdk


# --- aturan alat ------------------------------------------------------------------
def _ask(ctx, text, always_ask=False):
    """Mode akses penuh: langsung jalan (pemilik dikabari); selain itu tombol."""
    if tools.approve(ctx, text, always_ask):
        return True, None
    return False, "Dibatalkan oleh pemilik."


def _new_text(name, inp):
    if name == "Write":
        return inp.get("content", "")
    if name == "Edit":
        return inp.get("new_string", "")
    if name == "MultiEdit":
        return "\n---\n".join(str(e.get("new_string", "")) for e in inp.get("edits") or [])
    return inp.get("new_source", "")


def decide(name, inp, ctx, cwd):
    """(boleh?, alasan kalau tidak). Bisa menunggu tombol pemilik di Telegram."""
    if name in _FREE:
        return True, None
    if name in _SHELL:
        command = str(inp.get("command", "")).strip()
        if tools.is_read_only(command):
            return True, None
        text = f"Jalankan perintah ini?\n\n{command}"
        if inp.get("description"):
            text += f"\n\nAlasan: {inp['description']}"
        # Perintah yang menyebut berkas rahasia selalu ditanyakan, juga di akses penuh.
        return _ask(ctx, text, always_ask=tools.mentions_secret(command))
    path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or cwd
    path = path if os.path.isabs(path) else os.path.join(cwd, path)
    if tools.is_secret_path(path):
        return False, "Ditolak: berkas itu biasanya berisi rahasia (token, password, kunci)."
    if name == "Grep" and tools.too_broad_to_search(path):
        return False, (
            "Ditolak: pencarian di folder ini terlalu luas (ikut berisi data login "
            "aplikasi & browser). Cari di folder proyek yang lebih spesifik."
        )
    if name in _READ:
        base = ctx.get("website_dir")
        if ctx.get("full_access") or (base and tools._inside(path, base)):
            return True, None
        return _ask(ctx, f"Baca berkas di luar folder website?\n\n{path}")
    if name in _WRITE:
        return _ask(ctx, f"Ubah berkas ini?\n\n{path}\n\nIsi baru:\n{tools._preview(str(_new_text(name, inp)))}")
    detail = tools._preview(json.dumps(inp, ensure_ascii=False))
    return _ask(ctx, f"Izinkan Claude memakai alat {name}?\n\n{detail}")


def _hook(ctx, cwd):
    async def pre_tool_use(data, tool_use_id, context):
        allowed, reason = await asyncio.to_thread(
            decide, data.get("tool_name", ""), data.get("tool_input") or {}, ctx, cwd
        )
        output = {"hookEventName": "PreToolUse", "permissionDecision": "allow" if allowed else "deny"}
        if reason:
            output["permissionDecisionReason"] = reason
        return {"hookSpecificOutput": output}

    return pre_tool_use


# --- menjalankan Claude Code ---------------------------------------------------------
def _env(token=None):
    # API key milik It's Me dikosongkan: kalau ada, Claude Code memakainya
    # (ditagih per token) alih-alih akunmu — dan rahasia tidak ikut terlihat
    # oleh perintah yang dijalankan Claude.
    env = {name: "" for name in tools._SECRET_ENV}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token or config.CLAUDE_CODE_OAUTH_TOKEN or ""
    # Sesi & pengaturan Claude Code milik It's Me terpisah dari aplikasi Claude desktop.
    env["CLAUDE_CONFIG_DIR"] = os.path.join(config.DATA_DIR, "claude")
    return env


async def _collect(prompt, options):
    """Jalankan satu giliran; kembalikan ResultMessage terakhir (atau None)."""
    sdk = load_sdk()
    result = None
    try:
        async for message in sdk.query(prompt=prompt, options=options):
            if isinstance(message, sdk.ResultMessage):
                result = message
    except sdk.ResultError:
        if result is None:
            raise  # error tanpa hasil yang bisa dijelaskan
    return result


def _is_auth_error(result):
    return result.api_error_status in (401, 403)


def check_login(token):
    """Coba token dengan satu jawaban pendek. "ok" atau "rejected"; error lain
    berarti belum bisa diperiksa."""
    options = load_sdk().ClaudeAgentOptions(
        tools=[], max_turns=1, env=_env(token), cwd=os.path.expanduser("~")
    )
    result = asyncio.run(_collect("Balas hanya dengan: OK", options))
    if result is not None and result.is_error and _is_auth_error(result):
        return "rejected"
    if result is None or result.is_error:
        raise RuntimeError((result.result if result else None) or "Claude Code tidak menjawab.")
    return "ok"


def run_agent(messages, ctx):
    """Kirim pesan terakhir pemilik ke Claude Code dan kembalikan jawabannya.

    Konteks obrolan disimpan Claude Code sebagai sesi; id sesinya dicatat di
    riwayat (`messages`) supaya giliran berikutnya melanjutkan sesi yang sama.
    """
    if not config.CLAUDE_CODE_OAUTH_TOKEN:
        return LOGIN_HINT
    try:
        sdk = load_sdk()
    except ImportError:
        return "Paket Claude di komputer belum terpasang. " + LOGIN_HINT

    cwd = ctx.get("website_dir") or os.path.expanduser("~")
    session = next((m["session_id"] for m in reversed(messages) if m.get("session_id")), None)
    options = sdk.ClaudeAgentOptions(
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": claude_code_prompt(ctx["memory"].text()),
        },
        resume=session,
        cwd=cwd,
        model=config.CLAUDE_CODE_MODEL,
        max_turns=config.CLAUDE_CODE_MAX_TURNS,
        env=_env(),
        hooks={"PreToolUse": [sdk.HookMatcher(hooks=[_hook(ctx, cwd)], timeout=_HOOK_TIMEOUT)]},
    )
    try:
        result = asyncio.run(_collect(messages[-1]["content"], options))
    except sdk.CLINotFoundError:
        return "Program Claude Code tidak ditemukan. " + LOGIN_HINT
    except Exception as exc:  # noqa: BLE001 - pemilik harus selalu dapat jawaban
        return f"Maaf, Claude di komputer error: {type(exc).__name__}: {exc}"
    if result is None:
        return "Maaf, Claude di komputer tidak memberi jawaban."
    if result.is_error:
        if _is_auth_error(result):
            return "Token akun Claude ditolak (salah atau kedaluwarsa). " + LOGIN_HINT
        return f"⚠️ Claude di komputer error: {result.result or result.subtype}"
    reply = (result.result or "").strip() or "(selesai)"
    messages.append({"role": "assistant", "content": reply, "session_id": result.session_id})
    return reply
