"""Pengunduh model Ollama yang memeriksa setiap berkas.

Jalankan dari folder jarvis/:  python -m itsme.fetch_model [nama-model]

Kenapa tidak cukup "ollama pull"? Pengunduh bawaan Ollama memecah berkas
besar menjadi belasan potongan paralel tanpa memeriksa jawaban server per
potongan. Jawaban yang keliru (misalnya HTTP 200 berisi berkas utuh untuk
permintaan sepotong, atau halaman error dari CDN) ikut tertulis, baru
ketahuan di akhir sebagai "digest mismatch", lalu seluruh unduhan dibuang
(ollama/ollama#10267, #14554).

Modul ini mengunduh berkas model langsung dari registry Ollama dengan
pemeriksaan ketat (status 206 dan Content-Range yang persis), melanjutkan
unduhan yang terputus, dan mencocokkan sha256 sebelum berkas ditaruh di
folder model Ollama. Sesudahnya "ollama pull" tinggal mencatat modelnya
dalam hitungan detik.

Kode keluar: 0 semua berkas lengkap & cocok; 3 Ollama terlalu lama untuk
model ini; 4 ruang disk kurang; 2 tidak bisa dipakai di komputer ini; 1 gagal
karena hal lain. Untuk 1 dan 2, skrip setup kembali memakai "ollama pull".
Sengaja hanya memakai teks ASCII supaya aman di semua jenis terminal.
"""

import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import httpx

from . import config

# ITSME_REGISTRY_URL hanya untuk pengujian (registry tiruan).
REGISTRY = (os.environ.get("ITSME_REGISTRY_URL") or "https://registry.ollama.ai").rstrip("/")
LIST_TYPE = "application/vnd.ollama.manifest.list.v2+json"
MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
TENSOR_TYPE = "application/vnd.ollama.image.tensor"
# Urutan varian yang dipilih Ollama di Windows/Linux (manifest.runnerPreferences).
RUNNERS = ("llamacpp", "ggml")

CHUNK = 64 * 1024 * 1024  # satu potongan unduhan
WORKERS = 4  # koneksi paralel
MAX_STALLS = 20  # gagal berturut-turut tanpa kemajuan sebelum menyerah
SPARE = 512 * 1024 * 1024  # sisa ruang disk yang dijaga

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPO = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_EXPIRED = (400, 401, 403, 404, 410)  # tautan CDN kedaluwarsa/ditolak: minta yang baru


class NotApplicable(Exception):
    """Cara ini tidak bisa dipakai di sini; skrip setup memakai ollama pull."""


class TooOld(Exception):
    pass


class DiskFull(Exception):
    pass


class _Retry(Exception):
    """Jawaban server tidak bisa dipakai; potongan diulang dari awal."""


class _RangeIgnored(Exception):
    """Server mengabaikan permintaan sepotong dan mengirim berkas utuh."""


class _Stopped(Exception):
    """Pekerja lain meminta semua berhenti."""


def _gb(n):
    return f"{n / 1e9:.2f} GB"


def _replace(src, dst):
    """os.replace yang sabar: di Windows, antivirus kadang mengunci berkas baru sebentar."""
    for attempt in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def parse_name(name):
    """'qwen3.5:9b' -> ('library/qwen3.5', '9b'). None kalau bukan model registry Ollama."""
    name = (name or "").strip()
    repo, _, tag = name.partition(":")
    repo, tag = repo.lower(), tag or "latest"
    if "/" not in repo:
        repo = "library/" + repo
    elif "." in repo.split("/")[0]:
        return None  # registry lain (mis. hf.co/...)
    if not _REPO.match(repo) or not _TAG.match(tag):
        return None
    return repo, tag


def _version(text):
    """'0.40.5' / 'v0.30.0-rc1' -> (0, 40, 5). None kalau tidak terbaca."""
    core = str(text or "").strip().lstrip("v").split("-")[0].split("+")[0]
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))


def _sha256_file(path, label=None):
    hasher = hashlib.sha256()
    total = max(os.path.getsize(path), 1)
    done = 0
    shown = -1
    with open(path, "rb") as f:
        while True:
            block = f.read(8 * 1024 * 1024)
            if not block:
                break
            hasher.update(block)
            done += len(block)
            percent = done * 100 // total
            if label and percent != shown and sys.stdout.isatty():
                shown = percent
                print(f"\r    Memeriksa sha256 {label}: {percent}%   ", end="", flush=True)
    if label and sys.stdout.isatty():
        print()
    return hasher.hexdigest()


