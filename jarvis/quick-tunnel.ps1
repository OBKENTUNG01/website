# It's Me - nyalakan Cloudflare Quick Tunnel (alamat publik INSTAN untuk dashboard).
#
# Tanpa domain, tanpa ubah DNS, tanpa risiko ke website/email-mu. It's Me akan
# menjalankan cloudflared sendiri dan mengirim alamat (https://xxxx.trycloudflare.com)
# ke Telegram-mu. Alamatnya SEMENTARA (berubah tiap It's Me nyala ulang); cek yang
# terbaru kapan saja dengan /alamat. Untuk alamat TETAP pakai domain sendiri,
# pakai tunnel-setup.ps1.
#
#   powershell -ExecutionPolicy Bypass -File quick-tunnel.ps1
#   (opsional) ITSME_DIR = folder It's Me (bawaan: %USERPROFILE%\ItsMe)
#
# Hanya karakter ASCII (Windows PowerShell 5.1 salah membaca yang lain tanpa BOM).

$ErrorActionPreference = "Stop"
function Say($m){ Write-Host "==> $m" -ForegroundColor Cyan }

$ItsMeDir = if ("$env:ITSME_DIR".Trim()) { "$env:ITSME_DIR".Trim() } else { Join-Path $HOME "ItsMe" }
if (-not (Test-Path $ItsMeDir)) { throw "Folder It's Me tidak ada: $ItsMeDir. Pasang It's Me dulu (setup.ps1)." }

# 1. cloudflared (unduh ke folder It's Me kalau belum ada; tanpa admin)
function Find-Cloudflared {
    $c = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $env:LOCALAPPDATA "ItsMe\cloudflared.exe"
    if (Test-Path $p) { return $p }
    return $null
}
$cf = Find-Cloudflared
if (-not $cf) {
    Say "Mengunduh cloudflared ..."
    $cf = Join-Path $env:LOCALAPPDATA "ItsMe\cloudflared.exe"
    New-Item -ItemType Directory -Force (Split-Path $cf) | Out-Null
    Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cf -UseBasicParsing
}
Say "cloudflared: $cf"

# 2. Nyalakan di .env
function Set-Env($file, $key, $val) {
    $lines = @(); if (Test-Path $file) { $lines = @(Get-Content $file -Encoding UTF8) }
    $done = $false
    $out = foreach ($l in $lines) { if ($l -match "^[#\s]*$key=") { if (-not $done) { "$key=$val"; $done = $true } } else { $l } }
    if (-not $done) { $out = @($out) + "$key=$val" }
    [IO.File]::WriteAllText($file, ((@($out) -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
}
$envFile = Join-Path $ItsMeDir ".env"
Set-Env $envFile "ITSME_QUICK_TUNNEL" "1"
Set-Env $envFile "ITSME_TUNNEL_BIN" $cf
Set-Env $envFile "ITSME_DASHBOARD_PUBLIC" "1"
Say "Disetel di .env: ITSME_QUICK_TUNNEL=1, cloudflared, dan mode publik (login berkas kunci)."

# 3. Nyalakan ulang It's Me supaya langsung aktif (kalau peluncur senyap ada)
$vbs = Join-Path $ItsMeDir "ItsMe.vbs"
$stop = Join-Path $ItsMeDir "stop-itsme.cmd"
if ((Test-Path $vbs) -and (Test-Path $stop)) {
    Say "Menyalakan ulang It's Me ..."
    try { & cmd /c "`"$stop`"" | Out-Null } catch { }
    Start-Sleep 2
    Start-Process wscript.exe "`"$vbs`""
    Say "It's Me menyala lagi. Dalam beberapa detik, alamat dashboard dikirim ke Telegram-mu."
} else {
    Say "Tutup lalu jalankan It's Me lagi (atau tunggu pembaruan otomatis). Setelah itu alamat dikirim ke Telegram."
}
Write-Host ""
Say "Cek alamat kapan saja: ketik /alamat ke bot. Masuk dashboard pakai berkas data\dashboard.key (salin ke HP)."
Say "Catatan: pastikan It's Me sudah memasang pembaruan terbaru (kode tunnel). Kalau belum terkirim, tunggu ~2 menit lalu /alamat."
