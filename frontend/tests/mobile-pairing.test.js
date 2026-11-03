import test from 'node:test';
import assert from 'node:assert/strict';
import { createMobilePairingControl, registerMobilePairingIPC, readBoundedControlJSON } from '../electron/mobile-pairing.cjs';

const jsonResponse = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });

function streamedResponse(chunks, contentLength) {
  let reads = 0, cancelled = 0;
  return {
    ok: true,
    headers: new Headers(contentLength == null ? {} : { 'Content-Length': String(contentLength) }),
    body: { getReader: () => ({
      read: async () => { const value = chunks[reads++]; return value === undefined ? { done: true } : { value, done: false }; },
      cancel: async () => { cancelled++; }, releaseLock() {},
    }) },
    json() { throw Error('Unbounded JSON parsing must never be used'); },
    counts: () => ({ reads, cancelled }),
  };
}

function fixture(overrides = {}) {
  const calls = [];
  const bundle = { protocol_version: 1, server_id: 'server-one', public_key_pin: 'a'.repeat(64), endpoint: 'http://127.0.0.1:18766', ticket_id: 'ticket-one', ticket_secret: 'one-time-secret', device_name: 'Phone', expires_at: Date.now() / 1000 + 300 };
  const control = createMobilePairingControl({ enabled: true, isBackendAlive: () => true,
    request: async (path, options) => {
      calls.push({ path, ...options });
      const data = path.endsWith('pairing-tickets') ? { ...bundle, token: 'must-not-leak' } : path.endsWith('devices')
        ? { devices: [{ device_id: 'phone-one', name: 'Phone', scopes: ['catalog:read'], expires_at: null, revoked: false, token: 'must-not-leak' }] } : { enabled: true, revoked: true };
      return jsonResponse(data);
    }, ...overrides });
  return { control, calls, bundle };
}

test('pairing is explicit, fixed-path, catalog-only and filters privileged response fields', async () => {
  const { control, calls, bundle } = fixture();
  assert.equal(calls.length, 0);
  assert.deepEqual(await control.listDevices(), { devices: [{ device_id: 'phone-one', name: 'Phone', scopes: ['catalog:read'], expires_at: null, revoked: false }] });
  assert.equal(calls.length, 1, 'Listing must not enable a gateway');
  assert.deepEqual(await control.createBundle('Phone'), bundle);
  assert.deepEqual(calls.slice(1).map(x => [x.path, x.method]), [
    ['/api/mobile-hosting/enable', 'POST'], ['/api/mobile-hosting/pairing-tickets', 'POST'],
  ]);
  assert.deepEqual(JSON.parse(calls[2].body), { device_name: 'Phone' });
  assert.equal(calls[2].headers.Origin, 'http://localhost:8765');
  assert.match(calls[2].headers['X-Radio-Mobile-Owner'], /^[a-zA-Z0-9_-]{43}$/);
  assert.equal(calls[2].redirect, 'error');
  assert.equal(JSON.stringify(bundle).includes(calls[2].headers['X-Radio-Mobile-Owner']), false);
  await control.revokeDevice('phone-one');
  assert.equal(calls.at(-1).path, '/api/mobile-hosting/devices/phone-one');
  assert.equal(calls.at(-1).method, 'DELETE');
});

test('parent capability travels only over inherited private pipe, never launch env or argv', () => {
  const { control } = fixture();
  let payload;
  const child = { stdin: { end: value => { payload = value; }, on() {} } };
  assert.deepEqual(control.launchOptions(), { stdio: ['pipe', 'pipe', 'pipe'], env: { RADIO_MOBILE_CONTROL_PIPE: '1' } });
  control.attach(child);
  const value = JSON.parse(payload);
  assert.deepEqual(Object.keys(value), ['protocol_version', 'capability']);
  assert.equal(value.protocol_version, 1);
  assert.match(value.capability, /^[a-zA-Z0-9_-]{43}$/);
});

test('strict arguments, ownership and development gates reject without network I/O', async () => {
  const { control, calls } = fixture();
  for (const name of ['', '   ', 'x'.repeat(81), 'Phone\nInjected', { url: 'https://other' }]) await assert.rejects(control.createBundle(name));
  for (const id of ['../escape', 'phone?token=x', {}, 'x'.repeat(129)]) await assert.rejects(control.revokeDevice(id));
  assert.equal(calls.length, 0);
  for (const overrides of [{ enabled: false }, { isBackendAlive: () => false }]) {
    const f = fixture(overrides);
    await assert.rejects(f.control.createBundle('Phone'));
    await assert.rejects(f.control.listDevices());
    assert.equal(f.calls.length, 0);
  }
});

