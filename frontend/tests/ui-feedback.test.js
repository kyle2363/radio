import test from 'node:test';
import assert from 'node:assert/strict';
import { createDismissTimer, appendToast } from '../src/ui/feedback.js';

test('dismiss countdown preserves remaining time across overlapping hover and focus', () => {
  let now = 0, pending, fired = 0;
  const timer = createDismissTimer(4000, () => fired++, {
    now: () => now, schedule: (fn, delay) => { pending = { fn, delay }; return 1; },
    cancel: () => { pending = null; },
  });
  timer.start();
  now = 1500;
  timer.pause('hover');
  timer.pause('focus');
  now = 9000;
  timer.resume('hover');
  assert.equal(pending, null);
  assert.equal(timer.remaining(), 2500);
  timer.resume('focus');
  assert.equal(pending.delay, 2500);
  pending.fn();
  assert.equal(fired, 1);
  timer.dispose();
  assert.equal(pending, null);
});

test('disposed countdown cannot restart or dismiss after unmount', () => {
  let pending;
  const timer = createDismissTimer(1000, () => assert.fail('disposed timer fired'), {
    now: () => 0, schedule: fn => { pending = fn; return 1; }, cancel: () => {},
  });
  timer.start();
  timer.dispose();
  pending();
  timer.resume('hover');
});

test('repeat informational toasts combine, actionable notifications retain their callbacks', () => {
  const first = { id: 1, message: 'Saved', type: 'success' };
  const combined = appendToast([first], { ...first, id: 2 });
  assert.equal(combined.length, 1);
  assert.equal(combined[0].count, 2);
  assert.equal(combined[0].id, 2);
  const action = { id: 3, message: 'Saved', type: 'success', onAction: () => {} };
  assert.equal(appendToast(combined, action).length, 2);
});
