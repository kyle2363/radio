import test from 'node:test';
import assert from 'node:assert/strict';
import { PlaybackSessionController, createPlaybackApi } from '../src/api/playback.js';

function setup({ electron = false, saved = null, clientId = 'page', connectTimeout = 8000,
  handoffTimeout = 20000, handoffRetryDelay = 1000, socketFactory = null } = {}) {
  const calls = [], storage = new Map(saved ? [['radio.playback.session', JSON.stringify(saved)]] : []);
  const api = { request: async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    if (path === '' || path === '/desktop/claim') return { session: { id: 's', mode: electron ? 'vlc' : 'browser', revision: 0, status: 'idle', items: [], output_client_id: 'page' }, owner_key: 'secret' };
    return { id: 's', mode: 'browser', revision: 1, status: 'interrupted', active_index: 0, items: [{ item_id: 'one' }], output_client_id: 'old' };
  }};
  const engine = { loadSnapshot: () => {}, dispose: () => {}, activate: async () => true, applyControl: async () => true };
  const sockets = [];
  const controller = new PlaybackSessionController({ api, clientId, electron, connectTimeout, handoffTimeout, handoffRetryDelay,
    engineFactory: options => { engine.options = options; return engine; },
    storage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    socketFactory: socketFactory || (() => { const socket = { send: data => sockets.push(JSON.parse(data)), close() {} }; return socket; }), reconnectDelay: 0 });
  return { controller, calls, engine, sockets, storage };
}

test('playback requests time out without disguising timeouts as terminal authorization errors', async () => {
  const api = createPlaybackApi({ timeoutMs: 5, fetchImpl: async (_url, { signal }) =>
    new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true })) });
  await assert.rejects(api.request('POST', '/s/lease'), error => error.name === 'TimeoutError' && !error.status);
});

test('playback request preserves caller cancellation', async () => {
  const caller = new AbortController();
  const reason = new Error('caller cancelled');
  const api = createPlaybackApi({ timeoutMs: 1000, fetchImpl: async (_url, { signal }) => {
    const pending = new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
    caller.abort(reason);
    return pending;
  } });
  await assert.rejects(api.request('GET', '/s', undefined, 'owner', caller.signal), error => error === reason);
});

test('local pause and seek do not wait for a lease request during transport loss', async () => {
  const { controller, engine } = setup();
  await controller.start();
  controller.state.session = { id: 's', mode: 'browser', instance_id: 'i', output_client_id: 'page' };
  const applied = [];
  engine.pause = () => applied.push('pause');
  engine.seek = () => applied.push('seek');
  controller.ensureLease = () => { throw new Error('unexpected network dependency'); };
  try {
    await controller.localControl('pause');
    await controller.localControl('seek', { position_ms: 1000 });
    assert.deepEqual(applied, ['pause', 'seek']);
  } finally { controller.dispose(); }
});

test('new instance obtains its own permit when an old instance renewal is pending', async () => {
  const { controller, engine } = setup();
  await controller.start();
  const old = { id: 's', mode: 'browser', instance_id: 'old', output_client_id: 'page' };
  const next = { ...old, instance_id: 'next' };
  controller.state.session = old;
  const requested = [], granted = [];
  let finishOld;
  controller.api.request = async (_method, _path, body) => {
    requested.push(body.instance_id);
    if (body.instance_id === 'old') await new Promise(resolve => { finishOld = resolve; });
    return { instance_id: body.instance_id, client_id: 'page', lease_remaining_ms: 12000 };
  };
  engine.grantLease = lease => { granted.push(lease.instance_id); return true; };
  try {
    const first = controller.ensureLease(old, true);
    controller.state.session = next;
    const second = controller.ensureLease(next);
    finishOld();
    await Promise.all([first, second]);
    assert.deepEqual(requested, ['old', 'next']);
    assert.deepEqual(granted, ['next']);
    assert.equal(controller.leaseInstanceId, 'next');
  } finally { controller.dispose(); }
});

