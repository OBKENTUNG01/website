// Jembatan WhatsApp untuk It's Me (Baileys).
// Masuk: baris JSON {"jid":"..","text":".."} di stdin dari Python.
// Keluar: baris JSON {"jid":"..","text":".."} di stdout untuk dikirim.
// QR login dicetak ke stderr, jadi muncul di jendela It's Me.
const readline = require('readline');
const fs = require('fs');
const path = require('path');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, downloadMediaMessage } =
  require('@whiskeysockets/baileys');

const owner = (process.env.WA_OWNER || '').replace(/\D/g, '');
// Tamu: nomor lain yang boleh chat. WA_OPEN=1 -> semua nomor dilayani.
const guests = new Set((process.env.WA_GUESTS || '').split(/[,\s]+/)
  .map((s) => s.replace(/\D/g, '')).filter(Boolean));
const open = process.env.WA_OPEN === '1';
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

function bodyOf(m) {
  const msg = m.message || {};
  return (msg.conversation
    || msg.extendedTextMessage?.text
    || msg.imageMessage?.caption
    || msg.documentMessage?.caption
    || '').trim();
}

// Satu jalur kirim untuk teks & berkas, termasuk siasat @lid (JID @lid kadang
// ditolak server, jadi dicoba ulang ke nomor sendiri).
async function sendBody(jid, body) {
  let r;
  try {
    r = await sock.sendMessage(jid, body);
  } catch (e) {
    const own = selfNums()[0];
    if (!own || !jid.endsWith('@lid')) throw e;
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
  const msg = m.message || {};
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

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  sock = makeWASocket({ auth: state, browser: ["It's Me", 'Chrome', '1.0'] });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      qrcode.generate(qr, { small: true }, (t) => process.stderr.write(t + '\n'));
      QRCode.toBuffer(qr, { width: 600, margin: 1 })
        .then((b) => process.stdout.write(JSON.stringify({ qr: b.toString('base64') }) + '\n'))
        .catch((e) => process.stderr.write('gagal membuat QR: ' + e.message + '\n'));
    }
    if (connection === 'open') process.stderr.write('WhatsApp tersambung.\n');
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        process.stderr.write('WhatsApp keluar (logged out); hapus folder auth lalu nyalakan ulang.\n');
        process.exit(1);
      }
      setTimeout(start, 3000);
    }
  });
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    const self = selfNums();
    for (const m of messages) {
      const jid = m.key?.remoteJid || '';
      if (!jid) continue;
      const nums = numsOf(m);
      let guest;
      if (m.key?.fromMe) {
        // Pesan keluar dari HP pemilik: layani HANYA chat ke diri sendiri.
        // (dulu dibandingkan ke nomor pemilik saja, jadi gagal saat JID-nya @lid)
        if (!nums.some((n) => self.includes(n))) continue;
        if (sent.has(m.key.id)) continue; // jangan balas balasan sendiri
        guest = false;
      } else if (nums.includes(owner)) {
        guest = false;
      } else if (open || nums.some((n) => guests.has(n))) {
        guest = true;
      } else {
        continue; // nomor tak dikenal: diam (sama seperti bot Telegram)
      }
      const text = bodyOf(m);
      let file = null;
      try {
        file = await saveMedia(m, jid);
      } catch (e) {
        process.stderr.write('gagal unduh media WhatsApp: ' + e.message + '\n');
      }
      if (text || file) process.stdout.write(JSON.stringify({ jid, text, guest, file }) + '\n');
    }
  });
}

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  let out;
  try { out = JSON.parse(line); } catch { return; }
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
