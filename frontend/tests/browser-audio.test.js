import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserAudioEngine } from '../src/audio/BrowserAudioEngine.js';

class FakeAudio {
  constructor() { this.listeners = new Map(); this.currentTime = 0; this.paused = true; this.volume = 1; this.playCalls = 0; this.pauseCalls = 0; this.loadCalls = 0; }
  addEventListener(name, callback) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(callback); }
  removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
  emit(name) { for (const callback of [...(this.listeners.get(name) || [])]) callback({ type: name }); }
  play() { this.playCalls++; if (this.playError) return Promise.reject(this.playError); this.paused = false; return Promise.resolve(); }
  pause() { this.pauseCalls++; this.paused = true; this.emit('pause'); }
  load() { this.loadCalls++; }
  removeAttribute(name) { if (name === 'src') this.src = ''; }
}

const song = (id) => ({ item_id: `item-${id}`, kind: 'song', song_id: id, url: `/api/media/songs/${id}/stream`, duration_ms: 10000, gain_db: 0 });
const snapshot = (changes = {}) => ({ id: 'session-1', mode: 'browser', instance_id: 'instance-1', output_client_id: 'client-1', status: 'ready', items: [song(1), song(2), song(3)], active_index: 0, position_ms: 2000, context: { volume: 70, normalization: true, talk_volume: 50, eq: 'flat' }, revision: 1, ...changes });
const tick = () => new Promise(setImmediate);

function fixture(options = {}) {
  const audios = [], events = [], positions = [], states = [], errors = [], complete = [], acks = [];
  const engine = new BrowserAudioEngine({
    clientId: 'client-1', apiBase: 'https://radio.example',
    audioFactory: () => { const audio = new FakeAudio(); audios.push(audio); return audio; },
    audioContextFactory: null,
    postEvent: async (event) => { events.push(event); return { accepted: true }; },
    postPosition: async (position) => { positions.push(position); },
    onState: (state) => states.push(state), onError: (error) => errors.push(error),
    onPlanComplete: (plan) => complete.push(plan), onQuiesceAck: (ack) => acks.push(ack),
    ...options,
  });
  return { engine, audios, events, positions, states, errors, complete, acks };
}

test('only exact browser output grant may decode; duplicate or stale snapshots do not restart', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot({ mode: 'vlc' }));
  f.engine.loadSnapshot(snapshot({ output_client_id: 'other' }));
  assert.equal(f.audios.length, 0);
  f.engine.loadSnapshot(snapshot({ revision: 2 }));
  await tick();
  assert.equal(f.audios.length, 2, 'only active and next clip exist');
  const active = f.audios[0];
  assert.equal(active.crossOrigin, 'use-credentials');
  assert.equal(active.src, 'https://radio.example/api/media/songs/1/stream');
  assert.equal(active.currentTime, 2);
  assert.equal(active.playCalls, 1);
  active.currentTime = 5;
  f.engine.loadSnapshot(snapshot({ revision: 2 }));
  f.engine.loadSnapshot(snapshot({ revision: 1, active_index: 1 }));
  assert.equal(active.playCalls, 1);
  assert.equal(active.currentTime, 5);
  assert.equal(f.audios.length, 2);
  f.engine.dispose();
});

test('actual playing posts once, event posts serialize, and preload has no feedback', async () => {
  let finish;
  const sent = [];
  const f = fixture({ postEvent: (event) => { sent.push(event); return sent.length === 1 ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve({ accepted: true }); } });
  f.engine.loadSnapshot(snapshot());
  f.audios[1].emit('playing');
  assert.equal(sent.length, 0);
  f.audios[0].emit('playing');
  f.audios[0].emit('playing');
  f.engine.pause();
  await tick();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, 'started');
  assert.equal(sent[0].position_ms, 2000);
  assert.equal(sent[0].client_id, 'client-1');
  assert.equal(sent[0].instance_id, 'instance-1');
  assert.equal(sent[0].item_id, 'item-1');
  assert.ok(sent[0].event_id);
  finish({ accepted: true });
  await tick();
  assert.deepEqual(sent.map((event) => event.kind), ['started', 'paused']);
  await f.engine.play();
  f.audios[0].emit('playing');
  await tick();
  assert.equal(sent.at(-1).kind, 'resumed');
  assert.equal(new Set(sent.map((event) => event.event_id)).size, 3);
  f.engine.dispose();
});

