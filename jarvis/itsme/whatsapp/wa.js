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
const authDir = process.env.WA_AUTH || './auth';
const sent = new Set(); // id pesan yang kita kirim sendiri, supaya tidak dibalas lagi
let sock = null;

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
    for (const m of messages) {
      const jid = m.key?.remoteJid || '';
      // Hanya chat dengan nomor pemilik. Bot memakai akun WhatsApp pemilik sendiri,
      // jadi pesannya datang sebagai fromMe (chat ke diri sendiri) — bukan diabaikan.
      const num = jid.endsWith('@s.whatsapp.net')
        ? jid.split('@')[0]
        : (m.key?.remoteJidAlt || '').split('@')[0];
      if (num !== owner) continue;
      if (m.key?.fromMe && sent.has(m.key.id)) continue; // jangan balas balasan sendiri
      const text = bodyOf(m);
      if (text) process.stdout.write(JSON.stringify({ jid, text }) + '\n');
    }
  });
}

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  let out;
  try { out = JSON.parse(line); } catch { return; }
  if (!sock || !out.jid) return;
  try {
    const r = await sock.sendMessage(out.jid, { text: String(out.text).slice(0, 60000) });
    if (r?.key?.id) {
      if (sent.size > 200) sent.clear();
      sent.add(r.key.id);
    }
  } catch (e) {
    process.stderr.write('gagal kirim WhatsApp: ' + e.message + '\n');
  }
});

start();
