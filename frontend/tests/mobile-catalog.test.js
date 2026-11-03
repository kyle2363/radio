import test from 'node:test';
import assert from 'node:assert/strict';
import { createMobilePairingControl, registerMobilePairingIPC } from '../electron/mobile-pairing.cjs';

const ready = { state: 'ready', revision: 7, song_count: 42, vector_count: 30, error: null };
const response = value => new Response(JSON.stringify(value));
function fixture(result = ready, overrides = {}) {
  const calls = [];
  const control = createMobilePairingControl({ enabled: true, isBackendAlive: () => true,
    request: async (path, options) => { calls.push({ path, ...options }); return response(result); }, ...overrides });
  return { control, calls };
}

test('catalog preparation is explicit, fixed-path, privileged and never touches playback', async () => {
  const { control, calls } = fixture({ ...ready, path: 'private/export', token: 'secret', model: {} });
  assert.equal(calls.length, 0);
  assert.deepEqual(await control.catalogStatus(), ready);
  assert.deepEqual(await control.prepareCatalog(), ready);
  assert.deepEqual(calls.map(x => [x.path, x.method]), [
    ['/api/mobile-hosting/catalog/status', 'GET'], ['/api/mobile-hosting/enable', 'POST'], ['/api/mobile-hosting/catalog/prepare', 'POST'],
  ]);
  for (const call of calls) {
    assert.match(call.headers['X-Radio-Mobile-Owner'], /^[\w-]{43}$/);
    assert.equal(call.redirect, 'error');
    assert.equal(call.body, undefined);
    assert.equal(call.signal.aborted, false);
  }
});

test('gateway enable must finish successfully before explicit catalog preparation', async () => {
  const calls = [];
  let finishEnable;
  const { control } = fixture(ready, { request: async path => {
    calls.push(path);
    if (path.endsWith('/enable')) await new Promise(resolve => { finishEnable = resolve; });
    return response(ready);
  } });
  const pending = control.prepareCatalog();
  await Promise.resolve();
  assert.deepEqual(calls, ['/api/mobile-hosting/enable']);
  finishEnable();
  assert.deepEqual(await pending, ready);
  assert.deepEqual(calls, ['/api/mobile-hosting/enable', '/api/mobile-hosting/catalog/prepare']);

  const failedCalls = [];
  const failed = fixture(ready, { request: async path => {
    failedCalls.push(path);
    return new Response('{}', { status: 503 });
  } });
  await assert.rejects(failed.control.prepareCatalog());
  assert.deepEqual(failedCalls, ['/api/mobile-hosting/enable'], 'Never prepare when the USB gateway failed to start');
});

test('catalog status preserves previous published counts during running and failure', async () => {
  for (const state of ['idle', 'running', 'ready', 'failed']) {
    const status = { ...ready, state, error: state === 'failed' ? 'catalog_prepare_failed' : null };
    assert.deepEqual(await fixture(status).control.catalogStatus(), status);
  }
  const idle = { state: 'idle', revision: null, song_count: 0, vector_count: 0, error: null };
  assert.deepEqual(await fixture(idle).control.catalogStatus(), idle);
});

test('malformed catalog status never crosses the renderer boundary', async () => {
  for (const patch of [
    { state: 'unknown' }, { revision: -1 }, { revision: 1.2 }, { revision: null },
    { song_count: -1 }, { vector_count: Infinity }, { error: 'secret path or token' }, { error: {} },
  ]) await assert.rejects(fixture({ ...ready, ...patch }).control.catalogStatus());
  const echoed = fixture(null, { request: async (_path, options) => response({ ...ready, error: options.headers['X-Radio-Mobile-Owner'] }) });
  await assert.rejects(echoed.control.catalogStatus());
});

test('catalog controls fail closed on ownership/lifetime and bounded stream checks', async () => {
  for (const override of [{ enabled: false }, { isBackendAlive: () => false }]) {
    const f = fixture(ready, override);
    await assert.rejects(f.control.prepareCatalog()); await assert.rejects(f.control.catalogStatus());
    assert.equal(f.calls.length, 0);
  }
  const f = fixture();
  await f.control.shutdown();
  await assert.rejects(f.control.prepareCatalog()); await assert.rejects(f.control.catalogStatus());
  const oversized = fixture(null, { request: async () => new Response('x'.repeat(65537)) });
  await assert.rejects(oversized.control.catalogStatus());
});

test('catalog IPC is available only to the approved main frame with zero arguments', async () => {
  const handlers = new Map(), calls = [];
  const frame = { url: 'http://localhost:3000/settings' }, contents = { mainFrame: frame };
  const window = { webContents: contents, isDestroyed: () => false };
  registerMobilePairingIPC({ handle: (channel, callback) => handlers.set(channel, callback) }, {
    prepareCatalog: () => calls.push('prepare'), catalogStatus: () => calls.push('status'), cancelCatalog: () => calls.push('cancel'),
  }, () => window);
  const good = { sender: contents, senderFrame: frame };
  for (const channel of ['mobile-catalog-prepare', 'mobile-catalog-status', 'mobile-catalog-cancel']) {
    await assert.rejects(async () => handlers.get(channel)(good, '/arbitrary/path'));
    await assert.rejects(async () => handlers.get(channel)({ ...good, sender: {} }));
    await assert.rejects(async () => handlers.get(channel)({ ...good, senderFrame: { url: frame.url } }));
    frame.url = 'https://attacker.example';
    await assert.rejects(async () => handlers.get(channel)(good));
    frame.url = 'http://localhost:3000/settings';
    await handlers.get(channel)(good);
  }
  assert.deepEqual(calls, ['prepare', 'status', 'cancel']);
});


test('cancel is explicit and progress accepts bounded counts without source paths', async () => {
  const progress = { completed: 3, total: 10, hashed: 2, skipped: 1 };
  const f = fixture({ ...ready, state: 'running', hash_progress: { ...progress, path: 'private.mp3' } });
  assert.deepEqual(await f.control.cancelCatalog(), { ...ready, state: 'running', hash_progress: progress });
  assert.deepEqual(f.calls.map(x => [x.path, x.method]), [['/api/mobile-hosting/catalog/cancel', 'POST']]);
  for (const bad of [{ ...progress, completed: 11 }, { ...progress, skipped: -1 }, { ...progress, hashed: 3 }]) {
    await assert.rejects(fixture({ ...ready, hash_progress: bad }).control.catalogStatus());
  }
  assert.equal((await fixture({ ...ready, state: 'failed', error: 'catalog_prepare_cancelled' }).control.catalogStatus()).error, 'catalog_prepare_cancelled');
});
