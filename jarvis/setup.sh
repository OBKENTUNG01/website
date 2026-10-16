#!/usr/bin/env bash
# It's Me — penyetelan otomatis untuk macOS dan Linux.
#
# Cara paling gampang (tidak perlu Git), tempel di Terminal:
#   curl -fsSL https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.sh | bash
# Atau dari folder jarvis/ hasil clone:
#   bash setup.sh
#
# Yang dikerjakan: menyiapkan berkas, memasang Python & Ollama bila belum ada,
# menanyakan token bot (dicek langsung ke Telegram), mendeteksi user id-mu,
# memasang paket Python, mengunduh model, memeriksa semuanya, lalu menyalakan
# It's Me dan mengaturnya agar menyala sendiri setiap komputer hidup.
# Aman dijalankan ulang untuk memperbarui: .env dan memori tidak ditimpa.
#
# Bisa diatur lewat variabel lingkungan (semuanya opsional):
#   ITSME_DIR            folder pemasangan (bawaan: ~/itsme)
#   ITSME_REF            cabang GitHub sumber (bawaan: main, lalu cabang PR)
#   TELEGRAM_BOT_TOKEN   token bot (melewati pertanyaan)
#   OWNER_TELEGRAM_ID    user id pemilik (melewati deteksi)
#   OPENROUTER_API_KEY   key otak cloud OpenRouter (opsional, melewati pertanyaan)
#   ITSME_OLLAMA_MODEL   model lokal (bawaan: qwen3.5:9b), ikut disimpan ke .env
#   ITSME_AUTOSTART=0    jangan pasang penyalaan otomatis

set -euo pipefail

REPO="OBKENTUNG01/website"
FALLBACK_REF="claude/jarvis-iron-man-q0ud8c"
TELEGRAM_API="${TELEGRAM_API_URL:-https://api.telegram.org}"
OPENROUTER_API="${OPENROUTER_URL:-https://openrouter.ai/api/v1}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

if [ -t 1 ]; then
  C_STEP=$'\033[1;36m'; C_WARN=$'\033[1;33m'; C_BAD=$'\033[1;31m'; C_OK=$'\033[1;32m'; C_END=$'\033[0m'
else
  C_STEP=""; C_WARN=""; C_BAD=""; C_OK=""; C_END=""
fi
step() { printf '\n%s==> %s%s\n' "$C_STEP" "$*" "$C_END"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s[!] %s%s\n' "$C_WARN" "$*" "$C_END"; }
die()  { printf '\n%s[GAGAL] %s%s\n' "$C_BAD" "$*" "$C_END" >&2; exit 1; }

HAVE_TTY=0
if (exec </dev/tty) 2>/dev/null; then HAVE_TTY=1; fi

# ask "Pertanyaan: " → mencetak jawaban. Dibaca dari terminal, jadi tetap
# jalan walau skrip ini dijalankan lewat "curl ... | bash".
ask() {
  local answer=""
  [ "$HAVE_TTY" = 1 ] || die "Butuh jawaban dari terminal: \"$1\". Jalankan skrip ini dari Terminal."
  read -r -p "$1" answer </dev/tty || true
  printf '%s' "$answer"
}

# ask_secret "Pertanyaan: " → seperti ask, tapi ketikan tidak tampil di layar
# (untuk token & API key, supaya tidak ikut terlihat di screenshot).
ask_secret() {
  local answer=""
  [ "$HAVE_TTY" = 1 ] || die "Butuh jawaban dari terminal: \"$1\". Jalankan skrip ini dari Terminal."
  read -r -s -p "$1" answer </dev/tty || true
  printf '\n' >&2
  printf '%s' "$answer"
}

is_number() {
  case "${1:-}" in ''|*[!0-9]*) return 1 ;; esac
  return 0
}

# --- .env --------------------------------------------------------------------
get_env() {  # get_env KEY → nilai dari .env (kosong kalau tidak ada)
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | tail -n 1
}