test('browser creates its own idle session; capability only in storage/hello', async () => {
  const { controller, calls, sockets, storage } = setup();
  await controller.start();
  assert.deepEqual(calls[0].body, { client_id: 'page' });
  assert.equal(calls.some(call => call.path.includes('/next')), false);
  controller.socket.onopen();
  assert.deepEqual(sockets[0], { session_id: 's', owner_key: 'secret', client_id: 'page' });
  assert.equal(JSON.parse(storage.get('radio.playback.session')).owner_key, 'secret');
  controller.dispose();
});

test('Electron explicitly claims desktop and restored sessions await explicit reactivation', async () => {
  const desktop = setup({ electron: true });
  await desktop.controller.start();
  assert.equal(desktop.calls[0].path, '/desktop/claim');
  assert.ok(desktop.controller.socket);
  assert.deepEqual(desktop.controller.requestHeaders(), { 'X-Radio-Playback-Session': 's', 'X-Radio-Playback-Owner': 'secret' });
  desktop.controller.dispose();
  const restored = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  await restored.controller.start();
  assert.equal(restored.calls[0].path, '/s');
  assert.equal(restored.calls.some(call => call.path.endsWith('/reactivate')), false);
  await restored.controller.reactivate();
  assert.equal(restored.calls.at(-1).body.client_id, 'page');
  restored.controller.dispose();
});

test('handoff retries identical command after quiesce ack; never creates a new session', async () => {
  const { controller, calls, engine } = setup();
  await controller.start();
  let attempts = 0;
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    return path.endsWith('/handoff') && attempts++ === 0 ? { awaiting_confirmation: true } : { id: 's', revision: 2, mode: 'vlc', status: 'paused', items: [] };
  };
  await controller.switchOutput('vlc');
  const first = calls.at(-1);
  await engine.options.onQuiesceAck({ command_id: first.body.command_id, instance_id: 'i', client_id: 'page', position_ms: 5 });
  assert.deepEqual(calls.at(-1).body, first.body);
  assert.equal(calls.at(-2).path, '/s/ack');
  assert.equal(calls.at(-1).path, '/s/handoff');
  controller.dispose();
});

test('transport sends owner header and cookie credentials without URL secrets', async () => {
  let request;
  const api = createPlaybackApi({ baseUrl: 'https://radio.test', fetchImpl: async (url, options) => { request = { url, options }; return { ok: true, json: async () => ({}) }; } });
  await api.request('POST', '/s/pause', undefined, 'secret');
  assert.equal(request.url, 'https://radio.test/api/playback/sessions/s/pause');
  assert.equal(request.options.headers['X-Radio-Playback-Owner'], 'secret');
  assert.equal(request.options.credentials, 'include');
});

test('legacy controls stay scoped and plan completion only refreshes', async () => {
  const { controller, calls, engine } = setup();
  await controller.start();
  let sought;
  engine.seek = position => { sought = position; };
  await controller.routeLegacy('/api/play/seek', { method: 'POST', body: { ms: 120 } });
  assert.equal(sought, 120);
  assert.notEqual(calls.at(-1).path, '/s/seek');
  await controller.routeLegacy('/api/queue/replay');
  assert.equal(calls.at(-1).path, '/s/replay');
  assert.ok(calls.at(-1).body.command_id);
  await engine.options.onPlanComplete();
  assert.equal(calls.at(-1).path, '/s');
  assert.equal(calls.at(-1).method, 'GET');
  await assert.rejects(controller.routeLegacy('/api/queue/unknown'), /not available/);
  controller.dispose();
});

