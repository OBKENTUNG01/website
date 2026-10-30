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

// --- siapa yang dilayani -----------------------------------------------------------------------
const digits = (x) => String(x || '').replace(/\D/g, '');
const numberSet = (list) => new Set((list || []).map(digits).filter(Boolean));

// Jenis chat dari JID-nya: hanya 'user' (percakapan satu lawan satu) yang pernah dibalas.
function chatKind(jid) {
  const j = String(jid || '');
  if (j === 'status@broadcast') return 'status';
  if (j.endsWith('@g.us')) return 'group';
  if (j.endsWith('@broadcast')) return 'broadcast';
  if (j.endsWith('@newsletter')) return 'newsletter';
  return 'user'; // @s.whatsapp.net, @lid
}

// Pesan ini dari siapa, dan dilayani atau tidak? -> 'owner' | 'guest' | 'ignore'
//   msg: { jid, fromMe, nums: [nomor yang bisa menandai pengirim] }
//   ctx: { self: [nomor akun tertaut], owner: '62..', open: bool, guests: Set, block: Set }
// Grup, siaran, status, dan saluran TIDAK PERNAH dilayani (siapa pun pengirimnya): di mode terbuka,
// AI yang menjawab di grup atau status orang adalah kekacauan.
function classify(msg, ctx) {
  if (chatKind(msg.jid) !== 'user') return 'ignore';
  const nums = msg.nums || [];
  if (msg.fromMe) {
    // Pesan keluar dari HP pemilik: layani HANYA chat ke diri sendiri.
    return nums.some((n) => ctx.self.includes(n)) ? 'owner' : 'ignore';
  }
  if (ctx.owner && nums.includes(ctx.owner)) return 'owner';
  if (nums.some((n) => ctx.block.has(n))) return 'ignore';
  if (ctx.open || nums.some((n) => ctx.guests.has(n))) return 'guest';
  return 'ignore'; // nomor tak dikenal: diam
}

// Pengaturan awal dari lingkungan proses, dan perubahan langsung dari Python ({"cmd":"set",...}).
function parseSettings(env) {
  const list = (v) => String(v || '').split(/[,\s;]+/);
  return { open: env.WA_OPEN === '1', guests: numberSet(list(env.WA_GUESTS)), block: numberSet(list(env.WA_BLOCK)) };
}

function applySettings(current, cmd) {
  const next = { open: current.open, guests: current.guests, block: current.block };
  if (typeof cmd.open === 'boolean') next.open = cmd.open;
  if (Array.isArray(cmd.guests)) next.guests = numberSet(cmd.guests);
  if (Array.isArray(cmd.block)) next.block = numberSet(cmd.block);
  return next;
}

// --- isi pesan -----------------------------------------------------------------------------------
// Pembungkus yang dipakai WhatsApp untuk pesan sementara ("disappearing"), sekali-lihat, dan pesan
// yang disalin dari perangkat lain. Tanpa dibuka, teks di dalamnya tidak terbaca dan pesannya diam-diam
// diabaikan.
const WRAPPERS = ['ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'viewOnceMessageV2Extension',
  'documentWithCaptionMessage', 'deviceSentMessage'];

function unwrap(message) {
  let m = message || {};
  for (let i = 0; i < 6; i++) {
    const wrapper = WRAPPERS.find((k) => m[k] && m[k].message);
    if (!wrapper) break;
    m = m[wrapper].message;
  }
  return m;
}

function bodyOf(message) {
  const m = unwrap(message);
  return String(m.conversation
    || (m.extendedTextMessage && m.extendedTextMessage.text)
    || (m.imageMessage && m.imageMessage.caption)
    || (m.videoMessage && m.videoMessage.caption)
    || (m.documentMessage && m.documentMessage.caption)
    || '').trim();
}

// Jenis lampiran yang tidak bisa dibaca AI (hanya untuk tamu). Stiker sengaja tidak termasuk: dikirim
// sebagai tanggapan santai, bukan pertanyaan.
function mediaKind(message) {
  const m = unwrap(message);
  if (m.imageMessage) return 'gambar';
  if (m.videoMessage) return 'video';
  if (m.audioMessage) return 'audio';
  if (m.documentMessage) return 'dokumen';
  return null;
}

// --- pesan basi & ganda --------------------------------------------------------------------------
// messageTimestamp bisa angka atau objek Long ({low, high}). Tidak terbaca -> dianggap tidak basi.
function isStale(timestamp, nowSeconds, maxAgeSeconds) {
  const t = timestamp && typeof timestamp === 'object' ? Number(timestamp.low) : Number(timestamp);
  if (!Number.isFinite(t) || t <= 0) return false;
  return nowSeconds - t > maxAgeSeconds;
}

// Pencatat id pesan yang sudah diproses (terbatas): true = sudah pernah, abaikan.
function makeSeen(limit = 500) {
  const ids = new Set();
  return (id) => {
    if (!id) return false;
    if (ids.has(id)) return true;
    if (ids.size >= limit) ids.delete(ids.values().next().value);
    ids.add(id);
    return false;
  };
}

module.exports = {
  LOGGED_OUT, setAsideAuth, onConnectionUpdate,
  chatKind, classify, parseSettings, applySettings, unwrap, bodyOf, mediaKind, isStale, makeSeen,
};
