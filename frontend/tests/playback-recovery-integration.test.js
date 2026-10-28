import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserAudioEngine } from '../src/audio/BrowserAudioEngine.js';
import { PlaybackSessionController } from '../src/api/playback.js';

class FakeAudio {
  constructor() { this.listeners = new Map(); this.currentTime = 0; this.paused = true; this.volume = 1; }
  addEventListener(name, callback) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(callback); }
  removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
  emit(name) { for (const callback of [...(this.listeners.get(name) || [])]) callback({ type: name }); }
  play() { this.paused = false; return Promise.resolve(); }
  pause() { this.paused = true; this.emit('pause'); }
  load() {}
  removeAttribute(name) { if (name === 'src') this.src = ''; }
}

const tick = () => new Promise(setImmediate);

test('recovery flushes lost event ACKs, renews lease, then keeps newer local pause/resume/seek', async () => {
  const calls = [], audio = new FakeAudio();
  let online = false;
  const snapshot = { id: 'session', mode: 'browser', instance_id: 'instance', output_client_id: 'client',
    status: 'paused', revision: 4, active_index: 0, position_ms: 2000,
    items: [{ item_id: 'item', kind: 'song', song_id: 1, url: '/api/media/songs/1/stream', duration_ms: 10000 }] };
  const api = { request: async (method, path, body) => {
    calls.push({ method, path, body });
    if (path === '/session') return snapshot;
    if (path === '/session/lease') return { instance_id: 'instance', client_id: 'client', lease_remaining_ms: 12000 };
    if (path === '/session/events' && !online) throw Error('lost ACK');
    if (path === '/session/events') return { accepted: true, duplicate: true };
    return { session: snapshot };
  } };
  const controller = new PlaybackSessionController({ api, clientId: 'client', storage: {
    getItem: () => JSON.stringify({ session_id: 'session', owner_key: 'owner' }), setItem() {}, removeItem() {},
  }, reconnectDelay: 0, socketFactory: () => ({ send() {}, close() {} }),
  engineFactory: options => new BrowserAudioEngine({ ...options, apiBase: 'http://localhost:8765', requireLease: true,
    audioFactory: () => audio, audioContextFactory: null }) });

  await controller.start();
  await controller.activate();
  assert.ok(calls.some(call => call.path === '/session/lease'));
  audio.emit('playing');
  await tick();
  const started = calls.find(call => call.path === '/session/events')?.body;
  assert.equal(started.kind, 'started');
  audio.currentTime = 3;
  await controller.localControl('pause');
  await controller.localControl('resume');
  audio.emit('playing');
  await controller.localControl('seek', { position_ms: 6400 });
  assert.equal(audio.currentTime, 6.4);

  online = true;
  await controller.recoverControl();
  const eventCalls = calls.filter(call => call.path === '/session/events');
  assert.deepEqual(eventCalls.map(call => call.body.kind), ['started', 'started', 'started', 'paused', 'resumed']);
  assert.ok(eventCalls.filter(call => call.body.kind === 'started').every(call => call.body.event_id === started.event_id),
    'all retries use the exact event identity');
  const recoveryStart = calls.findLastIndex(call => call.path === '/session/events' && call.body.event_id === started.event_id);
  const recoveryLease = calls.findIndex((call, index) => index > recoveryStart && call.path === '/session/lease');
  const recoverySnapshot = calls.findIndex((call, index) => index > recoveryLease && call.path === '/session');
  assert.ok(recoveryStart >= 0 && recoveryLease > recoveryStart && recoverySnapshot > recoveryLease,
    'pending events flush before lease renewal and authoritative refresh');
  assert.equal(audio.paused, false);
  assert.equal(audio.currentTime, 6.4, 'the older server position does not undo the newer local seek');
  controller.dispose();
});