class _Lock:
    """Kunci antar-proses: dua setup tidak boleh mengunduh berkas yang sama
    bersamaan. Kunci lepas sendiri kalau prosesnya mati."""

    def __init__(self, path):
        self.path = path
        self.file = None

    def _try(self):
        f = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self.file = f
        return True

    def acquire(self):
        if self._try():
            return
        print("    Model ini sedang diunduh jendela setup lain; menunggu sampai selesai...", flush=True)
        while not self._try():
            time.sleep(2)

    def release(self):
        if not self.file:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.file.close()
        self.file = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


class _Progress:
    """Satu baris kemajuan unduhan, diperbarui tiap detik."""

    def __init__(self, label, total, done):
        self.label, self.total, self.done = label, total, done
        self.started, self.base = time.monotonic(), done
        self.lock = threading.Lock()
        self.last_line = 0.0
        self.tty = sys.stdout.isatty()

    def reset(self):
        with self.lock:
            self.done = self.base = 0
            self.started = time.monotonic()

    def add(self, n):
        with self.lock:
            self.done += n
            now = time.monotonic()
            if now - self.last_line >= (1 if self.tty else 60):
                self.last_line = now
                self._show(now)

    def _show(self, now):
        elapsed = max(now - self.started, 1e-6)
        speed = (self.done - self.base) / elapsed
        line = f"    {self.label}: {_gb(self.done)} / {_gb(self.total)} ({self.done * 100 // max(self.total, 1)}%)"
        if self.done >= self.total:
            line += ", selesai"
        elif speed > 0:
            line += f", {speed / 1e6:.1f} MB/s, sisa ~{max(1, int((self.total - self.done) / speed / 60))} menit"
        print(("\r" + line + "   ") if self.tty else line, end="" if self.tty else "\n", flush=True)

    def finish(self):
        with self.lock:
            self._show(time.monotonic())
            if self.tty:
                print()


class _Blob:
    def __init__(self, digest, size, media_type):
        self.digest, self.size, self.media_type = digest, size, media_type
        self.hex = digest.split(":", 1)[1]

    @property
    def label(self):
        return self.media_type.rsplit(".", 1)[-1] if self.media_type else self.hex[:12]


