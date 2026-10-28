import test from 'node:test';
import assert from 'node:assert/strict';
import { loadDevelopmentWindow, setThumbnailButtons } from '../electron/window-startup.cjs';

test('Windows thumbnail buttons use the installed Electron thumbar API', () => {
  const calls = [];
  assert.equal(setThumbnailButtons({ setThumbarButtons: buttons => { calls.push(buttons); return true; } }, ['play'], 'win32'), true);
  assert.deepEqual(calls, [['play']]);
  assert.equal(setThumbnailButtons({}, [], 'win32'), false);
  assert.equal(setThumbnailButtons({}, [], 'linux'), false);
});

test('development window waits for Vite and retries a load race', async () => {
  let probes = 0, loads = 0;
  const window = { isDestroyed: () => false, loadURL: async url => {
    assert.equal(url, 'http://localhost:3000/');
    if (++loads === 1) throw Error('ERR_CONNECTION_REFUSED');
  } };
  assert.equal(await loadDevelopmentWindow(window, { fetch: async url => {
    assert.equal(url, 'http://localhost:3000/');
    if (++probes === 1) throw Error('Vite starting');
    return { ok: true };
  } }, { sleep: async () => {} }), true);
  assert.equal(loads, 2);
  assert.equal(probes, 3);
});

test('closing a window cancels Vite startup without loading anything', async () => {
  assert.equal(await loadDevelopmentWindow({ isDestroyed: () => true }, {
    fetch: () => assert.fail('closed window must not probe'),
  }), false);
});
