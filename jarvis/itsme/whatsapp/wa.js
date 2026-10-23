// Jembatan WhatsApp untuk It's Me (Baileys).
// Masuk: baris JSON {"jid":"..","text":".."} di stdin dari Python.
// Keluar: baris JSON {"jid":"..","text":".."} di stdout untuk dikirim.
// QR login dicetak ke stderr, jadi muncul di jendela It's Me.
const readline = require('readline');
const qrcode = require('qrcode-terminal');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } =
  require('@whiskeysockets/baileys');

const owner = (process.env.WA_OWNER || '').replace(/\D/g, '');
const authDir = process.env.WA_AUTH || './auth';
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
    if (qr) qrcode.generate(qr, { small: true }, (t) => process.stderr.write(t + '\n'));
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
      if (m.key?.fromMe || !jid.endsWith('@s.whatsapp.net')) continue; // abaikan grup
      if (jid.split('@')[0] !== owner) continue;                        // hanya pemilik
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
    await sock.sendMessage(out.jid, { text: String(out.text).slice(0, 60000) });
  } catch (e) {
    process.stderr.write('gagal kirim WhatsApp: ' + e.message + '\n');
  }
});

start();
