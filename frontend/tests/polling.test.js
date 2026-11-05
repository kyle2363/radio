import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createNonOverlappingPoll } from '../src/polling.js';

const mainSource = fs.readFileSync(new URL('../electron/main.cjs', import.meta.url), 'utf8');

test('Electron startup errors use the dialog module API', () => {
  assert.match(mainSource, /\bdialog\.showErrorBox\(/);
  assert.doesNotMatch(mainSource, /app\.showErrorBox\(/);
});

test('overlapping poll callers coalesce and receive the same result', async () => {
  let resolve;
  let calls = 0;
  const poll = createNonOverlappingPoll(() => {
    calls++;
    return new Promise(r => { resolve = r; });
  });
  const first = poll.run();
  const second = poll.run();
  await Promise.resolve();
  assert.equal(calls, 1);
  resolve({ playing: true });
  assert.deepEqual(await first, { playing: true });
  assert.deepEqual(await second, { playing: true });
  poll.stop();
});

test('polling skips duplicate network work while sharing results', async () => {
  let resolve;
  let calls = 0;
  const poll = createNonOverlappingPoll(() => { calls++; return new Promise(r => { resolve = r; }); });
  const first = poll.run();
  const second = poll.run();
  await Promise.resolve();
  assert.equal(calls, 1);
  resolve('status');
  assert.equal(await first, 'status');
  assert.equal(await second, 'status');
  poll.stop();
});

test('failed polls release the in-flight slot for the next attempt', async () => {
  let calls = 0;
  const poll = createNonOverlappingPoll(() => {
    calls++;
    if (calls === 1) throw new Error('offline');
    return 'recovered';
  });
  await assert.rejects(poll.run(), /offline/);
  assert.equal(await poll.run(), 'recovered');
  assert.equal(calls, 2);
  poll.stop();
});

test('stopped polling suppresses late updates and future requests', async () => {
  let resolve;
  let updates = 0;
  const poll = createNonOverlappingPoll(async isActive => {
    await new Promise(r => { resolve = r; });
    if (isActive()) updates++;
    return 'status';
  });
  const pending = poll.run();
  await Promise.resolve();
  poll.stop();
  resolve();
  await pending;
  await poll.run();
  assert.equal(updates, 0);
});
