# Panduan: membuka dashboard It's Me dari HP / internet (dengan login)

Dashboard It's Me jalan **di komputermu** (bawaannya `http://localhost:8750`,
hanya dari komputer itu). Panduan ini menunjukkan dua cara aman membukanya dari
HP atau dari domain `ai.yantah-rutari.com`, **selalu dengan login**.

> Karena dashboard bisa mengendalikan komputer penuh, **jangan pernah**
> membukanya ke internet tanpa login. Aktifkan juga verifikasi 2 langkah Telegram.

---

## Langkah 0 — nyalakan login dashboard (wajib untuk akses jarak jauh)

Di komputer, buka `%USERPROFILE%\ItsMe\.env`, lalu pilih salah satu:

**Pilihan ketat (disarankan untuk domain/internet) — login BERKAS KUNCI:**

```
ITSME_DASHBOARD_PUBLIC=1
```

Saat It's Me menyala ulang, mode publik aktif: keamanan diperketat (header
keamanan, anti tebak-coba, tanpa jalan pintas localhost) dan masuk **wajib
mengunggah berkas kunci** `data/dashboard.key`. Berkas ini dibuat otomatis dan
**tidak berubah**. **Salin `%USERPROFILE%\ItsMe\data\dashboard.key` ke HP-mu**
sekali (lewat chat It's Me, USB, atau email ke diri sendiri). Di halaman login,
pilih berkas itu — selesai. Simpan berkasnya baik-baik; siapa pun yang punya
berkas itu bisa masuk (kalau bocor, hapus `dashboard.key` lalu It's Me membuat
yang baru).

**Pilihan sederhana — login user + password:**

```
ITSME_DASHBOARD_PASSWORD=ganti-dengan-password-kuat
```

Simpan, lalu nyalakan ulang It's Me (atau tunggu pembaruan otomatis). Untuk akses
di WiFi yang sama tanpa domain, tambahkan juga `ITSME_DASHBOARD_HOST=0.0.0.0`
(tidak perlu kalau lewat Tailscale/Cloudflare Tunnel yang masuk via localhost).

---

## Cara paling gampang: Cloudflare Tunnel OTOMATIS (satu perintah)

Kalau DNS domainmu (mis. `yantah-rutari.com`) sudah dikelola di **Cloudflare**, It's Me
bisa menyiapkan tunnel sendiri: unduh `cloudflared`, login sekali (lewat browser), buat
tunnel, arahkan DNS, tulis konfigurasi, dan menyetel It's Me supaya tunnel **mengikuti
komputer pemimpin** (ikut failover). Di PowerShell komputer utama, jalankan:

```powershell
$env:ITSME_TUNNEL_HOST='ai.yantah-rutari.com'; irm https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/tunnel-setup.ps1 | iex
```

Browser terbuka sekali untuk **login Cloudflare** (pilih zona domainmu, lalu Authorize).
Setelah itu skrip menyetel `ITSME_PUBLIC_URL` + `ITSME_DASHBOARD_PUBLIC=1` (login berkas
kunci) otomatis. Begitu It's Me menyala ulang, buka `https://ai.yantah-rutari.com` dan masuk
dengan berkas `data/dashboard.key` (lihat Langkah 0). Cara B di bawah hanya perlu kalau mau
mengatur manual atau DNS-mu belum di Cloudflare.

> Ingat: dashboard tetap berjalan **di komputermu**; tunnel hanya meneruskan domain ke sana.
> Hostinger (atau hosting file biasa) tidak bisa menjalankan dashboard ini.

---

## Cara A — Tailscale (paling aman & gratis, disarankan)

Tailscale membuat "jaringan pribadi" antara komputermu dan HP-mu. Dashboard
tidak terbuka ke internet umum — hanya perangkatmu sendiri yang bisa.

1. Buat akun di <https://tailscale.com> (gratis).
2. Pasang Tailscale di **komputer** (tailscale.com/download), login.
3. Pasang aplikasi **Tailscale** di **HP**, login dengan akun yang sama.
4. Di komputer, lihat alamat Tailscale-nya (mis. `100.x.y.z`) di aplikasi
   Tailscale, atau jalankan `tailscale ip -4`.
5. Dari HP (saat Tailscale aktif), buka browser ke:
   ```
   http://100.x.y.z:8750
   ```
   Lalu login.

Kelebihan: tidak ada yang terbuka ke internet, tidak perlu domain, gratis.

---

## Cara B — Cloudflare Tunnel ke ai.yantah-rutari.com (+ login Cloudflare Access)

Ini untuk membukanya lewat domainmu sendiri. Domain `yantah-rutari.com` harus
dikelola di Cloudflare (nameserver-nya mengarah ke Cloudflare).

1. Pasang **cloudflared** di komputer:
   <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>
2. Login: `cloudflared tunnel login` (pilih domainmu di browser).
3. Buat tunnel: `cloudflared tunnel create itsme`
4. Arahkan subdomain ke dashboard — buat berkas config `config.yml`:
   ```yaml
   tunnel: itsme
   credentials-file: C:\Users\ADMIN\.cloudflared\<ID-tunnel>.json
   ingress:
     - hostname: ai.yantah-rutari.com
       service: http://localhost:8750
     - service: http_status:404
   ```
5. Hubungkan DNS: `cloudflared tunnel route dns itsme ai.yantah-rutari.com`
6. Jalankan: `cloudflared tunnel run itsme` (bisa dijadikan service agar nyala
   terus: `cloudflared service install`).
7. **Tambahkan login Cloudflare Access** (lapisan login kedua, sangat disarankan):
   di dash.cloudflare.com → **Zero Trust → Access → Applications → Add an
   application → Self-hosted**, domain `ai.yantah-rutari.com`, lalu buat
   **Policy** yang hanya mengizinkan **email-mu** (login Google/email OTP).

Setelah ini, membuka `https://ai.yantah-rutari.com` akan:
1. minta login Cloudflare (email-mu) — kalau Access dipasang, lalu
2. minta **berkas kunci** It's Me (atau user + password) dari Langkah 0.

**Ganti domain sesukamu.** Domain hanya jembatan — It's Me tidak terikat ke nama
domain tertentu. Untuk pindah ke domain lain: ganti `hostname` di `config.yml`,
jalankan `cloudflared tunnel route dns itsme domain-baru.com`, lalu nyalakan ulang
tunnel. Tidak perlu mengubah `.env` (biarkan `ITSME_DASHBOARD_DOMAINS` kosong = terima
domain mana pun; keamanan tetap dari berkas kunci). Kalau mau mengunci ke domain
tertentu saja, isi `ITSME_DASHBOARD_DOMAINS=domainku.com,domainlain.com`.

> Catatan penting: dashboard **bukan** aplikasi yang "di-push" ke hosting biasa
> seperti website statis. Ia tetap berjalan di komputermu; Cloudflare Tunnel
> hanya meneruskan domain ke komputer itu. Komputer harus menyala & online.

### Keamanan saat lewat domain (mode publik)

Dengan `ITSME_DASHBOARD_PUBLIC=1`, It's Me menambah lapisan ini sendiri:

- **Login berkas kunci wajib** — tidak ada akses tanpa `dashboard.key`, dan **tidak
  ada jalan pintas localhost** (penting: tunnel masuk lewat localhost, jadi tanpa ini
  siapa pun lewat domain bisa masuk).
- **Anti tebak-coba** — salah berkas kunci beberapa kali → IP itu diblokir sementara
  (makin sering salah, makin lama), memakai IP asli dari Cloudflare.
- **Header keamanan** — `Content-Security-Policy`, `X-Frame-Options: DENY` (anti
  clickjacking/iframe), `Referrer-Policy`, `Permissions-Policy`, dan `HSTS`.
- **Cookie sesi** HttpOnly + SameSite=Strict; aksi yang mengubah butuh header khusus.

Lapisan tambahan yang sangat disarankan di Cloudflare: pasang **Access** (login email,
Langkah 7), nyalakan **WAF**/rate-limit dan **Bot Fight Mode** di dashboard Cloudflare,
dan jangan buka port apa pun di router (cloudflared keluar sendiri, tidak perlu
port-forward). Dengan begitu serangan ke domain dihadang di tepi Cloudflare dulu,
dan kalaupun lolos, masih terbentur berkas kunci.

---

## Failover otomatis lintas-jaringan (banyak komputer, satu alamat)

Supaya bot It's Me tetap hidup di **alamat yang sama** walau komputer pemimpin mati —
termasuk komputer di **jaringan/lokasi berbeda** — gabungkan It's Me Node (pemilihan
pemimpin + "bot ikut pemimpin") dengan tunnel yang **mengikuti pemimpin**:

