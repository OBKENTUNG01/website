'use strict';
// Logika murni jembatan WhatsApp (tanpa Baileys, tanpa jaringan) supaya bisa diuji:
//   node --test wa_core.test.js     (juga dijalankan dari tests/test_wa_core_js.py)
const fs = require('fs');

// DisconnectReason.loggedOut di Baileys: akun dicabut dari "Perangkat tertaut" (HP / WhatsApp).
const LOGGED_OUT = 401;

// Pindahkan folder sesi ke samping (TIDAK dihapus), supaya penautan berikutnya mulai dari nol
// dan WhatsApp membuat QR baru. Kembalikan jalur baru, atau null kalau folder tidak ada.
function setAsideAuth(authDir, now = new Date()) {
  if (!fs.existsSync(authDir)) return null;
  const target = `${authDir}.lama-${now.toISOString().replace(/[:.]/g, '-')}`;
  fs.renameSync(authDir, target);
  return target;
}

// Terjemahkan satu connection.update dari Baileys menjadi tindakan; `io` dipasok pemanggil:
//   io.qr(teksQr)  io.open()  io.closed(kode)  io.loggedOut()
function onConnectionUpdate(update, io) {
  const { connection, lastDisconnect, qr } = update || {};
  if (qr) io.qr(qr);
  if (connection === 'open') io.open();
  if (connection === 'close') {
    const code = lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
      ? lastDisconnect.error.output.statusCode : undefined;
    if (code === LOGGED_OUT) io.loggedOut();
    else io.closed(code);
  }
}

module.exports = { LOGGED_OUT, setAsideAuth, onConnectionUpdate };
