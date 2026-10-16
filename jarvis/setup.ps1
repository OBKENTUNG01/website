# It's Me - penyetelan otomatis untuk Windows 10/11.
#
# Cara paling gampang (tidak perlu Git): buka PowerShell, tempel:
#   irm https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.ps1 | iex
# Atau dari folder jarvis\ hasil clone/unduhan, di jendela PowerShell:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Yang dikerjakan: menyiapkan berkas, memasang Python & Ollama bila belum ada
# (lewat winget), menanyakan token bot (dicek langsung ke Telegram),
# mendeteksi user id-mu, memasang paket Python, mengunduh model, memeriksa
# semuanya, lalu menyalakan It's Me dan mengaturnya agar menyala sendiri
# setiap kamu masuk ke Windows. Aman dijalankan ulang untuk memperbarui:
# .env dan memori tidak ditimpa.
#
# Bisa diatur lewat variabel lingkungan (semuanya opsional):
#   ITSME_DIR            folder pemasangan (bawaan: %USERPROFILE%\ItsMe)
#   ITSME_REF            cabang GitHub sumber (bawaan: main, lalu cabang PR)
#   TELEGRAM_BOT_TOKEN   token bot (melewati pertanyaan)
#   OWNER_TELEGRAM_ID    user id pemilik (melewati deteksi)
#   OPENROUTER_API_KEY   key otak cloud OpenRouter (opsional, melewati pertanyaan)
#   ITSME_OLLAMA_MODEL   model lokal (bawaan: qwen3.5:9b), ikut disimpan ke .env
#   ITSME_NO_SLEEP       1 = cegah komputer tidur, 0 = jangan ubah (tanpa ditanya)
#   ITSME_AUTOSTART=0    jangan pasang penyalaan otomatis
#
# Berkas ini sengaja hanya berisi karakter ASCII: Windows PowerShell 5.1
# salah membaca karakter lain pada berkas tanpa BOM.