test('a late pause acknowledgement cannot undo a newer local resume', async () => {
  let acknowledgePause;
  const f = fixture({ postEvent: async event => {
    if (event.kind === 'paused') return new Promise(resolve => { acknowledgePause = resolve; });
    return { accepted: true };
  } });
  f.engine.loadSnapshot(snapshot());
  const audio = f.audios[0];
  audio.emit('playing');
  await tick();
  f.engine.pause();
  await tick();
  await f.engine.play();
  audio.emit('playing');
  acknowledgePause({ accepted: true, session: snapshot({ status: 'paused', revision: 2 }) });
  await tick();
  assert.equal(audio.paused, false);
  f.engine.dispose();
});

test('mirrored status and stale DOM pause events cannot issue extra output commands', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot());
  const audio = f.audios[0];
  audio.emit('playing');
  await tick();
  f.engine.pause();
  await tick();
  f.engine.loadSnapshot(snapshot({ status: 'paused', revision: 2 }));
  const plays = audio.playCalls;
  f.engine.loadSnapshot(snapshot({ status: 'playing', revision: 3 }));
  assert.equal(audio.playCalls, plays);
  await f.engine.play();
  audio.emit('playing');
  await tick();
  const events = f.events.length;
  audio.emit('pause'); // Queued pause callback from before the successful resume.
  await tick();
  assert.equal(f.events.length, events);
  f.engine.dispose();
});

test('completion is once per item and waits for acknowledgement before promoting preload', async () => {
  let acknowledge;
  const f = fixture({ postEvent: (event) => event.kind === 'completed' ? new Promise((resolve) => { acknowledge = resolve; }) : Promise.resolve({ accepted: true }) });
  f.engine.loadSnapshot(snapshot());
  const first = f.audios[0], next = f.audios[1];
  first.emit('playing');
  first.currentTime = 10;
  first.emit('ended');
  first.emit('ended');
  await tick();
  assert.equal(next.playCalls, 0);
  // A concurrent WS update cannot advance output before completion ACK.
  f.engine.loadSnapshot(snapshot({ revision: 3, active_index: 1, position_ms: 0 }));
  assert.equal(next.playCalls, 0);
  acknowledge({ accepted: true, session: snapshot({ revision: 2, active_index: 1, position_ms: 0 }) });
  await tick();
  assert.equal(next.playCalls, 1);
  assert.equal(first.src, '');
  assert.equal(first.listeners.get('ended').size, 0);
  assert.equal(f.audios.length, 3);
  first.emit('playing'); first.emit('ended');
  await tick();
  assert.equal(next.playCalls, 1);
  f.engine.dispose();
});

test('final clip completion notifies once only after server acceptance', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot({ items: [song(1)] }));
  f.audios[0].emit('ended');
  f.audios[0].emit('ended');
  await tick();
  assert.equal(f.events.filter((event) => event.kind === 'completed').length, 1);
  assert.equal(f.complete.length, 1);
  assert.equal(f.audios[0].src, '');
  f.engine.dispose();
});

test('autoplay denial awaits gesture without recording a failed play', async () => {
  const error = Object.assign(new Error('gesture needed'), { name: 'NotAllowedError' });
  const f = fixture({ audioFactory: () => { const audio = new FakeAudio(); audio.playError = error; f.audios.push(audio); return audio; } });
  f.engine.loadSnapshot(snapshot());
  await tick();
  assert.equal(f.states.at(-1).status, 'awaiting_user_gesture');
  assert.equal(f.events.length, 0);
  f.audios[0].playError = null;
  await f.engine.activate();
  f.audios[0].emit('playing');
  await tick();
  assert.equal(f.events[0].kind, 'started');
  f.engine.dispose();
});

