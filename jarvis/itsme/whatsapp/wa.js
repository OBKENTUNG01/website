// Jembatan WhatsApp untuk It's Me (Baileys).
// Masuk: baris JSON {"jid":"..","text":".."} di stdin dari Python.
// Keluar: baris JSON {"jid":"..","text":".."} di stdout untuk dikirim.
// QR login dicetak ke stderr, jadi muncul di jendela It's Me.
const readline = require('readline');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } =
  require('@whiskeysockets/baileys');

const owner = (process.env.WA_OWNER || '').replace(/\D/g, '');
// Tamu: nomor lain yang boleh chat. WA_OPEN=1 -> semua nomor dilayani.
const guests = new Set((process.env.WA_GUESTS || '').split(/[,\s]+/)
  .map((s) => s.replace(/\D/g, '')).filter(Boolean));
const open = process.env.WA_OPEN === '1';
const authDir = process.env.WA_AUTH || './auth';
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
  sock.ev.on('messages.upsert', ({ messages, type }) => {
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
      if (text) process.stdout.write(JSON.stringify({ jid, text, guest }) + '\n');
    }
  });
}

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  let out;
  try { out = JSON.parse(line); } catch { return; }
  if (!sock || !out.jid) return;
  try {
    const body = { text: String(out.text).slice(0, 60000) };
    let r;
    try {
      r = await sock.sendMessage(out.jid, body);
    } catch (e) {
      // JID @lid bisa ditolak server untuk pengiriman; coba nomor sendiri.
      const own = selfNums()[0];
      if (!own || !out.jid.endsWith('@lid')) throw e;
      r = await sock.sendMessage(own + '@s.whatsapp.net', body);
    }
    if (r?.key?.id) {
      if (sent.size > 200) sent.clear();
      sent.add(r.key.id);
    }
  } catch (e) {
    process.stderr.write('gagal kirim WhatsApp: ' + e.message + '\n');
  }
});

start();
