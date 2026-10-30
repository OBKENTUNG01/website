// Jembatan WhatsApp untuk It's Me (Baileys).
// Masuk (stdin, satu JSON per baris): {"jid","text"} / {"jid","file",...} untuk dikirim;
//   {"cmd":"logout"} = lepas akun dari "Perangkat tertaut", simpan sesi lama, minta QR baru;
//   {"cmd":"set","open":bool,"block":[nomor],"guests":[nomor]} = ubah siapa yang dilayani, langsung
//   (tanpa memutus sambungan WhatsApp).
// Keluar (stdout, satu JSON per baris): {"jid","text","guest","file","media"} = pesan masuk
//   ("media" = lampiran tamu yang tidak dibaca: gambar/video/audio/dokumen);
//   {"qr":"<PNG base64>"} = QR login; {"state":"open"|"qr"|"close"|"logged_out","me":"62.."}
//   = keadaan sambungan (dibaca whatsapp.py supaya /wa bisa bilang yang sebenarnya).
// QR juga dicetak ke stderr, jadi muncul di jendela It's Me.
// Siapa yang dilayani diputuskan di wa_core.js (classify): pemilik, tamu, atau diabaikan.
const readline = require('readline');
const fs = require('fs');
const path = require('path');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const { default: makeWASocket, useMultiFileAuthState, downloadMediaMessage } =
  require('@whiskeysockets/baileys');
const {
  setAsideAuth, onConnectionUpdate, classify, parseSettings, applySettings, bodyOf, mediaKind, unwrap,
  isStale, makeSeen,
} = require('./wa_core');

const owner = (process.env.WA_OWNER || '').replace(/\D/g, '');
// Siapa yang dilayani selain pemilik: tamu terdaftar, atau semua nomor (open), minus daftar blokir.
// Bisa diubah saat jalan lewat perintah {"cmd":"set"}.
let settings = parseSettings(process.env);
// Pesan tamu yang lebih tua dari ini (terkirim saat It's Me mati) tidak dibalas: terlambat berjam-jam
// lebih membingungkan daripada tidak dibalas.
const GUEST_MAX_AGE_S = 600;
const seenGuest = makeSeen(500);
const authDir = process.env.WA_AUTH || './auth';
// Berkas masuk disimpan di sini; batas ukuran berlaku untuk terima & kirim.
const filesDir = process.env.WA_FILES || './wa_files';
const maxBytes = (parseInt(process.env.WA_MAX_MB || '100', 10) || 100) * 1024 * 1024;
const maxMB = Math.round(maxBytes / 1048576);
const sent = new Set(); // id pesan yang kita kirim sendiri, supaya tidak dibalas lagi
let sock = null;

// Nomor dari sebuah JID: buang '@s.whatsapp.net' / '@lid' dan suffix ':device'.
function numOf(jid) {
  return String(jid || '').split('@')[0].split(':')[0].replace(/\D/g, '');
}

// Semua nomor yang mungkin menandai pengirim: WhatsApp 2024+ memakai LID, jadi
// satu kontak punya dua JID (nomor asli & @lid). remoteJid saja tidak cukup.
function numsOf(m) {
  return [m.key?.remoteJid, m.key?.remoteJidAlt, m.key?.senderPn, m.key?.participantPn]
    .map(numOf).filter(Boolean);
}

function selfNums() {
  return [sock?.user?.id, sock?.user?.lid].map(numOf).filter(Boolean);
}

// Satu jalur kirim untuk teks & berkas, termasuk siasat @lid (JID @lid kadang
// ditolak server, jadi dicoba ulang ke nomor sendiri). Siasat ini HANYA untuk chat ke diri sendiri:
// balasan untuk tamu tidak boleh dialihkan ke chat pemilik kalau JID tamunya ditolak.
async function sendBody(jid, body) {
  let r;
  try {
    r = await sock.sendMessage(jid, body);
  } catch (e) {
    const own = selfNums()[0];
    if (!own || !jid.endsWith('@lid') || !selfNums().includes(numOf(jid))) throw e;
    r = await sock.sendMessage(own + '@s.whatsapp.net', body);
  }
  if (r?.key?.id) {
    if (sent.size > 200) sent.clear();
    sent.add(r.key.id);
  }
  return r;
}