test('network/auth event failure pauses recoverably and does not advance queue', async () => {
  const f = fixture({ postEvent: async () => { throw Object.assign(Error('Unauthorized'), { status: 401 }); } });
  f.engine.loadSnapshot(snapshot());
  f.audios[0].emit('playing');
  await tick();
  assert.equal(f.audios[0].paused, true);
  assert.equal(f.audios[1].playCalls, 0);
  assert.equal(f.states.at(-1).status, 'interrupted');
  assert.equal(f.states.at(-1).recoverable, true);
  assert.equal(f.errors.length, 1);
  f.engine.dispose();
});

test('media network error pauses recoverably without failure history or losing playback position', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot());
  const audio = f.audios[0];
  audio.currentTime = 4.25;
  audio.error = { code: 2 };
  audio.emit('error');
  await tick();
  assert.equal(audio.paused, true);
  assert.equal(audio.currentTime, 4.25);
  assert.equal(f.states.at(-1).status, 'interrupted');
  assert.equal(f.states.at(-1).recoverable, true);
  assert.equal(f.events.length, 0);
  assert.equal(f.errors[0].code, 'media_network');
  audio.error = null;
  assert.equal(await f.engine.activate(), true);
  audio.emit('playing');
  await tick();
  assert.deepEqual(f.events.map((event) => event.kind), ['started']);
  assert.equal(audio.currentTime, 4.25);
  f.engine.dispose();
});

test('errors record failed once; replacing or revoking an output retires stale callbacks', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot());
  const stale = [...f.audios[0].listeners.get('ended')][0];
  f.audios[0].emit('error'); f.audios[0].emit('error');
  await tick();
  assert.equal(f.events.filter((event) => event.kind === 'failed').length, 1);
  f.engine.loadSnapshot(snapshot({ instance_id: 'instance-2', revision: 2 }));
  const current = f.audios[2];
  stale();
  await tick();
  assert.equal(current.playCalls, 1);
  assert.equal(f.events.filter((event) => event.kind === 'completed').length, 0);
  f.engine.loadSnapshot(snapshot({ instance_id: 'instance-2', revision: 3, output_client_id: 'other' }));
  assert.equal(current.src, '');
  assert.equal(current.paused, true);
  f.engine.dispose();
});

test('controls are instance/grant scoped, idempotent and acknowledge quiesce without completion', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot());
  const control = (id, action, changes = {}) => ({ id, action, instance_id: 'instance-1', output_client_id: 'client-1', ...changes });
  assert.equal(await f.engine.applyControl(control('wrong', 'stop', { output_client_id: 'other' })), false);
  assert.equal(await f.engine.applyControl(control('old', 'stop', { instance_id: 'old' })), false);
  await f.engine.applyControl(control('seek', 'seek', { position_ms: 4500 }));
  assert.equal(f.audios[0].currentTime, 4.5);
  await f.engine.applyControl(control('volume', 'volume', { volume: 20 }));
  assert.equal(f.audios[0].volume, 0.2);
  await f.engine.applyControl(control('pause', 'pause'));
  const pauses = f.audios[0].pauseCalls;
  await f.engine.applyControl(control('pause', 'pause'));
  assert.equal(f.audios[0].pauseCalls, pauses);
  await f.engine.applyControl(control('quiesce', 'quiesce'));
  assert.deepEqual(f.acks, [{ command_id: 'quiesce', instance_id: 'instance-1', client_id: 'client-1', position_ms: 4500 }]);
  assert.equal(f.events.some((event) => event.kind === 'completed'), false);
  assert.equal(f.audios[0].paused, true);
  f.engine.dispose();
});

