# It's Me - pemasang Cloudflare Tunnel otomatis (satu perintah).
#
# Membuat tunnel supaya dashboard It's Me (yang jalan di komputermu) bisa dibuka
# lewat domainmu sendiri (mis. ai.yantah-rutari.com) TANPA Tailscale dan tanpa
# membuka port router. Tunnel berjalan di komputer PEMIMPIN (lewat tunnel_cmd
# It's Me Node), jadi alamatnya ikut pindah saat failover. Syarat: DNS domain
# dikelola di Cloudflare.
#
#   powershell -ExecutionPolicy Bypass -File tunnel-setup.ps1
#
# Bisa diatur lewat variabel lingkungan (semuanya opsional):
#   ITSME_TUNNEL_HOST  domain, mis. ai.yantah-rutari.com (kalau kosong, ditanya)
#   ITSME_TUNNEL_NAME  nama tunnel (bawaan: itsme)
#   ITSME_DASH_PORT    port dashboard (bawaan: 8750)
#   ITSME_DIR          folder It's Me (bawaan: %USERPROFILE%\ItsMe)
#
# Hanya karakter ASCII (Windows PowerShell 5.1 salah membaca yang lain tanpa BOM).

$ErrorActionPreference = "Stop"
function Say($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }

$Domain = "$env:ITSME_TUNNEL_HOST".Trim()
while (-not $Domain) { $Domain = (Read-Host "Domain untuk dashboard (mis. ai.yantah-rutari.com)").Trim() }
$TunName = if ("$env:ITSME_TUNNEL_NAME".Trim()) { "$env:ITSME_TUNNEL_NAME".Trim() } else { "itsme" }
$Port = if ("$env:ITSME_DASH_PORT".Trim()) { "$env:ITSME_DASH_PORT".Trim() } else { "8750" }
$ItsMeDir = if ("$env:ITSME_DIR".Trim()) { "$env:ITSME_DIR".Trim() } else { Join-Path $HOME "ItsMe" }

$CfDir = Join-Path $HOME ".cloudflared"
New-Item -ItemType Directory -Force $CfDir | Out-Null

# 1. cloudflared (unduh ke folder It's Me, tanpa perlu admin kalau belum ada)
function Find-Cloudflared {
    $c = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("$env:LOCALAPPDATA\ItsMe\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { return $p }
    }
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

# 2. Login Cloudflare (sekali; browser terbuka, pilih domainmu lalu Authorize)
if (-not (Test-Path (Join-Path $CfDir "cert.pem"))) {
    Say "Login Cloudflare: browser akan terbuka. Pilih zona domain '$Domain', lalu Authorize."
    & $cf tunnel login
    if (-not (Test-Path (Join-Path $CfDir "cert.pem"))) {
        throw "Login Cloudflare belum selesai (cert.pem tidak ada). Jalankan skrip ini lagi."
    }
}

# 3. Buat tunnel kalau belum ada, ambil UUID + berkas kredensial
function Tunnel-Id($name) {
    try {
        $j = & $cf tunnel list --output json 2>$null | Out-String | ConvertFrom-Json
        $t = @($j | Where-Object { $_.name -eq $name }) | Select-Object -First 1
        if ($t) { return $t.id }
    } catch { }
    foreach ($line in (& $cf tunnel list 2>$null)) {
        $cols = (($line -replace '\s+', ' ').Trim() -split ' ')
        if ($cols.Count -ge 2 -and $cols[1] -eq $name) { return $cols[0] }
    }
    return $null
}
$id = Tunnel-Id $TunName
if (-not $id) {
    Say "Membuat tunnel '$TunName' ..."
    & $cf tunnel create $TunName | Out-Host
    $id = Tunnel-Id $TunName
}
if (-not $id) { throw "Gagal membuat/menemukan tunnel '$TunName'." }
$cred = Join-Path $CfDir "$id.json"
if (-not (Test-Path $cred)) { throw "Berkas kredensial tunnel tidak ditemukan: $cred" }
Say "Tunnel: $TunName ($id)"

# 4. config.yml (ingress domain -> dashboard localhost)
$config = @(
    "tunnel: $id",
    "credentials-file: $cred",
    "ingress:",
    "  - hostname: $Domain",
    "    service: http://localhost:$Port",
    "  - service: http_status:404"
) -join "`n"
[IO.File]::WriteAllText((Join-Path $CfDir "config.yml"), $config + "`n", (New-Object Text.UTF8Encoding($false)))

# 5. DNS: arahkan domain ke tunnel (--overwrite-dns menimpa catatan A/CNAME lama
#    untuk nama 'ai' yang mungkin sudah ada dari Hostinger)
Say "Mengarahkan DNS $Domain ke tunnel ..."
try { & $cf tunnel route dns --overwrite-dns $TunName $Domain | Out-Host }
catch { Warn "Route DNS: $($_.Exception.Message) (cek domain sudah aktif di Cloudflare)." }

# 6. tunnel_cmd di It's Me Node: jalan HANYA di pemimpin, jadi ikut failover
$nodeCfg = Join-Path $env:LOCALAPPDATA "ItsMeNode\data\config.json"
$runCmd = '"' + $cf + '" tunnel run ' + $TunName
if (Test-Path $nodeCfg) {
    $o = Get-Content $nodeCfg -Raw | ConvertFrom-Json
    $o | Add-Member -NotePropertyName tunnel_cmd -NotePropertyValue $runCmd -Force
    [IO.File]::WriteAllText($nodeCfg, ($o | ConvertTo-Json -Depth 6))
    Say "It's Me Node akan menjalankan tunnel otomatis di komputer pemimpin."
    Say "Salin kredensial (.cloudflared\cert.pem, $id.json, config.yml) ke calon-pemimpin lain agar failover mulus."
} else {
    Warn "It's Me Node belum terpasang di sini; tunnel tidak otomatis mengikuti pemimpin."
    Say "Supaya jalan terus di komputer ini, pasang sebagai layanan: & '$cf' service install"
}

# 7. .env bot: alamat publik + mode publik (login berkas kunci)
function Set-Env($file, $key, $val) {
    $lines = @()
    if (Test-Path $file) { $lines = @(Get-Content $file -Encoding UTF8) }
    $done = $false
    $out = foreach ($l in $lines) { if ($l -match "^[#\s]*$key=") { if (-not $done) { "$key=$val"; $done = $true } } else { $l } }
    if (-not $done) { $out = @($out) + "$key=$val" }
    [IO.File]::WriteAllText($file, ((@($out) -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
}
$envFile = Join-Path $ItsMeDir ".env"
if (Test-Path $ItsMeDir) {
    Set-Env $envFile "ITSME_PUBLIC_URL" "https://$Domain"
    Set-Env $envFile "ITSME_DASHBOARD_PUBLIC" "1"
    Say "Di .env: ITSME_PUBLIC_URL=https://$Domain dan ITSME_DASHBOARD_PUBLIC=1 (login berkas kunci)."
} else {
    Warn "Folder It's Me tidak ditemukan ($ItsMeDir); isi sendiri di .env: ITSME_PUBLIC_URL=https://$Domain dan ITSME_DASHBOARD_PUBLIC=1"
}

Write-Host ""
Say "Selesai! Setelah It's Me menyala ulang (atau tunggu pembaruan otomatis), buka: https://$Domain"
Say "Masuk pakai berkas kunci data\dashboard.key (salin ke HP-mu sekali). Lihat PANDUAN_AKSES_JARAK_JAUH.md."
Say "Disarankan tambah Cloudflare Access (login email) di dash.cloudflare.com -> Zero Trust -> Access."
