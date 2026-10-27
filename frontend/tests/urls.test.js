import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveApiUrls } from '../src/api/urls.js';

for (const [name, page, config, apiBase, wsUrl] of [
  ['Vite proxy', 'http://localhost:3000', {}, 'http://localhost:3000', 'ws://localhost:3000/ws/events'],
  ['LAN origin', 'http://192.168.1.20:8765/library', {}, 'http://192.168.1.20:8765', 'ws://192.168.1.20:8765/ws/events'],
  ['HTTPS origin', 'https://radio.example/library', {}, 'https://radio.example', 'wss://radio.example/ws/events'],
  ['Electron local backend', 'file:///C:/radio/dist/index.html', {}, 'http://localhost:8765', 'ws://localhost:8765/ws/events'],
  ['HTTP override with proxy prefix', 'http://localhost:3000', { apiBaseUrl: ' https://radio.example/radio/ ' }, 'https://radio.example/radio', 'wss://radio.example/radio/ws/events'],
  ['WS override', 'http://localhost:3000', { wsUrl: ' ws://127.0.0.1:8765/ws/events ' }, 'http://localhost:3000', 'ws://127.0.0.1:8765/ws/events'],
  ['IPv6', 'http://[::1]:3000', {}, 'http://[::1]:3000', 'ws://[::1]:3000/ws/events'],
]) {
  test(name, () => assert.deepEqual(resolveApiUrls(new URL(page), config), { apiBase, wsUrl }));
}

test('invalid or secret-bearing configuration fails visibly', () => {
  const page = new URL('https://radio.example');
  for (const apiBaseUrl of ['/relative', 'ftp://radio.example', 'https://user:secret@radio.example', 'https://radio.example?token=secret', 'https://radio.example#fragment']) {
    assert.throws(() => resolveApiUrls(page, { apiBaseUrl }), /API base URL/);
  }
  for (const wsUrl of ['/ws/events', 'https://radio.example/ws/events', 'wss://user:secret@radio.example/ws/events', 'wss://radio.example/ws/events?token=secret']) {
    assert.throws(() => resolveApiUrls(page, { wsUrl }), /WebSocket URL/);
  }
});
