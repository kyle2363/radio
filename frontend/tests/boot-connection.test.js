import test from 'node:test';
import assert from 'node:assert/strict';
import { connectToServer } from '../src/ui/bootConnection.js';

test('startup becomes ready only after a successful health response, without overlapping requests', async () => {
  let attempts = 0, ready = 0, retry;
  const stop = connectToServer({ probe: async () => ({ ok: ++attempts === 2 }),
    onReady: () => ready++, onError: () => assert.fail('unexpected error'),
    schedule: fn => { retry = fn; return 1; }, cancel: () => {}, now: () => 0 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ready, 0);
  retry();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ready, 1);
  assert.equal(attempts, 2);
  stop();
});
test('unmount fences late health responses', async () => {
  let resolve;
  const stop = connectToServer({ probe: () => new Promise(r => { resolve = r; }),
    onReady: () => assert.fail('late success'), onError: () => assert.fail('late failure') });
  stop(); resolve({ ok: true });
  await new Promise(r => setImmediate(r));
});
test('timeout shows an error, and a new connection attempt can succeed', async () => {
  let now = 0, retry, errors = 0, ready = 0;
  const stop = connectToServer({ probe: async () => ({ ok: false }), now: () => now,
    schedule: fn => { retry = fn; return 1; }, cancel: () => {},
    onReady: () => assert.fail('not ready'), onError: () => errors++ });
  await new Promise(r => setImmediate(r));
  now = 30001; retry();
  await new Promise(r => setImmediate(r));
  assert.equal(errors, 1); stop();
  const stopRetry = connectToServer({ probe: async () => ({ ok: true }), onReady: () => ready++, onError: () => assert.fail('retry failed') });
  await new Promise(r => setImmediate(r));
  assert.equal(ready, 1); stopRetry();
});