test('position updates are scoped and throttled; disposal retires all media listeners', async () => {
  let now = 1000;
  const f = fixture({ now: () => now });
  f.engine.loadSnapshot(snapshot());
  f.audios[0].currentTime = 3;
  f.audios[0].emit('timeupdate');
  f.audios[0].emit('timeupdate');
  await tick();
  assert.deepEqual(f.positions, [{ instance_id: 'instance-1', item_id: 'item-1', client_id: 'client-1', position_ms: 3000, sequence: 1 }]);
  now = 2000; f.audios[0].emit('timeupdate');
  await tick();
  assert.equal(f.positions.length, 2);
  assert.equal(f.positions[1].sequence, 2);
  f.engine.dispose();
  for (const audio of f.audios) {
    assert.equal(audio.src, '');
    assert.equal([...audio.listeners.values()].reduce((sum, set) => sum + set.size, 0), 0);
    assert.equal(audio.paused, true);
  }
  f.audios[0].emit('timeupdate');
  f.engine.loadSnapshot(snapshot({ revision: 9 }));
  assert.equal(f.audios.length, 2);
});

test('context revisions update preferences without restarting playback; sequence resets only on new instance', async () => {
  let now = 1000;
  const f = fixture({ now: () => now });
  f.engine.loadSnapshot(snapshot({ revision: 4, context_revision: 1 }));
  const first = f.audios[0];
  f.engine.loadSnapshot(snapshot({ revision: 4, context_revision: 2, context: { volume: 25 } }));
  assert.equal(first.volume, 0.25);
  assert.equal(first.playCalls, 1);
  f.engine.loadSnapshot(snapshot({ revision: 4, context_revision: 1, context: { volume: 10 } }));
  assert.equal(first.volume, 0.25);
  first.emit('timeupdate');
  await tick();
  now = 2000;
  f.engine.loadSnapshot(snapshot({ revision: 5, instance_id: 'new-instance' }));
  f.audios[2].emit('timeupdate');
  await tick();
  assert.deepEqual(f.positions.map((event) => event.sequence), [1, 1]);
  f.engine.dispose();
});

test('completion with final stopped session still notifies once and cannot replay stale snapshots', async () => {
  const f = fixture({ postEvent: async () => ({ accepted: true, session: snapshot({ revision: 2, items: [song(1)], status: 'stopped' }) }) });
  f.engine.loadSnapshot(snapshot({ items: [song(1)] }));
  f.audios[0].emit('ended');
  await tick();
  assert.equal(f.complete.length, 1);
  f.engine.loadSnapshot(snapshot({ items: [song(1)] }));
  assert.equal(f.audios.length, 1);
  f.engine.dispose();
});

test('recoverable completion retries the same event id after reconnect without replaying clip', async () => {
  const sent = [];
  const f = fixture({ postEvent: async (event) => { sent.push(event); if (sent.length === 1) throw Error('offline'); return { accepted: true }; } });
  f.engine.loadSnapshot(snapshot({ items: [song(1)] }));
  const first = f.audios[0];
  first.emit('ended');
  await tick();
  assert.equal(f.states.at(-1).status, 'interrupted');
  await f.engine.activate();
  assert.equal(sent.length, 2);
  assert.equal(sent[0].event_id, sent[1].event_id);
  assert.equal(first.playCalls, 1);
  assert.equal(f.complete.length, 1);
  f.engine.dispose();
});

test('revocation immediately silences output even while completion acknowledgement is pending', async () => {
  let acknowledge;
  const f = fixture({ postEvent: () => new Promise((resolve) => { acknowledge = resolve; }) });
  f.engine.loadSnapshot(snapshot());
  const first = f.audios[0], next = f.audios[1];
  first.emit('ended');
  await tick();
  f.engine.loadSnapshot(snapshot({ revision: 2, output_client_id: 'other' }));
  assert.equal(first.src, '');
  assert.equal(next.src, '');
  acknowledge({ accepted: true, session: snapshot({ revision: 1, active_index: 1 }) });
  await tick();
  assert.equal(next.playCalls, 0);
  f.engine.dispose();
});

test('late play rejection after a pause cannot replace intentional paused state', async () => {
  let rejectPlay;
  const f = fixture({ audioFactory: () => {
    const audio = new FakeAudio();
    audio.play = () => { audio.playCalls++; return new Promise((_resolve, reject) => { rejectPlay = reject; }); };
    f.audios.push(audio); return audio;
  } });
  f.engine.loadSnapshot(snapshot());
  f.engine.pause();
  rejectPlay(Object.assign(Error('denied'), { name: 'NotAllowedError' }));
  await tick();
  assert.equal(f.states.at(-1).status, 'paused');
  assert.equal(f.events.length, 0);
  f.engine.dispose();
});

