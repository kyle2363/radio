import test from 'node:test';
import assert from 'node:assert/strict';
import { backendFetch, backendCookieHeader, waitForBackendReady } from '../electron/backend-session.cjs';

test('desktop controls use Electron cookie session and the fixed trusted API', async () => {
  const calls = [];
  const session = { fetch: async (...args) => { calls.push(args); return { ok: true }; } };
  await backendFetch(session, '/api/play/pause', { method: 'POST', credentials: 'omit' });
  assert.equal(calls[0][0], 'http://localhost:8765/api/play/pause');
  assert.equal(calls[0][1].credentials, 'include');
  for (const url of ['https://elsewhere/api/play', '//elsewhere/api', '/api/../../elsewhere', '/api/play#x', '/api/play?x=1']) {
    await assert.rejects(backendFetch(session, url), /Invalid backend path/);
  }
  assert.equal(calls.length, 1);
});

test('shutdown cookie is read only for the trusted backend', async () => {
  let query;
  const result = await backendCookieHeader({ cookies: { get: async (filter) => { query = filter; return [{ name: 'radio_session', value: 'opaque' }, { name: 'another', value: 'value' }]; } } });
  assert.deepEqual(query, { url: 'http://localhost:8765' });
  assert.equal(result, 'radio_session=opaque; another=value');
});

test('packaged readiness retries public auth status without requiring login', async () => {
  const calls = [], waits = [];
  const session = { fetch: async (url) => { calls.push(url); if (calls.length === 1) throw Error('starting'); return { ok: true, json: async () => ({ configured: true, authenticated: false }) }; } };
  assert.equal(await waitForBackendReady(session, { sleep: async (ms) => waits.push(ms) }), true);
  assert.deepEqual(waits, [500]);
  assert.deepEqual(calls, ['http://localhost:8765/api/auth/status', 'http://localhost:8765/api/auth/status']);
});

test('closing packaged window cancels readiness without loading or making requests', async () => {
  const session = { fetch() { throw Error('must not fetch'); } };
  assert.equal(await waitForBackendReady(session, { isCancelled: () => true }), false);
});

test('tray controls retain the logical session across output changes', async () => {
  const { createPlaybackBinding } = await import('../electron/backend-session.cjs');
  const binding = createPlaybackBinding();
  assert.deepEqual(binding.headers(), {});
  binding.set({ session_id: 'listening-one', owner_key: 'opaque-owner' });
  assert.equal(binding.headers()['X-Radio-Playback-Session'], 'listening-one');
  const headers = binding.headers();
  headers['X-Radio-Playback-Owner'] = 'tampered';
  assert.equal(binding.headers()['X-Radio-Playback-Owner'], 'opaque-owner');
  assert.throws(() => binding.set({ session_id: '../escape', owner_key: 'owner' }));
  binding.set(null);
  assert.deepEqual(binding.headers(), {});
});
