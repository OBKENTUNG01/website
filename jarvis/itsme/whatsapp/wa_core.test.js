'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { LOGGED_OUT, setAsideAuth, onConnectionUpdate } = require('./wa_core');

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
