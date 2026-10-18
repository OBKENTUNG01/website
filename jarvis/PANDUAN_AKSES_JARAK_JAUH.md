# Panduan: membuka dashboard It's Me dari HP / internet (dengan login)

Dashboard It's Me jalan **di komputermu** (bawaannya `http://localhost:8750`,
hanya dari komputer itu). Panduan ini menunjukkan dua cara aman membukanya dari
HP atau dari domain `ai.yantah-rutari.com`, **selalu dengan login**.

> Karena dashboard bisa mengendalikan komputer penuh, **jangan pernah**
> membukanya ke internet tanpa login. Aktifkan juga verifikasi 2 langkah Telegram.

---

## Langkah 0 — nyalakan login dashboard (wajib untuk akses jarak jauh)

Di komputer, buka `%USERPROFILE%\ItsMe\.env`, tambahkan (ganti passwordnya):

```
ITSME_DASHBOARD_HOST=0.0.0.0
ITSME_DASHBOARD_USER=admin
ITSME_DASHBOARD_PASSWORD=ganti-dengan-password-kuat
```

Simpan, lalu nyalakan ulang It's Me (atau tunggu pembaruan otomatis menyala
ulang). Sekarang dashboard minta **login user + password**.

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
1. minta login Cloudflare (email-mu), lalu
2. minta login dashboard It's Me (user + password dari `.env`).

> Catatan penting: dashboard **bukan** aplikasi yang "di-push" ke hosting biasa
> seperti website statis. Ia tetap berjalan di komputermu; Cloudflare Tunnel
> hanya meneruskan domain ke komputer itu. Komputer harus menyala & online.

---

## Mana yang dipilih?

- Cuma kamu yang pakai, dari HP-mu → **Tailscale** (Cara A). Paling simpel & aman.
- Mau alamat cantik `ai.yantah-rutari.com` → **Cloudflare Tunnel + Access** (Cara B),
  dan tetap dengan login dashboard menyala.
