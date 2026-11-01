import test from 'node:test';
import assert from 'node:assert/strict';
import { createCatalogPreparationState } from '../src/api/catalogPreparationState.js';

const status = state => ({ state, revision: state === 'idle' ? null : 3, song_count: 10, vector_count: 8, error: state === 'failed' ? 'catalog_prepare_failed' : null });
function fixture(responses = ['idle']) {
  const calls = [], observed = [], timers = new Map(); let nextTimer = 0;
  const controller = createCatalogPreparationState({
    readStatus: async () => { calls.push('status'); return status(responses.shift() || 'ready'); },
    prepare: async () => { calls.push('prepare'); return status('running'); },
    onChange: value => observed.push(value),
    schedule: callback => { timers.set(++nextTimer, callback); return nextTimer; },
    unschedule: id => timers.delete(id),
  });
  const tick = async () => { const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); };
  return { controller, calls, observed, timers, tick };
}

test('settings reads once but exports only on an explicit click and polls only while running', async () => {
  const f = fixture(['idle', 'running', 'ready']);
  assert.deepEqual(f.calls, []);
  await f.controller.load();
  assert.deepEqual(f.calls, ['status']); assert.equal(f.timers.size, 0);
  await f.controller.start();
  assert.deepEqual(f.calls, ['status', 'prepare']); assert.equal(f.timers.size, 1);
  await f.controller.start(); assert.equal(f.calls.length, 2, 'Do not duplicate a running preparation');
  await f.tick(); await f.tick();
  assert.equal(f.observed.at(-1).status.state, 'ready'); assert.equal(f.timers.size, 0);
});

test('closing settings cancels its timer and ignores a late status without cancelling the server job', async () => {
  const f = fixture(['running']); await f.controller.load();
  assert.equal(f.timers.size, 1); f.controller.dispose(); assert.equal(f.timers.size, 0);
  await f.controller.start(); assert.deepEqual(f.calls, ['status']);
  let resolve; const seen = [];
  const controller = createCatalogPreparationState({ readStatus: () => new Promise(r => { resolve = r; }), prepare: () => assert.fail(), onChange: value => seen.push(value) });
  const pending = controller.load(); controller.dispose(); const count = seen.length;
  resolve(status('running')); await pending;
  assert.equal(seen.length, count);
});

test('polling has a finite bound and does not reflect raw failures', async () => {
  const f = fixture(Array(130).fill('running')); await f.controller.load();
  let ticks = 0;
  while (f.timers.size && ticks < 130) { await f.tick(); ticks++; }
  assert.ok(ticks <= 120); assert.equal(f.timers.size, 0); assert.match(f.observed.at(-1).error, /status/i);
  const seen = [];
  const controller = createCatalogPreparationState({ readStatus: async () => { throw Error('private-token-and-path'); }, prepare: () => assert.fail(), onChange: x => seen.push(x) });
  await controller.load();
  assert.equal(JSON.stringify(seen).includes('private-token'), false); assert.match(seen.at(-1).error, /catalog/i);
});

test('a rejected explicit preparation reconciles only a running or safely failed job status', async () => {
  const seen = [];
  const controller = createCatalogPreparationState({
    readStatus: async () => ({ ...status('failed'), error: 'ingest_active' }),
    prepare: async () => { throw Error('private diagnostics'); }, onChange: x => seen.push(x),
  });
  await controller.start();
  assert.equal(seen.at(-1).status.error, 'ingest_active');
  assert.equal(seen.at(-1).error, '');
});