const sendText = (jid, text) => sendBody(jid, { text: String(text).slice(0, 60000) });

// Berkas masuk (gambar/video/audio/dokumen) diunduh ke filesDir kalau <= maxBytes.
// Mengembalikan keterangan berkas untuk Python, atau null kalau tidak ada/ditolak.
async function saveMedia(m, jid) {
  const msg = unwrap(m.message);
  const mm = msg.imageMessage || msg.videoMessage || msg.audioMessage || msg.documentMessage;
  if (!mm) return null;
  const kind = msg.imageMessage ? 'gambar' : msg.videoMessage ? 'video'
    : msg.audioMessage ? 'audio' : 'dokumen';
  if (Number(mm.fileLength || 0) > maxBytes) {
    await sendText(jid, `Berkasnya terlalu besar (maks ${maxMB} MB).`);
    return null;
  }
  const buf = await downloadMediaMessage(m, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage });
  if (buf.length > maxBytes) {
    await sendText(jid, `Berkasnya terlalu besar (maks ${maxMB} MB).`);
    return null;
  }
  fs.mkdirSync(filesDir, { recursive: true });
  const asli = msg.documentMessage?.fileName || `${kind}_${Date.now()}`;
  const aman = String(asli).replace(/[^A-Za-z0-9._-]/g, '_').slice(-80) || 'berkas';
  const fp = path.join(filesDir, `${Date.now()}_${aman}`);
  fs.writeFileSync(fp, buf);
  return { path: fp, name: asli, kind, size: buf.length };
}

const emit = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
let generation = 0; // start() menaikkannya; kejadian dari soket lama diabaikan
let resets = []; // waktu reset sesi terakhir (penjaga supaya tidak berputar tanpa henti)

// Yang dilakukan jembatan untuk tiap kejadian sambungan (pemetaannya ada di wa_core.js, teruji).
const io = {
  qr(qr) {
    qrcode.generate(qr, { small: true }, (t) => process.stderr.write(t + '\n'));
    QRCode.toBuffer(qr, { width: 600, margin: 1 })
      .then((b) => { emit({ qr: b.toString('base64') }); emit({ state: 'qr' }); })
      .catch((e) => process.stderr.write('gagal membuat QR: ' + e.message + '\n'));
  },
  open() {
    process.stderr.write('WhatsApp tersambung.\n');
    emit({ state: 'open', me: selfNums()[0] || '' });
  },
  closed(code) {
    emit({ state: 'close', code: code === undefined ? null : code });
    setTimeout(start, 3000);
  },
  loggedOut() {
    // Dulu: process.exit(1) -> Python menyalakan ulang dengan sesi basi yang sama, ditolak lagi,
    // lalu menyerah TANPA PERNAH mengirim QR. Sekarang: sesi lama disimpan, mulai dari nol -> QR baru.
    process.stderr.write("WhatsApp keluar dari Perangkat tertaut; sesi lama disimpan, QR baru dibuat.\n");
    emit({ state: 'logged_out' });
    resetSession();
  },
};

function resetSession() {
  const now = Date.now();
  resets = resets.filter((t) => now - t < 60000);
  resets.push(now);
  if (resets.length > 3) {
    process.stderr.write('sesi dikosongkan berulang-ulang; berhenti supaya tidak berputar.\n');
    process.exit(1);
  }
  try {
    const moved = setAsideAuth(authDir);
    if (moved) process.stderr.write('sesi lama disimpan di ' + moved + '\n');
  } catch (e) {
    process.stderr.write('gagal memindahkan sesi lama: ' + e.message + '\n');
    process.exit(1);
  }
  setTimeout(start, 1000);
}

