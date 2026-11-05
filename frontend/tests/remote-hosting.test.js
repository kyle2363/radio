import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import { generateKeyPairSync, sign } from 'node:crypto';
import { createRemoteHostingControl, parseQuickTunnelUrl, validateDescriptor, RETRY_DELAYS_MS } from '../electron/remote-hosting.cjs';

const endpoint = 'https://blue-circles.trycloudflare.com';

function descriptor(generation = 4, overrides = {}) {
  const pair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const payload = Buffer.from(JSON.stringify({ protocol_version: 1, server_id: 'b3167188-96c1-4968-84c5-7bad4e70a632', endpoint, connection_generation: generation, publication_revision: 1, available: true, issued_at: Math.floor(Date.now() / 1000), expires_at: Math.floor(Date.now() / 1000) + 300, ...overrides }));
  const b64 = value => value.toString('base64url');
  return { payload: b64(payload), signature: b64(sign('sha256', payload, pair.privateKey)), public_key: b64(pair.publicKey.export({ format: 'der', type: 'spki' })) };
}

function fixtures() {
  const calls = [], timers = { intervals: [], timeouts: [], setInterval(fn, delay) { const id = { fn, delay }; this.intervals.push(id); return id; }, clearInterval(id) { this.intervals = this.intervals.filter(x => x !== id); }, setTimeout(fn, delay) { const id = { fn, delay }; this.timeouts.push(id); return id; }, clearTimeout(id) { this.timeouts = this.timeouts.filter(x => x !== id); } };
  const children = [];
  const control = createRemoteHostingControl({
    timers, initialState: { binaryPath: 'C:\\radio\\cloudflared.exe' },
    request: async (path, options) => { calls.push({ path, body: JSON.parse(options.body || '{}') }); if (path.endsWith('/start')) return { enabled: true, endpoint, connection_generation: 4, descriptor: descriptor() }; return { enabled: true, endpoint }; },
    startProcess: async () => { const child = new EventEmitter(); child.onLine = fn => { child.line = fn; }; child.onExit = fn => child.on('exit', fn); child.ready = Promise.resolve(); child.stop = false; children.push(child); return child; },
    stopProcess: async child => { child.stop = true; }, probe: async (_endpoint, value, generation) => validateDescriptor(value, endpoint, generation),
  });
  return { control, calls, timers, children };
}

test('Quick Tunnel parser accepts the exact hostname and rejects lookalikes and URL decorations', () => {
  assert.equal(parseQuickTunnelUrl('INF https://blue-circles.trycloudflare.com'), endpoint);
  for (const text of ['https://trycloudflare.com', 'https://a.trycloudflare.com.attacker.test', 'https://a.trycloudflare.com:443', 'https://a.trycloudflare.com/path', 'http://a.trycloudflare.com', 'https://user@a.trycloudflare.com']) assert.equal(parseQuickTunnelUrl(text), null, text);
  assert.equal(parseQuickTunnelUrl('x'.repeat(9000)), null);
});

test('signed descriptor verification binds the exact HTTPS endpoint and generation', () => {
  const valid = descriptor();
  assert.equal(validateDescriptor(valid, endpoint, 4).payload, valid.payload);
  assert.throws(() => validateDescriptor(valid, 'https://other.trycloudflare.com', 4));
  assert.throws(() => validateDescriptor(valid, endpoint, 5));
  assert.throws(() => validateDescriptor({ ...valid, signature: 'bad' }, endpoint, 4));
});

