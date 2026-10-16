# It's Me 🫡

Asisten pribadi bergaya **JARVIS** (Iron Man) yang kamu kendalikan lewat
**Telegram di HP**. Otaknya berjalan di komputermu sendiri (model lokal lewat
Ollama, gratis dan privat), dengan otak cloud OpenRouter atau Claude sebagai
pilihan yang lebih pintar.

Semua kode, cara pasang, dan pengaturannya ada di folder [`jarvis/`](jarvis/README.md).

**Pasang di Windows** — buka PowerShell, lalu tempel:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.ps1 | iex
```

**Pasang di macOS / Linux** — buka Terminal, lalu tempel:

```bash
curl -fsSL https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.sh | bash
```

Jalankan perintah yang sama lagi kapan saja untuk memperbarui; `.env` dan
memori tidak ditimpa.
