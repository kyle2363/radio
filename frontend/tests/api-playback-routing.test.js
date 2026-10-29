import test from 'node:test';
import assert from 'node:assert/strict';
import { setPlaybackController } from '../src/api/sessionBridge.js';

test('real API facade routes supported playback controls through the session controller', async () => {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  const calls = [], fetched = [];
  globalThis.window = { location: { protocol: 'http:', host: 'localhost:8765' }, dispatchEvent() {} };
  globalThis.fetch = async (url, options) => {
    fetched.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => '' };
  };
  const controller = {
    requestHeaders: () => ({ 'X-Radio-Playback-Session': 's' }),
    routeLegacy: (path, options) => { calls.push({ path, options }); return Promise.resolve({ session: true }); },
    play: songId => { calls.push({ path: 'play', songId }); return Promise.resolve({ played: true }); },
  };
  setPlaybackController(controller);
  try {
    const api = await import('../src/api/index.js?playback-routing-test');
    await api.pauseSong();
    await api.seekTo(1234);
    await api.setVolume(35);
    await api.post('/api/play/stop');
    await api.post('/api/play/next');
    await api.skipSong();
    await api.replaySong();
    await api.clearQueue();
    await api.addToQueue(7);
    await api.removeFromQueue(8);
    await api.reorderQueue([8, 7]);
    await api.setMood('focus');
    await api.setSleepTimer(20);
    await api.cancelSleepTimer();
    await api.fetchPlayStatus();
    await api.fetchQueue();
    await api.playSong(9);
    await api.submitSkipFeedback('too_slow');

    assert.deepEqual(calls.map(call => call.path), [
      '/api/play/pause', '/api/play/seek', '/api/play/volume', '/api/play/stop', '/api/queue/replay',
      '/api/queue/clear', '/api/queue/add/7', '/api/queue/remove/8', '/api/queue/reorder', '/api/mood/set',
      '/api/sleep/timer', '/api/sleep/timer', 'play',
    ]);
    assert.deepEqual(calls[1].options, { method: 'POST', body: { ms: 1234 } });
    assert.deepEqual(calls[7].options, { method: 'DELETE', body: undefined });
    assert.deepEqual(calls[11].options, { method: 'DELETE', body: undefined });
    assert.deepEqual(calls[12], { path: 'play', songId: 9 });
    assert.equal(fetched.length, 5);
    assert.deepEqual(fetched.map(call => new URL(call.url).pathname), [
      '/api/play/next', '/api/queue/skip', '/api/play/status', '/api/queue', '/api/queue/skip-feedback',
    ]);
  } finally {
    setPlaybackController(null);
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow;
    if (oldFetch === undefined) delete globalThis.fetch; else globalThis.fetch = oldFetch;
  }
});

test('reranking settings carry session and profile scope without issuing playback commands', async () => {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  const fetched = [];
  globalThis.window = { location: { protocol: 'http:', host: 'localhost:8765' }, dispatchEvent() {} };
  globalThis.fetch = async (url, options) => {
    fetched.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => '' };
  };
  setPlaybackController({ requestHeaders: () => ({ 'X-Radio-Playback-Session': 's', 'X-Radio-Playback-Owner': 'secret' }),
    routeLegacy: () => { throw new Error('Reranking must not restart playback'); } });
  try {
    const api = await import('../src/api/index.js?reranking-settings-test');
    await api.updateReranking(false);
    await api.updateReranking(true, 'global');
    assert.deepEqual(fetched.map(call => JSON.parse(call.options.body)), [
      { value: false, scope: 'session' }, { value: true, scope: 'global' },
    ]);
    for (const { url, options } of fetched) {
      assert.equal(new URL(url).pathname, '/api/settings/llm_reranking_enabled');
      assert.equal(options.method, 'PUT');
      assert.equal(options.headers['X-Radio-Playback-Owner'], 'secret');
    }
  } finally {
    setPlaybackController(null);
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow;
    if (oldFetch === undefined) delete globalThis.fetch; else globalThis.fetch = oldFetch;
  }
});
