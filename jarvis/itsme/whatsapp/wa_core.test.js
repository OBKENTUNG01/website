'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  LOGGED_OUT, setAsideAuth, onConnectionUpdate,
  chatKind, classify, parseSettings, applySettings, unwrap, bodyOf, mediaKind, isStale, makeSeen,
} = require('./wa_core');

function recorder() {
  const calls = [];
  return {
    calls,
    qr: (q) => calls.push(['qr', q]),
    open: () => calls.push(['open']),
    closed: (c) => calls.push(['closed', c]),
    loggedOut: () => calls.push(['loggedOut']),
  };
}

test('QR diteruskan apa adanya', () => {
  const io = recorder();
  onConnectionUpdate({ qr: '2@abc' }, io);
  assert.deepStrictEqual(io.calls, [['qr', '2@abc']]);
});

test('connection open dilaporkan', () => {
  const io = recorder();
  onConnectionUpdate({ connection: 'open' }, io);
  assert.deepStrictEqual(io.calls, [['open']]);
});

test('putus biasa membawa kode alasannya', () => {
  const io = recorder();
  onConnectionUpdate({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 515 } } } }, io);
  assert.deepStrictEqual(io.calls, [['closed', 515]]);
});

test('putus tanpa keterangan dianggap putus biasa, bukan keluar', () => {
  const io = recorder();
  onConnectionUpdate({ connection: 'close' }, io);
  onConnectionUpdate({ connection: 'close', lastDisconnect: {} }, io);
  onConnectionUpdate({ connection: 'close', lastDisconnect: { error: {} } }, io);
  assert.deepStrictEqual(io.calls, [['closed', undefined], ['closed', undefined], ['closed', undefined]]);
});

test('kode 401 berarti akun dicabut dari Perangkat tertaut', () => {
  assert.strictEqual(LOGGED_OUT, 401);
  const io = recorder();
  onConnectionUpdate({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 401 } } } }, io);
  assert.deepStrictEqual(io.calls, [['loggedOut']]);
});

test('update kosong atau connecting tidak melakukan apa pun', () => {
  const io = recorder();
  onConnectionUpdate(undefined, io);
  onConnectionUpdate({}, io);
  onConnectionUpdate({ connection: 'connecting' }, io);
  assert.deepStrictEqual(io.calls, []);
});

