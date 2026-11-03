import test from 'node:test';
import assert from 'node:assert/strict';
import { createPairingBundleState } from '../src/api/pairingBundleState.js';

test('bundle secrets clear immediately on replacement/revoke and expire without persistence', () => {
  let now = 1000;
  const observed = [];
  const state = createPairingBundleState(value => observed.push(value), () => now);
  const first = state.begin();
  state.accept(first, { ticket_secret: 'first', expires_at: 2 });
  assert.equal(observed.at(-1).ticket_secret, 'first');
  const second = state.begin();
  assert.equal(observed.at(-1), null);
  state.accept(first, { ticket_secret: 'stale', expires_at: 3 });
  assert.equal(observed.at(-1), null, 'Late prior bundle must not return');
  state.accept(second, { ticket_secret: 'second', expires_at: 2 });
  now = 2000;
  state.tick();
  assert.equal(observed.at(-1), null);
  const third = state.begin();
  state.clear();
  state.accept(third, { ticket_secret: 'revoked', expires_at: 3 });
  assert.equal(observed.at(-1), null);
});

test('unmount invalidates pending requests and refuses expired responses', () => {
  const observed = [];
  const state = createPairingBundleState(value => observed.push(value), () => 1000);
  const first = state.begin();
  state.accept(first, { ticket_secret: 'expired', expires_at: 1 });
  assert.equal(observed.at(-1), null);
  state.dispose();
  state.accept(first, { ticket_secret: 'late', expires_at: 3 });
  assert.equal(observed.at(-1), null);
});