class Fetcher:
    def __init__(self, model, registry=REGISTRY, ollama_url=None, workers=WORKERS, chunk=CHUNK):
        parsed = parse_name(model)
        if parsed is None:
            raise NotApplicable(f"nama model '{model}' bukan dari registry Ollama")
        self.model = model
        self.repo, self.tag = parsed
        self.registry = registry.rstrip("/")
        self.ollama_url = (ollama_url or config.OLLAMA_URL).rstrip("/")
        self.workers, self.chunk = workers, chunk
        self.blobs_dir = None

    # --- HTTP ----------------------------------------------------------------
    def _client(self, timeout=60.0):
        # Penyimpanan sertifikat sistem (bukan bawaan certifi), supaya tetap
        # jalan di komputer dengan antivirus/proxy yang memeriksa HTTPS.
        return httpx.Client(
            verify=ssl.create_default_context(),
            timeout=httpx.Timeout(timeout, connect=30.0),
            # identity: byte yang diterima harus persis byte berkas (cocok dengan Content-Range).
            headers={"User-Agent": "itsme-setup/1.0", "Accept-Encoding": "identity"},
        )

    def _blob_url(self, digest):
        return f"{self.registry}/v2/{self.repo}/blobs/{digest}"

    def _direct_url(self, client, digest):
        """Alamat unduhan sebenarnya: registry mengalihkan ke CDN dengan tautan bertanda tangan."""
        url = self._blob_url(digest)
        with client.stream("GET", url, follow_redirects=False) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, resp.headers.get("location", ""))
                if url.startswith("https:") and not target.startswith("https:"):
                    raise _Retry("registry mengalihkan ke alamat tanpa https")
                return target
            if resp.status_code == 200:
                return url  # registry melayani berkasnya sendiri
            raise httpx.TransportError(f"registry menjawab HTTP {resp.status_code}")

    def _small_blob(self, client, blob, limit=16 * 1024 * 1024):
        """Unduh berkas kecil ke memori dan cocokkan sha256-nya."""
        if blob.size > limit:
            raise ValueError("berkas terlalu besar untuk dibaca ke memori")
        last = None
        for attempt in range(4):
            try:
                resp = client.get(self._direct_url(client, blob.digest), follow_redirects=True)
                if resp.status_code == 200 and hashlib.sha256(resp.content).hexdigest() == blob.hex:
                    return resp.content
                last = f"HTTP {resp.status_code}" if resp.status_code != 200 else "sha256 tidak cocok"
            except (httpx.HTTPError, _Retry) as exc:
                last = str(exc) or type(exc).__name__
            time.sleep(2 * (attempt + 1))
        raise _Retry(f"gagal mengunduh {blob.digest[:19]}: {last}")

    # --- manifest ------------------------------------------------------------
    def manifest(self, client):
        """Daftar berkas model (config + layer) untuk varian yang dipakai Ollama di sini."""
        for attempt in range(3):
            try:
                resp = client.get(
                    f"{self.registry}/v2/{self.repo}/manifests/{self.tag}",
                    headers={"Accept": f"{LIST_TYPE}, {MANIFEST_TYPE}"},
                )
                if resp.status_code < 500 and resp.status_code != 429:
                    break
            except httpx.TransportError:
                if attempt == 2:
                    raise
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        if resp.status_code == 404:
            raise NotApplicable(f"model {self.model} tidak ditemukan di registry Ollama")
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise NotApplicable("format manifest tidak dikenal")
        if data.get("mediaType") == LIST_TYPE or "manifests" in data:
            children = data.get("manifests") or []
            child = next(
                (c for runner in RUNNERS for c in children if str(c.get("runner", "")).lower() == runner),
                None,
            )
            if not child or not _DIGEST.match(str(child.get("digest", ""))):
                raise NotApplicable("tidak ada varian GGUF untuk komputer ini")
            # Seperti Ollama: manifest varian diambil sebagai berkas lewat alamat blobs.
            child_blob = _Blob(child["digest"], 0, MANIFEST_TYPE)
            child_bytes = self._small_blob(client, child_blob, limit=4 * 1024 * 1024)
            data = json.loads(child_bytes)
            if not isinstance(data, dict):
                raise NotApplicable("format manifest tidak dikenal")
            self.child = (child_blob, child_bytes)
        else:
            self.child = None
        blobs = []
        for item in [data.get("config")] + list(data.get("layers") or []):
            if not isinstance(item, dict):
                raise NotApplicable("format manifest tidak dikenal")
            digest, size = str(item.get("digest", "")), item.get("size")
            if not _DIGEST.match(digest) or not isinstance(size, int) or size < 0:
                raise NotApplicable("format manifest tidak dikenal")
            if item.get("mediaType") == TENSOR_TYPE:
                raise NotApplicable("model berformat tensor diunduh Ollama dengan cara lain")
            if all(b.digest != digest for b in blobs):
                blobs.append(_Blob(digest, size, str(item.get("mediaType", ""))))
        self.config_blob = blobs[0]
        return blobs

    def check_version(self, client, config_bytes):
        """Model baru kadang butuh Ollama versi tertentu ("requires" di config)."""
        try:
            requires = str(json.loads(config_bytes).get("requires") or "")
        except (ValueError, AttributeError):
            return
        wanted = _version(requires)
        if wanted is None:
            return
        try:
            current = str(client.get(f"{self.ollama_url}/api/version", timeout=10).json().get("version", ""))
        except (httpx.HTTPError, ValueError, AttributeError):
            return  # ollama pull nanti memberi pesannya sendiri
        have = _version(current)
        if have is not None and have != (0, 0, 0) and have < wanted:
            raise TooOld(f"Model {self.model} butuh Ollama {requires} atau lebih baru (terpasang: {current}).")

    # --- folder model Ollama ---------------------------------------------------
    @staticmethod
    def _windows_env(name):
        try:
            import winreg
        except ImportError:
            return []
        values = []
        keys = (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        )
        for root, path in keys:
            try:
                with winreg.OpenKey(root, path) as key:
                    values.append(str(winreg.QueryValueEx(key, name)[0]))
            except OSError:
                pass
        return values

    def candidate_dirs(self):
        names = [os.environ.get("OLLAMA_MODELS")]
        if os.name == "nt":
            names += self._windows_env("OLLAMA_MODELS")
        names.append(str(Path.home() / ".ollama" / "models"))
        seen, dirs = set(), []
        for name in names:
            if not name or not name.strip():
                continue
            path = Path(os.path.expandvars(os.path.expanduser(name.strip().strip('"'))))
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                dirs.append(path)
        return dirs

    def find_blobs_dir(self, client):
        """Cari folder blobs yang benar-benar dipakai server Ollama.

        Caranya: taruh berkas penanda unik di tiap calon folder, lalu tanya
        server (HEAD /api/blobs) apakah ia melihat berkas itu.
        """
        content = b"itsme-probe-" + uuid.uuid4().hex.encode()
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        for models in self.candidate_dirs():
            blobs = models / "blobs"
            if not blobs.is_dir():
                continue
            probe = blobs / digest.replace(":", "-")
            try:
                probe.write_bytes(content)
            except OSError:
                continue  # tidak bisa ditulis
            try:
                resp = client.head(f"{self.ollama_url}/api/blobs/{digest}", timeout=10)
            except httpx.HTTPError:
                return None  # server Ollama tidak bisa dihubungi
            finally:
                try:
                    probe.unlink()
                except OSError:
                    pass
            if resp.status_code == 200:
                return blobs
        return None

    # --- unduhan ---------------------------------------------------------------
    def ensure(self, blob, part_dir):
        """Pastikan berkas ada di folder blobs dan sha256-nya cocok."""
        final = self.blobs_dir / f"sha256-{blob.hex}"
        big = blob.size >= 64 * 1024 * 1024
        if final.exists():
            if final.stat().st_size == blob.size and _sha256_file(final, blob.label if big else None) == blob.hex:
                if big:
                    print(f"    {blob.label}: sudah ada dan cocok.")
                return
            print(f"    {blob.label}: berkas lama rusak, diunduh ulang.")
            final.unlink()
        part = part_dir / f"sha256-{blob.hex}.part"
        state = part_dir / f"sha256-{blob.hex}.json"
        for attempt in (1, 2):
            _ChunkedDownload(self, blob, part, state).run()
            if _sha256_file(part, blob.label if big else None) == blob.hex:
                _replace(part, final)
                state.unlink(missing_ok=True)
                return
            print(f"    {blob.label}: sha256 tidak cocok, diunduh ulang dari awal.")
            part.unlink(missing_ok=True)
            state.unlink(missing_ok=True)
        raise _Retry(f"sha256 {blob.label} tetap tidak cocok setelah 2 kali unduh")

    def remaining_bytes(self, blobs, part_dir):
        total = 0
        for blob in blobs:
            final = self.blobs_dir / f"sha256-{blob.hex}"
            if final.exists() and final.stat().st_size == blob.size:
                continue
            done = set()
            if (part_dir / f"sha256-{blob.hex}.part").exists():
                done = _ChunkedDownload.load_done(part_dir / f"sha256-{blob.hex}.json", blob, self.chunk)
            total += blob.size - sum(min(self.chunk, blob.size - i * self.chunk) for i in done)
        return total

    def run(self):
        if sys.platform == "darwin":
            raise NotApplicable("di Mac, Ollama memilih varian model sendiri")
        with self._client() as client:
            blobs = self.manifest(client)
            config_bytes = self._small_blob(client, self.config_blob)
            self.check_version(client, config_bytes)
            self.blobs_dir = self.find_blobs_dir(client)
            if self.blobs_dir is None:
                raise NotApplicable("folder model Ollama tidak ditemukan atau tidak bisa ditulis")
            part_dir = self.blobs_dir.parent / "itsme-download"
            part_dir.mkdir(exist_ok=True)
            lock = _Lock(part_dir / "itsme.lock")
            lock.acquire()
            try:
                self._download_all(blobs, config_bytes, part_dir)
            finally:
                lock.release()
        try:
            part_dir.rmdir()
        except OSError:
            pass

    def _download_all(self, blobs, config_bytes, part_dir):
        need = self.remaining_bytes(blobs, part_dir)
        free = shutil.disk_usage(part_dir).free
        if need and free < need + SPARE:
            raise DiskFull(
                f"Ruang disk kurang di {self.blobs_dir.anchor or self.blobs_dir}: model butuh "
                f"{_gb(need + SPARE)} lagi, tersedia {_gb(free)}. Kosongkan ruang lalu jalankan setup lagi."
            )
        if need:
            print(f"    Perlu diunduh: {_gb(need)} (bisa dilanjutkan kalau terputus).")

        # Berkas kecil yang sudah diunduh tidak perlu diambil lagi oleh Ollama.
        small = [(self.config_blob, config_bytes)] + ([self.child] if self.child else [])
        for blob, data in small:
            final = self.blobs_dir / f"sha256-{blob.hex}"
            if not final.exists():
                tmp = part_dir / f"sha256-{blob.hex}.tmp"
                tmp.write_bytes(data)
                _replace(tmp, final)
        for blob in sorted(blobs, key=lambda b: b.size):
            self.ensure(blob, part_dir)


