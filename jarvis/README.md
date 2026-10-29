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
skrip setup (`setup.ps1`) yang memasang penyala **senyap**: `ItsMe.vbs`
menjalankan `run-itsme.ps1` tersembunyi (tanpa jendela/taskbar) dan menyalakan
ulang sendiri. Hentikan kapan saja dengan `stop-itsme.cmd`. It's Me tetap proses
`python.exe` biasa yang bisa kamu lihat & hentikan di Task Manager — tidak
disembunyikan dari pemilik atau antivirus.

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
| `/kunci <key>` | Simpan API key Claude (`sk-ant-…`), token akun Claude (`sk-ant-oat…`), key OpenRouter (`sk-or-…`), atau API key Tailscale (`tskey-api-…`, untuk `/pasang` tanpa login); pesannya langsung dihapus |
| `/akses` | Lihat mode akses: 🔒 aman (bawaan) atau 🔓 penuh |
| `/akses_penuh` | Langsung bertindak tanpa tombol ([lihat di bawah](#akses-penuh-akses_penuh)) |
| `/akses_aman` | Kembali minta tombol "Ya" untuk aksi yang mengubah sesuatu |
| `/memori` | Lihat isi memori jangka panjang |
| `/hapus_memori` | Kosongkan memori (minta konfirmasi) |
| `/reset` | Mulai percakapan baru (memori tetap) |
| `/perangkat` | Daftar komputer lain yang terhubung ([lihat di bawah](#banyak-komputer-satu-bot)) |
| `/pakai <nama>` | Pesan berikutnya dikerjakan komputer itu; `/pakai` saja = kembali ke komputer utama |
| `/pasang` | Satu baris PowerShell untuk memasang It's Me + node di komputer lain tanpa mengetik token (lihat "Tanpa komputer inti" di bawah) |
| `/komputer` | Daftar komputer terhubung + cara buka layar & berkasnya (butuh It's Me Node) |
| `/layar <nama>` | Kirim foto layar komputer itu ke chat (mis. `/layar Laptop`) |
| `/nyalakan <nama>` | Coba bangunkan komputer itu dari jarak jauh (Wake-on-LAN) — lihat `PANDUAN_AKSES_JARAK_JAUH.md` |
| `/diagnosa` | Laporan kesehatan **komputer yang membalas ini**: nama komputer, versi, mode Telegram (webhook/polling), komputer lain yang terlihat, status Node.js & `npm install` untuk WhatsApp, dan peringatan terakhir di log ([lihat di bawah](#kalau-jawabannya-terasa-janggal-diagnosa)) |
| `/lock` | Kunci bot; kata sandi diminta lagi sebelum bisa dipakai (kalau diatur) |
| `/id` | Tampilkan user id Telegram-mu |

### Banyak komputer, satu bot

It's Me bisa dipasang di beberapa komputer dan semuanya dikendalikan dari
**bot Telegram yang sama** — tidak perlu bikin bot baru.

- **Hub** = komputer utama, memakai token Telegram seperti biasa.
- **Perangkat** = komputer lain. Ia tidak memakai token; ia menghubungi
  dashboard Hub dan menunggu perintah dari sana.

Cara menambah perangkat:

1. Di Hub, isi `ITSME_HUB_SECRET` (kata sandi acak yang panjang) di `.env`,
   dan buat dashboard-nya bisa dijangkau komputer lain (mis. lewat Tailscale,
   lihat `PANDUAN_AKSES_JARAK_JAUH.md`, dengan `ITSME_DASHBOARD_HOST=0.0.0.0`).
   Nyalakan ulang It's Me.
2. Di komputer lain, pasang It's Me seperti biasa, lalu isi `.env`-nya:
   `ITSME_HUB_URL=http://<alamat-hub>:8750`, `ITSME_HUB_SECRET=` (sama persis),
   dan boleh `ITSME_DEVICE_NAME=Laptop`. Token Telegram tidak perlu diisi.
3. Saat perangkat menyala, bot mengabari: "🔌 Perangkat baru online".

Di Telegram, kirim `/pakai Laptop`; pesan berikutnya dikerjakan laptop itu dan
jawabannya muncul di chat yang sama dengan awalan 🖥️ Laptop. Tombol Ya/Batal
untuk aksi berisiko juga muncul di Telegram. Kirim `/pakai` untuk kembali.

Supaya cara kerjanya sama persis dengan komputer utama, perangkat otomatis
menyalin **memori**, **otak yang dipilih**, dan **mode akses** dari Hub (catatan
memori yang dibuat di perangkat juga tersimpan di Hub). Yang tetap diurus per
komputer: login/API key otak (mis. `/claudepc` perlu login Claude di komputer
itu juga) dan Ollama kalau memakai otak lokal. Berkas & foto belum bisa
diteruskan ke perangkat — kirim teks.

### Tanpa komputer inti: bot pindah sendiri ([It's Me Node](https://github.com/OBKENTUNG01/itsme-node))

Dengan Hub, bot mati kalau komputer Hub mati. Dengan **It's Me Node**, tidak ada
komputer inti: semua komputer setara, satu dipilih menjadi **pemimpin**, dan bot
Telegram hanya aktif di pemimpin. Kalau pemimpin mati, komputer lain dipilih
dalam ±20 detik dan bot langsung aktif di sana.

1. Di komputer yang sudah menjalankan It's Me, pasang node sekali:
   ```powershell
   irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
   ```
2. Untuk setiap komputer lain, kirim **`/pasang`** ke bot. Bot membalas satu
   baris PowerShell berisi kode undangan sekali pakai (hangus dalam 30 menit).
   Tempel di PowerShell komputer itu dan klik **Ya** untuk izin Administrator.
   Node dan It's Me terpasang sekaligus; token bot, pemilik, dan kunci otak
   diambil sendiri dari komputer ini lewat Tailscale, jadi tidak ada yang
   diketik. Dengan `/kunci tskey-api-…` (API key Tailscale), login Tailscale
   di komputer baru pun tidak perlu.
3. Selesai. It's Me di komputer yang bukan pemimpin **siaga** (tidak mengambil
   pesan Telegram), dan node menyalin **memori**, **otak yang dipilih**, dan
   **mode akses** dari pemimpin tiap 20 detik. Saat bot pindah, kamu dikabari
   "🔁 It's Me sekarang aktif di komputer …".

Yang tetap diurus per komputer: login otak (mis. `/claudepc` perlu
`itsme.claude_login` di komputer itu) dan Ollama kalau memakai otak lokal.
Riwayat obrolan mulai baru setelah bot pindah; memori tetap. Tanpa node,
It's Me jalan seperti biasa. Matikan perilaku ini dengan `ITSME_FOLLOW_NODE=0`.

Komputer yang sudah punya kode gabung yang sama **saling menemukan sendiri** di LAN yang
sama (node menyapu jaringannya; tidak perlu diundang ulang), dan **Hub** (komputer pertama)
selalu diutamakan sebagai pemimpin — rincian di
[README It's Me Node](https://github.com/OBKENTUNG01/itsme-node#saling-menemukan--keamanan-antar-komputer).
Jadi pada keadaan normal hanya Hub yang memegang bot Telegram/WhatsApp; komputer lain siaga.

### Kalau jawabannya terasa janggal: `/diagnosa`

Kalau bot memakai token yang sama di beberapa komputer, jawabannya datang dari komputer
mana pun yang sedang memegang bot — belum tentu komputer yang kamu kira. Kirim
**`/diagnosa`** ke bot: balasannya selalu menyebut **nama komputer yang membalas**,
versi It's Me di sana, mode Telegram (webhook atau polling; di mode webhook juga alamat
tunnel yang terdaftar di Telegram dan jumlah pesan yang antre — rahasia webhook tidak
ditampilkan), berapa komputer yang terlihat dan siapa pemimpinnya, apakah Node.js ada dan
`npm install` WhatsApp sudah dijalankan, serta peringatan/galat terakhir di log. Hanya
untuk pemilik (juga jalan lewat WhatsApp).

### Lihat & kendalikan layar, telusuri berkas komputer lain

Kalau It's Me Node terpasang, kamu bisa **melihat & mengendalikan layar** dan
**menelusuri berkas** semua komputer yang terhubung — seperti remote desktop,
tanpa aplikasi tambahan, dan tetap jalan walau komputernya di jaringan/lokasi lain
(lewat Tailscale).

- **Dari dashboard (satu tempat):** buka dashboard It's Me (`http://localhost:8750`),
  lalu tab **🖥️ Komputer**. Pilih komputer di atas, lalu **Layar** (lihat + centang
  "Kendalikan" untuk mouse & keyboard) atau **Berkas** (telusuri, lihat isi, unduh).
  Dari HP, buka dashboard lewat Tailscale/Cloudflare — lihat
  [`PANDUAN_AKSES_JARAK_JAUH.md`](PANDUAN_AKSES_JARAK_JAUH.md).
- **Dari HP (bot):** `/komputer` menampilkan komputer yang terhubung; `/layar <nama>`
  mengirim foto layar komputer itu ke chat.

> Komputer yang kamu lihat dan komputer tempat kamu duduk boleh berbeda jaringan —
> aksesnya lewat Tailscale. Mengendalikan komputer yang **sama** dengan yang sedang
> kamu pakai akan terasa bentrok (mouse aslimu dan yang dikirim saling berebut);
> kendali paling berguna untuk komputer **lain**. Halaman node `http://localhost:7171/`
> tetap ada sebagai cadangan untuk komputer yang tidak menjalankan dashboard.

Berkas rahasia (`.env`, kunci, token, login browser/aplikasi, `.ssh`, `.claude`)
ditandai dan **tidak bisa dibuka/diunduh**. Karena akses ini mengendalikan komputer,
jaringan Tailscale-mu (dan kode gabung node) adalah kuncinya — jangan dibagikan.

### Kata sandi (lapisan kedua, dengan pengecoh)

Selain hanya pemilik (`OWNER_TELEGRAM_ID`) yang boleh memerintah, kamu bisa menambah
**kata sandi**: sebelum memakai bot, kata sandi harus dimasukkan. Isi `ITSME_PASSPHRASE`
di `.env`. Yang penting: **jawaban salah SELALU dibalas "kata sandi minimal 20 karakter"**
— padahal kata sandi aslimu boleh lebih pendek. Jadi orang yang mengakses sembarangan
menebak-nebak panjang yang salah dan tidak pernah tahu bentuk aslinya.

- Sekali kata sandi benar dimasukkan, chat terbuka dan tetap terbuka (tidak ditanya tiap
  bot menyala ulang). Kirim `/lock` untuk mengunci lagi, atau isi `ITSME_LOCK_TTL` (menit)
  untuk mengunci otomatis setelah diam.
- Kata sandi hanya di `.env` — tidak pernah di kode, riwayat, atau log. Pesan kata sandi
  (dan tebakan yang salah) langsung dihapus dari chat.
- Kata sandi ikut disalin ke komputer baru yang dipasang lewat `/pasang`.
- Kosong = mati (perilaku lama).

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

Tampilannya modern: top bar, **tab** (Ngobrol, Pengaturan, Berkas, Drive,
Memori, Log), mode terang/gelap (tombol di kanan atas), dan responsif untuk HP.

Bawaannya dashboard **hanya bisa dibuka dari komputer itu sendiri**
(`127.0.0.1`). Untuk membukanya dari **HP atau internet** lewat **domainmu sendiri**
(mis. `domainku.com`, yang jadi *jembatan* saja — bot tetap di komputermu), lihat
panduan lengkap: [`PANDUAN_AKSES_JARAK_JAUH.md`](PANDUAN_AKSES_JARAK_JAUH.md).

Ringkasnya, cara paling aman: set `ITSME_DASHBOARD_PUBLIC=1` lalu pasang
**Cloudflare Tunnel** ke `localhost:8750`. Mode publik mewajibkan **login berkas
kunci** (`data/dashboard.key` — berkas tetap yang kamu salin ke HP dan unggah saat
masuk), memperketat keamanan (anti tebak-coba, header keamanan, tanpa jalan pintas
localhost), dan membiarkanmu **ganti domain sesuka hati** (kosongkan
`ITSME_DASHBOARD_DOMAINS`, atau isi untuk mengunci ke domain tertentu). Alternatif
lama: `ITSME_DASHBOARD_PASSWORD` (login user+password) atau **Tailscale** (privat,
gratis). Matikan dashboard dengan `ITSME_DASHBOARD=0`.

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
| `ITSME_FOLLOW_NODE` | `1` | Kalau It's Me Node terpasang, bot hanya aktif di komputer pemimpin (`0` = abaikan node) |
| `ITSME_NODE_URL` | `http://127.0.0.1:7171` | Alamat API node di komputer ini |
| `ITSME_PUBLIC_URL` | — | Alamat publik dashboard ini (mis. `https://domainku.com`), dipakai `/pasang` agar komputer baru menyambung keluar ke sini **tanpa Tailscale**. Kosong = diambil dari domain pertama `ITSME_DASHBOARD_DOMAINS` |
| `ITSME_TG_WEBHOOK` | `0` | `1` = pesan Telegram diterima lewat webhook ke alamat tunnel dashboard (bukan polling); lihat catatan di bawah tabel |
| `ITSME_PASSPHRASE` | — | Kata sandi lapisan kedua sebelum bot bisa dipakai; salah selalu dibalas "minimal 20 karakter" (pengecoh). Kosong = mati |
| `ITSME_LOCK_TTL` | `0` | Menit tanpa dipakai sebelum terkunci otomatis lagi (`0` = tetap terbuka sampai `/lock`) |
| `ITSME_DASHBOARD_PUBLIC` | `0` | Mode publik (lewat domain/tunnel): wajib login berkas kunci, perketat keamanan, tanpa jalan pintas localhost |
| `ITSME_DASHBOARD_KEY_LOGIN` | `0` | Wajib login dengan berkas `data/dashboard.key` (otomatis `1` saat mode publik) |
| `ITSME_DASHBOARD_DOMAINS` | — | Batasi domain yang boleh (Host), dipisah koma. Kosong = bebas ganti domain |
| `ITSME_DASHBOARD_TRUST_PROXY` | `1` | Pakai IP asli dari Cloudflare/tunnel untuk anti brute-force |

**Mode webhook (`ITSME_TG_WEBHOOK=1`).** Tunnel Cloudflare gratis memberi alamat acak baru
tiap It's Me menyala, dan Telegram sering belum bisa menemukan alamat baru itu beberapa
saat pertama ("Failed to resolve host") — bisa sampai beberapa menit, dan setiap `setWebhook`
yang gagal malah **menghapus** webhook yang sedang terpasang. Karena itu It's Me: (1) menunggu
sampai DNS publik mengenal alamat baru, lalu memasang webhook dengan **IP tetap hasil DNS
publik itu** (parameter `ip_address` milik `setWebhook`), jadi Telegram tidak perlu
me-resolve apa pun; kalau cara itu gagal baru dicoba lewat DNS Telegram; (2) **tidak pernah
menyerah** — gagal dicoba lagi dengan jeda makin panjang (5 detik → 5 menit); (3) selama
webhook belum terpasang, tetap mengambil pesan dengan polling, jadi bot tidak tuli; (4)
memeriksa tiap 5 menit dan memasang ulang kalau webhook hilang, berganti alamat, atau
Telegram gagal mengantar. Kirim `/diagnosa` untuk melihat keadaannya.

## WhatsApp (opsional)

Balas pesan dari WhatsApp-mu sendiri, bukan cuma Telegram.

1. Pasang Node.js (nodejs.org), lalu sekali saja:
   `cd itsme\whatsapp` lalu `npm install --allow-git=all`
   (npm 12 memblokir paket git; Baileys butuh satu.)
2. Di `.env`: `ITSME_WHATSAPP=1` dan `ITSME_WA_OWNER=62812xxxxxxx` (tanpa +).
3. Kirim `/wa <nomor>` ke bot Telegram (atau nyalakan It's Me). Bot melaporkan keadaan
   yang **sebenarnya**, tidak berjanji apa-apa:
   - **"sudah TERSAMBUNG"** — sesi yang tersimpan di `data\whatsapp` masih berlaku. Dalam
     keadaan ini **memang tidak ada QR**: WhatsApp hanya membuat QR untuk akun yang *belum*
     tertaut. Tinggal tes dengan langkah 4.
   - **"QR sudah kukirim"** — akun belum tertaut; QR dikirim sebagai foto ke Telegram
     (maks. 3 per permintaan, jarak ≥ 45 detik). Pindai dari WhatsApp HP > Perangkat tertaut >
     Tautkan perangkat. QR tidak bisa dipindai dari layar HP yang *sama*: kalau Telegram
     dibuka di HP itu, buka dulu di komputer atau tablet. Setelah terpindai bot mengabari
     "WhatsApp tersambung".
   - **"tersambung ke akun lain"** — nomor yang tertaut berbeda dari yang kamu ketik; bot
     bilang akun mana dan cara menggantinya.
   - **"belum ada kabar"** — WhatsApp tidak menjawab dalam 20 detik (internet lambat?).
   `/wa` saja menampilkan status sambungan.
   **`/wa_ulang`** menautkan ulang / mengganti akun: akun yang sekarang dilepas dari
   "Perangkat tertaut", sesi lama **disimpan** di `data\whatsapp.lama-<waktu>` (tidak
   dihapus), lalu QR baru dikirim. Kalau WhatsApp sedang tersambung, bot minta konfirmasi
   dulu: `/wa_ulang ya`. Kalau akunmu dicabut dari HP, bot menyiapkan QR baru sendiri dan
   memberi tahu (dulu jembatan keluar dan menyerah tanpa pernah mengirim QR).
4. Kirim pesan ke nomornya sendiri (chat "Pesan ke diri sendiri"). Riwayat
   percakapan dipisah dari Telegram. Buktinya jalur pesan hidup: `/diagnosa` menampilkan
   "Pesan WhatsApp terakhir masuk: HH:MM" (hanya jam; isi pesan dan nomor pengirim tidak
   pernah dicatat).
5. **Berkas (maks 100 MB).** Kirim gambar/dokumen/audio/video ke bot: berkasnya
   diunduh ke `data\wa_files` lalu dibahas AI (berkas teks kecil langsung
   dibacakan isinya). Untuk meminta bot **mengirim** berkas dari komputer ini:
   `/kirim C:\path\berkas.pdf` — hanya pemilik, dan berkas rahasia (token,
   password, kunci) ditolak. Batasnya diatur lewat `ITSME_WA_MAX_MB`.
6. **Perintah yang juga jalan di WhatsApp** (selain chat biasa & `/kirim`,
   semua hanya untuk pemilik): `/komputer` (daftar komputer terhubung),
   `/nyalakan <nama>` (Wake-on-LAN), `/reset` (lupakan konteks obrolan WA),
   `/otak` (otak yang sedang dipakai), `/memori` & `/hapus_memori` (lihat/
   kosongkan memori jangka panjang — menghapus tetap minta konfirmasi lewat
   Telegram, karena WhatsApp tidak punya tombol Ya/Tidak), dan `/diagnosa`.
7. **Kalau WhatsApp tidak mau menyala.** Pesan galatnya (di Telegram setelah `/wa <nomor>`,
   atau satu kali sehari saat It's Me menyala) selalu menyebut **nama komputer** yang
   bermasalah dan **tepat satu sebab**: *Node.js belum terpasang* (pasang dari nodejs.org
   versi LTS) **atau** *`npm install` belum dijalankan* (perintah lengkap dengan folder yang
   benar ikut ditampilkan). Ingat: WhatsApp berjalan di komputer yang **memegang bot**
   (Hub); komputer lain yang bergabung lewat `/pasang` tidak otomatis punya Node.js/npm,
   dan itu tidak masalah selama mereka siaga. `/diagnosa` menunjukkan keadaan Node.js
   dan `npm install` di komputer yang membalas.

Sesi login disimpan di `data/whatsapp/` (jangan di-commit). Hanya nomor
`ITSME_WA_OWNER` yang dilayani; grup dan nomor lain diabaikan. Tombol
konfirmasi aksi tetap lewat Telegram.

## Google Drive (opsional)

It's Me bisa mengakses Google Drive-mu — **cari, unggah, unduh, dan kelola**
berkas/folder — lewat **chat (semua otak, termasuk "Claude di komputer")**
maupun **panel Google Drive di dashboard**.

> **Panduan lengkap langkah demi langkah (bahasa Indonesia, untuk pemula):**
> lihat [`PANDUAN_GOOGLE_DRIVE.md`](PANDUAN_GOOGLE_DRIVE.md). Ringkasannya di bawah.

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
tetap memakai akunmu (API key hanya untuk `/claude`). Batas langkah per pesan
bawaannya tanpa batas; atur lewat `ITSME_CLAUDE_CODE_MAX_TURNS` kalau perlu.

Kalau filter keamanan Claude menolak percakapan (pesan "safeguards flagged
this session"; kadang salah sasaran untuk pekerjaan biasa), It's Me otomatis
mencoba sekali lagi di sesi baru tanpa konteks lama. Kalau tetap ditolak, It's
Me menjelaskannya dalam bahasa Indonesia dan memulai percakapan dari awal;
memori tetap.

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