test('sesi lama dipindah ke samping, bukan dihapus', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-core-'));
  try {
    const auth = path.join(root, 'whatsapp');
    fs.mkdirSync(auth);
    fs.writeFileSync(path.join(auth, 'creds.json'), '{"x":1}');
    const moved = setAsideAuth(auth, new Date('2026-10-29T15:04:05.678Z'));
    assert.ok(!fs.existsSync(auth), 'folder sesi lama harus pergi dari tempatnya');
    assert.ok(moved.endsWith('.lama-2026-10-29T15-04-05-678Z'), moved);
    assert.strictEqual(fs.readFileSync(path.join(moved, 'creds.json'), 'utf8'), '{"x":1}');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('tanpa folder sesi tidak ada yang dipindah', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-core-'));
  try {
    assert.strictEqual(setAsideAuth(path.join(root, 'tidak-ada')), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// --- siapa yang dilayani -------------------------------------------------------------------------
const SELF = ['6283125645146', '99887766554433']; // nomor akun tertaut + LID-nya
const ctx = (over = {}) => ({
  self: SELF, owner: '6283125645146', open: false, guests: new Set(), block: new Set(), ...over,
});
const dm = (jid, nums, fromMe = false) => ({ jid, fromMe, nums });

test('jenis chat: hanya percakapan satu lawan satu yang dilayani', () => {
  assert.strictEqual(chatKind('6281234567890@s.whatsapp.net'), 'user');
  assert.strictEqual(chatKind('99887766554433@lid'), 'user');
  assert.strictEqual(chatKind('120363025246125486@g.us'), 'group');
  assert.strictEqual(chatKind('status@broadcast'), 'status');
  assert.strictEqual(chatKind('12345@broadcast'), 'broadcast');
  assert.strictEqual(chatKind('120363@newsletter'), 'newsletter');
  assert.strictEqual(chatKind(''), 'user');
  assert.strictEqual(chatKind(undefined), 'user');
});

test('chat ke diri sendiri (dari HP pemilik) dilayani sebagai pemilik, juga lewat LID', () => {
  assert.strictEqual(classify(dm('6283125645146@s.whatsapp.net', ['6283125645146'], true), ctx()), 'owner');
  assert.strictEqual(classify(dm('99887766554433@lid', ['99887766554433'], true), ctx()), 'owner');
});

test('pesan yang pemilik tulis ke ORANG LAIN dari HP-nya tidak dibalas AI', () => {
  assert.strictEqual(classify(dm('6281234567890@s.whatsapp.net', ['6281234567890'], true), ctx({ open: true })), 'ignore');
});

test('mode tertutup: orang lain diam, mode terbuka: orang lain jadi tamu', () => {
  const orang = dm('6281234567890@s.whatsapp.net', ['6281234567890']);
  assert.strictEqual(classify(orang, ctx()), 'ignore');
  assert.strictEqual(classify(orang, ctx({ open: true })), 'guest');
});

test('nomor tamu terdaftar dilayani walau mode terbuka mati', () => {
  const orang = dm('6281234567890@s.whatsapp.net', ['6281234567890']);
  assert.strictEqual(classify(orang, ctx({ guests: new Set(['6281234567890']) })), 'guest');
});

test('daftar blokir menang atas mode terbuka dan atas daftar tamu', () => {
  const orang = dm('6281234567890@s.whatsapp.net', ['6281234567890']);
  assert.strictEqual(classify(orang, ctx({ open: true, block: new Set(['6281234567890']) })), 'ignore');
  assert.strictEqual(classify(orang, ctx({
    guests: new Set(['6281234567890']), block: new Set(['6281234567890']),
  })), 'ignore');
});

test('pengirim yang punya dua penanda (LID + nomor asli) tetap kena blokir lewat nomor aslinya', () => {
  const orang = dm('55512345@lid', ['55512345', '6281234567890']);
  assert.strictEqual(classify(orang, ctx({ open: true, block: new Set(['6281234567890']) })), 'ignore');
});

test('grup, siaran, status, dan saluran TIDAK PERNAH dilayani, siapa pun pengirimnya', () => {
  for (const jid of ['120363025246125486@g.us', 'status@broadcast', '555@broadcast', '120363@newsletter']) {
    for (const fromMe of [false, true]) {
      const c = ctx({ open: true, guests: new Set(['6281234567890']) });
      assert.strictEqual(classify(dm(jid, ['6281234567890', '6283125645146', jid.split('@')[0]], fromMe), c), 'ignore',
        `${jid} fromMe=${fromMe}`);
    }
  }
});

test('nomor pemilik yang berbeda dari akun tertaut tetap dilayani sebagai pemilik', () => {
  const c = ctx({ owner: '6285111222333' });
  assert.strictEqual(classify(dm('6285111222333@s.whatsapp.net', ['6285111222333']), c), 'owner');
});

test('tanpa nomor pemilik, tidak ada pengirim yang dikira pemilik', () => {
  assert.strictEqual(classify(dm('x@s.whatsapp.net', ['62812']), ctx({ owner: '' })), 'ignore');
});

test('pengaturan awal dibaca dari lingkungan, nomor dibersihkan dari tanda baca', () => {
  const s = parseSettings({ WA_OPEN: '1', WA_GUESTS: '+62812-3456-7890, 6285', WA_BLOCK: '0811;0822 0833' });
  assert.strictEqual(s.open, true);
  assert.deepStrictEqual([...s.guests], ['6281234567890', '6285']);
  assert.deepStrictEqual([...s.block], ['0811', '0822', '0833']);
  const kosong = parseSettings({});
  assert.strictEqual(kosong.open, false);
  assert.strictEqual(kosong.guests.size + kosong.block.size, 0);
  assert.strictEqual(parseSettings({ WA_OPEN: '0' }).open, false);
});

test('perubahan langsung hanya mengubah yang disebut', () => {
  const awal = parseSettings({ WA_OPEN: '0', WA_GUESTS: '111', WA_BLOCK: '222' });
  const a = applySettings(awal, { open: true });
  assert.strictEqual(a.open, true);
  assert.deepStrictEqual([...a.guests], ['111']);
  assert.deepStrictEqual([...a.block], ['222']);
  const b = applySettings(a, { block: ['+333', '444'] });
  assert.deepStrictEqual([...b.block], ['333', '444']);
  assert.strictEqual(b.open, true);
  const c = applySettings(b, { open: false, guests: [] });
  assert.strictEqual(c.open, false);
  assert.strictEqual(c.guests.size, 0);
  const d = applySettings(c, { open: 'ya', block: 'bukan-daftar' }); // salah tipe: diabaikan
  assert.strictEqual(d.open, false);
  assert.deepStrictEqual([...d.block], ['333', '444']);
  assert.deepStrictEqual([...awal.block], ['222'], 'pengaturan lama tidak boleh berubah');
});

// --- isi pesan -----------------------------------------------------------------------------------
test('teks dibaca dari semua bentuk pesan teks', () => {
  assert.strictEqual(bodyOf({ conversation: ' halo ' }), 'halo');
  assert.strictEqual(bodyOf({ extendedTextMessage: { text: 'balasan' } }), 'balasan');
  assert.strictEqual(bodyOf({ imageMessage: { caption: 'foto ini' } }), 'foto ini');
  assert.strictEqual(bodyOf({ videoMessage: { caption: 'video ini' } }), 'video ini');
  assert.strictEqual(bodyOf({ documentMessage: { caption: 'berkas ini' } }), 'berkas ini');
  assert.strictEqual(bodyOf({ stickerMessage: {} }), '');
  assert.strictEqual(bodyOf(undefined), '');
  assert.strictEqual(bodyOf(null), '');
});

test('chat dengan pesan sementara (disappearing) tetap terbaca', () => {
  const wrapped = { ephemeralMessage: { message: { extendedTextMessage: { text: 'rahasia sementara' } } } };
  assert.strictEqual(bodyOf(wrapped), 'rahasia sementara');
  const dobel = { deviceSentMessage: { message: wrapped } };
  assert.strictEqual(bodyOf(dobel), 'rahasia sementara');
  assert.deepStrictEqual(unwrap({ conversation: 'x' }), { conversation: 'x' });
});

test('pembungkus tanpa isi tidak membuat perulangan tanpa henti', () => {
  assert.doesNotThrow(() => unwrap({ ephemeralMessage: {} }));
  const loop = {};
  loop.ephemeralMessage = { message: loop };
  assert.doesNotThrow(() => unwrap(loop));
});

test('lampiran yang tidak bisa dibaca AI dikenali, stiker diabaikan', () => {
  assert.strictEqual(mediaKind({ imageMessage: {} }), 'gambar');
  assert.strictEqual(mediaKind({ videoMessage: {} }), 'video');
  assert.strictEqual(mediaKind({ audioMessage: { ptt: true } }), 'audio');
  assert.strictEqual(mediaKind({ documentMessage: {} }), 'dokumen');
  assert.strictEqual(mediaKind({ ephemeralMessage: { message: { audioMessage: {} } } }), 'audio');
  assert.strictEqual(mediaKind({ stickerMessage: {} }), null);
  assert.strictEqual(mediaKind({ conversation: 'halo' }), null);
  assert.strictEqual(mediaKind(undefined), null);
});

// --- pesan basi & ganda --------------------------------------------------------------------------
test('pesan lama (terkirim saat It\'s Me mati) dikenali basi', () => {
  const sekarang = 1_800_000_000;
  assert.strictEqual(isStale(sekarang - 30, sekarang, 600), false);
  assert.strictEqual(isStale(sekarang - 601, sekarang, 600), true);
  assert.strictEqual(isStale({ low: sekarang - 3600, high: 0, unsigned: true }, sekarang, 600), true);
  assert.strictEqual(isStale(String(sekarang - 5), sekarang, 600), false);
});

test('cap waktu yang tidak terbaca tidak membuang pesan', () => {
  for (const t of [undefined, null, 0, 'abc', NaN, {}]) assert.strictEqual(isStale(t, 1_800_000_000, 600), false, String(t));
});

test('id pesan yang sama tidak diproses dua kali, dan catatan tidak tumbuh tanpa batas', () => {
  const seen = makeSeen(3);
  assert.strictEqual(seen('a'), false);
  assert.strictEqual(seen('a'), true);
  assert.strictEqual(seen('b'), false);
  assert.strictEqual(seen('c'), false);
  assert.strictEqual(seen('d'), false); // 'a' tersingkir
  assert.strictEqual(seen('a'), false);
  assert.strictEqual(seen('d'), true);
  assert.strictEqual(seen(undefined), false);
  assert.strictEqual(seen(''), false);
});