test('shutdown waits for pending create, disables gateway and prevents later requests', async () => {
  let finishEnable;
  const order = [];
  const { control } = fixture({ request: async path => {
    order.push(path);
    if (path.endsWith('enable')) await new Promise(resolve => { finishEnable = resolve; });
    return jsonResponse(path.endsWith('pairing-tickets') ? { protocol_version: 1, server_id: 's', public_key_pin: 'a'.repeat(64), endpoint: 'http://127.0.0.1:18766', ticket_id: 't', ticket_secret: 'secret', device_name: 'Phone', expires_at: Date.now() / 1000 + 300 } : {});
  } });
  const create = control.createBundle('Phone');
  await Promise.resolve();
  const shutdown = control.shutdown();
  finishEnable();
  await Promise.all([create, shutdown]);
  assert.deepEqual(order, ['/api/mobile-hosting/enable', '/api/mobile-hosting/pairing-tickets', '/api/mobile-hosting/disable']);
  await assert.rejects(control.createBundle('Phone'));
  assert.equal(order.length, 3);
});

test('IPC only accepts approved main frame and exact argument counts', async () => {
  const handlers = new Map(), calls = [];
  const frame = { url: 'http://localhost:3000/settings' }, contents = { mainFrame: frame };
  const window = { webContents: contents, isDestroyed: () => false };
  registerMobilePairingIPC({ handle: (name, callback) => handlers.set(name, callback) }, {
    createBundle: async name => { calls.push(name); return {}; }, listDevices: async () => ({}), revokeDevice: async () => ({}),
  }, () => window);
  const good = { sender: contents, senderFrame: frame };
  for (const event of [{ ...good, sender: {} }, { ...good, senderFrame: { url: frame.url } }, { ...good, senderFrame: null }]) {
    await assert.rejects(async () => handlers.get('mobile-pairing-create')(event, 'Phone'));
  }
  frame.url = 'https://attacker.example';
  await assert.rejects(async () => handlers.get('mobile-pairing-create')(good, 'Phone'));
  frame.url = 'http://localhost:3000/settings';
  await assert.rejects(async () => handlers.get('mobile-pairing-create')(good, 'Phone', 'extra'));
  await assert.rejects(async () => handlers.get('mobile-pairing-devices')(good, 'extra'));
  await handlers.get('mobile-pairing-create')(good, 'Phone');
  assert.deepEqual(calls, ['Phone']);
});

test('backend failures expose a fixed error and never reflect credential-bearing diagnostics', async () => {
  const { control } = fixture({ request: async () => { throw Error('token=private-token'); } });
  await assert.rejects(control.createBundle('Phone'), { message: 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.' });
});

test('unexpected parent capability echoes cannot cross the renderer boundary', async () => {
  const { control } = fixture({ request: async (path, options) => jsonResponse(path.endsWith('devices')
    ? { devices: [{ device_id: 'one', name: options.headers['X-Radio-Mobile-Owner'], scopes: ['catalog:read'], expires_at: null, revoked: false }] } : {}) });
  await assert.rejects(control.listDevices());
});

test('oversized declared control response is cancelled before any body read', async () => {
  const response = streamedResponse([Buffer.from('{"devices":[]}')], 65537);
  const { control } = fixture({ request: async () => response });
  await assert.rejects(control.listDevices(), { message: 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.' });
  assert.deepEqual(response.counts(), { reads: 0, cancelled: 1 });
});

test('chunked and understated control responses count actual bytes and cancel oversize', async () => {
  for (const length of [undefined, 2]) {
    const response = streamedResponse([Buffer.alloc(32768, 32), Buffer.alloc(32769, 32), Buffer.from('private-token')], length);
    const { control } = fixture({ request: async () => response });
    await assert.rejects(control.listDevices(), { message: 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.' });
    assert.deepEqual(response.counts(), { reads: 2, cancelled: 1 });
  }
});

test('bounded reader accepts exact byte limit and rejects malformed JSON without reflection', async () => {
  const bytes = Buffer.from(JSON.stringify({ devices: [], padding: 'é'.repeat(32754) }));
  const padding = Buffer.alloc(65536 - bytes.byteLength, 32);
  const response = streamedResponse([bytes.subarray(0, 11), bytes.subarray(11), padding]);
  const result = await readBoundedControlJSON(response, new AbortController().signal);
  assert.deepEqual(result.devices, []);
  assert.equal(response.counts().cancelled, 0);
  const malformed = fixture({ request: async (_path, options) => streamedResponse([Buffer.from(`{"private":"${options.headers['X-Radio-Mobile-Owner']}`)]) });
  await assert.rejects(malformed.control.listDevices(), { message: 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.' });
});

test('bounded reader aborts and cancels a stalled response before parsing', async () => {
  let cancelled = 0;
  const abort = new AbortController();
  const response = { headers: new Headers(), body: { getReader: () => ({
    read: () => new Promise(() => {}), cancel: async () => { cancelled++; }, releaseLock() {},
  }) } };
  const pending = readBoundedControlJSON(response, abort.signal);
  abort.abort(Error('capability=must-not-reflect'));
  await assert.rejects(pending, { message: 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.' });
  assert.equal(cancelled, 1);
  const expired = streamedResponse([Buffer.from('{"devices":[]}')]);
  await assert.rejects(readBoundedControlJSON(expired, new AbortController().signal, performance.now() - 1),
    { message: 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.' });
  assert.equal(expired.counts().reads, 0);
});