class _ChunkedDownload:
    """Unduhan satu berkas dalam potongan paralel yang diperiksa ketat.

    Tiap potongan wajib dijawab HTTP 206 dengan Content-Range yang persis
    sama dengan yang diminta; selain itu jawabannya dibuang. Potongan yang
    selesai dicatat di berkas .json supaya unduhan bisa dilanjutkan.
    """

    def __init__(self, fetcher, blob, part, state):
        self.f, self.blob, self.part, self.state = fetcher, blob, part, state
        self.chunk = fetcher.chunk
        self.count = max(1, -(-blob.size // self.chunk))
        self.lock = threading.Lock()
        self.url_lock = threading.Lock()
        self.stop = threading.Event()
        self.url = None
        self.next_index = 0
        self.done = set()
        self.progress = None

    def bounds(self, index):
        start = index * self.chunk
        return start, min(start + self.chunk, self.blob.size) - 1

    @staticmethod
    def load_done(state, blob, chunk):
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        if not isinstance(data, dict):
            return set()
        if data.get("digest") != blob.digest or data.get("size") != blob.size or data.get("chunk") != chunk:
            return set()
        count = max(1, -(-blob.size // chunk))
        return {i for i in data.get("done") or [] if isinstance(i, int) and 0 <= i < count}

    def _save(self):
        tmp = self.state.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"digest": self.blob.digest, "size": self.blob.size, "chunk": self.chunk, "done": sorted(self.done)}),
            encoding="utf-8",
        )
        _replace(tmp, self.state)

    def _url(self, client, stale=None):
        with self.url_lock:
            if self.url is None or self.url == stale:
                self.url = None
                self.url = self.f._direct_url(client, self.blob.digest)
            return self.url

    def _take(self):
        with self.lock:
            while self.next_index < self.count and self.next_index in self.done:
                self.next_index += 1
            if self.next_index >= self.count:
                return None
            index = self.next_index
            self.next_index += 1
            return index

    def _pause(self, seconds):
        if self.stop.wait(seconds):
            raise _Stopped()

    def run(self):
        if self.blob.size == 0:
            self.part.write_bytes(b"")
            return
        if self.part.exists() and self.state.exists():
            self.done = self.load_done(self.state, self.blob, self.chunk)
            size = self.part.stat().st_size
            if size > self.blob.size or any(self.bounds(i)[1] >= size for i in self.done):
                self.done = set()  # catatan tidak sesuai isi berkas: mulai lagi
        if not self.done:
            self.part.write_bytes(b"")
            self.state.unlink(missing_ok=True)
        if len(self.done) == self.count:
            return
        done_bytes = sum(self.bounds(i)[1] - self.bounds(i)[0] + 1 for i in self.done)
        if self.blob.size >= 64 * 1024 * 1024:
            self.progress = _Progress(self.blob.label, self.blob.size, done_bytes)
        try:
            error = self._parallel()
            if isinstance(error, _RangeIgnored):
                self._sequential()
            elif error is not None:
                raise error
        finally:
            if self.progress:
                self.progress.finish()

    def _parallel(self):
        """Jalankan pekerja; kembalikan kesalahan terpenting (atau None)."""
        workers = min(self.f.workers, self.count)
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = [pool.submit(self._worker) for _ in range(workers)]
        errors = []
        try:
            for future in futures:
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - dikumpulkan, dilaporkan setelah semua berhenti
                    errors.append(exc)
        except KeyboardInterrupt:
            self.stop.set()
            raise
        finally:
            pool.shutdown(wait=True)
        real = [e for e in errors if not isinstance(e, _RangeIgnored)]
        return real[0] if real else (errors[0] if errors else None)

    def _worker(self):
        try:
            with self.f._client() as client, open(self.part, "r+b") as out:
                while not self.stop.is_set():
                    index = self._take()
                    if index is None:
                        return
                    self._fetch(client, out, index)
                    with self.lock:
                        self.done.add(index)
                        self._save()
        except _Stopped:
            return  # pekerja lain yang bermasalah; kesalahannya dilaporkan olehnya
        except BaseException:
            self.stop.set()  # hentikan pekerja lain secepatnya
            raise

    def _fetch(self, client, out, index):
        start, end = self.bounds(index)
        pos, stalls, bad = start, 0, 0
        whole = start == 0 and end == self.blob.size - 1
        while pos <= end:
            if self.stop.is_set():
                raise _Stopped()
            before = pos
            try:
                url = self._url(client)
                with client.stream("GET", url, headers={"Range": f"bytes={pos}-{end}"}, follow_redirects=True) as resp:
                    if resp.status_code in _EXPIRED:
                        self._url(client, stale=url)
                        raise httpx.TransportError(f"HTTP {resp.status_code}")
                    if resp.status_code == 200 and not (whole and pos == 0):
                        raise _RangeIgnored()
                    if resp.status_code == 206:
                        expected = f"bytes {pos}-{end}/{self.blob.size}"
                        if resp.headers.get("content-range", "").strip() != expected:
                            raise _Retry("Content-Range tidak sesuai")
                    elif resp.status_code != 200:
                        raise httpx.TransportError(f"HTTP {resp.status_code}")
                    out.seek(pos)
                    for data in resp.iter_bytes(1024 * 1024):
                        if pos + len(data) > end + 1:
                            raise _Retry("server mengirim data berlebih")
                        out.write(data)
                        pos += len(data)
                        if self.progress:
                            self.progress.add(len(data))
                        if self.stop.is_set():
                            raise _Stopped()
                if pos <= end:
                    raise httpx.TransportError("koneksi terputus")
            except _Retry:
                # Jawaban aneh: buang seluruh potongan ini dan ulangi dari awal.
                if self.progress:
                    self.progress.add(start - pos)
                pos, bad = start, bad + 1
                if bad > 5:
                    raise
                self._pause(min(2**bad, 30))
            except (httpx.HTTPError, OSError) as exc:
                if isinstance(exc, OSError) and not isinstance(exc, (ConnectionError, TimeoutError)):
                    raise  # masalah disk, bukan jaringan
                stalls = 0 if pos > before else stalls + 1
                if stalls > MAX_STALLS:
                    raise _Retry(f"koneksi terus gagal: {exc}") from exc
                self._pause(min(2 ** max(stalls, 1), 30))
        out.flush()
        os.fsync(out.fileno())

    def _sequential(self):
        """Untuk server yang mengabaikan Range: satu aliran utuh dari awal."""
        print(f"\n    {self.blob.label}: server tidak mendukung unduhan sepotong, diunduh utuh.")
        self.stop.clear()
        self.done = set()
        self.state.unlink(missing_ok=True)
        stalls = 0
        with self.f._client() as client:
            while True:
                if self.progress:
                    self.progress.reset()
                pos = 0
                try:
                    url = self._url(client)
                    with client.stream("GET", url, follow_redirects=True) as resp:
                        if resp.status_code in _EXPIRED:
                            self._url(client, stale=url)
                            raise httpx.TransportError(f"HTTP {resp.status_code}")
                        if resp.status_code != 200:
                            raise httpx.TransportError(f"HTTP {resp.status_code}")
                        with open(self.part, "wb") as out:
                            for data in resp.iter_bytes(1024 * 1024):
                                if pos + len(data) > self.blob.size:
                                    raise httpx.TransportError("server mengirim data berlebih")
                                out.write(data)
                                pos += len(data)
                                if self.progress:
                                    self.progress.add(len(data))
                            out.flush()
                            os.fsync(out.fileno())
                    if pos == self.blob.size:
                        return
                    raise httpx.TransportError("koneksi terputus")
                except httpx.HTTPError as exc:
                    stalls += 1
                    if stalls > 6:
                        raise _Retry(f"unduhan utuh terus gagal: {exc}") from exc
                    time.sleep(min(2**stalls, 30))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    model = argv[0] if argv else config.OLLAMA_MODEL
    try:
        Fetcher(model).run()
    except NotApplicable as exc:
        print(f"    Pengunduh It's Me dilewati: {exc}.")
        return 2
    except TooOld as exc:
        print(f"    {exc}")
        return 3
    except DiskFull as exc:
        print(f"    {exc}")
        return 4
    except KeyboardInterrupt:
        print("\n    Dihentikan. Jalankan lagi untuk melanjutkan unduhan.")
        return 130
    except Exception as exc:  # noqa: BLE001 - setup lalu memakai ollama pull biasa
        print(f"\n    Pengunduh It's Me gagal ({type(exc).__name__}): {exc}")
        return 1
    print(f"    Semua berkas {model} lengkap dan sha256-nya cocok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
