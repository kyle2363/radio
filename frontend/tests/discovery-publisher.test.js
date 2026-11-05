import test from 'node:test';
import assert from 'node:assert/strict';
import { createDiscoveryPublisher, buildDiscoveryCommit } from '../electron/discovery-publisher.cjs';

function setup() {
  const calls = [], timers = { items: [], setInterval(fn, ms) { const t = { fn, ms }; this.items.push(t); return t; }, clearInterval(t) { this.items = this.items.filter(x => x !== t); }, setTimeout(fn) { queueMicrotask(fn); return 0; } };
  let saved = null, descriptorRevision = 0, timestamp = 1_800_000_000_000;
  const publisher = createDiscoveryPublisher({ timers, now: () => timestamp,
    fetch: async (url, init = {}) => { calls.push({ url, init }); if (url.includes('signInWithPassword')) return response({ localId: 'publisher-uid', idToken: 'id-secret', refreshToken: 'refresh-secret', expiresIn: '3600' }); if (url.includes('token?')) return response({ id_token: 'new-id', refresh_token: 'new-refresh', user_id: 'publisher-uid', expires_in: '3600' }); return response({}); },
    storage: { load: () => saved, save: v => { saved = v; }, clear: () => { saved = null; } },
    freshDescriptor: async () => { const issued_at = Math.floor(timestamp / 1000); return { payload: encode({ server_id: 'b3167188-96c1-4968-84c5-7bad4e70a632', connection_generation: 4, publication_revision: ++descriptorRevision, available: true, endpoint: 'https://a.trycloudflare.com', issued_at, expires_at: issued_at + 300 }), signature: 'sig', public_key: 'pub' }; } });
  return { publisher, calls, timers, get saved() { return saved; }, setNow: value => { timestamp = value; } };
}
function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');

test('publisher stores refresh credentials only in protected storage and does not return tokens', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' });
  const result = await f.publisher.signIn('owner@example.test', 'password');
  assert.deepEqual(result, { signedIn: true, uid: 'publisher-uid' });
  assert.match(f.saved, /refresh-secret/); assert.equal(JSON.stringify(result).includes('secret'), false);
  assert.equal(f.calls.some(x => x.init.body?.includes('password')), true);
  assert.equal(f.calls.every(x => x.init.redirect === 'error'), true);
});

test('active hosting publishes immediately and renews once per minute with exact discovery fields', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  await f.publisher.hostingChanged({ state: 'available' });
  assert.equal(f.calls.filter(c => c.url.includes('/documents:commit')).length, 1);
  const commit = JSON.parse(f.calls.at(-1).init.body), doc = commit.writes[0];
  assert.deepEqual(Object.keys(doc.update.fields).sort(), ['available','connection_generation','endpoint','expires_at','issued_at','payload','publication_revision','public_key','server_id','signature'].sort());
  assert.deepEqual(doc.updateTransforms, [{ fieldPath: 'updated_at', setToServerValue: 'REQUEST_TIME' }]);
  assert.equal(f.calls.at(-1).init.body.includes('refresh-secret'), false);
  assert.equal(f.timers.items[0].ms, 60000);
  f.timers.items[0].fn(); await new Promise(resolve => setImmediate(resolve)); assert.equal(f.calls.filter(c => c.url.includes('/documents:commit')).length, 2);
});

test('sign-out fences an in-flight publication and clears protected credentials', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  let release; f.publisher.setFetch(async () => new Promise(resolve => { release = resolve; }));
  const publishing = f.publisher.hostingChanged({ state: 'available' }); await new Promise(resolve => setImmediate(resolve));
  await f.publisher.signOut(); release(response({})); await publishing;
  assert.equal(f.saved, null); assert.equal(f.publisher.status().signedIn, false);
});

test('expired ID token refreshes from the protected refresh token', async () => {
  const f = setup(); let now = 1_800_000_000_000;
  // Replace the clock before creating a separate control so token expiry is deterministic.
  const calls = [];
  const publisher = createDiscoveryPublisher({ now: () => now, timers: f.timers,
    storage: { load: () => f.saved, save: () => {}, clear: () => {} },
    freshDescriptor: async () => { const issued_at = Math.floor(now / 1000); return { payload: encode({ server_id: 'b3167188-96c1-4968-84c5-7bad4e70a632', connection_generation: 4, publication_revision: 3, available: true, endpoint: 'https://a.trycloudflare.com', issued_at, expires_at: issued_at + 300 }), signature: 'sig', public_key: 'pub' }; },
    fetch: async (url, init = {}) => { calls.push({ url, init }); if (url.includes('signInWithPassword')) return response({ localId: 'u', idToken: 'old-id', refreshToken: 'refresh-secret', expiresIn: '60' }); if (url.includes('token?')) return response({ id_token: 'fresh-id', refresh_token: 'rotated-refresh', user_id: 'u', expires_in: '3600' }); return response({}); } });
  await publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await publisher.signIn('x@y.z', 'pw'); now += 61000;
  await publisher.hostingChanged({ state: 'available' });
  assert.equal(calls.some(c => c.url.includes('token?')), true);
  assert.match(calls.find(c => c.url.includes('/documents:commit')).init.headers.Authorization, /fresh-id/);
});

