import test from 'node:test';
import assert from 'node:assert/strict';

// The Electron dev window loads http://localhost:3000, so every backend socket
// has to survive the Vite proxy. In this Vite version an upgrade is only
// forwarded when the matching proxy entry opts into `ws`.
test('development proxy forwards WebSocket upgrades for playback control', async () => {
  const { default: config } = await import('../vite.config.js');
  const proxy = config.server.proxy;
  assert.equal(proxy['/api'].ws, true, '/api must forward the playback events upgrade');
  assert.equal(proxy['/api'].target, 'http://localhost:8765');
  assert.equal(proxy['/ws'].ws, true);
});
