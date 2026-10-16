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
(macOS/Linux). Jalankan perintah yang sama lagi kapan saja untuk
**memperbarui** — `.env` dan memori tidak ditimpa.

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
   (mis. $5) supaya aman kalau key-nya bocor. Jangan kirim key ke chat mana pun.
2. Jalankan lagi perintah setup di atas. Saat diminta, tempel key-nya
   (ketikannya tidak tampil di layar). Setup memilih model otomatis:
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
| `/lokal` | Pakai otak lokal (Ollama) |
| `/memori` | Lihat isi memori jangka panjang |
| `/hapus_memori` | Kosongkan memori (minta konfirmasi) |
| `/reset` | Mulai percakapan baru (memori tetap) |
| `/id` | Tampilkan user id Telegram-mu |

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

## Memakai otak Claude (opsional)

Claude jauh lebih pintar untuk tugas bertahap seperti mengedit kode atau riset,
tapi butuh API key berbayar.

1. Buat API key di <https://platform.claude.com>. Pelanggan **Max** dan
   **Team** mendapat kredit API bulanan gratis: di claude.ai buka **Settings →
   Billing → API credits**, lalu hubungkan organisasi Console-mu.
2. Di `.env`: `ITSME_BACKEND=claude` dan isi `ANTHROPIC_API_KEY`.
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
