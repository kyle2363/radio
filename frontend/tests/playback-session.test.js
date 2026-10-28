import test from 'node:test';
import assert from 'node:assert/strict';
import { PlaybackSessionController, createPlaybackApi } from '../src/api/playback.js';

function setup({ electron = false, saved = null, clientId = 'page', connectTimeout = 8000,
  handoffTimeout = 20000, socketFactory = null } = {}) {
  const calls = [], storage = new Map(saved ? [['radio.playback.session', JSON.stringify(saved)]] : []);
  const api = { request: async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    if (path === '' || path === '/desktop/claim') return { session: { id: 's', mode: electron ? 'vlc' : 'browser', revision: 0, status: 'idle', items: [], output_client_id: 'page' }, owner_key: 'secret' };
    return { id: 's', mode: 'browser', revision: 1, status: 'interrupted', active_index: 0, items: [{ item_id: 'one' }], output_client_id: 'old' };
  }};
  const engine = { loadSnapshot: () => {}, dispose: () => {}, activate: async () => true, applyControl: async () => true };
  const sockets = [];
  const controller = new PlaybackSessionController({ api, clientId, electron, connectTimeout, handoffTimeout,
    engineFactory: options => { engine.options = options; return engine; },
    storage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    socketFactory: socketFactory || (() => { const socket = { send: data => sockets.push(JSON.parse(data)), close() {} }; return socket; }), reconnectDelay: 0 });
  return { controller, calls, engine, sockets, storage };
}

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
  await controller.routeLegacy('/api/play/seek', { method: 'POST', body: { ms: 120 } });
  assert.equal(calls.at(-1).path, '/s/seek');
  assert.deepEqual(calls.at(-1).body, { position_ms: 120 });
  await engine.options.onPlanComplete();
  assert.equal(calls.at(-1).path, '/s');
  assert.equal(calls.at(-1).method, 'GET');
  await assert.rejects(controller.routeLegacy('/api/queue/unknown'), /not available/);
  controller.dispose();
});

test('socket loss silences immediately and snapshot refresh cannot restart audio', async () => {
  const { controller, engine } = setup();
  await controller.start();
  const snapshots = [];
  engine.loadSnapshot = state => snapshots.push(state);
  const playing = { id: 's', revision: 1, status: 'playing', mode: 'browser', output_client_id: 'page', items: [{ item_id: 'one', terminal: false }] };
  controller.gesture = true;
  controller.accept(playing);
  controller.socket.onclose();
  assert.equal(snapshots.at(-1).output_client_id, null);
  const count = snapshots.length;
  controller.accept({ ...playing, revision: 2 });
  assert.equal(snapshots.length, count);
  assert.equal(controller.getState().needsReconnect, true);
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

test('offline silences browser while desktop controller loss never takes over output', async () => {
  const browser = setup();
  await browser.controller.start();
  browser.controller.gesture = true;
  browser.controller.offline();
  assert.equal(browser.controller.gesture, false);
  assert.equal(browser.controller.getState().needsReconnect, true);
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

test('a reloaded page resumes with its stored grant instead of a foreign identity', async () => {
  const { controller, calls, engine } = setup({ saved: { session_id: 's', owner_key: 'secret', client_id: 'page' }, clientId: null });
  controller.api.request = async (method, path, body, owner) => {
    calls.push({ method, path, body, owner });
    return live();
  };
  await controller.start();
  assert.equal(controller.clientId, 'page');
  assert.equal(engine.options.clientId, 'page');
  assert.equal(controller.getState().needsReconnect, false);
  assert.equal(calls.filter(call => call.path === '').length, 0);
  controller.dispose();
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

test('a reconnected control socket resumes the granted page without a button', async () => {
  const { controller, engine } = setup({ saved: { session_id: 's', owner_key: 'secret' } });
  controller.api.request = async () => live();
  await controller.start();
  let loads = 0, activated = 0;
  engine.loadSnapshot = () => { loads++; };
  engine.activate = async () => { activated++; return true; };
  controller.gesture = true;
  controller.accept(live({ revision: 4 }));
  controller.socket.onclose();
  assert.equal(controller.getState().needsReconnect, true);
  loads = 0;
  controller.connect();
  controller.socket.onopen();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(controller.getState().needsReconnect, false);
  assert.equal(controller.getState().error, null);
  assert.equal(loads, 1);
  assert.equal(activated, 1);
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

test('a handoff that never confirms stops blocking the output selector', async () => {
  const { controller } = setup({ handoffTimeout: 40 });
  await controller.start();
  controller.api.request = async () => ({ awaiting_confirmation: true });
  await controller.switchOutput('vlc');
  assert.equal(controller.getState().pendingHandoff, true);
  await new Promise(resolve => setTimeout(resolve, 90));
  assert.equal(controller.getState().pendingHandoff, false);
  assert.match(controller.getState().error, /did not confirm/);
  controller.dispose();
});