test('accepted final completion tombstones the finished instance against duplicate snapshots', async () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot({ items: [song(1)] }));
  f.audios[0].emit('ended');
  await tick();
  f.engine.loadSnapshot(snapshot({ items: [song(1)] }));
  assert.equal(f.audios.length, 1);
  assert.equal(f.complete.length, 1);
  f.engine.dispose();
});

test('quiesce acknowledgement network failure stays silent and can be retried with same command id', async () => {
  let attempts = 0;
  const f = fixture({ onQuiesceAck: async () => { if (++attempts === 1) throw Error('offline'); } });
  f.engine.loadSnapshot(snapshot());
  const command = { id: 'handoff', action: 'quiesce', instance_id: 'instance-1', output_client_id: 'client-1' };
  assert.equal(await f.engine.applyControl(command), false);
  assert.equal(f.audios[0].paused, true);
  assert.equal(f.states.at(-1).status, 'interrupted');
  assert.equal(await f.engine.applyControl(command), true);
  assert.equal(attempts, 2);
  assert.equal(f.events.length, 0);
  f.engine.dispose();
});

test('media URLs cannot escape configured backend/media prefix or inject secrets', () => {
  for (const url of ['https://evil.example/api/media/x/stream', '//evil.example/api/media/x', '/api/settings', '/api/media/../../settings', '/api/media/x?password=secret', '/api/media/x#fragment', '/api/media/%2e%2e/settings']) {
    const f = fixture();
    f.engine.loadSnapshot(snapshot({ items: [{ ...song(1), url }] }));
    assert.equal(f.audios.length, 0, url);
    assert.equal(f.errors.length, 1, url);
    f.engine.dispose();
  }
});

test('HTML fallback caps normalization/speech gain and explicitly reports unsupported EQ', () => {
  const f = fixture();
  f.engine.loadSnapshot(snapshot({ items: [{ ...song(1), kind: 'speech', gain_db: 12 }], context: { volume: 80, talk_volume: 25, normalization: true, eq: [0,0,0,0,3,0,0,0,0,0] } }));
  assert.ok(f.audios[0].volume > 0 && f.audios[0].volume <= 1);
  assert.equal(f.states.at(-1).capabilities.webAudio, false);
  assert.equal(f.states.at(-1).capabilities.eq, false);
  assert.equal(f.states.at(-1).eqApplied, false);
  f.engine.dispose();
});

test('WebAudio creates one graph per activated item, applies ten EQ bands, and disconnects on disposal', async () => {
  const nodes = [];
  const node = (fields = {}) => { const value = { connect() {}, disconnect() { this.disconnected = true; }, ...fields }; nodes.push(value); return value; };
  const context = { state: 'running', destination: {},
    createMediaElementSource: () => node(),
    createGain: () => node({ gain: { value: 1 } }),
    createBiquadFilter: () => node({ frequency: { value: 0 }, Q: { value: 0 }, gain: { value: 0 } }),
    resume: async () => {}, close: async () => { context.closed = true; },
  };
  const f = fixture({ audioContextFactory: () => context });
  f.engine.loadSnapshot(snapshot({ context: { volume: 90, normalization: true, eq: { bands: [0,0,0,0,6,0,0,0,0,0] } } }));
  assert.equal(nodes.length, 12);
  assert.equal(f.audios[0].volume, 1);
  assert.equal(f.states.at(-1).capabilities.eq, true);
  assert.equal(nodes.filter((value) => value.type === 'peaking').length, 10);
  assert.ok(nodes.find((value) => value.gain && !value.frequency).gain.value <= 10 ** (-6/20));
  f.engine.dispose();
  await tick();
  assert.ok(nodes.every((value) => value.disconnected));
  assert.equal(context.closed, true);
});
