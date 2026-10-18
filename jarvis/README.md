# It's Me 🫡

Asisten pribadi bergaya **JARVIS** (Iron Man) yang kamu kendalikan lewat
**Telegram di HP**. Otaknya berjalan di **komputermu sendiri** memakai model
AI lokal lewat [Ollama](https://ollama.com) — gratis, tanpa API key, dan
obrolanmu tidak dikirim ke penyedia AI mana pun.

Mau otak yang jauh lebih pintar? Satu pengaturan mengganti otaknya ke
**Claude** (lihat [Memakai otak Claude](#memakai-otak-claude-opsional)).

## Cara kerjanya

```
HP (Telegram)  ⇄  bot It's Me di komputermu  ⇄  Ollama (model lokal)
                         │
                         ├─ cari_web / baca_halaman  → internet
                         ├─ baca/tulis berkas, perintah, deploy Git
                         └─ memori & tugas latar
```

Komputer harus **menyala dan tersambung internet** selama kamu ingin memakai
bot dari HP.

## Yang bisa dilakukan

- **Ngobrol & tanya apa saja** dalam bahasa Indonesia.
- **Cari info terbaru di internet** (`cari_web`, lewat DuckDuckGo) dan **baca
  isi halaman** (`baca_halaman`).
- **Multitasking** — beberapa tugas bisa jalan paralel lewat tugas latar;
  kamu tetap bisa mengobrol sementara It's Me bekerja.
- **Edit & deploy website lewat Git** — It's Me mengubah kode lalu `git push`;
  kalau Hostinger diatur auto-deploy, website langsung terbarui.
- **Kompres & ekstrak berkas** — minta It's Me nge-zip berkas/folder (jadi satu
  `.zip`) atau mengekstrak arsip (`.zip`, `.tar`, `.tar.gz`, `.tgz`). Selalu
  minta persetujuanmu dulu, **juga di akses penuh**. Saat nge-zip, berkas
  rahasia (`.env`, kunci, login) otomatis dilewati dan dilaporkan; arsip
  berbahaya yang mencoba menulis di luar folder tujuan (zip-slip) ditolak.
- **Ingat** hal penting yang kamu beri tahu (memori jangka panjang).
- **Aman** — hanya kamu (pemilik) yang bisa memerintah.
  - Perintah yang hanya melihat keadaan (`git status`, `git diff`, `git log`,
    `ls`, `dir`, …) langsung jalan. Perintah lain, menulis berkas, deploy, dan
    menyimpan memori minta konfirmasi satu tombol — dan tombolnya menampilkan
    apa persisnya yang akan terjadi (isi berkas, daftar berkas yang di-push).
  - Berkas di luar folder website minta konfirmasi untuk dibaca; berkas
    rahasia (`.env`, kunci SSH, dll.) selalu ditolak. Token & API key milik
    It's Me tidak ikut terlihat oleh perintah yang dijalankan.
  - Alamat web dari hasil pencarian atau dari pesanmu sendiri langsung dibuka;
    alamat lain minta konfirmasi. `baca_halaman` menolak alamat lokal (router,
    `localhost`, dll.) supaya bot tidak bisa diarahkan mengintip jaringan rumahmu.
  - Deploy dibatalkan kalau ada berkas rahasia yang ikut berubah.
- **Tidak diam saja** — selama berpikir, status "mengetik…" terus tampil, dan
  kalau lama It's Me memberi tahu. Error apa pun dikirim ke Telegram dan
  dicatat di `data/itsme.log`.
- **Memperbarui diri sendiri** — setiap 2 menit It's Me memeriksa repo GitHub
  ini. Kalau ada commit baru di cabang `main`, pesan yang sedang dikerjakan
  diselesaikan dulu, kode baru dipasang (`.env` dan memori tetap), lalu It's Me
  menyala ulang sendiri dan mengabarimu di Telegram.
- Untuk sekarang hanya **pesan teks** yang dibaca (belum pesan suara/gambar).

## Cara paling gampang: satu perintah

Skrip setup memasang semuanya sendiri — Python, Ollama, model AI, paket —
menanyakan token bot (langsung dicek ke Telegram), **mendeteksi user id-mu
otomatis**, lalu menyalakan It's Me dan mengaturnya agar menyala sendiri
setiap komputer hidup. Kamu cukup menjawab 2–3 pertanyaan.

**Sebelum mulai:** di Telegram, chat [@BotFather](https://t.me/BotFather),
kirim `/newbot`, ikuti langkahnya, lalu siapkan token yang diberikan.

**Windows** — klik Start, ketik `PowerShell`, buka, lalu tempel:
```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.ps1 | iex
```

**macOS / Linux** — buka Terminal, lalu tempel:
```bash
curl -fsSL https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.sh | bash
```

It's Me dipasang di `%USERPROFILE%\ItsMe` (Windows) atau `~/itsme`
(macOS/Linux). Setelah itu It's Me **memperbarui dirinya sendiri** dari GitHub
dan menyala ulang otomatis. Menjalankan perintah yang sama lagi juga
memperbarui — `.env` dan memori tidak ditimpa. Pemasangan dari sebelum ada
pembaruan otomatis perlu menjalankan perintah setup itu sekali lagi.

Kalau It's Me jalan dari folder **clone Git** repo ini, pembaruan dipasang
dengan `git merge --ff-only`; kalau ada perubahan yang belum di-commit atau
commit lokal yang berbeda dari GitHub, pembaruan ditunda dan kamu diberi tahu
lewat Telegram supaya pekerjaanmu tidak tertimpa.

**Ada masalah?** Jalankan pemeriksa dari folder It's Me — ia memberi tahu
apa yang salah dan cara membetulkannya:
- Windows: `.venv\Scripts\python -m itsme.doctor`
- macOS/Linux: `.venv/bin/python -m itsme.doctor`

### Biar Claude di komputermu yang memasang

Kalau di komputer itu ada Claude Code / aplikasi Claude, kamu bisa
memintanya menjalankan skrip setup. Karena Claude tidak bisa menjawab
pertanyaan skrip untukmu, beri dia nilai-nilainya lewat variabel lingkungan:
`TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID` (dari
[@userinfobot](https://t.me/userinfobot)), dan di Windows `ITSME_NO_SLEEP`
(`1` = cegah komputer tidur, `0` = biarkan).

## Otak cloud lewat OpenRouter (opsional, jauh lebih pintar)

Model lokal di komputer 16 GB terbatas kepintarannya. Dengan API key
[OpenRouter](https://openrouter.ai), It's Me bisa memakai model cloud yang jauh
lebih pintar — dan **otak lokal tetap jadi cadangan otomatis** kalau saldo
habis, batas harian tercapai, atau internet putus. Kalau model gratisnya sedang
penuh, It's Me mencoba lagi sebentar dan pindah ke model gratis lain
(`openrouter/free`) sebelum menyerahkan ke otak lokal.

1. Di openrouter.ai → **Settings → API Keys → New Key**. **Isi batas kredit**
   (mis. $5) supaya aman kalau key-nya bocor. Jangan kirim key ke chat mana
   pun, kecuali lewat [`/kunci`](#mengirim-api-key-lewat-telegram-kunci) ke It's Me.
2. Kirim `/kunci <key>` ke bot, atau jalankan lagi perintah setup di atas dan
   tempel key-nya saat diminta (ketikannya tidak tampil di layar). Setup
   memilih model otomatis:
   - belum pernah isi saldo → `google/gemma-4-31b-it:free` (gratis)
   - sudah pernah isi saldo → `anthropic/claude-haiku-5.5`
3. Di Telegram: `/cloud` = otak cloud, `/lokal` = otak lokal, `/otak` = lihat
   yang aktif dan sisa kuota model gratis hari ini.

| Model | Harga per 1 juta token (masuk / keluar) | Catatan |
|---|---|---|
| `google/gemma-4-31b-it:free` | gratis | maks. 50 permintaan/hari (1000/hari kalau pernah isi saldo ≥ $10); di jam sibuk kadang penuh |
| `anthropic/claude-haiku-5.5` | $0,10 / $0,50 | murah (±$0,001 per pesan), pemakaian alat sangat andal |
| `deepseek/deepseek-v4-flash` | $0,09 / $0,18 | paling murah |
| `anthropic/claude-sonnet-5.5`, `openai/gpt-6-sol` | $2 / $10 | paling pintar, untuk tugas berat |

Ganti model lewat `ITSME_OPENROUTER_MODEL` di `.env` — harus model yang
mendukung *tools* (`python -m itsme.doctor` akan mengeceknya). Satu pesan bisa
memakai beberapa permintaan kalau It's Me memakai alat.

> **Menambah banyak API key tidak membuat It's Me lebih pintar.** Satu key
> sudah bisa memakai semua model, dan batas pemakaian OpenRouter dihitung per
> akun, bukan per key. Yang menentukan kepintaran adalah **model** yang dipilih.

> **Privasi:** dengan otak cloud, isi obrolan dikirim ke OpenRouter dan
> penyedia modelnya; sebagian penyedia model gratis mencatat data. Cek
> pengaturan privasi akun OpenRouter-mu, dan pakai `/lokal` untuk hal yang
> sangat pribadi.

## Cara manual

Kalau lebih suka memasang sendiri langkah demi langkah:

### Langkah 1 — Pasang Ollama dan unduh model

1. Pasang Ollama (butuh Windows 10 22H2+ / macOS 14+ / Linux):
   - **Windows (PowerShell):** `irm https://ollama.com/install.ps1 | iex`
   - **macOS / Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
   - atau unduh installer-nya dari <https://ollama.com/download>.
2. Unduh model (sekali saja, sekitar 6,6 GB):
   ```bash
   ollama pull qwen3.5:9b
   ```
   Kalau gagal dengan **digest mismatch**, pengunduh bawaan Ollama menulis
   potongan unduhan yang rusak. Pakai pengunduh It's Me yang memeriksa setiap
   potongan dan sha256-nya, lalu tarik lagi (kali ini hanya beberapa detik).
   Jalankan dari folder `jarvis/` dengan virtualenv dari Langkah 3 yang aktif:
   ```bash
   python -m itsme.fetch_model qwen3.5:9b
   ollama pull qwen3.5:9b
   ```
   Skrip setup sudah melakukan ini otomatis.
3. Coba: `ollama run qwen3.5:9b "Halo, apa kabar?"` — kalau dijawab, otaknya siap.

### Model untuk RAM 16 GB

| Model | Unduhan | Kapan dipakai |
|---|---|---|
| `qwen3.5:9b` (bawaan) | 6,6 GB | Paling pintar yang muat di 16 GB: skor multibahasa & pemanggilan alat tertinggi, ±8 GB RAM saat dipakai |
| `qwen3.5:4b` | 3,3 GB | Komputer terasa lambat — sekitar 2× lebih cepat |
| `qwen3:8b` | 5,2 GB | Alternatif yang paling teruji untuk pemanggilan alat |

Ganti model lewat `ITSME_OLLAMA_MODEL` di `.env` (unduh dulu dengan
`ollama pull <nama>`). Untuk melihat kecepatan di komputermu:
`ollama run qwen3.5:9b --verbose` → lihat `eval rate` (token/detik).
Pakai Ollama versi terbaru — versi lama kadang "menulis" panggilan alat
sebagai teks alih-alih menjalankannya.

### Langkah 2 — Siapkan bot Telegram

1. **Token bot** — chat [@BotFather](https://t.me/BotFather), kirim `/newbot`,
   salin token-nya.
2. **User id Telegram-mu** — chat [@userinfobot](https://t.me/userinfobot).
   Setelah bot jalan, kirim `/id` ke bot untuk memastikan angkanya sama.

### Langkah 3 — Jalankan It's Me di komputer

Butuh [Python](https://www.python.org/downloads/) 3.10 atau lebih baru.

**Windows (PowerShell):**
```powershell
cd jarvis
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env        # isi TELEGRAM_BOT_TOKEN dan OWNER_TELEGRAM_ID
python -m itsme.main
```

**macOS / Linux:**
```bash
cd jarvis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env           # isi TELEGRAM_BOT_TOKEN dan OWNER_TELEGRAM_ID
python -m itsme.main
```

Sebelum menyalakan, kamu bisa memeriksa semuanya dengan
`python -m itsme.doctor`. Kalau berhasil menyala, muncul tulisan
`It's Me jalan. Otak: lokal qwen3.5:9b ...`. Buka Telegram di HP, cari
bot-mu, kirim `/start`.

**Biar jalan terus:** biarkan jendela terminal itu tetap terbuka, dan matikan
mode *sleep* komputer (komputer boleh mematikan layar, asal tidak tidur).
Di Windows, It's Me yang dinyalakan manual seperti ini berhenti setelah
memasang pembaruan otomatis — jalankan `python -m itsme.main` lagi, atau pakai
skrip setup yang memasang penyala `start-itsme.cmd` (menyalakan ulang sendiri).

## Menghubungkan ke website Hostinger

1. Di hPanel Hostinger → menu **Git** → hubungkan repo GitHub website-mu dan
   aktifkan **auto-deploy**.
2. Clone repo website itu ke komputermu, lalu isi `WEBSITE_REPO_DIR` di `.env`
   dengan foldernya.
3. Tinggal bilang ke It's Me, misalnya: *"ganti judul halaman jadi X lalu
   deploy"*. Ia mengedit kode, minta konfirmasi, push, dan Hostinger menarik
   perubahannya.

## Perintah di Telegram

| Perintah | Fungsi |
|---|---|
| `/start`, `/help` | Bantuan |
| `/otak` | Lihat otak yang sedang dipakai |
| `/cloud` | Pakai otak cloud (OpenRouter) |
| `/claudepc` | Pakai Claude di komputer dengan akun Claude-mu, tanpa API key ([cara login](#memakai-akun-claude-mu-tanpa-api-key-claudepc)) |
| `/claude` | Pakai otak Claude (Anthropic API, butuh `ANTHROPIC_API_KEY` di `.env`) |
| `/lokal` | Pakai otak lokal (Ollama) |
| `/kunci <key>` | Simpan API key Claude (`sk-ant-…`), token akun Claude (`sk-ant-oat…`), atau key OpenRouter (`sk-or-…`); pesannya langsung dihapus |
| `/akses` | Lihat mode akses: 🔒 aman (bawaan) atau 🔓 penuh |
| `/akses_penuh` | Langsung bertindak tanpa tombol ([lihat di bawah](#akses-penuh-akses_penuh)) |
| `/akses_aman` | Kembali minta tombol "Ya" untuk aksi yang mengubah sesuatu |
| `/memori` | Lihat isi memori jangka panjang |
| `/hapus_memori` | Kosongkan memori (minta konfirmasi) |
| `/reset` | Mulai percakapan baru (memori tetap) |
| `/id` | Tampilkan user id Telegram-mu |

### Dashboard web

Selain Telegram, It's Me punya **halaman web** untuk melihat status dan
mengaturnya. Buka di komputer tempat It's Me jalan:

```
http://localhost:8750
```

Di sana kamu bisa: **ngobrol langsung dengan It's Me** (berbagi konteks dengan
Telegram), lihat otak yang aktif dan ganti otak (lokal/cloud/Claude/Claude di
komputer), lihat & ubah mode akses, lihat & edit memori, lihat log terbaru,
serta status ringkas (lama nyala, Ollama hidup/mati, sisa kuota cloud).

Ada juga akses ke komputermu:
- **Berkas komputer** — jelajah folder, lihat isi berkas teks, buka berkas/
  folder di aplikasi bawaannya, dan zip/ekstrak lewat tombol.
- **Perintah** — jalankan perintah apa pun (mis. buka aplikasi dengan
  `Start-Process ...`).

Perubahan langsung berlaku pada It's Me yang sedang jalan. Aturan keamanan tetap
sama: saat mode akses "aman", perintah yang mengubah sesuatu, buka aplikasi,
dan zip/ekstrak minta persetujuan lewat tombol di Telegram; zip/ekstrak selalu
minta persetujuan walau akses penuh; dan **berkas rahasia** (`.env`, kunci,
login aplikasi & browser) selalu dijaga — ditandai 🔒, tidak bisa dibuka isinya,
dan dilewati saat nge-zip. Karena dashboard bisa mengendalikan komputer,
biarkan bawaannya hanya di `127.0.0.1`, pakai token kalau diakses dari HP, dan
aktifkan verifikasi dua langkah Telegram.

Bawaannya dashboard **hanya bisa dibuka dari komputer itu sendiri**
(`127.0.0.1`). Mau membukanya dari HP di WiFi yang sama? Set
`ITSME_DASHBOARD_HOST=0.0.0.0` di `.env`, lalu buka
`http://ALAMAT-IP-PC:8750/?token=ISI_TOKEN` dari HP. Tokennya dibuat otomatis,
tersimpan di `data/dashboard_token.txt`, dan ditampilkan di log It's Me saat
menyala. Matikan dashboard dengan `ITSME_DASHBOARD=0`.

### Akses penuh (`/akses_penuh`)

Bawaannya It's Me memakai **akses aman**: aksi yang mengubah sesuatu, membaca
berkas di luar folder website, dan membuka alamat web baru menunggu tombol "Ya".
Kirim `/akses_penuh` (lalu tekan "Ya") supaya It's Me **langsung bertindak**:
membaca berkas di mana saja, menjalankan perintah, menulis berkas, deploy, dan
menyimpan memori tanpa tombol. Setiap aksi yang mengubah sesuatu tetap
dikabarkan di chat. Mode ini tersimpan, jadi tetap berlaku setelah It's Me
menyala ulang; matikan kapan saja dengan `/akses_aman`.

Yang **tetap dijaga** di akses penuh: berkas rahasia (password, token, kunci,
`.env`, login & cookie browser, serta login aplikasi Claude di `~/.claude` dan
folder data aplikasi Claude) selalu ditolak; perintah yang menyebut berkas
seperti itu tetap ditanyakan; dan pencarian isi berkas di seluruh folder home
atau AppData ditolak karena bisa ikut menyedot data login aplikasi.

Risikonya: siapa pun yang bisa membuka Telegram-mu bisa mengendalikan komputer
ini — aktifkan verifikasi dua langkah Telegram — dan perintah tersembunyi di
halaman web bisa ikut dijalankan tanpa kamu sempat menolak.

Otak `/claudepc` juga diberi tahu letak pekerjaanmu: isi percakapan Claude Code
di `~/.claude/projects`, daftar sesi aplikasi Claude desktop (judul, ringkasan,
folder proyek), dan sesi yang terakhir kamu pakai. Jadi pertanyaan seperti
"lanjutkan pekerjaan website toko kemarin" langsung dicari di tempat yang benar.
Obrolan di claude.ai (web/HP) tersimpan di akun, bukan di komputer, jadi tidak
bisa dibaca dari sini.

### Mengirim API key lewat Telegram (`/kunci`)

Kirim `/kunci sk-ant-…` (Claude) atau `/kunci sk-or-…` (OpenRouter) ke bot.
It's Me langsung **menghapus pesanmu** dari chat (di semua perangkat),
memeriksa key ke penyedianya tanpa biaya, menyimpannya ke `.env`, dan
memakainya tanpa perlu dinyalakan ulang. Balasannya hanya menampilkan awalan
dan 4 huruf terakhir (`sk-ant-…a1b2`). Key tidak pernah masuk riwayat obrolan,
tidak dibaca otak AI, dan tidak dicatat di log. Key yang ditolak tidak disimpan.

Yang tetap perlu diingat: chat bot Telegram tidak terenkripsi ujung-ke-ujung,
jadi key sempat lewat server Telegram sebelum dihapus, dan key terlihat sesaat
di layarmu saat ditempel. Paling aman tetap mengisi `.env` langsung di
komputer; kalau memakai `/kunci`, buat key khusus untuk It's Me dengan batas
pengeluaran, supaya bisa dicabut kapan saja.

## Pengaturan (opsional, di `.env`)

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `ITSME_BACKEND` | `ollama` | Otak awal: `ollama` (lokal), `openrouter` (cloud), atau `claude` |
| `OPENROUTER_API_KEY` | — | Key otak cloud OpenRouter |
| `ITSME_OPENROUTER_MODEL` | `google/gemma-4-31b-it:free` | Model cloud (harus mendukung tools) |
| `ITSME_OPENROUTER_FALLBACKS` | `openrouter/free` (untuk model `:free`) | Model cadangan kalau model utama penuh; `off` = matikan |
| `ITSME_OPENROUTER_REASONING` | `medium` | Seberapa keras model cloud berpikir (`none` … `max`, atau `default`) |
| `ITSME_FALLBACK_LOCAL` | `1` | Otak lokal menjawab kalau otak cloud gagal (`0` = matikan) |
| `ITSME_OLLAMA_MODEL` | `qwen3.5:9b` | Model lokal yang dipakai |
| `ITSME_OLLAMA_NUM_CTX` | `16384` | Panjang konteks; turunkan kalau RAM penuh |
| `ITSME_OLLAMA_NUM_GPU` | otomatis | Lapisan model di GPU; `0` = CPU saja (kalau driver GPU bermasalah) |
| `ITSME_OLLAMA_KEEP_ALIVE` | `30m` | Lama model tetap di RAM (`-1` = selamanya) |
| `ITSME_OLLAMA_THINK` | `false` | `true` = mode berpikir (lebih teliti, jauh lebih lambat); `default` = ikuti model |
| `ITSME_OLLAMA_TIMEOUT` | `600` | Batas waktu satu jawaban (detik) |
| `ITSME_SEARCH_REGION` | `id-id` | Wilayah pencarian web (`id-id`, `us-en`, …) |
| `OLLAMA_URL` | `http://localhost:11434` | Alamat Ollama |
| `WEBSITE_REPO_DIR` | — | Folder repo website untuk fitur deploy |
| `ITSME_AUTO_UPDATE` | `1` | Pasang commit baru dari GitHub & menyala ulang sendiri (`0` = matikan) |
| `ITSME_UPDATE_INTERVAL` | `120` | Jeda antar pemeriksaan GitHub (detik, minimal 60) |
| `ITSME_UPDATE_REPO` | `OBKENTUNG01/website` | Repo GitHub yang diikuti |
| `ITSME_UPDATE_BRANCH` | `main` | Cabang yang diikuti |

## Google Drive (opsional)

It's Me bisa mengakses Google Drive-mu — **cari, unggah, unduh, dan kelola**
berkas/folder — lewat chat (otak lokal/cloud/Claude API) maupun **panel Google
Drive di dashboard** (berfungsi untuk otak apa pun, termasuk Claude di komputer).

**Hubungkan sekali, di komputer:**
1. Buka [console.cloud.google.com](https://console.cloud.google.com/), buat/pilih
   sebuah project, lalu di **APIs & Services → Library** aktifkan **Google Drive API**.
2. Di **OAuth consent screen**: pilih *External*, isi nama aplikasi, dan tambahkan
   email Google-mu sebagai **Test user**.
3. Di **Credentials → Create credentials → OAuth client ID**, pilih jenis
   **Desktop app**, lalu unduh berkas JSON-nya.
4. Simpan berkas JSON itu sebagai `data\drive_credentials.json` di folder It's Me.
5. Jalankan:
   ```powershell
   cd $HOME\ItsMe; .venv\Scripts\python -m itsme.drive_login
   ```
   Browser terbuka untuk login Google (pilih akun, izinkan akses Drive). Token
   disimpan ke `data\drive_token.json`. Paket Google dipasang otomatis saat ini.

Setelah itu, dari HP tinggal minta It's Me (mis. *"cari laporan di Google
Drive"*, *"unggah C:\…\laporan.pdf ke Drive"*, *"buat folder Arsip di Drive"*),
atau pakai panel **Google Drive** di dashboard untuk menjelajah, mengunggah,
mengunduh, dan mengelola.

Aturan keamanan sama seperti fitur lain: **unggah, unduh, buat folder, ganti
nama, pindah** minta persetujuan (tombol Telegram saat akses aman); **hapus**
selalu minta konfirmasi dan hanya membuang ke **sampah** Drive (bisa
dipulihkan); berkas rahasia tidak ikut diunggah; dan `drive_token.json` /
`drive_credentials.json` diperlakukan sebagai berkas rahasia.

## Memakai akun Claude-mu tanpa API key (`/claudepc`)

Punya langganan Claude (Pro/Max)? It's Me bisa memakai **Claude Code di
komputermu dengan akun itu** — tanpa API key; pemakaiannya dihitung dari kuota
paketmu. Claude Code bisa menjalankan perintah, membaca & mengubah berkas, dan
mencari di internet, dengan aturan yang sama seperti otak lain: aksi yang
mengubah sesuatu minta tombol "Ya" di Telegram, berkas rahasia selalu ditolak.

Login aplikasi Claude desktop terkunci untuk aplikasi itu dan tidak bisa
dipakai program lain — karena itu It's Me butuh token akunnya sendiri. **Sekali
saja, di komputer**, buka PowerShell lalu jalankan:

```powershell
cd $HOME\ItsMe; .venv\Scripts\python -m itsme.claude_login
```

(macOS/Linux: `cd ~/itsme && .venv/bin/python -m itsme.claude_login`)

Skrip itu memasang Claude Code (sekali, ±250 MB), membuka browser untuk login
dengan akun Claude-mu, lalu meminta token yang muncul (`sk-ant-oat…`, berlaku
±1 tahun) dan menyimpannya ke `.env`. Setelah itu kirim `/claudepc` ke bot dari
HP — tidak perlu menyalakan ulang It's Me, dan tidak ada lagi link login di HP.
Tokennya juga bisa dikirim lewat `/kunci` kalau kamu membuatnya di tempat lain.

Model bawaan akunmu dipakai; ganti lewat `ITSME_CLAUDE_CODE_MODEL` (`sonnet`,
`opus`, `haiku`). Kalau `ANTHROPIC_API_KEY` juga ada di `.env`, `/claudepc`
tetap memakai akunmu (API key hanya untuk `/claude`).

## Memakai otak Claude (opsional)

Claude jauh lebih pintar untuk tugas bertahap seperti mengedit kode atau riset,
tapi butuh API key berbayar.

1. Buat API key di <https://platform.claude.com>. Pelanggan **Max** dan
   **Team** mendapat kredit API bulanan gratis: di claude.ai buka **Settings →
   Billing → API credits**, lalu hubungkan organisasi Console-mu.
2. Kirim [`/kunci <key>`](#mengirim-api-key-lewat-telegram-kunci) ke bot, atau
   isi `ANTHROPIC_API_KEY` di `.env` di komputer lalu nyalakan ulang It's Me.
   Kirim `/claude` ke bot untuk beralih ke otak Claude (dan `/lokal` atau
   `/cloud` untuk kembali), atau isi juga `ITSME_BACKEND=claude` di `.env`
   supaya Claude langsung dipakai setiap It's Me menyala.
3. Model bawaan `claude-opus-5-5` dengan effort `medium` (atur lewat `ITSME_MODEL`
   dan `ITSME_EFFORT`). `high`/`max` lebih teliti untuk tugas berat, tapi setiap
   pesan — bahkan "halo" — jadi jauh lebih mahal.

## Catatan jujur

- **Model lokal jauh kurang pintar dari Claude.** Untuk obrolan, mencari info,
  dan tugas sederhana sudah cukup; untuk mengedit kode yang rumit, sering salah
  paham. Selalu baca dulu apa yang akan dijalankan sebelum menekan "Ya".
- **Kecepatan tergantung komputermu.** Tanpa kartu grafis yang didukung, satu
  jawaban bisa butuh beberapa menit. Contoh terukur: di Core i7-3770 (2012)
  dengan `qwen3.5:9b` di CPU, jawaban "halo" pertama butuh ±3 menit (membaca
  prompt ±2,5 menit, menulis ±4 token/detik). Di komputer seperti itu pakai
  `qwen3.5:4b`, atau jadikan otak cloud sebagai otak utama.
- **Kalau Ollama berhenti mendadak saat menjalankan model** (biasanya karena
  driver atau memori GPU — mis. kartu grafis lama lewat Vulkan: `device lost`),
  It's Me otomatis mencoba lagi dengan pengaturan yang lebih aman. Kalau log
  Ollama menunjukkan GPU-nya yang mati, It's Me langsung memakai CPU saja.
  Pengaturan yang berhasil **diingat** di `data/ollama_state.json`, jadi tidak
  crash lagi setiap kali dinyalakan ulang; hapus berkas itu setelah memperbarui
  driver GPU untuk mencoba GPU lagi. Penyebab crash tercatat di
  `%LOCALAPPDATA%\Ollama\server.log` (Windows).
- **Bot terasa macet?** Lihat `data/itsme.log` di folder It's Me — semua error
  tercatat di sana. It's Me hanya boleh jalan satu kali sekaligus; menyalakan
  yang kedua akan ditolak (dua bot dengan token sama saling berebut pesan).
- **Model lokal lebih mudah tertipu** isi halaman web yang berisi perintah
  tersembunyi. Karena itu tombol konfirmasi tetap aktif — termasuk untuk
  membaca berkas di luar folder website, membuka alamat web baru, dan
  menyimpan memori. Baca isi tombol sebelum menekan "Ya".
- **Menjalankan di HP (Termux)** hanya cocok untuk otak Claude; model lokal
  butuh komputer.
- **Keamanan akun:** untuk layanan seperti Google, pakai login resmi (OAuth)
  atau sesi browser yang sudah login — jangan menaruh username & password di
  dalam berkas.
