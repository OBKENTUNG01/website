"""Akses internet untuk otak lokal (Ollama).

Claude punya web_search/web_fetch di server Anthropic; model lokal tidak.
Modul ini menggantinya dengan dua fungsi yang jalan di komputermu sendiri:

  search(query) — cari di DuckDuckGo lewat paket `ddgs` (gratis, tanpa key)
  fetch(url)    — ambil sebuah halaman dan ubah jadi teks biasa

fetch() hanya mau membuka alamat internet publik. Alamat lokal (localhost,
192.168.x.x, router, dll.) ditolak — termasuk kalau dicapai lewat redirect —
supaya isi halaman web yang aneh tidak bisa menyuruh bot mengintip perangkat
di jaringan rumahmu.
"""

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from . import config

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 ItsMe/1.0"
)
MAX_BYTES = 2_000_000  # cukup untuk halaman berita; berkas besar tidak diunduh
MAX_REDIRECTS = 5


# --- pencarian ----------------------------------------------------------------
def search(query, max_results=5, seen=None):
    """Cari di DuckDuckGo. Alamat hasilnya ditambahkan ke `seen` (kalau ada),
    supaya baca_halaman boleh langsung membukanya tanpa konfirmasi."""
    query = (query or "").strip()
    if not query:
        return "Kata kunci pencarian kosong."
    try:
        from ddgs import DDGS
    except ImportError:
        return "Paket 'ddgs' belum terpasang. Jalankan: pip install -r requirements.txt"
    try:
        # Minta sedikit lebih banyak: sebagian hasil bisa berupa link pelacak
        # relatif (mis. "/clev?...") yang tidak bisa dibuka, dan dibuang.
        results = DDGS(timeout=10).text(
            query,
            region=config.SEARCH_REGION,
            safesearch="moderate",
            max_results=max_results + 3,
        ) or []
    except Exception as exc:  # noqa: BLE001 - ddgs melempar banyak jenis error
        if "No results found" in str(exc):
            # ddgs juga memakai pesan ini saat mesin pencari sedang membatasi.
            return "Tidak ada hasil (atau mesin pencari sedang membatasi). Coba kata kunci lain."
        return f"Pencarian gagal ({type(exc).__name__}): {exc}. Coba lagi sebentar."
    results = [
        r for r in results
        if str(r.get("href", "")).startswith(("http://", "https://"))
    ][:max_results]
    if not results:
        return "Tidak ada hasil."
    if seen is not None:
        seen.update(str(r.get("href", "")).strip() for r in results)
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r.get('title', '').strip()}\n"
            f"   {r.get('href', '').strip()}\n"
            f"   {r.get('body', '').strip()}"
        )
    return "\n\n".join(lines)


# --- pengaman alamat -------------------------------------------------------------
def _is_public_host(host):
    """True hanya jika SEMUA alamat IP milik host adalah alamat internet publik."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _check_url(url):
    """Kembalikan pesan penolakan, atau None kalau URL boleh dibuka."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "URL harus diawali http:// atau https://"
    if not _is_public_host(parsed.hostname):
        return "Ditolak: alamat itu bukan alamat internet publik (atau tidak ditemukan)."
    return None


# --- HTML → teks -----------------------------------------------------------------
_SKIP = {"script", "style", "noscript", "svg", "template", "head"}
_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
    "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return parser.title.strip(), text.strip()


# --- ambil halaman ---------------------------------------------------------------
def _peer_is_public(resp):
    """True bila koneksi benar-benar menuju alamat internet publik.

    _check_url() memeriksa hasil DNS sekali, lalu httpx me-resolve ulang saat
    menyambung — domain yang mengubah jawabannya (DNS rebinding) bisa lolos ke
    127.0.0.1 atau alamat metadata. Jadi IP yang benar-benar tersambung ikut diperiksa.
    """
    stream = resp.extensions.get("network_stream")
    raw = stream.get_extra_info("server_addr") if stream else None
    if not raw:  # transport tanpa info alamat (mis. di-mock) — jangan gagal
        return True
    try:
        ip = ipaddress.ip_address(str(raw[0]).split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def fetch(url, max_chars=12000):
    url = (url or "").strip()
    with httpx.Client(
        timeout=httpx.Timeout(20.0),
        follow_redirects=False,  # redirect diikuti manual supaya tiap tujuan dicek
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            problem = _check_url(url)
            if problem:
                return problem
            try:
                with client.stream("GET", url) as resp:
                    if not _peer_is_public(resp):
                        return "Ditolak: alamat yang tersambung bukan alamat internet publik."
                    if resp.is_redirect:
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
                    ctype = resp.headers.get("content-type", "").lower()
                    if not any(t in ctype for t in ("text/", "json", "xml")):
                        return f"Bukan halaman teks ({ctype or 'tipe tidak diketahui'})."
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    encoding = resp.encoding or "utf-8"
                    status = resp.status_code
            except httpx.HTTPError as exc:
                return f"Gagal membuka halaman: {exc}"
            raw = body.decode(encoding, errors="replace")
            if "html" in ctype:
                title, text = html_to_text(raw)
            else:
                title, text = "", raw
            header = f"[{status}] {url}" + (f"\nJudul: {title}" if title else "")
            if len(text) > max_chars:
                text = text[:max_chars] + "\n…(dipotong)"
            return f"{header}\n\n{text}"
    return "Terlalu banyak redirect."