[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$ItsMeRepo = 'OBKENTUNG01/website'
$ItsMeFallbackRef = 'claude/jarvis-iron-man-q0ud8c'
$ItsMeTelegramApi = if ($env:TELEGRAM_API_URL) { $env:TELEGRAM_API_URL.TrimEnd('/') } else { 'https://api.telegram.org' }
$ItsMeOllamaUrl = if ($env:OLLAMA_URL) { $env:OLLAMA_URL.TrimEnd('/') } else { 'http://localhost:11434' }
$ItsMeOpenRouterApi = if ($env:OPENROUTER_URL) { $env:OPENROUTER_URL.TrimEnd('/') } else { 'https://openrouter.ai/api/v1' }
$ItsMeOnWindows = ($env:OS -eq 'Windows_NT')

function Write-ItsMeStep([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-ItsMeInfo([string]$Text) { Write-Host "    $Text" }
function Write-ItsMeWarn([string]$Text) { Write-Host "[!] $Text" -ForegroundColor Yellow }

function Update-ItsMeSessionPath {
    # Program yang baru dipasang winget belum masuk PATH jendela ini.
    if (-not $ItsMeOnWindows) { return }
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user;$env:Path"
}

function Invoke-ItsMeProgram([string]$File, [string[]]$Arguments, [string]$WorkDir) {
    # Menjalankan program di jendela ini (progress-nya tetap terlihat) dan
    # mengembalikan exit code-nya.
    #
    # Sengaja TIDAK memakai Start-Process -Wait: -Wait menunggu seluruh pohon
    # proses (program + semua turunannya). Pemasang Ollama menyalakan aplikasi
    # Ollama yang terus berjalan di tray, sehingga -Wait menunggu selamanya.
    # WaitForExit() hanya menunggu program itu sendiri.
    $params = @{ FilePath = $File; NoNewWindow = $true; PassThru = $true }
    if ($Arguments -and $Arguments.Count -gt 0) { $params.ArgumentList = $Arguments }
    if ($WorkDir) { $params.WorkingDirectory = $WorkDir }
    $proc = Start-Process @params
    $null = $proc.Handle  # simpan handle sekarang supaya ExitCode tetap terbaca nanti
    $proc.WaitForExit()
    if ($null -eq $proc.ExitCode) { return 0 }
    return [int]$proc.ExitCode
}

# --- .env ----------------------------------------------------------------------
function Get-ItsMeEnvValue([string]$File, [string]$Key) {
    if (-not (Test-Path $File)) { return '' }
    $line = Get-Content -Path $File -Encoding UTF8 | Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
    if ($line) { return $line.Substring($Key.Length + 1).Trim() }
    return ''
}

function Set-ItsMeEnvValue([string]$File, [string]$Key, [string]$Value) {
    # Ganti baris "KEY=" atau "# KEY=", atau tambahkan di akhir.
    $done = $false
    $result = foreach ($line in @(Get-Content -Path $File -Encoding UTF8)) {
        if ($line -match "^[#\s]*$Key=") {
            if (-not $done) { "$Key=$Value"; $done = $true }
        } else {
            $line
        }
    }
    if (-not $done) { $result = @($result) + "$Key=$Value" }
    [IO.File]::WriteAllText($File, ((@($result) -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
}

# --- 1. berkas -------------------------------------------------------------------
function Get-ItsMeSource {
    # Folder sumber yang berisi itsme\main.py: folder skrip ini, atau unduhan
    # dari GitHub kalau skrip dijalankan lewat "irm ... | iex".
    if ($PSScriptRoot -and (Test-Path (Join-Path (Join-Path $PSScriptRoot 'itsme') 'main.py'))) {
        return $PSScriptRoot
    }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('itsme-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $refs = if ($env:ITSME_REF) { @($env:ITSME_REF) } else { @('main', $ItsMeFallbackRef) }
    foreach ($ref in $refs) {
        $zip = Join-Path $tmp 'src.zip'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$ItsMeRepo/zip/refs/heads/$ref" -OutFile $zip
        } catch {
            continue
        }
        $out = Join-Path $tmp ('x-' + ($ref -replace '[^A-Za-z0-9]', '-'))
        Expand-Archive -Path $zip -DestinationPath $out -Force
        $main = Get-ChildItem -Path $out -Recurse -Filter 'main.py' |
            Where-Object { $_.Directory.Name -eq 'itsme' -and $_.Directory.Parent.Name -eq 'jarvis' } |
            Select-Object -First 1
        if ($main) {
            Write-ItsMeInfo "Sumber: github.com/$ItsMeRepo (cabang $ref)"
            return $main.Directory.Parent.FullName
        }
    }
    throw "Gagal mengunduh It's Me dari GitHub. Cek koneksi internet."
}

function Install-ItsMeFiles([string]$Src, [string]$Dir) {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $Dir = (Resolve-Path $Dir).Path
    if ((Resolve-Path $Src).Path -ne $Dir) {
        # Salin kode saja; .env, memori (data), dan .venv milikmu tidak disentuh.
        Get-ChildItem -Path $Src -Force |
            Where-Object { @('.env', 'data', '.venv', '__pycache__') -notcontains $_.Name } |
            ForEach-Object { Copy-Item -Path $_.FullName -Destination $Dir -Recurse -Force }
    }
    $envFile = Join-Path $Dir '.env'
    if (-not (Test-Path $envFile)) { Copy-Item -Path (Join-Path $Dir '.env.example') -Destination $envFile }
    Write-ItsMeInfo "Folder It's Me: $Dir"
    return $Dir
}

# --- 2. Python -------------------------------------------------------------------
function Find-ItsMePython {
    # Kembalikan path python.exe 3.10+ yang asli (bukan alias Microsoft Store).
    $ErrorActionPreference = 'Continue'
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $candidates += , @($py.Source, '-3') }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe", "$env:WINDIR\py.exe")) {
        if ($ItsMeOnWindows -and (Test-Path $p)) { $candidates += , @($p, '-3') }
    }
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $candidates += , @($cmd.Source) }
    }
    foreach ($c in $candidates) {
        $extra = @($c | Select-Object -Skip 1)
        try {
            $out = & $c[0] @extra -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $exe = ([string]($out | Select-Object -Last 1)).Trim()
                if ($exe) { return $exe }
            }
        } catch {
            continue  # kandidat ini tidak bisa dijalankan, coba berikutnya
        }
    }
    return $null
}

function Install-ItsMePython {
    $python = Find-ItsMePython
    if ($python) {
        Write-ItsMeInfo "Python ditemukan: $python"
        return $python
    }
    Write-ItsMeInfo 'Python 3.10+ belum ada, memasang Python 3.12...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        [void](Invoke-ItsMeProgram 'winget' @('install', '-e', '--id', 'Python.Python.3.12', '--scope', 'user', '--silent', '--accept-source-agreements', '--accept-package-agreements'))
    } else {
        $exe = Join-Path $env:TEMP 'python-3.12-setup.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile $exe
        [void](Invoke-ItsMeProgram $exe @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1'))
    }
    Update-ItsMeSessionPath
    $python = Find-ItsMePython
    if (-not $python) { throw 'Python belum terpasang. Pasang dari https://www.python.org/downloads/ (centang "Add to PATH") lalu jalankan skrip ini lagi.' }
    Write-ItsMeInfo "Python terpasang: $python"
    return $python
}

# --- 3. Telegram ------------------------------------------------------------------
function Read-ItsMeSecret([string]$Prompt) {
    # Seperti Read-Host, tapi ketikan tidak tampil di layar (untuk token &
    # API key, supaya tidak ikut terlihat di screenshot).
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    if (-not $secure -or $secure.Length -eq 0) { return '' }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Get-ItsMeBotUsername([string]$Token) {
    try {
        $me = Invoke-RestMethod -Uri "$ItsMeTelegramApi/bot$Token/getMe" -TimeoutSec 15
        if ($me.ok) { return [string]$me.result.username }
    } catch {
        return $null  # token ditolak (401) atau Telegram tidak terjangkau
    }
    return $null
}

function Get-ItsMeLatestSender([string]$Token) {
    try {
        $updates = Invoke-RestMethod -Uri "$ItsMeTelegramApi/bot$Token/getUpdates?timeout=5" -TimeoutSec 20
    } catch {
        return $null
    }
    $items = @($updates.result)
    for ($i = $items.Count - 1; $i -ge 0; $i--) {
        $msg = $items[$i].message
        if ($msg -and $msg.from -and $msg.from.id -and -not $msg.from.is_bot) {
            $name = ('{0} {1}' -f $msg.from.first_name, $msg.from.last_name).Trim()
            return [pscustomobject]@{ Id = [string]$msg.from.id; Name = $name }
        }
    }
    return $null
}

function Find-ItsMeOwner([string]$Token, [string]$Username) {
    Write-ItsMeInfo "Sekarang buka Telegram di HP, cari @$Username, lalu tekan START (atau kirim 'halo')."
    [void](Read-Host '    Sudah? Tekan Enter')
    $from = $null
    for ($i = 0; $i -lt 6 -and -not $from; $i++) {
        $from = Get-ItsMeLatestSender $Token
        if (-not $from) { Start-Sleep -Seconds 2 }
    }
    $id = ''
    if ($from) {
        $answer = Read-Host "    Pemilik terdeteksi: $($from.Name) (id $($from.Id)). Benar? [Y/n]"
        if ($answer -notmatch '^[nN]') { $id = $from.Id }
    } else {
        Write-ItsMeWarn 'Belum ada pesan yang masuk ke bot.'
    }
    $tries = 0
    while ($id -notmatch '^\d+$') {
        $tries++
        if ($tries -gt 3) { throw 'User id harus berupa angka.' }
        $id = ([string](Read-Host '    Ketik user id Telegram-mu (cek lewat @userinfobot)')).Trim()
    }
    return $id
}

function Set-ItsMeTelegram([string]$EnvFile) {
    $token = if ($env:TELEGRAM_BOT_TOKEN) { $env:TELEGRAM_BOT_TOKEN } else { Get-ItsMeEnvValue $EnvFile 'TELEGRAM_BOT_TOKEN' }
    $username = $null
    if ($token) { $username = Get-ItsMeBotUsername $token }
    if ($username) {
        Write-ItsMeInfo "Token bot valid: @$username"
    } else {
        Write-ItsMeInfo 'Belum punya bot? Di Telegram chat @BotFather, kirim /newbot, ikuti'
        Write-ItsMeInfo 'langkahnya, lalu salin token yang diberikan.'
        for ($i = 0; $i -lt 3 -and -not $username; $i++) {
            $token = (Read-ItsMeSecret '    Tempel token bot di sini (tidak akan tampil)') -replace '\s', ''
            if ($token) { $username = Get-ItsMeBotUsername $token }
            if ($username) { Write-ItsMeInfo "Token valid: @$username" }
            else { Write-ItsMeWarn 'Token ditolak Telegram. Salin ulang dari @BotFather.' }
        }
        if (-not $username) { throw 'Token bot tidak valid.' }
    }
    Set-ItsMeEnvValue $EnvFile 'TELEGRAM_BOT_TOKEN' $token

    $owner = if ($env:OWNER_TELEGRAM_ID) { $env:OWNER_TELEGRAM_ID } else { Get-ItsMeEnvValue $EnvFile 'OWNER_TELEGRAM_ID' }
    if ($owner -match '^\d+$') {
        Write-ItsMeInfo "Pemilik: user id $owner"
    } else {
        $owner = Find-ItsMeOwner $token $username
    }
    Set-ItsMeEnvValue $EnvFile 'OWNER_TELEGRAM_ID' $owner
    return $username
}

# --- 3b. otak cloud OpenRouter (opsional) -----------------------------------------
function Get-ItsMeOpenRouterKeyInfo([string]$Key) {
    try {
        $r = Invoke-RestMethod -Uri "$ItsMeOpenRouterApi/key" -Headers @{ Authorization = "Bearer $Key" } -TimeoutSec 15
        if ($r.data) { return $r.data }
    } catch {
        return $null  # key ditolak (401) atau OpenRouter tidak terjangkau
    }
    return $null
}

function Set-ItsMeOpenRouter([string]$EnvFile) {
    $key = if ($env:OPENROUTER_API_KEY) { $env:OPENROUTER_API_KEY } else { Get-ItsMeEnvValue $EnvFile 'OPENROUTER_API_KEY' }
    $info = $null
    if ($key) { $info = Get-ItsMeOpenRouterKeyInfo $key }
    if ($info) {
        Write-ItsMeInfo 'API key OpenRouter tersimpan masih valid.'
    } else {
        if ([Console]::IsInputRedirected) {
            Write-ItsMeInfo 'Otak cloud OpenRouter dilewati (opsional).'
            return
        }
        Write-ItsMeInfo 'Opsional: otak cloud lewat OpenRouter (jauh lebih pintar). Tekan Enter untuk melewati.'
        $key = ''
        for ($i = 0; $i -lt 3 -and -not $info; $i++) {
            $key = (Read-ItsMeSecret '    API key OpenRouter (tidak akan tampil)') -replace '\s', ''
            if (-not $key) { break }
            $info = Get-ItsMeOpenRouterKeyInfo $key
            if (-not $info) { Write-ItsMeWarn 'API key ditolak OpenRouter. Coba lagi, atau tekan Enter untuk melewati.' }
        }
        if (-not $info) {
            Write-ItsMeInfo "Dilewati - It's Me memakai otak lokal."
            return
        }
    }
    Set-ItsMeEnvValue $EnvFile 'OPENROUTER_API_KEY' $key
    Set-ItsMeEnvValue $EnvFile 'ITSME_BACKEND' 'openrouter'
    if (-not (Get-ItsMeEnvValue $EnvFile 'ITSME_OPENROUTER_MODEL')) {
        # Belum pernah isi saldo -> model gratis; sudah -> model berbayar yang murah & andal.
        $model = if ($info.is_free_tier) { 'google/gemma-4-31b-it:free' } else { 'anthropic/claude-haiku-5.5' }
        Set-ItsMeEnvValue $EnvFile 'ITSME_OPENROUTER_MODEL' $model
    }
    Write-ItsMeInfo "Otak cloud aktif: $(Get-ItsMeEnvValue $EnvFile 'ITSME_OPENROUTER_MODEL') (otak lokal jadi cadangan)"
    if ($null -eq $info.limit) {
        Write-ItsMeWarn 'Key ini TANPA batas kredit. Demi keamanan, buat key dengan batas (mis. $5) di openrouter.ai/settings/keys'
    } else {
        Write-ItsMeInfo ('Batas key: sisa ${0} dari ${1}' -f $info.limit_remaining, $info.limit)
    }
}

# --- 4. paket Python ----------------------------------------------------------------
function Get-ItsMeVenvPython([string]$Dir) {
    if ($ItsMeOnWindows) { return Join-Path $Dir '.venv\Scripts\python.exe' }
    return Join-Path (Join-Path (Join-Path $Dir '.venv') 'bin') 'python'
}

function Install-ItsMePackages([string]$Python, [string]$Dir) {
    $vpy = Get-ItsMeVenvPython $Dir
    if (-not (Test-Path $vpy)) {
        if ((Invoke-ItsMeProgram $Python @('-m', 'venv', '.venv') $Dir) -ne 0) { throw 'Gagal membuat virtualenv Python.' }
    }
    Write-ItsMeInfo 'Memasang paket (1-3 menit)...'
    $code = Invoke-ItsMeProgram $vpy @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', 'requirements.txt') $Dir
    if ($code -ne 0) { throw 'Gagal memasang paket Python. Cek koneksi internet lalu jalankan lagi.' }
    return $vpy
}

# --- 5. Ollama -------------------------------------------------------------------
function Find-ItsMeOllama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($ItsMeOnWindows -and $env:LOCALAPPDATA) {
        $p = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-ItsMeOllamaUp {
    try {
        Invoke-RestMethod -Uri "$ItsMeOllamaUrl/api/version" -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-ItsMeOllama([int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-ItsMeOllamaUp) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Invoke-ItsMeOllamaInstaller {
    # Pemasang resmi dari ollama.com (memasang baru atau memperbarui), dijalankan
    # di proses terpisah supaya tidak bisa mengganggu skrip ini.
    [void](Invoke-ItsMeProgram 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', '"irm https://ollama.com/install.ps1 | iex"'))
    Update-ItsMeSessionPath
}

function Start-ItsMeOllama([string]$Ollama) {
    if (Test-ItsMeOllamaUp) { return }
    Write-ItsMeInfo 'Menyalakan Ollama...'
    if ($ItsMeOnWindows) { Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Hidden }
    else { Start-Process -FilePath $Ollama -ArgumentList 'serve' }
    if (-not (Wait-ItsMeOllama 30)) { throw 'Ollama tidak mau menyala. Buka aplikasi Ollama dari menu Start, lalu jalankan skrip ini lagi.' }
}

function Install-ItsMeOllama {
    $ollama = Find-ItsMeOllama
    if (-not $ollama) {
        Write-ItsMeInfo 'Memasang Ollama dengan pemasang resmi dari ollama.com...'
        Invoke-ItsMeOllamaInstaller
        $ollama = Find-ItsMeOllama
        if (-not $ollama -and (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-ItsMeInfo 'Mencoba lewat winget...'
            [void](Invoke-ItsMeProgram 'winget' @('install', '-e', '--id', 'Ollama.Ollama', '--silent', '--accept-source-agreements', '--accept-package-agreements'))
            Update-ItsMeSessionPath
            $ollama = Find-ItsMeOllama
        }
        if (-not $ollama) { throw 'Ollama belum terpasang. Pasang dari https://ollama.com/download lalu jalankan skrip ini lagi.' }
    }
    Write-ItsMeInfo "Ollama: $ollama"
    Start-ItsMeOllama $ollama
    return $ollama
}

function Get-ItsMeModelName([string]$Vpy, [string]$Dir) {
    Push-Location $Dir
    try {
        $model = ([string](& $Vpy -c 'from itsme import config; print(config.OLLAMA_MODEL)' | Select-Object -Last 1)).Trim()
    } finally {
        Pop-Location
    }
    if (-not $model) { throw 'Tidak bisa membaca nama model dari konfigurasi.' }
    return $model
}

function Get-ItsMeModel([string]$Ollama, [string]$Model, [int]$Attempts, [string]$Vpy, [string]$Dir) {
    # Unduh model; kembalikan $true kalau berhasil. Kalau gagal karena sebab
    # yang jelas, alasannya disimpan di $script:ItsMeModelHint.
    $script:ItsMeModelHint = ''
    $have = & $Ollama list | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] } | Where-Object { $_ -eq $Model }
    if ($have) {
        Write-ItsMeInfo "Model $Model sudah ada."
        return $true
    }
    Write-ItsMeInfo "Mengunduh $Model (beberapa GB, hanya sekali - silakan ditinggal)..."
    # Pengunduh It's Me memeriksa setiap potongan & sha256 dan bisa melanjutkan
    # unduhan yang terputus. Pengunduh bawaan Ollama kadang merusak unduhan
    # besar ("digest mismatch"); dengan berkas yang sudah lengkap, ollama pull
    # di bawah hanya mencatat modelnya dalam beberapa detik.
    $code = Invoke-ItsMeProgram $Vpy @('-m', 'itsme.fetch_model', $Model) $Dir
    if ($code -eq 3) {
        # Model ini butuh Ollama yang lebih baru: perbarui, lalu coba lagi.
        $updateHint = 'Ollama perlu diperbarui untuk model ini. Pasang versi terbaru dari https://ollama.com/download lalu jalankan setup ini lagi.'
        Write-ItsMeInfo 'Memperbarui Ollama ke versi terbaru...'
        try {
            Invoke-ItsMeOllamaInstaller
            [void](Wait-ItsMeOllama 60)
            $found = Find-ItsMeOllama
            if ($found) { $Ollama = $found }
            Start-ItsMeOllama $Ollama
        } catch {
            $script:ItsMeModelHint = $updateHint
            return $false
        }
        $code = Invoke-ItsMeProgram $Vpy @('-m', 'itsme.fetch_model', $Model) $Dir
        if ($code -eq 3) {
            $script:ItsMeModelHint = $updateHint
            return $false
        }
    }
    if ($code -eq 4) {
        $script:ItsMeModelHint = 'Ruang disk tidak cukup untuk model (lihat pesan di atas). Kosongkan ruang lalu jalankan setup ini lagi.'
        return $false
    }
    # Kode 0: berkas lengkap, ollama pull hanya mencatat. Kode lain: ollama pull biasa.
    for ($i = 1; $i -le $Attempts; $i++) {
        if ($i -gt 1) { Write-ItsMeWarn "Unduhan gagal atau rusak di perjalanan (biasanya koneksi tidak stabil). Mencoba lagi ($i/$Attempts)..." }
        if ((Invoke-ItsMeProgram $Ollama @('pull', $Model)) -eq 0) { return $true }
    }
    return $false
}

# --- 6. menyalakan -------------------------------------------------------------------
function Stop-ItsMeRunning([string]$Dir) {
    # Matikan It's Me yang masih jalan dari pemasangan sebelumnya.
    if (-not $ItsMeOnWindows) { return }
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like '*start-itsme.cmd*' -or ($_.CommandLine -like '*itsme.main*' -and $_.CommandLine -like "*$Dir*")) }
    foreach ($p in @($procs)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Install-ItsMeLauncher([string]$Dir) {
    $startCmd = Join-Path $Dir 'start-itsme.cmd'
    $lines = @(
        '@echo off',
        "title It's Me",
        'cd /d "%~dp0"',
        'set PYTHONUTF8=1',
        'set PYTHONUNBUFFERED=1',
        ':loop',
        '".venv\Scripts\python.exe" -m itsme.main',
        'if %ERRORLEVEL% EQU 3 exit /b 0',
        'echo.',
        "echo It's Me berhenti. Menyala lagi dalam 10 detik... (tutup jendela ini untuk mematikan)",
        'timeout /t 10 /nobreak >nul',
        'goto loop'
    )
    [IO.File]::WriteAllText($startCmd, (($lines -join "`r`n") + "`r`n"), [Text.Encoding]::ASCII)
    $link = $null
    if ($env:ITSME_AUTOSTART -ne '0') {
        $startup = [Environment]::GetFolderPath('Startup')
        if ($startup) {
            $link = Join-Path $startup 'ItsMe.cmd'
            [IO.File]::WriteAllText($link, ('@start "" /min "' + $startCmd + '"' + "`r`n"), [Text.Encoding]::Default)
        }
    }
    return [pscustomobject]@{ StartCmd = $startCmd; Autostart = $link }
}

function Install-ItsMe {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # unduhan jauh lebih cepat tanpa progress bar
    try {
        if (-not $ItsMeOnWindows -and $env:ITSME_SETUP_FORCE -ne '1') {
            throw 'Skrip ini untuk Windows. Di macOS/Linux pakai setup.sh.'
        }

        Write-ItsMeStep "1/7 Menyiapkan berkas It's Me"
        $src = Get-ItsMeSource
        $dir = $env:ITSME_DIR
        if (-not $dir) {
            if ($PSScriptRoot -and $src -eq $PSScriptRoot) { $dir = $src } else { $dir = Join-Path $HOME 'ItsMe' }
        }
        $dir = Install-ItsMeFiles $src $dir
        $envFile = Join-Path $dir '.env'

        Write-ItsMeStep '2/7 Memeriksa Python'
        $python = Install-ItsMePython

        Write-ItsMeStep '3/7 Bot Telegram & otak cloud'
        $username = Set-ItsMeTelegram $envFile
        Set-ItsMeOpenRouter $envFile

        $noSleep = $env:ITSME_NO_SLEEP
        if ($ItsMeOnWindows -and -not $noSleep) {
            $answer = Read-Host "    Cegah komputer tidur (sleep) saat dicolok listrik, supaya It's Me selalu bisa dihubungi? [Y/n]"
            $noSleep = if ($answer -match '^[nN]') { '0' } else { '1' }
        }

        Write-ItsMeStep '4/7 Memasang paket Python'
        $vpy = Install-ItsMePackages $python $dir

        # Model pilihan lewat variabel lingkungan disimpan ke .env, supaya It's Me
        # yang menyala sendiri nanti memakai model yang sama.
        if ($env:ITSME_OLLAMA_MODEL) { Set-ItsMeEnvValue $envFile 'ITSME_OLLAMA_MODEL' $env:ITSME_OLLAMA_MODEL }

        # Dengan otak cloud, otak lokal hanya cadangan: kegagalannya tidak boleh
        # menghalangi It's Me menyala.
        $cloud = (Get-ItsMeEnvValue $envFile 'ITSME_BACKEND') -eq 'openrouter'
        $model = Get-ItsMeModelName $vpy $dir

        Write-ItsMeStep '5/7 Memeriksa Ollama (otak lokal)'
        $ollama = $null
        try {
            $ollama = Install-ItsMeOllama
        } catch {
            if (-not $cloud) { throw }
            Write-ItsMeWarn "Otak lokal (cadangan) belum siap: $($_.Exception.Message)"
        }

        Write-ItsMeStep '6/7 Model AI'
        if ($cloud) {
            Write-ItsMeInfo "Otak cloud aktif - model lokal (cadangan) diunduh setelah It's Me menyala."
        } elseif (-not (Get-ItsMeModel $ollama $model 3 $vpy $dir)) {
            if ($script:ItsMeModelHint) { throw $script:ItsMeModelHint }
            throw ("Model $model gagal diunduh 3 kali - biasanya karena koneksi internet tidak stabil. " +
                'Jalankan setup ini lagi nanti (bagian yang sudah beres tidak diulang), coba jaringan lain, ' +
                'atau isi API key OpenRouter saat ditanya supaya It''s Me bisa memakai otak cloud.')
        }

        Write-ItsMeStep "7/7 Pemeriksaan akhir & menyalakan It's Me"
        if ((Invoke-ItsMeProgram $vpy @('-m', 'itsme.doctor') $dir) -ne 0) {
            throw 'Pemeriksaan gagal - lihat pesan di atas, betulkan, lalu jalankan skrip ini lagi.'
        }
        if ($ItsMeOnWindows -and $noSleep -eq '1') {
            & powercfg /change standby-timeout-ac 0 | Out-Null
            & powercfg /change hibernate-timeout-ac 0 | Out-Null
            Write-ItsMeInfo 'Komputer tidak akan tidur saat dicolok listrik.'
        }
        $launcher = Install-ItsMeLauncher $dir
        if ($ItsMeOnWindows) {
            Stop-ItsMeRunning $dir
            Start-Process -FilePath $launcher.StartCmd -WindowStyle Minimized
        }

        Write-Host ''
        Write-Host "It's Me sudah menyala!" -ForegroundColor Green
        Write-ItsMeInfo "Buka Telegram di HP, chat @$username, lalu coba kirim: halo"
        Write-ItsMeInfo 'Ganti otak      : kirim /cloud atau /lokal ke bot (/otak untuk melihat)'
        Write-ItsMeInfo "Jendela 'It's Me' yang terkecilkan di taskbar = It's Me sedang jalan (tutup = mati)."
        if ($launcher.Autostart) { Write-ItsMeInfo "Menyala otomatis setiap kamu masuk Windows ($($launcher.Autostart))." }
        Write-ItsMeInfo "Nyalakan manual : $($launcher.StartCmd)"
        Write-ItsMeInfo 'Perbarui        : jalankan perintah setup yang sama lagi'

        if ($cloud -and $ollama) {
            Write-ItsMeStep 'Tambahan: model cadangan untuk otak lokal'
            Write-ItsMeInfo "It's Me sudah bisa dipakai sekarang; unduhan ini berjalan sambil menunggu."
            if (Get-ItsMeModel $ollama $model 2 $vpy $dir) {
                Write-ItsMeInfo 'Model cadangan siap. Kalau otak cloud bermasalah, otak lokal otomatis menjawab.'
            } else {
                Write-ItsMeWarn ("Model cadangan $model belum berhasil diunduh. It's Me tetap jalan dengan otak cloud; " +
                    'coba lagi nanti dengan menjalankan setup ini lagi.')
            }
        }
    } catch {
        Write-Host ''
        Write-Host "[GAGAL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

if ($env:ITSME_SETUP_NO_RUN -ne '1') { Install-ItsMe }
