import test from 'node:test';
import assert from 'node:assert/strict';
import { hasConnectedRadio } from '../src/api/connectedRadioState.js';

test('connected radio is on only when the device carries recommend:read', () => {
  assert.equal(hasConnectedRadio({ scopes: ['catalog:read', 'recommend:read'] }), true);
  assert.equal(hasConnectedRadio({ scopes: ['catalog:read', 'profile:read'] }), false);
  assert.equal(hasConnectedRadio({ scopes: [] }), false);
  assert.equal(hasConnectedRadio({ scopes: ['recommend:read:extra'] }), false);
});
