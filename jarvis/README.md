# It's Me 🫡

Asisten pribadi bergaya **JARVIS** (Iron Man) yang berjalan sebagai **bot
Telegram**, jadi bisa langsung kamu pakai dari HP. Otaknya **Claude
(Opus 5.5, effort `max`)**.

## Yang bisa dilakukan

- **Ngobrol & tanya apa saja** dalam bahasa Indonesia.
- **Belajar dari internet** — cari info terbaru (`web_search`) dan baca isi
  halaman (`web_fetch`), karena pengetahuan bawaan AI ada batas waktunya.
- **Multitasking** — beberapa tugas bisa jalan paralel lewat tugas latar;
  kamu tetap bisa mengobrol sementara It's Me bekerja.
- **Edit & deploy website lewat Git** — It's Me mengubah kode lalu `git push`;
  kalau Hostinger diatur auto-deploy, website langsung terbarui.
- **Ingat** hal penting yang kamu beri tahu (memori jangka panjang).
- **Aman** — hanya kamu (pemilik) yang bisa memerintah; aksi berisiko minta
  konfirmasi satu tombol, dan setelah kamu tekan "Ya" langsung dijalankan.

## Yang perlu kamu siapkan

1. **Token bot Telegram** — chat [@BotFather](https://t.me/BotFather), kirim
   `/newbot`, salin token-nya.
2. **User id Telegram-mu** — chat [@userinfobot](https://t.me/userinfobot),
   atau jalankan bot ini lalu kirim `/id`.
3. **API key Anthropic** — dari <https://console.anthropic.com>.

## Cara menjalankan (di komputer / server)

```bash
cd jarvis
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # lalu isi TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_ID, ANTHROPIC_API_KEY
python -m itsme.main
```

Buka Telegram, cari bot-mu, kirim `/start`.

## Cara menjalankan langsung dari HP (Android, Termux)

1. Pasang **Termux** (dari F-Droid).
2. Di Termux:
   ```bash
   pkg install python git
   git clone <repo-ini> && cd website/jarvis
   pip install -r requirements.txt
   cp .env.example .env
   nano .env        # isi tokennya
   python -m itsme.main
   ```
3. Biarkan Termux tetap jalan. Sekarang botmu hidup dan bisa kamu ajak chat
   dari aplikasi Telegram yang sama.

> iPhone tidak punya Termux. Untuk iPhone, jalankan di sebuah server/komputer
> kecil yang menyala terus, lalu kendalikan dari Telegram di iPhone.

## Menghubungkan ke website Hostinger

1. Di hPanel Hostinger → menu **Git** → hubungkan repo GitHub website-mu dan
   aktifkan **auto-deploy**.
2. Di `.env`, isi `WEBSITE_REPO_DIR` ke folder clone lokal repo itu.
3. Tinggal bilang ke It's Me, misalnya: *"ganti judul halaman jadi X lalu
   deploy"*. Ia mengedit kode, minta konfirmasi, push, dan Hostinger menarik
   perubahannya.

## Perintah di Telegram

| Perintah | Fungsi |
|---|---|
| `/start`, `/help` | Bantuan |
| `/reset` | Mulai percakapan baru (memori tetap) |
| `/id` | Tampilkan user id Telegram-mu |

## Pengaturan (opsional, di `.env`)

| Variabel | Default | Fungsi |
|---|---|---|
| `ITSME_MODEL` | `claude-opus-5-5` | Model otak |
| `ITSME_EFFORT` | `max` | Tingkat upaya: `low`/`medium`/`high`/`xhigh`/`max` |
| `ITSME_MAX_TOKENS` | `16000` | Panjang jawaban maksimum |
| `WEBSITE_REPO_DIR` | — | Folder repo website untuk fitur deploy |

> Catatan: effort `max` adalah yang paling cerdas tapi paling lambat dan paling
> boros kuota. Kalau terasa lambat untuk obrolan ringan, turunkan ke `high`.

## Catatan jujur soal batasan

- **Model dikunci** ke `claude-opus-5-5` dan tidak diganti otomatis ke model
  lain oleh kode ini. (Jika server Anthropic sedang penuh, sistem mereka bisa
  mengalihkan sementara — itu di luar kendali aplikasi ini.)
- **Pedoman keamanan bawaan Claude tidak bisa dimatikan** — itu ada di sisi
  model, bukan di aplikasi. Untuk tugas sehari-hari (website, email, cari info)
  pedoman ini praktis tidak menghalangi.
- **Keamanan akun:** demi keamanan, untuk layanan seperti Google sebaiknya
  pakai login resmi (OAuth) atau sesi browser yang sudah login — bukan menaruh
  username & password di dalam berkas.