test('hosting admits only after URL parsing, signed local approval, and endpoint probe', async () => {
  const { control, calls, timers, children } = fixtures();
  assert.equal(control.status().state, 'off');
  await control.start();
  const child = children[0];
  child.line(`Quick Tunnel available at ${endpoint}`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(control.status().state, 'available');
  assert.equal(control.status().endpoint, endpoint);
  assert.deepEqual(calls[0], { path: '/remote/start', body: { endpoint } });
  assert.equal(timers.intervals[0].delay, 5000);
  timers.intervals[0].fn();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(calls.some(call => call.path === '/remote/renew' && call.body.connection_generation === 4));
  await control.stop();
  assert.equal(control.status().state, 'off');
  assert.equal(child.stop, true);
  assert.ok(calls.some(call => call.path === '/remote/stop' && call.body.connection_generation === 4));
  assert.equal(timers.intervals.length, 0);
});

test('temporary Cloudflare propagation retries the same endpoint while its owner lease is renewed', async () => {
  const calls = [], delays = [], children = [];
  const timers = { setInterval(fn, delay) { this.renew = fn; this.leaseDelay = delay; return fn; }, clearInterval() {}, setTimeout(fn, delay) { if (delay <= 2000) { delays.push(delay); queueMicrotask(fn); } return fn; }, clearTimeout() {} };
  let probeAttempts = 0;
  const control = createRemoteHostingControl({ timers, initialState: { binaryPath: 'C:\\radio\\cloudflared.exe' },
    request: async (path, options) => { calls.push({ path, body: JSON.parse(options.body || '{}') }); return path.endsWith('/start') ? { endpoint, connection_generation: 11, descriptor: descriptor(11) } : { endpoint, enabled: true }; },
    startProcess: async () => { const child = new EventEmitter(); child.onLine = fn => { child.line = fn; }; child.onExit = fn => child.on('exit', fn); child.ready = Promise.resolve(); children.push(child); return child; },
    stopProcess: async () => {}, probe: async (_url, value, generation) => { assert.equal(validateDescriptor(value, endpoint, generation).payload, value.payload); if (probeAttempts++ < 2) { const error = Error('edge not ready'); error.code = 'TRANSIENT'; throw error; } return value; },
  });
  await control.start(); children[0].line(`Tunnel: ${endpoint}`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(control.status().state, 'available');
  assert.equal(probeAttempts, 3);
  assert.deepEqual(delays, [1000, 2000]);
  assert.equal(timers.leaseDelay, 5000);
  assert.equal(calls.filter(call => call.path === '/remote/start').length, 1, 'one hostname is retained during edge propagation');
  timers.renew(); await new Promise(resolve => setImmediate(resolve));
  assert.ok(calls.some(call => call.path === '/remote/renew' && call.body.connection_generation === 11));
  await control.stop();
});

test('stale URL callbacks after Stop cannot reopen gateway admission', async () => {
  const { control, calls, children } = fixtures();
  await control.start();
  const child = children[0];
  await control.stop();
  child.line(`Quick Tunnel available at ${endpoint}`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(control.status().state, 'off');
  assert.equal(calls.some(call => call.path === '/remote/start'), false);
});

test('repeated cloudflared URL logs admit once; a tunnel that never reports a URL is retried', async () => {
  const { control, calls, timers, children } = fixtures();
  await control.start();
  const child = children[0];
  child.line(`Tunnel: ${endpoint}`);
  child.line(`Repeated Tunnel: ${endpoint}`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.filter(call => call.path === '/remote/start').length, 1);
  await control.stop();

  const f = fixtures();
  await f.control.start();
  const startup = f.timers.timeouts.find(timer => timer.delay === 90000);
  assert.ok(startup);
  startup.fn();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.children[0].stop, true);
  assert.equal(f.control.status().state, 'reconnecting');
  assert.ok(f.timers.timeouts.some(timer => timer.delay === 1000));
});

test('retry delays are bounded and capped at six attempts per outage', () => {
  assert.deepEqual(RETRY_DELAYS_MS, [1000, 2000, 4000, 8000, 16000, 30000]);
});

test('Windows Job Object kills a harmless child when the owning helper crashes', { skip: process.platform !== 'win32' }, async () => {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const python = path.join(repo, 'backend', 'venv', 'Scripts', 'python.exe');
  const helper = path.join(repo, 'frontend', 'electron', 'mobile-tunnel-job.py');
  if (!fs.existsSync(python)) return;
  const script = 'import os,time; print("TUNNEL_CHILD_PID="+str(os.getpid()),flush=True); time.sleep(120)';
  const owner = spawn(python, [helper, '--parent-pid', String(process.pid), '--', python, '-c', script], { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
  let output = '';
  const childPid = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error(`Job helper did not start: ${output}`)), 10000);
    owner.stdout.on('data', chunk => {
      output += chunk.toString('utf8');
      const match = output.match(/TUNNEL_CHILD_PID=(\d+)/);
      if (output.includes('RADIO_JOB_READY') && match) { clearTimeout(timer); resolve(Number(match[1])); }
    });
    owner.once('exit', code => { clearTimeout(timer); reject(Error(`Job helper exited early (${code}): ${output}`)); });
  });
  owner.kill(); // Crash-like termination skips the helper's Python finally block.
  await new Promise(resolve => owner.once('exit', resolve));
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try { process.kill(childPid, 0); } catch (error) { if (error.code === 'ESRCH') return; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail(`Job Object left child ${childPid} running after helper termination`);
});

test('Windows Job Object kills tunnel child when Electron parent disappears', { skip: process.platform !== 'win32' }, async () => {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const python = path.join(repo, 'backend', 'venv', 'Scripts', 'python.exe');
  const helper = path.join(repo, 'frontend', 'electron', 'mobile-tunnel-job.py');
  if (!fs.existsSync(python)) return;
  const parent = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { windowsHide: true, stdio: 'ignore' });
  const script = 'import os,time; print("TUNNEL_CHILD_PID="+str(os.getpid()),flush=True); time.sleep(120)';
  const owner = spawn(python, [helper, '--parent-pid', String(parent.pid), '--', python, '-c', script], { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
  let output = '';
  const childPid = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error(`Job helper did not start: ${output}`)), 10000);
    owner.stdout.on('data', chunk => { output += chunk.toString('utf8'); const match = output.match(/TUNNEL_CHILD_PID=(\d+)/); if (output.includes('RADIO_JOB_READY') && match) { clearTimeout(timer); resolve(Number(match[1])); } });
    owner.once('exit', code => { clearTimeout(timer); reject(Error(`Job helper exited early (${code}): ${output}`)); });
  });
  parent.kill();
  await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('Helper did not detect parent exit')), 5000); owner.once('exit', () => { clearTimeout(timer); resolve(); }); });
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try { process.kill(childPid, 0); } catch (error) { if (error.code === 'ESRCH') return; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail(`Job Object left child ${childPid} running after parent exit`);
});