test('browser controls acquire a producer lease and apply pause, seek, and volume locally', async () => {
  const { controller, calls, engine } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  const order = [];
  engine.grantLease = (_lease, startedAt) => order.push(['lease', startedAt]);
  engine.loadSnapshot = () => order.push(['load']);
  engine.pause = () => order.push(['pause']);
  engine.play = async () => order.push(['resume']);
  engine.activate = async () => order.push(['activate']);
  engine.seek = position => order.push(['seek', position]);
  engine.setVolume = volume => order.push(['volume', volume]);
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    if (path.endsWith('/lease')) return { instance_id: 'instance', client_id: 'page', lease_remaining_ms: 12000 };
    return live({ instance_id: 'instance' });
  };
  await controller.start();
  await controller.activate();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(order.slice(0, 2).map(entry => entry[0]), ['lease', 'load']);
  assert.equal(calls.some(call => call.path === '/s/lease'), true);
  await controller.routeLegacy('/api/play/pause');
  await controller.routeLegacy('/api/play/seek', { method: 'POST', body: { ms: 400 } });
  await controller.routeLegacy('/api/play/volume', { method: 'POST', body: { volume: 33 } });
  assert.ok(order.some(entry => entry[0] === 'pause'));
  assert.ok(order.some(entry => entry[0] === 'seek' && entry[1] === 400));
  assert.ok(order.some(entry => entry[0] === 'volume' && entry[1] === 33));
  controller.dispose();
});

test('Play on an idle browser session reserves the next plan and preserves the click gesture', async () => {
  const { controller, calls, engine } = setup();
  await controller.start();
  controller.state.session = { id: 's', revision: 0, mode: 'browser', status: 'idle',
    output_client_id: 'page', active_index: 0, items: [] };
  let localPlayCalls = 0;
  engine.play = async () => { localPlayCalls++; };
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    return { id: 's', revision: 1, mode: 'browser', status: 'playing', output_client_id: 'page',
      active_index: 0, items: [{ item_id: 'new-item', terminal: false }] };
  };

  await controller.routeLegacy('/api/play/resume');

  assert.equal(calls.at(-1).path, '/s/next');
  assert.equal(controller.gesture, true);
  assert.equal(localPlayCalls, 0);
  controller.dispose();
});

test('control socket loss preserves the active buffered media element', async () => {
  const { controller, engine } = setup();
  await controller.start();
  const snapshots = [];
  engine.loadSnapshot = state => snapshots.push(state);
  const playing = { id: 's', revision: 1, status: 'playing', mode: 'browser', output_client_id: 'page', items: [{ item_id: 'one', terminal: false }] };
  controller.gesture = true;
  controller.accept(playing);
  const count = snapshots.length;
  controller.socket.onclose();
  assert.equal(snapshots.length, count);
  assert.equal(controller.getState().needsReconnect, false);
  assert.equal(controller.getState().connected, false);
  controller.dispose();
});

test('reloading idle or live old browser grants takes the same session explicitly', async () => {
  for (const status of ['idle', 'playing']) {
    const { controller, calls } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
    controller.api.request = async (method, path, body, owner) => {
      calls.push({ method, path, body, owner });
      return { id: 's', revision: 2, mode: 'browser', status, output_client_id: 'old', items: status === 'idle' ? [] : [{ item_id: 'one' }] };
    };
    await controller.start();
    assert.equal(controller.getState().needsReconnect, true);
    await controller.reactivate();
    assert.equal(calls.at(-1).path, '/s/handoff');
    assert.equal(calls.at(-1).body.target_client_id, 'page');
    assert.equal(calls.filter(call => call.path === '').length, 0);
    controller.dispose();
  }
});

test('exhausted plan advances rather than resuming a terminal item', async () => {
  const { controller, calls } = setup();
  await controller.start();
  controller.accept({ id: 's', revision: 1, mode: 'browser', output_client_id: 'page', status: 'idle', items: [{ terminal: true }], active_index: 1 });
  await controller.play();
  assert.equal(calls.at(-1).path, '/s/next');
  controller.dispose();
});

test('disposed async creation closes only its unused session and cannot overwrite another mount', async () => {
  const { controller, storage, calls } = setup();
  let resolve;
  controller.api.request = (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    if (path === '') return new Promise(done => { resolve = done; });
    return Promise.resolve({});
  };
  const starting = controller.start();
  controller.dispose();
  storage.set('radio.playback.session', JSON.stringify({ session_id: 'new', owner_key: 'new-secret' }));
  resolve({ session: { id: 'unused', revision: 0 }, owner_key: 'unused-secret' });
  await starting;
  assert.equal(JSON.parse(storage.get('radio.playback.session')).session_id, 'new');
  assert.equal(calls.at(-1).path, '/unused/close');
  assert.equal(calls.at(-1).owner, 'unused-secret');
});

