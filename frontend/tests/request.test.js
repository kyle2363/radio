import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequest } from '../src/api/request.js';

test('API request includes browser cookies and reports authentication expiry', async () => {
  let options;
  let expired = 0;
  const request = createRequest({ baseUrl: 'https://radio.test',
    fetchImpl: async (url, opts) => {
      assert.equal(url, 'https://radio.test/api/play/status');
      options = opts;
      return { ok: false, status: 401, text: async () => 'Authentication required' };
    }, onUnauthorized: () => expired++,
  });
  await assert.rejects(request('GET', '/api/play/status'), /Authentication required/);
  assert.equal(options.credentials, 'include');
  assert.equal(expired, 1);
});

test('timeout remains effective when caller supplies an external signal', async () => {
  const caller = new AbortController();
  const request = createRequest({ baseUrl: '', fetchImpl: (_url, opts) => new Promise((resolve, reject) => {
    opts.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }) });
  await assert.rejects(request('GET', '/api/slow', undefined, 10, caller.signal), /timed out/);
  assert.equal(caller.signal.aborted, false);
});

test('caller cancellation stays cancellation rather than a timeout', async () => {
  const caller = new AbortController();
  const request = createRequest({ baseUrl: '', fetchImpl: (_url, opts) => new Promise((resolve, reject) => {
    opts.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }) });
  const pending = request('GET', '/api/slow', undefined, 1000, caller.signal);
  caller.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
});

test('session capability headers are resolved separately for each request', async () => {
  let owner = 'owner-one';
  const headers = [];
  const request = createRequest({ baseUrl: '', getHeaders: () => ({ 'X-Radio-Playback-Owner': owner }),
    fetchImpl: async (_url, options) => {
      headers.push(options.headers);
      return { ok: true, json: async () => ({}) };
    } });
  await request('GET', '/api/play/status');
  owner = 'owner-two';
  await request('POST', '/api/play/pause');
  assert.equal(headers[0]['X-Radio-Playback-Owner'], 'owner-one');
  assert.equal(headers[1]['X-Radio-Playback-Owner'], 'owner-two');
});
