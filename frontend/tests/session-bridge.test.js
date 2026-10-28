import test from 'node:test';
import assert from 'node:assert/strict';
import { setPlaybackController, getPlaybackController, playbackHeaders, subscribePlayback, createPlaybackFacade } from '../src/api/sessionBridge.js';

function fake() {
  const controller = { state: { connected: true, session: { id: 's', revision: 1, context_revision: 1, position_ms: 0, queue: [] } },
    onChange: () => {}, getState() { return this.state; }, requestHeaders() { return { 'X-Radio-Playback-Session': 's', 'X-Radio-Playback-Owner': 'secret' }; } };
  controller.update = patch => { controller.state = { ...controller.state, ...patch }; controller.onChange(controller.state); };
  return controller;
}

test('bridge wraps state callback, filters local audio updates, and cleans listeners', () => {
  const controller = fake(), received = [];
  let originalCalls = 0;
  const original = () => { originalCalls++; };
  controller.onChange = original;
  setPlaybackController(controller);
  const unsubscribe = subscribePlayback(state => received.push(state));
  assert.equal(received.length, 1);
  controller.update({ audio: { position_ms: 500 } });
  assert.equal(received.length, 1);
  assert.equal(originalCalls, 1);
  controller.update({ session: { ...controller.state.session, position_ms: 500 } });
  assert.equal(received.length, 2);
  unsubscribe();
  controller.update({ connected: false });
  assert.equal(received.length, 2);
  setPlaybackController(null);
  assert.equal(controller.onChange, original);
  assert.equal(getPlaybackController(), null);
  assert.deepEqual(playbackHeaders(), {});
});

test('capabilities appear only in current request headers and never emitted data', () => {
  const first = fake(), second = fake(), received = [];
  setPlaybackController(first);
  const unsubscribe = subscribePlayback(state => received.push(state));
  first.update({ session: { ...first.state.session, owner_key: 'secret', context: { token: 'secret' } } });
  assert.equal(JSON.stringify(received).includes('secret'), false);
  assert.equal(playbackHeaders()['X-Radio-Playback-Owner'], 'secret');
  setPlaybackController(second);
  assert.equal(getPlaybackController(), second);
  const count = received.length;
  first.update({ connected: false });
  assert.equal(received.length, count);
  unsubscribe();
  setPlaybackController(null);
});

test('facade dispatches scoped logical updates and position requests without a socket', async () => {
  const controller = fake();
  setPlaybackController(controller);
  let positions = [], queues = [], songs = 0, fetches = 0;
  const facade = createPlaybackFacade({ fetchStatus: async () => { fetches++; return { position_ms: 550, duration_ms: 1000 }; } });
  facade._positionHandler = value => positions.push(value);
  facade._queueHandler = value => queues.push(value);
  facade._songStartedHandler = () => songs++;
  controller.update({ session: { ...controller.state.session, revision: 2, position_ms: 500,
    items: [{ kind: 'song', item_id: 'one', song_id: 1, started: true, duration_ms: 1000 }], queue: [{ song_id: 2 }] } });
  assert.equal(songs, 1);
  assert.equal(positions.at(-1).elapsed_ms, 500);
  assert.deepEqual(queues.at(-1).queue, [{ song_id: 2 }]);
  facade.send('position_request');
  await Promise.resolve();
  assert.equal(positions.at(-1).elapsed_ms, 550);
  facade.close();
  const count = fetches;
  controller.update({ connected: false });
  facade.send('position_request');
  assert.equal(fetches, count);
  assert.equal(facade.readyState, 3);
  setPlaybackController(null);
});
