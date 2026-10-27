import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { shutdownOwnedBackend } from '../electron/backend-shutdown.cjs';

function fixture() {
  const child = Object.assign(new EventEmitter(), { pid: 123, exitCode: null, signalCode: null });
  const req = Object.assign(new EventEmitter(), { end() {}, destroy() { this.destroyed = true; } });
  const killed = [];
  let respond;
  const options = {
    request(_options, callback) { respond = callback; return req; },
    forceKill(process) { killed.push(process.pid); },
    timeoutMs: 30,
  };
  return { child, req, killed, options, reply(statusCode = 200) {
    const response = Object.assign(new EventEmitter(), { statusCode, resume() {} });
    respond(response);
    return response;
  } };
}

test('waits for full shutdown acknowledgement before terminating owned backend', async () => {
  const f = fixture();
  const done = shutdownOwnedBackend(f.child, f.options);
  assert.deepEqual(f.killed, []);
  const response = f.reply();
  assert.deepEqual(f.killed, []);
  response.emit('end');
  assert.equal(await done, 'acknowledged');
  assert.deepEqual(f.killed, [123]);
  assert.equal(f.child.listenerCount('exit'), 0);
});

test('timeout bounds an unresponsive backend and destroys request', async () => {
  const f = fixture();
  assert.equal(await shutdownOwnedBackend(f.child, f.options), 'timeout');
  assert.deepEqual(f.killed, [123]);
  assert.equal(f.req.destroyed, true);
});

test('request errors fall back to owned-process termination', async () => {
  const f = fixture();
  const done = shutdownOwnedBackend(f.child, f.options);
  f.req.emit('error', new Error('connection refused'));
  assert.equal(await done, 'error');
  assert.deepEqual(f.killed, [123]);
});

test('process exit while awaiting response does not kill a stale PID', async () => {
  const f = fixture();
  const done = shutdownOwnedBackend(f.child, f.options);
  f.child.exitCode = 0;
  f.child.emit('exit', 0);
  assert.equal(await done, 'exited');
  assert.deepEqual(f.killed, []);
});

test('no owned live process means no request or kill', async () => {
  const f = fixture();
  f.options.request = () => { throw new Error('must not request'); };
  assert.equal(await shutdownOwnedBackend(null, f.options), 'not-running');
  f.child.exitCode = 1;
  assert.equal(await shutdownOwnedBackend(f.child, f.options), 'not-running');
  assert.deepEqual(f.killed, []);
});

test('unsuccessful shutdown response is reported as error', async () => {
  const f = fixture();
  const done = shutdownOwnedBackend(f.child, f.options);
  f.reply(500).emit('end');
  assert.equal(await done, 'error');
});