test('quota errors stop scheduled retries while preserving signed-in hosting status', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  f.publisher.setFetch(async url => url.includes('/documents:commit') ? response({ error: { status: 'RESOURCE_EXHAUSTED' } }, 429) : response({}));
  await f.publisher.hostingChanged({ state: 'available' });
  assert.equal(f.timers.items.length, 0);
  assert.equal(f.publisher.status().signedIn, true);
  assert.match(f.publisher.status().error, /quota/i);
});

test('cleared renewal callbacks cannot publish after hosting becomes unavailable', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  await f.publisher.hostingChanged({ state: 'available' });
  const oldTick = f.timers.items[0].fn, before = f.calls.filter(c => c.url.includes('/documents:commit')).length;
  await f.publisher.hostingChanged({ state: 'off' }); oldTick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.filter(c => c.url.includes('/documents:commit')).length, before);
});

test('a late refresh response after sign-out cannot restore credentials or publish', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  f.setNow(1_800_000_000_000 + 3_700_000);
  let releaseRefresh;
  f.publisher.setFetch(async (url, init) => { if (url.includes('token?')) return new Promise(resolve => { releaseRefresh = resolve; }); return response({}); });
  const hostingUpdate = f.publisher.hostingChanged({ state: 'available', endpoint: 'https://a.trycloudflare.com', generation: 4 });
  for (let attempt = 0; attempt < 10 && !releaseRefresh; attempt++) await new Promise(resolve => setImmediate(resolve));
  await f.publisher.signOut(); releaseRefresh(response({ id_token: 'late-id', refresh_token: 'late-refresh', user_id: 'publisher-uid', expires_in: '3600' })); await hostingUpdate;
  assert.equal(f.saved, null); assert.equal(f.publisher.status().signedIn, false);
  assert.equal(f.calls.filter(c => c.url.includes('/documents:commit')).length, 0);
});

test('changing Firebase project config invalidates stored credentials from the previous project', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  await f.publisher.configure({ projectId: 'proj-2', apiKey: 'different-api-key' });
  assert.equal(f.saved, null); assert.equal(f.publisher.status().signedIn, false); assert.equal(f.publisher.status().projectId, 'proj-2');
});

test('protected credentials restore only under the same Firebase project config', async () => {
  const first = setup(); await first.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await first.publisher.signIn('x@y.z', 'pw');
  const restored = createDiscoveryPublisher({ timers: first.timers, storage: { load: () => first.saved, save: () => {}, clear: () => {} }, freshDescriptor: async () => ({}), fetch: async () => response({}) });
  await restored.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await restored.restore();
  assert.equal(restored.status().signedIn, true); assert.equal(restored.status().uid, 'publisher-uid');
  const otherProject = createDiscoveryPublisher({ timers: first.timers, storage: { load: () => first.saved, save: () => {}, clear: () => { first.clearCalled = true; } }, freshDescriptor: async () => ({}), fetch: async () => response({}) });
  await otherProject.configure({ projectId: 'proj-2', apiKey: 'different-api-key' }); await otherProject.restore();
  assert.equal(otherProject.status().signedIn, false); assert.equal(first.clearCalled, true);
});

test('signing back in resumes the still-active host, while a signed-out host still exposes its server ID', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' });
  await f.publisher.hostingChanged({ state: 'available', endpoint: 'https://a.trycloudflare.com', generation: 4 });
  assert.ok(f.publisher.status().serverId); assert.equal(f.publisher.status().publishing, false);
  await f.publisher.signIn('x@y.z', 'pw');
  assert.equal(f.calls.filter(c => c.url.includes('/documents:commit')).length, 1);
  await f.publisher.signOut(); await f.publisher.signIn('x@y.z', 'pw');
  assert.equal(f.calls.filter(c => c.url.includes('/documents:commit')).length, 2);
});

test('repeated transient Firebase failures pause publisher retries without affecting hosting', async () => {
  const f = setup(); await f.publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await f.publisher.signIn('x@y.z', 'pw');
  f.publisher.setFetch(async () => response({ error: { status: 'UNAVAILABLE' } }, 503));
  await f.publisher.hostingChanged({ state: 'available', endpoint: 'https://a.trycloudflare.com', generation: 4 });
  assert.equal(f.publisher.status().publishing, false); assert.equal(f.timers.items.length, 0);
  assert.match(f.publisher.status().error, /retry sign-in or hosting/i);
});

