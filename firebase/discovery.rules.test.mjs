import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { initializeTestEnvironment, assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import { doc, setDoc, getDoc, getDocs, collection, deleteDoc, serverTimestamp, Timestamp } from 'firebase/firestore';
import publisher from '../frontend/electron/discovery-publisher.cjs';

const server = '11111111-1111-4111-8111-111111111111';
let env;
const record = (changes = {}) => {
  const now = Math.floor(Date.now() / 1000);
  return { server_id: server, connection_generation: 1, publication_revision: 1, available: true,
    endpoint: 'https://radio-test.trycloudflare.com', issued_at: now, expires_at: now + 300,
    payload: 'cGF5bG9hZA', signature: 'c2lnbmF0dXJl', public_key: 'cHVibGlj', updated_at: serverTimestamp(), ...changes };
};
const db = uid => uid ? env.authenticatedContext(uid).firestore() : env.unauthenticatedContext().firestore();
const ref = uid => doc(db(uid), 'discovery', server);
before(async () => {
  const rules = readFileSync(new URL('./firestore.rules', import.meta.url), 'utf8')
    .replaceAll('__SERVER_ID__', server).replaceAll('__PUBLISHER_UID__', 'publisher').replaceAll('__READER_UID__', 'reader');
  env = await initializeTestEnvironment({ projectId: 'demo-radio-discovery', firestore: { rules, host: '127.0.0.1', port: 18880 } });
});
after(async () => { await env?.cleanup(); });
beforeEach(async () => { await env.clearFirestore(); });
test('publisher creates and authorized identities get the exact document', async () => {
  await assertSucceeds(setDoc(ref('publisher'), record()));
  await assertSucceeds(getDoc(ref('reader')));
  await assertSucceeds(getDoc(ref('publisher')));
});
test('public, strangers and reader cannot publish or read private documents', async () => {
  for (const uid of [null, 'stranger', 'reader']) await assertFails(setDoc(ref(uid), record()));
  await assertFails(getDoc(ref(null)));
  await assertFails(getDoc(ref('stranger')));
});
test('listing, deletes and unrelated paths are denied even to publisher', async () => {
  await assertSucceeds(setDoc(ref('publisher'), record()));
  await assertFails(getDocs(collection(db('publisher'), 'discovery')));
  await assertFails(deleteDoc(ref('publisher')));
  await assertFails(setDoc(doc(db('publisher'), 'discovery', 'other'), record()));
  await assertFails(setDoc(doc(db('publisher'), 'other', server), record()));
});
test('only advancing revision and nondecreasing generation are accepted', async () => {
  await assertSucceeds(setDoc(ref('publisher'), record()));
  await assertFails(setDoc(ref('publisher'), record()));
  await assertSucceeds(setDoc(ref('publisher'), record({ publication_revision: 2 })));
  await assertFails(setDoc(ref('publisher'), record({ publication_revision: 3, connection_generation: 0 })));
  await assertSucceeds(setDoc(ref('publisher'), record({ publication_revision: 3, connection_generation: 2 })));
});
test('field injection, missing fields and type changes are denied', async () => {
  for (const changes of [{ bearer: 'never' }, { server_id: 'other' }, { available: 'true' },
    { connection_generation: 1.5 }, { publication_revision: -1 }, { issued_at: 'now' }, { payload: '' },
    { public_key: 'x'.repeat(513) }, { signature: 'x'.repeat(129) }, { reader_uid: 'stranger' }]) {
    await assertFails(setDoc(ref('publisher'), record(changes)));
  }
  const missing = record(); delete missing.signature;
  await assertFails(setDoc(ref('publisher'), missing));
});
test('endpoint shapes and stale or unbounded advertisements fail closed', async () => {
  for (const endpoint of ['http://radio.trycloudflare.com', 'https://radio.trycloudflare.com.evil.com',
    'https://u@radio.trycloudflare.com', 'https://radio.trycloudflare.com/path', 'https://radio.trycloudflare.com:443',
    'https://radio.trycloudflare.com?q=x', 'https://-radio.trycloudflare.com']) {
    await assertFails(setDoc(ref('publisher'), record({ endpoint })));
  }
  const now = Math.floor(Date.now() / 1000);
  for (const changes of [{ issued_at: now - 1000, expires_at: now - 700 }, { issued_at: now + 120 },
    { expires_at: now + 1000 }, { expires_at: now - 1 }, { updated_at: Timestamp.fromMillis(0) }]) {
    await assertFails(setDoc(ref('publisher'), record(changes)));
  }
});
test('REST commit server timestamp transform and protected one-document read work', async () => {
  const project = 'demo-radio-discovery';
  const document = `projects/${project}/databases/(default)/documents/discovery/${server}`;
  const token = uid => `${Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')}.${Buffer.from(JSON.stringify({
    sub: uid, user_id: uid, aud: project, iss: `https://securetoken.google.com/${project}`,
    iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 3600,
    firebase: { sign_in_provider: 'password', identities: {} },
  })).toString('base64url')}.`;
  const source = record(); delete source.updated_at;
  const descriptor = { payload: Buffer.from(JSON.stringify({ protocol_version: 1,
    ...Object.fromEntries(Object.entries(source).filter(([key]) => !['payload', 'signature', 'public_key'].includes(key))) })).toString('base64url'),
    signature: source.signature, public_key: source.public_key };
  const commit = publisher.buildDiscoveryCommit(project, descriptor);
  const result = await fetch(`http://127.0.0.1:18880/v1/projects/${project}/databases/(default)/documents:commit`, {
    method: 'POST', headers: { Authorization: `Bearer ${token('publisher')}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(commit),
  });
  assert.equal(result.status, 200, await result.text());
  const read = await fetch(`http://127.0.0.1:18880/v1/${document}`, { headers: { Authorization: `Bearer ${token('reader')}` } });
  assert.equal(read.status, 200);
  const body = await read.json();
  assert.ok(body.fields.updated_at.timestampValue);
  const denied = await fetch(`http://127.0.0.1:18880/v1/${document}`);
  assert.equal(denied.status, 403);
});