1. **Alamat (tunnel) mengikuti pemimpin.** Di tiap komputer calon-pemimpin, isi di
   `%LOCALAPPDATA%\ItsMeNode\data\config.json`:
   ```json
   "tunnel_cmd": "cloudflared tunnel run itsme"
   ```
   It's Me Node menjalankan perintah itu **hanya selama komputer itu menjadi pemimpin**,
   dan mematikannya begitu bukan pemimpin lagi. Jadi domain `ai.yantah-rutari.com` selalu
   menunjuk ke pemimpin yang hidup. Saat pemimpin mati dan node memilih pemimpin baru,
   pemimpin baru menyalakan tunnel otomatis — HP/komputer lain tersambung lagi ke alamat
   yang sama tanpa diubah apa pun.
   - Salin kredensial cloudflared (`%USERPROFILE%\.cloudflared\<ID>.json` + `config.yml`)
     ke tiap calon-pemimpin. Satu tunnel bernama `itsme`, dijalankan bergantian oleh
     pemimpin. Jangan `cloudflared service install` kalau pakai cara ini — biar Node yang
     menyalakan/mematikan tunnel.

2. **Token HP sama untuk semua.** Supaya HP & komputer-jembatan tetap bisa masuk ke
   pemimpin mana pun setelah failover, samakan token: di `.env` komputer pertama isi
   `ITSME_HP_SECRET=<rahasia-acak-yang-sama>` **sebelum** mengundang komputer lain lewat
   `/pasang` (nilai ini otomatis ikut tersalin ke komputer baru). Tanpa ini tiap komputer
   punya token sendiri dan HP perlu memasukkan token baru setiap kali pemimpin berpindah.

3. **Minimal dua calon-pemimpin yang saling terjangkau** (mis. satu jaringan lokal, atau
   node yang saling gosip) supaya mereka bisa memilih pemimpin baru saat salah satu mati.

**Batas jujur:** komputer yang HANYA terjangkau lewat jembatan (tidak punya tunnel sendiri
dan tidak sejaringan dengan calon-pemimpin lain) tetap berguna sebagai pekerja dan bisa
dikendalikan, tapi **tidak bisa menjadi pemimpin** — sebab kalau ia yang memimpin, tak ada
yang bisa menghubunginya. Pastikan pemimpin selalu komputer yang bisa menjalankan tunnel.

---

## Mana yang dipilih?

- Cuma kamu yang pakai, dari HP-mu → **Tailscale** (Cara A). Paling simpel & aman.
- Mau alamat cantik `ai.yantah-rutari.com` → **Cloudflare Tunnel + Access** (Cara B),
  dan tetap dengan login dashboard menyala.