test('endpoint rotation publishes the new descriptor immediately and fences an older pending descriptor', async () => {
  const timers = { items: [], setInterval(fn, ms) { const t = { fn, ms }; this.items.push(t); return t; }, clearInterval(t) { this.items = this.items.filter(x => x !== t); }, setTimeout(fn) { queueMicrotask(fn); return 0; } };
  const commits = []; let releaseOld, signalOldStarted; const oldStarted = new Promise(resolve => { signalOldStarted = resolve; }); let descriptorCalls = 0;
  const descriptor = (endpoint, generation, revision) => ({ payload: encode({ server_id: 'b3167188-96c1-4968-84c5-7bad4e70a632', connection_generation: generation, publication_revision: revision, available: true, endpoint, issued_at: 1800000000, expires_at: 1800000300 }), signature: 'sig', public_key: 'pub' });
  const publisher = createDiscoveryPublisher({ now: () => 1_800_000_000_000, timers, storage: { load: () => null, save: () => {}, clear: () => {} },
    freshDescriptor: async () => { if (descriptorCalls++ === 0) { signalOldStarted(); return new Promise(resolve => { releaseOld = resolve; }); } return descriptor('https://new.trycloudflare.com', 5, 2); },
    fetch: async (url, init = {}) => { if (url.includes('signInWithPassword')) return response({ localId: 'u', idToken: 'id', refreshToken: 'refresh', expiresIn: '3600' }); if (url.includes('/documents:commit')) commits.push(JSON.parse(init.body)); return response({}); } });
  await publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await publisher.signIn('x@y.z', 'pw');
  const oldPublication = publisher.hostingChanged({ state: 'available', endpoint: 'https://old.trycloudflare.com', generation: 4 }); await oldStarted;
  await publisher.hostingChanged({ state: 'available', endpoint: 'https://new.trycloudflare.com', generation: 5 });
  releaseOld(descriptor('https://old.trycloudflare.com', 4, 1)); await oldPublication;
  assert.equal(commits.length, 1); assert.match(commits[0].writes[0].update.name, /new\.trycloudflare\.com|b3167188-96c1-4968-84c5-7bad4e70a632/);
  const payload = JSON.parse(Buffer.from(commits[0].writes[0].update.fields.payload.stringValue, 'base64url').toString());
  assert.equal(payload.endpoint, 'https://new.trycloudflare.com'); assert.equal(payload.connection_generation, 5);
});

test('request timeouts consume the bounded retry budget and pause scheduled publication', async () => {
  const timers = { items: [], setInterval(fn, ms) { const t = { fn, ms }; this.items.push(t); return t; }, clearInterval(t) { this.items = this.items.filter(x => x !== t); }, setTimeout(fn) { queueMicrotask(fn); return 0; } };
  const publisher = createDiscoveryPublisher({ timers, requestTimeoutMs: 5, now: () => 1_800_000_000_000, storage: { load: () => null, save: () => {}, clear: () => {} },
    freshDescriptor: async () => ({ payload: encode({ server_id: 'b3167188-96c1-4968-84c5-7bad4e70a632', connection_generation: 4, publication_revision: 1, available: true, endpoint: 'https://a.trycloudflare.com', issued_at: 1800000000, expires_at: 1800000300 }), signature: 'sig', public_key: 'pub' }),
    fetch: async (url, init) => {
      if (url.includes('signInWithPassword')) return response({ localId: 'u', idToken: 'id', refreshToken: 'refresh', expiresIn: '3600' });
      return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(Error('aborted')), { once: true }));
    } });
  await publisher.configure({ projectId: 'proj-1', apiKey: 'long-api-key' }); await publisher.signIn('x@y.z', 'pw');
  await publisher.hostingChanged({ state: 'available', endpoint: 'https://a.trycloudflare.com', generation: 4 });
  assert.equal(publisher.status().publishing, false); assert.equal(timers.items.length, 0); assert.match(publisher.status().error, /retry sign-in or hosting/i);
});

test('Firestore write shape stays within the discovery rules field and freshness contract', () => {
  const descriptor = { payload: encode({ server_id: 'b3167188-96c1-4968-84c5-7bad4e70a632', connection_generation: 4, publication_revision: 1, available: true, endpoint: 'https://a.trycloudflare.com', issued_at: 1800000000, expires_at: 1800000300 }), signature: 'sig', public_key: 'pub' };
  const write = buildDiscoveryCommit('proj-1', descriptor, 1_800_000_000_000).writes[0];
  assert.deepEqual(Object.keys(write.update.fields).sort(), ['server_id','connection_generation','publication_revision','available','endpoint','issued_at','expires_at','payload','signature','public_key'].sort());
  assert.deepEqual(write.updateTransforms, [{ fieldPath: 'updated_at', setToServerValue: 'REQUEST_TIME' }]);
  assert.throws(() => buildDiscoveryCommit('proj-1', { ...descriptor, payload: 'not-json' }, 1_800_000_000_000));
});