test('invalid saved ownership clears only saved capability and waits for explicit retry', async () => {
  const { controller, storage, calls } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  const normal = controller.api.request;
  controller.api.request = async (...args) => { if (args[1] === '/s') throw Object.assign(new Error('No access'), { status: 403 }); return normal(...args); };
  await assert.rejects(controller.start());
  assert.equal(storage.has('radio.playback.session'), false);
  assert.equal(controller.getState().retryNeeded, true);
  assert.equal(calls.length, 0);
  await controller.retry();
  assert.equal(calls[0].path, '');
  controller.dispose();
});

test('network media failure locally silences then rechecks authenticated snapshot', async () => {
  const { controller, calls, engine } = setup();
  await controller.start();
  let silenced = false;
  engine.loadSnapshot = state => { if (state.output_client_id === null) silenced = true; };
  await engine.options.onError({ code: 'media_network', message: 'Network unavailable' });
  assert.equal(silenced, true);
  assert.equal(calls.at(-1).path, '/s');
  assert.equal(controller.gesture, false);
  controller.dispose();
});

test('offline preserves browser output while desktop controller loss never takes over output', async () => {
  const browser = setup();
  await browser.controller.start();
  browser.controller.gesture = true;
  browser.controller.offline();
  assert.equal(browser.controller.gesture, true);
  assert.equal(browser.controller.getState().needsReconnect, false);
  await Promise.resolve();
  browser.controller.dispose();
  const desktop = setup({ electron: true });
  await desktop.controller.start();
  desktop.controller.socket.onclose();
  assert.equal(desktop.controller.getState().needsReconnect, false);
  assert.equal(desktop.calls.some(call => call.path.endsWith('/handoff')), false);
  desktop.controller.dispose();
});

test('disposed desktop claim does not close the shared native listening session', async () => {
  const { controller, storage, calls } = setup({ electron: true });
  let resolve;
  controller.api.request = (method, path) => { calls.push({ method, path }); return new Promise(done => { resolve = done; }); };
  const started = controller.start();
  controller.dispose();
  resolve({ session: { id: 'shared', revision: 1 }, owner_key: 'shared-owner' });
  await started;
  assert.equal(calls.length, 1);
  assert.equal(storage.size, 0);
});

const live = (changes = {}) => ({ id: 's', revision: 3, mode: 'browser', status: 'playing',
  output_client_id: 'page', active_index: 0, items: [{ item_id: 'one', terminal: false }], position_ms: 900, ...changes });

test('restored storage never reuses a copied producer identity', async () => {
  const { controller, calls, engine } = setup({ saved: { session_id: 's', owner_key: 'secret', client_id: 'page' }, clientId: null });
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    return live();
  };
  await controller.start();
  assert.notEqual(controller.clientId, 'page');
  assert.equal(engine.options.clientId, controller.clientId);
  assert.equal(controller.getState().needsReconnect, true);
  assert.equal(calls.filter(call => call.path === '').length, 0);
  controller.dispose();
});

test('a copied session capability cannot make duplicated document modules share producer ID', async () => {
  const saved = { session_id: 's', owner_key: 'secret', client_id: 'copied' };
  const first = setup({ saved, clientId: null });
  const { PlaybackSessionController: DuplicateController } = await import('../src/api/playback.js?duplicate-tab');
  const second = new DuplicateController({ clientId: null, api: { request: async () => ({}) }, storage: {
    getItem: () => JSON.stringify(saved), setItem() {}, removeItem() {},
  }, engineFactory: () => ({ loadSnapshot() {}, dispose() {} }), reconnectDelay: 0 });
  assert.notEqual(first.controller.clientId, 'copied');
  assert.notEqual(second.clientId, 'copied');
  assert.notEqual(first.controller.clientId, second.clientId);
  first.controller.dispose();
  second.dispose();
});

test('the audio engine always receives the page identity the station granted', () => {
  const { controller, engine } = setup({ clientId: null });
  assert.equal(typeof controller.clientId, 'string');
  assert.ok(controller.clientId.length > 0);
  assert.equal(engine.options.clientId, controller.clientId);
  controller.dispose();
});

