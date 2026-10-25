"""Skrip peluncur yang ikut diperbarui, bukan tertinggal di versi lama.

`updater` hanya memasang paket yang sedang berjalan (`itsme/`), sedangkan skrip
peluncur dibuat sekali oleh setup.ps1. Akibatnya keputusan "nyalakan lagi atau
berhenti" tetap dipegang berkas lama — dan dua keputusan di sana terbalik:

  kode 3 (It's Me sudah jalan)  -> launcher lama MENYERAH, bot tidak kembali
  kode 0 (berhenti disengaja)   -> launcher lama justru MENYALAKAN ulang

Menulis ulang skrip dari dalam paket membuat logika itu selalu ikut pembaruan
yang kita kirim, tanpa pemilik perlu menjalankan setup lagi.
"""

import os

# 3 = sudah jalan (tunggu, jangan menyerah), 0 = berhenti disengaja (keluar),
# 5 = pembaruan terpasang (nyalakan lagi segera), lainnya = crash (jeda lalu ulangi).
_PS1 = """# Dijalankan tersembunyi oleh ItsMe.vbs saat login (tanpa jendela).
# Ditulis ulang otomatis oleh itsme/launch.py setiap It's Me menyala.
$Dir = $PSScriptRoot
Set-Location $Dir
$env:PYTHONUTF8 = "1"; $env:PYTHONUNBUFFERED = "1"
$data = Join-Path $Dir "data"
New-Item -ItemType Directory -Force $data | Out-Null
$log = Join-Path $data "run.log"
while ($true) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) { Move-Item $log "$log.old" -Force }
    & ".venv\\Scripts\\python.exe" -u -m itsme.main *>> $log
    $code = $LASTEXITCODE
    if ($code -eq 0) { break }          # berhenti disengaja
    if ($code -eq 5) { continue }       # pembaruan terpasang, nyalakan lagi segera
    if ($code -eq 3) { Start-Sleep 30 } # masih ada It's Me lain: tunggu, JANGAN menyerah
    else { Start-Sleep 10 }
}
"""

_CMD = """@echo off
title It's Me
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
:loop
".venv\\Scripts\\python.exe" -u -m itsme.main
if %ERRORLEVEL% EQU 0 exit /b 0
if %ERRORLEVEL% EQU 5 goto loop
if %ERRORLEVEL% EQU 3 timeout /t 30 /nobreak >nul
if %ERRORLEVEL% EQU 3 goto loop
echo.
echo It's Me berhenti. Menyalakan lagi dalam 10 detik... (tutup jendela ini untuk mematikan)
timeout /t 10 /nobreak >nul
goto loop
"""

FILES = {"run-itsme.ps1": _PS1, "start-itsme.cmd": _CMD}


def ensure(directory):
    """Tulis skrip peluncur yang isinya berbeda. Kembalikan nama yang ditulis.

    Idempoten: berkas yang isinya sudah benar tidak disentuh (mtime tetap).
    Gagal menulis (folder read-only) tidak boleh menghentikan bot.
    """
    written = []
    for name, text in FILES.items():
        path = os.path.join(directory, name)
        body = text.replace("\n", "\r\n").encode("utf-8")
        try:
            with open(path, "rb") as f:
                if f.read() == body:
                    continue
        except OSError:
            pass
        try:
            with open(path, "wb") as f:
                f.write(body)
            written.append(name)
        except OSError:
            pass
    return written
