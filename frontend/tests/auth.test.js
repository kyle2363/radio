import test from 'node:test';
import assert from 'node:assert/strict';
import { createAuthClient, watchAuthentication } from '../src/api/auth.js';

const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

test('auth checks status with cookies and accepts unconfigured bootstrap', async () => {
  const calls = [];
  const client = createAuthClient(async (...args) => { calls.push(args); return response({ configured: false, authenticated: false }); }, 'https://radio.example');
  assert.deepEqual(await client.status(), { configured: false, authenticated: false });
  assert.equal(calls[0][0], 'https://radio.example/api/auth/status');
  assert.equal(calls[0][1].credentials, 'include');
  assert.equal(calls[0][1].cache, 'no-store');
});

test('login sends password only in JSON body, then verifies authenticated status', async () => {
  const calls = [];
  const client = createAuthClient(async (url, opts) => {
    calls.push([url, opts]);
    return response(url.endsWith('/login') ? {} : { configured: true, authenticated: true });
  });
  assert.equal((await client.login('example password')).authenticated, true);
  assert.equal(calls[0][0], '/api/auth/login');
  assert.equal(calls[0][1].method, 'POST');
  assert.equal(calls[0][1].credentials, 'include');
  assert.deepEqual(JSON.parse(calls[0][1].body), { password: 'example password' });
  assert.equal(calls[1][0], '/api/auth/status');
});

test('wrong password and failed cookie authentication give concise errors', async () => {
  const denied = createAuthClient(async () => response({ detail: 'server internals' }, 401));
  await assert.rejects(denied.login('bad'), /Incorrect password/);
  const missingCookie = createAuthClient(async (url) => response(url.endsWith('/status') ? { configured: true, authenticated: false } : {}));
  await assert.rejects(missingCookie.login('example'), /Sign-in could not be verified/);
});

test('logout includes session cookies and returns the new locked status', async () => {
  const calls = [];
  const client = createAuthClient(async (...args) => { calls.push(args); return response({ configured: true, authenticated: false }); });
  assert.equal((await client.logout()).authenticated, false);
  assert.equal(calls[0][0], '/api/auth/logout');
  assert.equal(calls[0][1].method, 'POST');
  assert.equal(calls[0][1].credentials, 'include');
});

test('invalid status fails closed instead of bypassing gate', async () => {
  for (const data of [{}, { configured: 'false', authenticated: false }, { configured: false }]) {
    await assert.rejects(createAuthClient(async () => response(data)).status(), /Unable to check sign-in/);
  }
});

test('connection failure retries and can reach unconfigured or authenticated bootstrap', async () => {
  for (const status of [{ configured: false, authenticated: false }, { configured: true, authenticated: true }]) {
    const changes = [], tasks = [];
    let attempts = 0;
    const client = { status: async () => { if (++attempts === 1) throw Error('offline'); return status; } };
    const stop = watchAuthentication(client, (state) => changes.push(state), { schedule: (fn, delay) => { tasks.push({ fn, delay }); return 1; }, cancel() {} });
    await new Promise(setImmediate);
    assert.equal(changes.at(-1).phase, 'connecting');
    assert.equal(tasks[0].delay, 1000);
    await tasks.shift().fn();
    assert.equal(changes.at(-1).phase, 'ready');
    stop();
  }
});

test('configured unauthenticated sessions remain locked, and stopping ignores pending results', async () => {
  const states = [];
  const stop = watchAuthentication({ status: async () => ({ configured: true, authenticated: false }) }, (s) => states.push(s));
  await new Promise(setImmediate);
  assert.equal(states.at(-1).phase, 'locked');
  stop();
  let finish;
  const late = [];
  const cancel = watchAuthentication({ status: () => new Promise((resolve) => { finish = resolve; }) }, (s) => late.push(s));
  cancel();
  finish({ configured: false, authenticated: false });
  await new Promise(setImmediate);
  assert.deepEqual(late, []);
});
