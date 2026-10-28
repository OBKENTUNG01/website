# Panduan lengkap menghubungkan Google Drive ke It's Me

Ini dikerjakan **sekali saja**. Memang paling ribet dari semua fitur, karena
aturan Google: akun harus pakai verifikasi 2 langkah, dan kamu membuat
"kunci" (kredensial) sendiri. Setelah tersambung, tinggal pakai dari HP.

Butuh waktu ±15 menit. Ikuti urut dari atas. Kalau bingung di langkah mana pun,
bilang ke It's Me (lewat Claude) — bisa dibantu langsung di browser.

---

## Bagian 0 — Aktifkan Verifikasi 2 Langkah (WAJIB dulu)

Google memblokir Google Cloud kalau akunmu belum pakai verifikasi 2 langkah.
Ini harus **kamu sendiri** yang aktifkan (butuh HP-mu).

1. Buka **<https://myaccount.google.com/security>**
2. Di bagian **"Cara Anda login ke Google"**, klik **"Verifikasi 2 Langkah"**.
3. Klik **"Mulai"** / **"Get started"**, masukkan password Google-mu.
4. Masukkan **nomor HP**, pilih **SMS**, lalu masukkan kode yang dikirim.
5. Klik **"Aktifkan"**.
6. **Tunggu 2–5 menit** supaya Google memprosesnya.

> Cek: buka <https://console.cloud.google.com/> — kalau TIDAK muncul lagi tulisan
> "access blocked", berarti sudah aktif dan bisa lanjut ke Bagian 1.

---

## Bagian 1 — Buat Project

1. Buka **<https://console.cloud.google.com/projectcreate>**
2. **Project name**: ketik bebas, mis. `ItsMe Drive`.
3. Klik **"Create"**. Tunggu beberapa detik.
4. Pastikan project itu terpilih: lihat kiri atas (di sebelah tulisan "Google
   Cloud"). Kalau belum, klik pemilih project dan pilih `ItsMe Drive`.

---

## Bagian 2 — Aktifkan Google Drive API

1. Pastikan project `ItsMe Drive` terpilih (kiri atas).
2. Buka **<https://console.cloud.google.com/apis/library/drive.googleapis.com>**
3. Klik tombol biru **"Enable"**. Tunggu sampai aktif.

---

## Bagian 3 — Atur layar persetujuan (OAuth consent / Audience)

Ini supaya Google mengizinkan aplikasimu memakai akunmu.

1. Buka **<https://console.cloud.google.com/auth/overview>**
2. Kalau muncul **"Get started"**:
   - **App name**: `It's Me`
   - **User support email**: pilih email Google-mu.
   - **Audience**: pilih **External**.
   - **Contact information**: isi email Google-mu.
   - Centang persetujuan, klik **"Create"**.
3. Tambahkan dirimu sebagai penguji (penting, kalau tidak login akan ditolak):
   - Buka **<https://console.cloud.google.com/auth/audience>**
   - Di bagian **"Test users"**, klik **"Add users"**.
   - Ketik **email Google-mu**, klik **"Save"**.

---

## Bagian 4 — Buat kunci (OAuth client ID jenis Desktop)

1. Buka **<https://console.cloud.google.com/auth/clients>**
2. Klik **"Create client"** (atau "Create credentials" → "OAuth client ID").
3. **Application type**: pilih **"Desktop app"**.
4. **Name**: `It's Me Desktop`. Klik **"Create"**.
5. Muncul kotak kecil → klik **"Download JSON"**.
   (Kalau terlewat: di daftar client, klik ikon unduh ⬇ di baris client tadi.)
6. File tersimpan di folder **Downloads**, namanya seperti
   `client_secret_1234....json`.

---

## Bagian 5 — Pasang di It's Me, lalu login

1. **Pindahkan & ganti nama** file JSON tadi menjadi tepat:
   ```
   C:\Users\ADMIN\ItsMe\data\drive_credentials.json
   ```
   > It's Me (lewat Claude) bisa memindahkannya otomatis untukmu — tinggal minta.
2. Buka **PowerShell**, lalu jalankan:
   ```powershell
   cd $HOME\ItsMe; .venv\Scripts\python -m itsme.drive_login
   ```
3. Browser akan terbuka:
   - Pilih **akun Google-mu**.
   - Kalau muncul **"Google hasn't verified this app"** / "Google belum
     memverifikasi aplikasi ini" → klik **"Advanced"** / "Lanjutan" → klik
     **"Go to It's Me (unsafe)"**. **Ini aman** karena aplikasi ini milikmu
     sendiri, bukan orang lain.
   - Klik **"Continue"** / "Izinkan" untuk memberi akses Drive.
4. Jendela PowerShell akan menulis **"Berhasil"** dan beberapa isi Drive-mu.

---

## Selesai — cara pakai

- **Dari dashboard**: buka <http://localhost:8750>, refresh, lalu pakai panel
  **"Google Drive"** (jelajah, cari, unggah, unduh, buat folder, ganti nama, hapus).
- **Dari Telegram / chat**: minta It's Me, mis.
  - "cari laporan di Google Drive"
  - "unggah C:\Users\ADMIN\Downloads\foto.jpg ke Google Drive"
  - "buat folder Arsip di Google Drive"

## Kalau ada masalah

- **"access blocked" masih muncul** → verifikasi 2 langkah belum aktif / belum
  diproses. Tunggu beberapa menit, ulangi Bagian 0.
- **Login ditolak / "access_denied"** → emailmu belum ditambahkan sebagai
  **Test user** (Bagian 3, langkah 3).
- **drive_login bilang kredensial belum ada** → file JSON belum ada di
  `data\drive_credentials.json` atau namanya belum tepat (Bagian 5, langkah 1).
- **Sudah pernah tersambung, tapi sekarang "Login Google Drive sudah tidak
  berlaku" / minta login lagi, padahal kemarin baru dicoba** → ini **NORMAL**
  untuk layar persetujuan status **"Testing"** (Bagian 3): Google otomatis
  mencabut token tiap **~7 hari**, tanpa pemberitahuan apa pun. Dua pilihan:
  - **Cepat** (ulang tiap ~7 hari): `cd $HOME\ItsMe; .venv\Scripts\python -m
    itsme.drive_login` lagi.
  - **Permanen** (sekali saja, selesai untuk seterusnya): buka
    **<https://console.cloud.google.com/auth/audience>** → tombol
    **"PUBLISH APP"** → konfirmasi. Aman untuk aplikasi pribadi (bukan untuk
    orang lain) — pesan "Google belum memverifikasi aplikasi ini" tetap
    muncul sekali saat login, itu wajar, lanjutkan seperti Bagian 5 langkah 3.
    Setelah publish, jalankan `drive_login` **sekali lagi** supaya token baru
    ikut aturan baru (tidak kedaluwarsa lagi tiap minggu).
- **Masih bingung** → minta It's Me (lewat Claude) membantu langsung di browser;
  Bagian 1–4 bisa dikerjakan bersama.