set_env() {  # set_env KEY VALUE → ganti baris "KEY=" atau "# KEY=", atau tambahkan
  local tmp
  tmp="$(mktemp)"
  awk -v k="$1" -v v="$2" '
    $0 ~ "^[#[:space:]]*" k "=" { if (!done) { print k "=" v; done = 1 } ; next }
    { print }
    END { if (!done) print k "=" v }
  ' .env >"$tmp"
  cat "$tmp" >.env
  rm -f "$tmp"
}

# --- 1. berkas ---------------------------------------------------------------
locate_source() {  # menentukan SRC (sumber kode) dan DIR (folder pemasangan)
  local here="" ref found
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -n "$here" ] && [ -f "$here/itsme/main.py" ]; then
    SRC="$here"
    DIR="${ITSME_DIR:-$here}"
    return
  fi
  DIR="${ITSME_DIR:-$HOME/itsme}"
  TMP_SRC="$(mktemp -d)"
  trap 'rm -rf "$TMP_SRC"' EXIT
  for ref in ${ITSME_REF:-main $FALLBACK_REF}; do
    if curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$ref" 2>/dev/null \
        | tar -xzf - -C "$TMP_SRC" 2>/dev/null; then
      found="$(find "$TMP_SRC" -path '*/jarvis/itsme/main.py' | head -n 1)"
      if [ -n "$found" ]; then
        SRC="$(dirname "$(dirname "$found")")"
        info "Sumber: github.com/$REPO (cabang $ref)"
        return
      fi
      rm -rf "${TMP_SRC:?}"/*
    fi
  done
  die "Gagal mengunduh It's Me dari GitHub. Cek koneksi internet."
}

install_files() {
  if [ "$SRC" != "$DIR" ]; then
    mkdir -p "$DIR"
    # Salin kode saja; .env, memori (data/), dan .venv milikmu tidak disentuh.
    (cd "$SRC" && tar -cf - --exclude=.env --exclude=data --exclude=.venv --exclude=__pycache__ .) \
      | (cd "$DIR" && tar -xf -)
  fi
  cd "$DIR"
  DIR="$(pwd)"  # selalu absolut — systemd & launchd butuh path absolut
  [ -f .env ] || cp .env.example .env
  chmod 600 .env
  info "Folder It's Me: $DIR"
}

# --- 2. Python ---------------------------------------------------------------
find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 \
        && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

ensure_python() {
  if PY="$(find_python)"; then
    info "$("$PY" --version) ditemukan"
    return
  fi
  info "Python 3.10+ belum ada, memasang..."
  case "$(uname -s)" in
    Darwin)
      command -v brew >/dev/null 2>&1 \
        || die "Pasang Python dari https://www.python.org/downloads/ lalu jalankan skrip ini lagi."
      brew install python@3.12 ;;
    *)
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-pip
      elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm python python-pip
      else
        die "Pasang Python 3.10+ lewat pengelola paket sistemmu, lalu jalankan skrip ini lagi."
      fi ;;
  esac
  PY="$(find_python)" || die "Python 3.10+ masih belum ditemukan."
}

# --- 3. Telegram -------------------------------------------------------------
telegram_username() {  # telegram_username TOKEN → mencetak username bot, gagal kalau ditolak
  local json
  json="$(curl -fsS --max-time 15 "$TELEGRAM_API/bot$1/getMe" 2>/dev/null)" || return 1
  printf '%s' "$json" | "$PY" -c '
import json, sys
data = json.load(sys.stdin)
if not data.get("ok"):
    sys.exit(1)
print(data["result"].get("username", "?"))'
}

latest_sender() {  # latest_sender TOKEN → "id nama" pengirim pesan terakhir ke bot
  curl -fsS --max-time 20 "$TELEGRAM_API/bot$1/getUpdates?timeout=5" 2>/dev/null | "$PY" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit()
for update in reversed(data.get("result", [])):
    sender = (update.get("message") or {}).get("from") or {}
    if sender.get("id") and not sender.get("is_bot"):
        name = (sender.get("first_name", "") + " " + sender.get("last_name", "")).strip()
        print(sender["id"], name or "-")
        break' || true
}

detect_owner() {  # detect_owner TOKEN USERNAME → mencetak user id pemilik
  local id="" name="" answer attempt
  info "Sekarang buka Telegram di HP, cari @$2, lalu tekan START (atau kirim \"halo\")." >&2
  ask "    Sudah? Tekan Enter... " >/dev/null
  for attempt in 1 2 3 4 5 6; do
    read -r id name <<<"$(latest_sender "$1")" || true
    [ -n "$id" ] && break
    sleep 2
  done
  if is_number "$id"; then
    answer="$(ask "    Pemilik terdeteksi: $name (id $id). Benar? [Y/n] ")"
    case "$answer" in [nN]*) id="" ;; esac
  else
    warn "Belum ada pesan yang masuk ke bot." >&2
  fi
  attempt=0
  while ! is_number "$id"; do
    attempt=$((attempt + 1))
    [ "$attempt" -le 3 ] || die "User id harus berupa angka."
    id="$(ask "    Ketik user id Telegram-mu (cek lewat @userinfobot): ")"
  done
  printf '%s' "$id"
}

configure_telegram() {
  local token owner username="" attempt
  token="${TELEGRAM_BOT_TOKEN:-$(get_env TELEGRAM_BOT_TOKEN)}"
  if [ -n "$token" ] && username="$(telegram_username "$token")"; then
    info "Token bot valid: @$username"
  else
    info "Belum punya bot? Di Telegram chat @BotFather, kirim /newbot, ikuti"
    info "langkahnya, lalu salin token yang diberikan."
    token=""
    for attempt in 1 2 3; do
      token="$(ask_secret "    Tempel token bot di sini (tidak akan tampil): " | tr -d '[:space:]')"
      if [ -n "$token" ] && username="$(telegram_username "$token")"; then
        info "Token valid: @$username"
        break
      fi
      warn "Token ditolak Telegram. Salin ulang dari @BotFather."
      token=""
    done
    [ -n "$token" ] || die "Token bot tidak valid."
  fi
  set_env TELEGRAM_BOT_TOKEN "$token"
  BOT_USERNAME="$username"

  owner="${OWNER_TELEGRAM_ID:-$(get_env OWNER_TELEGRAM_ID)}"
  if is_number "$owner"; then
    info "Pemilik: user id $owner"
  else
    owner="$(detect_owner "$token" "$username")"
  fi
  set_env OWNER_TELEGRAM_ID "$owner"
}

# --- 3b. otak cloud OpenRouter (opsional) -----------------------------------------
openrouter_key_info() {  # openrouter_key_info KEY → "free|paid batas sisa", gagal kalau ditolak
  local json
  json="$(curl -fsS --max-time 15 -H "Authorization: Bearer $1" "$OPENROUTER_API/key" 2>/dev/null)" || return 1
  printf '%s' "$json" | "$PY" -c '
import json, sys
info = json.load(sys.stdin).get("data") or {}
limit, remaining = info.get("limit"), info.get("limit_remaining")
print("free" if info.get("is_free_tier") else "paid",
      "none" if limit is None else limit, "none" if remaining is None else remaining)'
}

configure_openrouter() {
  local key details="" attempt tier limit remaining model
  key="${OPENROUTER_API_KEY:-$(get_env OPENROUTER_API_KEY)}"
  if [ -n "$key" ] && details="$(openrouter_key_info "$key")"; then
    info "API key OpenRouter tersimpan masih valid."
  else
    key=""
    if [ "$HAVE_TTY" != 1 ]; then
      info "Otak cloud OpenRouter dilewati (opsional)."
      return 0
    fi
    info "Opsional: otak cloud lewat OpenRouter (jauh lebih pintar). Tekan Enter untuk melewati."
    for attempt in 1 2 3; do
      key="$(ask_secret "    API key OpenRouter (tidak akan tampil): " | tr -d '[:space:]')"
      [ -n "$key" ] || break
      if details="$(openrouter_key_info "$key")"; then
        break
      fi
      warn "API key ditolak OpenRouter. Coba lagi, atau tekan Enter untuk melewati."
      key=""
    done
    if [ -z "$key" ]; then
      info "Dilewati — It's Me memakai otak lokal."
      return 0
    fi
  fi
  read -r tier limit remaining <<<"$details"
  set_env OPENROUTER_API_KEY "$key"
  set_env ITSME_BACKEND openrouter
  if [ -z "$(get_env ITSME_OPENROUTER_MODEL)" ]; then
    # Belum pernah isi saldo → model gratis; sudah → model berbayar yang murah & andal.
    if [ "$tier" = free ]; then model="google/gemma-4-31b-it:free"; else model="anthropic/claude-haiku-5.5"; fi
    set_env ITSME_OPENROUTER_MODEL "$model"
  fi
  info "Otak cloud aktif: $(get_env ITSME_OPENROUTER_MODEL) (otak lokal jadi cadangan)"
  if [ "$limit" = none ]; then
    warn "Key ini TANPA batas kredit. Demi keamanan, buat key dengan batas (mis. \$5) di openrouter.ai/settings/keys"
  else
    info "Batas key: sisa \$$remaining dari \$$limit"
  fi
}

# --- 4. paket Python ---------------------------------------------------------
make_venv() {
  local ver
  if [ ! -x .venv/bin/python ]; then
    if ! "$PY" -m venv .venv >/dev/null 2>&1; then
      rm -rf .venv
      command -v apt-get >/dev/null 2>&1 || die "Gagal membuat virtualenv dengan $PY."
      ver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
      info "Memasang python$ver-venv..."
      sudo apt-get install -y "python$ver-venv" || sudo apt-get install -y python3-venv
      "$PY" -m venv .venv || die "Gagal membuat virtualenv."
    fi
  fi
  VPY="$DIR/.venv/bin/python"
  info "Memasang paket (sebentar)..."
  "$VPY" -m pip install --quiet --disable-pip-version-check -r requirements.txt \
    || die "Gagal memasang paket Python. Cek koneksi internet lalu jalankan lagi."
}

# --- 5. Ollama ---------------------------------------------------------------
ollama_up() { curl -fsS --max-time 3 "$OLLAMA_URL/api/version" >/dev/null 2>&1; }

wait_ollama() {
  local _
  for _ in $(seq 1 "$1"); do
    ollama_up && return 0
    sleep 1
  done
  return 1
}

find_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
  elif [ -x /Applications/Ollama.app/Contents/Resources/ollama ]; then
    # macOS: perintah "ollama" baru masuk PATH setelah aplikasinya dibuka sekali.
    echo /Applications/Ollama.app/Contents/Resources/ollama
  else
    return 1
  fi
}

# Dengan otak cloud (CLOUD=1), otak lokal hanya cadangan: masalahnya cukup
# diperingatkan, tidak menghentikan setup.
ollama_fail() {
  if [ "${CLOUD:-0}" = 1 ]; then
    warn "Otak lokal (cadangan) belum siap: $*"
    return 1
  fi
  die "$*"
}

ensure_ollama() {
  if ! OLLAMA_BIN="$(find_ollama)"; then
    info "Memasang Ollama dengan pemasang resmi dari ollama.com (mungkin diminta password)..."
    if ! curl -fsSL https://ollama.com/install.sh | sh; then
      ollama_fail "Gagal memasang Ollama. Pasang manual dari https://ollama.com/download lalu jalankan skrip ini lagi."
      return 1
    fi
    if ! OLLAMA_BIN="$(find_ollama)"; then
      ollama_fail "Ollama belum terpasang."
      return 1
    fi
  fi
  info "$("$OLLAMA_BIN" --version 2>/dev/null | head -n 1)"
  ollama_up && return
  if [ "$(uname -s)" = Darwin ]; then
    open -a Ollama >/dev/null 2>&1 || true
  elif command -v systemctl >/dev/null 2>&1 && systemctl cat ollama.service >/dev/null 2>&1; then
    sudo systemctl enable --now ollama.service >/dev/null 2>&1 || true
  fi
  wait_ollama 20 && return
  info "Menyalakan Ollama di latar belakang..."
  nohup "$OLLAMA_BIN" serve >"$DIR/ollama.log" 2>&1 &
  if ! wait_ollama 30; then
    ollama_fail "Ollama tidak mau menyala. Lihat $DIR/ollama.log"
    return 1
  fi
}

# pull_model PERCOBAAN → 0 kalau model siap. Kalau gagal karena sebab yang
# jelas, alasannya disimpan di MODEL_HINT.
pull_model() {
  local attempt code=0
  MODEL_HINT=""
  if "$OLLAMA_BIN" list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -qx "$MODEL"; then
    info "Model $MODEL sudah ada."
    return 0
  fi
  info "Mengunduh $MODEL (beberapa GB, hanya sekali — silakan ditinggal)..."
  # Pengunduh It's Me memeriksa setiap potongan & sha256 dan bisa melanjutkan
  # unduhan yang terputus. Pengunduh bawaan Ollama kadang merusak unduhan
  # besar ("digest mismatch"); dengan berkas yang sudah lengkap, ollama pull
  # di bawah hanya mencatat modelnya dalam beberapa detik.
  "$VPY" -m itsme.fetch_model "$MODEL" || code=$?
  if [ "$code" = 3 ]; then
    # Model ini butuh Ollama yang lebih baru: perbarui, lalu coba lagi.
    info "Memperbarui Ollama ke versi terbaru (mungkin diminta password)..."
    code=3
    if curl -fsSL https://ollama.com/install.sh | sh && wait_ollama 60; then
      code=0
      "$VPY" -m itsme.fetch_model "$MODEL" || code=$?
    fi
    if [ "$code" = 3 ]; then
      MODEL_HINT="Ollama perlu diperbarui untuk model ini. Pasang versi terbaru dari https://ollama.com/download lalu jalankan setup ini lagi."
      return 1
    fi
  fi
  if [ "$code" = 4 ]; then
    MODEL_HINT="Ruang disk tidak cukup untuk model (lihat pesan di atas). Kosongkan ruang lalu jalankan setup ini lagi."
    return 1
  fi
  # Kode 0: berkas lengkap, ollama pull hanya mencatat. Kode lain: ollama pull biasa.
  for attempt in $(seq 1 "$1"); do
    if [ "$attempt" != 1 ]; then
      warn "Unduhan gagal atau rusak di perjalanan (biasanya koneksi tidak stabil). Mencoba lagi ($attempt/$1)..."
    fi
    "$OLLAMA_BIN" pull "$MODEL" && return 0
  done
  return 1
}

# --- 6. menyalakan -------------------------------------------------------------
xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

start_background() {  # tanpa layanan sistem: jalan sekarang, tidak otomatis saat boot
  if [ -f itsme.pid ] && kill -0 "$(cat itsme.pid)" 2>/dev/null; then
    kill "$(cat itsme.pid)" 2>/dev/null || true
    sleep 1
  fi
  PYTHONUNBUFFERED=1 nohup "$VPY" -m itsme.main >>"$DIR/itsme.log" 2>&1 &
  echo $! >itsme.pid
  STOP_HINT="kill \$(cat \"$DIR/itsme.pid\")"
  LOG_HINT="tail -f \"$DIR/itsme.log\""
}

start_bot() {
  local unit plist
  if [ "${ITSME_AUTOSTART:-1}" = 0 ]; then
    start_background
    return
  fi
  case "$(uname -s)" in
    Darwin)
      plist="$HOME/Library/LaunchAgents/com.itsme.bot.plist"
      mkdir -p "$(dirname "$plist")"
      cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.itsme.bot</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$VPY")</string><string>-m</string><string>itsme.main</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml_escape "$DIR")</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$(xml_escape "$DIR/itsme.log")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$DIR/itsme.log")</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF
      launchctl unload "$plist" >/dev/null 2>&1 || true
      launchctl load -w "$plist"
      STOP_HINT="launchctl unload -w \"$plist\""
      LOG_HINT="tail -f \"$DIR/itsme.log\"" ;;
    *)
      if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
        unit="$HOME/.config/systemd/user/itsme.service"
        mkdir -p "$(dirname "$unit")"
        cat >"$unit" <<EOF
[Unit]
Description=It's Me - asisten pribadi di Telegram
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart="$VPY" -m itsme.main
Restart=always
RestartSec=10
# Exit code 3 = It's Me lain sudah jalan; jangan dinyalakan ulang terus.
RestartPreventExitStatus=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable itsme.service >/dev/null 2>&1
        systemctl --user restart itsme.service
        # Supaya tetap jalan walau kamu belum login setelah komputer menyala.
        loginctl enable-linger "$(id -un)" >/dev/null 2>&1 \
          || sudo loginctl enable-linger "$(id -un)" >/dev/null 2>&1 \
          || warn "Tidak bisa mengaktifkan linger; It's Me baru menyala setelah kamu login."
        STOP_HINT="systemctl --user disable --now itsme"
        LOG_HINT="journalctl --user -u itsme -f"
      else
        warn "systemd tidak tersedia: It's Me dinyalakan sekarang, tapi tidak otomatis saat komputer menyala."
        start_background
      fi ;;
  esac
}

main() {
  case "$(uname -s)" in
    Linux|Darwin) ;;
    *) die "Sistem $(uname -s) belum didukung skrip ini. Untuk Windows pakai setup.ps1." ;;
  esac
  command -v curl >/dev/null 2>&1 || die "Butuh 'curl'. Pasang dulu lalu jalankan lagi."

  step "1/7 Menyiapkan berkas It's Me"
  locate_source
  install_files

  step "2/7 Memeriksa Python"
  ensure_python

  step "3/7 Bot Telegram & otak cloud"
  configure_telegram
  configure_openrouter

  step "4/7 Memasang paket Python"
  make_venv

  # Model pilihan lewat variabel lingkungan disimpan ke .env, supaya It's Me
  # yang menyala sendiri nanti memakai model yang sama.
  if [ -n "${ITSME_OLLAMA_MODEL:-}" ]; then set_env ITSME_OLLAMA_MODEL "$ITSME_OLLAMA_MODEL"; fi

  # Dengan otak cloud, otak lokal hanya cadangan: kegagalannya tidak boleh
  # menghalangi It's Me menyala.
  CLOUD=0
  if [ "$(get_env ITSME_BACKEND)" = openrouter ]; then CLOUD=1; fi
  MODEL="$("$VPY" -c 'from itsme import config; print(config.OLLAMA_MODEL)')"

  step "5/7 Memeriksa Ollama (otak lokal)"
  OLLAMA_READY=1
  ensure_ollama || OLLAMA_READY=0

  step "6/7 Model AI"
  if [ "$CLOUD" = 1 ]; then
    info "Otak cloud aktif — model lokal (cadangan) diunduh setelah It's Me menyala."
  elif ! pull_model 3; then
    [ -z "$MODEL_HINT" ] || die "$MODEL_HINT"
    die "Model $MODEL gagal diunduh 3 kali — biasanya karena koneksi internet tidak stabil. Jalankan setup ini lagi nanti (bagian yang sudah beres tidak diulang), coba jaringan lain, atau isi API key OpenRouter saat ditanya supaya It's Me bisa memakai otak cloud."
  fi

  step "7/7 Pemeriksaan akhir & menyalakan It's Me"
  "$VPY" -m itsme.doctor || die "Pemeriksaan gagal — lihat pesan di atas, betulkan, lalu jalankan skrip ini lagi."
  start_bot

  printf '\n%sIt'"'"'s Me sudah menyala!%s\n' "$C_OK" "$C_END"
  info "Buka Telegram di HP, chat @$BOT_USERNAME, lalu coba kirim: halo"
  info "Ganti otak     : kirim /cloud atau /lokal ke bot (/otak untuk melihat)"
  info "Lihat log      : $LOG_HINT"
  info "Hentikan       : $STOP_HINT"
  info "Perbarui       : jalankan perintah setup yang sama lagi"

  if [ "$CLOUD" = 1 ] && [ "$OLLAMA_READY" = 1 ]; then
    step "Tambahan: model cadangan untuk otak lokal"
    info "It's Me sudah bisa dipakai sekarang; unduhan ini berjalan sambil menunggu."
    if pull_model 2; then
      info "Model cadangan siap. Kalau otak cloud bermasalah, otak lokal otomatis menjawab."
    else
      warn "Model cadangan $MODEL belum berhasil diunduh. It's Me tetap jalan dengan otak cloud; coba lagi nanti dengan menjalankan setup ini lagi."
    fi
  fi
}

[ "${ITSME_SETUP_NO_RUN:-}" = 1 ] || main "$@"