// {"cmd":"logout"}: lepas dari "Perangkat tertaut" di HP; WhatsApp membalas dengan close(401),
// yang memicu io.loggedOut() -> resetSession() -> QR baru.
async function logoutAndRelink() {
  try {
    await sock.logout();
  } catch (e) {
    process.stderr.write('logout WhatsApp gagal (' + e.message + '); mengosongkan sesi lokal saja.\n');
    try { sock.end(undefined); } catch (_) { /* sudah mati */ }
    emit({ state: 'logged_out' });
    resetSession();
  }
}

async function start() {
  const gen = ++generation;
  if (sock) { try { sock.end(undefined); } catch (_) { /* sudah mati */ } }
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  if (gen !== generation) return; // start() lain sudah mengambil alih
  sock = makeWASocket({ auth: state, browser: ["It's Me", 'Chrome', '1.0'] });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (update) => {
    if (gen !== generation) return;
    onConnectionUpdate(update, io);
  });
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (gen !== generation || type !== 'notify') return;
    const self = selfNums();
    for (const m of messages) {
      const jid = m.key?.remoteJid || '';
      if (!jid) continue;
      // Pemilik / tamu / diabaikan (grup, siaran, status, saluran, nomor diblokir atau tak dikenal).
      // Pesan pemilik ke ORANG LAIN dari HP-nya (fromMe) juga diabaikan - hanya chat ke diri sendiri.
      const role = classify({ jid, fromMe: !!m.key?.fromMe, nums: numsOf(m) }, { self, owner, ...settings });
      if (role === 'ignore') continue;
      if (m.key?.fromMe && sent.has(m.key.id)) continue; // jangan balas balasan sendiri
      const guest = role === 'guest';
      let file = null;
      let media = null;
      if (guest) {
        // Tamu: pesan basi (terkirim saat It's Me mati) dan pesan ganda dilewati. Lampiran dari orang
        // asing TIDAK diunduh (bisa memenuhi disk dan tidak dibaca AI); hanya dicatat jenisnya.
        if (isStale(m.messageTimestamp, Date.now() / 1000, GUEST_MAX_AGE_S)) continue;
        if (seenGuest(m.key?.id)) continue;
        media = mediaKind(m.message);
      } else {
        try {
          file = await saveMedia(m, jid);
        } catch (e) {
          process.stderr.write('gagal unduh media WhatsApp: ' + e.message + '\n');
        }
      }
      const text = bodyOf(m.message);
      if (text || file || media) emit({ jid, text, guest, file, media });
    }
  });
}

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  let out;
  try { out = JSON.parse(line); } catch { return; }
  if (out.cmd === 'logout') { if (sock) logoutAndRelink(); return; }
  if (out.cmd === 'set') {
    settings = applySettings(settings, out);
    process.stderr.write(`pengaturan tamu diperbarui: terbuka=${settings.open}, diblokir=${settings.block.size}, tamu=${settings.guests.size}\n`);
    return;
  }
  if (!sock || !out.jid) return;
  try {
    if (out.file) {
      if (!fs.existsSync(out.file)) {
        process.stderr.write('berkas tidak ada: ' + out.file + '\n');
        return;
      }
      if (fs.statSync(out.file).size > maxBytes) {
        await sendText(out.jid, `Berkas terlalu besar untuk dikirim (maks ${maxMB} MB).`);
        return;
      }
      await sendBody(out.jid, {
        document: fs.readFileSync(out.file),
        fileName: out.name || path.basename(out.file),
        mimetype: out.mimetype || 'application/octet-stream',
        caption: out.caption || undefined,
      });
      return;
    }
    await sendText(out.jid, out.text);
  } catch (e) {
    process.stderr.write('gagal kirim WhatsApp: ' + e.message + '\n');
  }
});

start().catch((e) => {
  process.stderr.write('WhatsApp: gagal menyala: ' + e.message + '\n');
  process.exit(1);
});