test('a socket stuck before its upgrade is abandoned so a retry can happen', async () => {
  const sockets = [];
  const { controller } = setup({ connectTimeout: 40, socketFactory: () => {
    const socket = { readyState: 0, closes: 0, send() {}, onopen: null, onmessage: null, onclose: null, onerror: null,
      close() { this.closes++; this.readyState = 3; this.onclose?.(); } };
    sockets.push(socket);
    return socket;
  } });
  await controller.start();
  assert.equal(sockets.length, 1);
  await new Promise(resolve => setTimeout(resolve, 90));
  assert.equal(sockets[0].closes, 1);
  assert.equal(controller.socket, null);
  controller.dispose();
});

test('a live grant owned by this page reactivates locally without a handoff', async () => {
  const { controller, calls, engine } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  let activated = 0;
  engine.activate = async () => { activated++; return true; };
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    return live();
  };
  await controller.start();
  await controller.reactivate();
  assert.equal(calls.some(call => call.path.endsWith('/handoff')), false);
  assert.equal(calls.some(call => call.path.endsWith('/reactivate')), false);
  assert.equal(activated, 1);
  assert.equal(controller.getState().needsReconnect, false);
  controller.dispose();
});

test('a reconnected control socket reconciles without restarting buffered audio', async () => {
  const { controller, engine } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  controller.api.request = async () => live({ revision: 5 });
  await controller.start();
  let loads = 0, activated = 0;
  engine.loadSnapshot = () => { loads++; };
  engine.activate = async () => { activated++; return true; };
  controller.gesture = true;
  controller.accept(live({ revision: 4 }));
  controller.socket.onclose();
  assert.equal(controller.getState().needsReconnect, false);
  loads = 0;
  controller.connect();
  controller.socket.onopen();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(controller.getState().needsReconnect, false);
  assert.equal(controller.getState().error, null);
  assert.equal(loads, 1);
  assert.equal(activated, 0);
  controller.dispose();
});

test('reconnecting a paused grant restores control without starting audio', async () => {
  const { controller, engine } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  controller.api.request = async () => live({ status: 'paused' });
  await controller.start();
  let activated = 0;
  engine.activate = async () => { activated++; return true; };
  controller.suspended = true;
  await controller.resume();
  assert.equal(activated, 0);
  assert.equal(controller.getState().needsReconnect, false);
  assert.equal(controller.getState().session.status, 'paused');
  controller.dispose();
});

test('handoff deadline cancels or reconciles using the original command identity', async () => {
  const { controller } = setup({ handoffTimeout: 40 });
  await controller.start();
  const attempts = [];
  controller.api.request = async (_method, path, body) => {
    attempts.push({ path, body });
    if (path.endsWith('/handoff/cancel')) return { ...live({ mode: 'vlc' }), cancelled: true };
    return { awaiting_confirmation: true };
  };
  await controller.switchOutput('vlc');
  assert.equal(controller.getState().pendingHandoff, true);
  await new Promise(resolve => setTimeout(resolve, 90));
  assert.equal(controller.getState().pendingHandoff, false);
  assert.equal(attempts.at(-1).path, '/s/handoff/cancel');
  assert.deepEqual(attempts.at(-1).body, attempts[0].body);
  controller.dispose();
});

test('pending handoff polls the server until an expired producer can be reclaimed', async () => {
  const { controller, calls } = setup({ handoffTimeout: 200, handoffRetryDelay: 10 });
  await controller.start();
  let count = 0;
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    count++;
    return count < 3 ? { awaiting_confirmation: true } : { ...live({ mode: 'browser', revision: 4 }), duplicate: true };
  };
  await controller.switchOutput('browser');
  const original = calls.at(-1).body;
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(count >= 3, `expected repeated handoff calls, saw ${count}`);
  assert.ok(calls.filter(call => call.path.endsWith('/handoff')).every(call => JSON.stringify(call.body) === JSON.stringify(original)));
  assert.equal(controller.getState().pendingHandoff, false);
  controller.dispose();
});
