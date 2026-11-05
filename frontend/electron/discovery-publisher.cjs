'use strict';

const INTERVAL_MS = 60000;
const RETRIES = [1000, 2000, 4000];
function buildDiscoveryCommit(projectId, descriptor, now = Date.now()) {
  if (!descriptor || typeof descriptor.payload !== 'string' || descriptor.payload.length > 12000 || !/^[A-Za-z0-9_-]+$/.test(descriptor.payload) ||
      typeof descriptor.signature !== 'string' || descriptor.signature.length > 128 || !/^[A-Za-z0-9_-]+$/.test(descriptor.signature) ||
      typeof descriptor.public_key !== 'string' || descriptor.public_key.length > 512 || !/^[A-Za-z0-9_-]+$/.test(descriptor.public_key)) throw Error('Invalid signed discovery descriptor.');
  let payload;
  try { payload = JSON.parse(Buffer.from(descriptor.payload, 'base64url').toString('utf8')); } catch { throw Error('Invalid signed discovery descriptor.'); }
  const nowSeconds = Math.floor(now / 1000);
  if (payload.available !== true || !/^[0-9a-f-]{36}$/i.test(payload.server_id || '') || !Number.isSafeInteger(payload.connection_generation) || payload.connection_generation < 1 ||
      !Number.isSafeInteger(payload.publication_revision) || payload.publication_revision < 1 || typeof payload.endpoint !== 'string' ||
      !Number.isSafeInteger(payload.issued_at) || Math.abs(payload.issued_at - nowSeconds) > 60 || !Number.isSafeInteger(payload.expires_at) || payload.expires_at <= nowSeconds || payload.expires_at > payload.issued_at + 300) throw Error('Invalid or stale signed discovery descriptor.');
  const endpoint = new URL(payload.endpoint);
  if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.port || endpoint.pathname !== '/' || endpoint.search || endpoint.hash || payload.endpoint !== `https://${endpoint.hostname}`) throw Error('Invalid signed discovery endpoint.');
  const fields = { server_id: { stringValue: payload.server_id }, connection_generation: { integerValue: String(payload.connection_generation) }, publication_revision: { integerValue: String(payload.publication_revision) }, available: { booleanValue: true }, endpoint: { stringValue: payload.endpoint }, issued_at: { integerValue: String(payload.issued_at) }, expires_at: { integerValue: String(payload.expires_at) }, payload: { stringValue: descriptor.payload }, signature: { stringValue: descriptor.signature }, public_key: { stringValue: descriptor.public_key } };
  return { writes: [{ update: { name: `projects/${projectId}/databases/(default)/documents/discovery/${payload.server_id}`, fields }, updateMask: { fieldPaths: Object.keys(fields) }, updateTransforms: [{ fieldPath: 'updated_at', setToServerValue: 'REQUEST_TIME' }] }] };
}
function createDiscoveryPublisher({ fetch: fetchFn = globalThis.fetch, storage, freshDescriptor, timers = globalThis, now = Date.now, requestTimeoutMs = 10000 }) {
  let config = { projectId: '', apiKey: '' }, refreshToken = null, idToken = null, idTokenExpiresAt = 0, uid = null, lastError = null, serverId = null;
  let active = false, fence = 0, authEpoch = 0, timer = null, inFlight = null, publicationController = null, quotaStopped = false, paused = false, hostingIdentity = null;
  const status = () => ({ configured: Boolean(config.projectId && config.apiKey), projectId: config.projectId, apiKey: config.apiKey, signedIn: Boolean(refreshToken), uid, serverId, publishing: active && Boolean(refreshToken) && !paused && !quotaStopped, error: lastError });
  function abortPublication() { publicationController?.abort(); }
  function abortRequests() { abortPublication(); for (const controller of authControllers) controller.abort(); authControllers.clear(); }
  const authControllers = new Set();
  async function request(url, init = {}, controller = null) {
    const local = controller || new AbortController();
    const temporary = !controller;
    if (temporary) authControllers.add(local);
    const requestController = new AbortController();
    let timedOut = false;
    const onAbort = () => requestController.abort();
    local.signal.addEventListener('abort', onAbort, { once: true });
    const timeout = setTimeout(() => { timedOut = true; requestController.abort(); }, requestTimeoutMs);
    try {
      const response = await fetchFn(url, { ...init, redirect: 'error', signal: requestController.signal });
      if (local.signal.aborted) { const e = Error('Firebase request cancelled.'); e.code = 'ABORTED'; throw e; }
      const declared = Number(response.headers?.get?.('content-length'));
      if (Number.isFinite(declared) && declared > 65536) throw Error('Firebase response exceeded the size limit.');
      let body = {};
      if (response.body?.getReader) {
        const reader = response.body.getReader(), chunks = []; let size = 0;
        try { while (true) { const { done, value } = await reader.read(); if (done) break; size += value.byteLength; if (size > 65536) { await reader.cancel(); throw Error('Firebase response exceeded the size limit.'); } chunks.push(value); } }
        finally { reader.releaseLock?.(); }
        if (size) body = JSON.parse(new TextDecoder().decode(Buffer.concat(chunks.map(chunk => Buffer.from(chunk)), size)));
      } else body = await response.json().catch(() => ({}));
      if (!response.ok) { const e = Error('Firebase discovery request failed.'); e.code = body?.error?.status || body?.error?.message || String(response.status); e.httpStatus = response.status; throw e; }
      return body;
    } catch (error) {
      if (timedOut) { error = Error('Firebase request timed out.'); error.code = 'TIMEOUT'; }
      else if (local.signal.aborted) { error = Error('Firebase request cancelled.'); error.code = 'ABORTED'; }
      throw error;
    } finally { clearTimeout(timeout); local.signal.removeEventListener('abort', onAbort); if (temporary) authControllers.delete(local); }
  }
  function currentAuth(epoch, projectId = config.projectId, apiKey = config.apiKey) { return epoch === authEpoch && projectId === config.projectId && apiKey === config.apiKey; }
  async function getIdToken(epoch, controller) {
    if (!currentAuth(epoch) || controller.signal.aborted) throw Error('Discovery authentication changed.');
    if (!idToken || now() >= idTokenExpiresAt - 60000) {
      if (!refreshToken) throw Error('Sign in to Firebase discovery first.');
      const priorRefresh = refreshToken, priorUid = uid, projectId = config.projectId, apiKey = config.apiKey;
      const result = await request(`https://securetoken.googleapis.com/v1/token?key=${encodeURIComponent(apiKey)}`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: priorRefresh }) }, controller);
      if (!currentAuth(epoch, projectId, apiKey) || controller.signal.aborted || refreshToken !== priorRefresh || uid !== priorUid) throw Error('Discovery authentication changed.');
      const expires = Number(result.expires_in);
      if (typeof result.id_token !== 'string' || !result.id_token || typeof result.refresh_token !== 'string' || !result.refresh_token || result.user_id !== priorUid || !Number.isFinite(expires) || expires < 60 || expires > 86400) throw Error('Firebase returned invalid refresh credentials.');
      const nextCredential = JSON.stringify({ projectId, apiKey, refreshToken: result.refresh_token, uid });
      try { storage.save(nextCredential); } catch { throw Error('Protected credential storage is unavailable.'); }
      if (!currentAuth(epoch, projectId, apiKey) || controller.signal.aborted) throw Error('Discovery authentication changed.');
      idToken = result.id_token; idTokenExpiresAt = now() + expires * 1000; refreshToken = result.refresh_token;
    }
    return idToken;
  }
  async function publish(f) {
    if (!active || f !== fence || (inFlight && !publicationController?.signal.aborted) || quotaStopped || paused) return;
    const controller = new AbortController(); publicationController = controller;
    const epoch = authEpoch;
    const task = (async () => {
      const descriptor = await freshDescriptor();
      if (!active || f !== fence || controller.signal.aborted) return;
      const body = buildDiscoveryCommit(config.projectId, descriptor, now());
      serverId = body.writes[0].update.fields.server_id.stringValue;
      if (!refreshToken) { lastError = 'Sign in to Firebase discovery to publish this server.'; paused = true; if (timer) timers.clearInterval(timer); timer = null; return; }
      for (let attempt = 0; attempt <= RETRIES.length; attempt++) {
        try {
          const projectId = config.projectId;
          const token = await getIdToken(epoch, controller);
          if (!active || f !== fence || controller.signal.aborted || !currentAuth(epoch, projectId)) return;
          await request(`https://firestore.googleapis.com/v1/projects/${encodeURIComponent(projectId)}/databases/(default)/documents:commit`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, controller);
          if (!active || f !== fence || controller.signal.aborted || !currentAuth(epoch, projectId)) return;
          lastError = null;
          return;
        } catch (error) {
          if (!active || f !== fence || controller.signal.aborted || !currentAuth(epoch)) return;
          const code = String(error.code || '').toUpperCase();
          if (/QUOTA|RESOURCE_EXHAUSTED/.test(code) || error.httpStatus === 429) { quotaStopped = true; lastError = 'Firebase quota reached; automatic publishing is paused.'; if (timer) timers.clearInterval(timer); timer = null; return; }
          if (/PERMISSION_DENIED|UNAUTHENTICATED|INVALID_REFRESH_TOKEN|TOKEN_EXPIRED|USER_DISABLED|INVALID_LOGIN_CREDENTIALS/.test(code) || error.httpStatus === 401 || error.httpStatus === 403) {
            paused = true; lastError = /INVALID_REFRESH_TOKEN|TOKEN_EXPIRED|USER_DISABLED/.test(code) ? 'Firebase sign-in expired. Sign in again to resume publishing.' : 'Firebase denied publishing. Check the publisher account and Firestore rules.';
            if (/INVALID_REFRESH_TOKEN|TOKEN_EXPIRED|USER_DISABLED/.test(code)) { refreshToken = idToken = uid = null; idTokenExpiresAt = 0; storage.clear(); }
            if (timer) timers.clearInterval(timer); timer = null; return;
          }
          if (attempt === RETRIES.length) { paused = true; lastError = 'Firebase discovery update failed; retry sign-in or hosting to resume.'; if (timer) timers.clearInterval(timer); timer = null; return; }
          await new Promise(resolve => timers.setTimeout(resolve, RETRIES[attempt]));
          if (!active || f !== fence || controller.signal.aborted || !currentAuth(epoch)) return;
        }
      }
    })();
    inFlight = task;
    try { await task; } catch { if (active && f === fence && !controller.signal.aborted) { paused = true; lastError = 'Signed discovery data could not be refreshed; retry hosting to resume.'; if (timer) timers.clearInterval(timer); timer = null; } } finally { if (inFlight === task) inFlight = null; if (publicationController === controller) publicationController = null; }
  }
  function startPublisherNow() {
    if (!active) return;
    if (timer) timers.clearInterval(timer);
    timer = timers.setInterval(() => void publish(fence), INTERVAL_MS);
    return publish(fence);
  }
  return {
    setFetch(fn) { fetchFn = fn; }, status,
    async configure(value) {
      if (!value || typeof value.projectId !== 'string' || !/^[a-z0-9-]{4,64}$/.test(value.projectId) || typeof value.apiKey !== 'string' || value.apiKey.length < 10 || value.apiKey.length > 200) throw Error('Enter a valid Firebase project ID and Web API key.');
      if ((config.projectId || config.apiKey) && (config.projectId !== value.projectId || config.apiKey !== value.apiKey)) {
        authEpoch++; abortRequests(); refreshToken = idToken = uid = null; idTokenExpiresAt = 0; paused = true; quotaStopped = false; lastError = 'Sign in with the publisher account for this Firebase project.';
        if (timer) timers.clearInterval(timer); timer = null;
        storage.clear();
      }
      config = { projectId: value.projectId, apiKey: value.apiKey };
    },
    async restore() {
      const epoch = authEpoch, projectId = config.projectId, apiKey = config.apiKey;
      try { const saved = JSON.parse(storage.load() || 'null'); if (!currentAuth(epoch, projectId, apiKey)) return; if (saved?.projectId === projectId && saved?.apiKey === apiKey && typeof saved.refreshToken === 'string' && typeof saved.uid === 'string') { refreshToken = saved.refreshToken; uid = saved.uid; paused = false; } else if (saved) storage.clear(); }
      catch { storage.clear(); refreshToken = idToken = uid = null; }
      if (active && refreshToken) await startPublisherNow();
    },
    async signIn(email, password) {
      if (!config.projectId || !config.apiKey) throw Error('Configure Firebase project ID and API key first.');
      if (typeof email !== 'string' || email.length > 254 || typeof password !== 'string' || password.length > 1024 || !email.trim() || !password) throw Error('Enter valid Firebase sign-in details.');
      authEpoch++; const epoch = authEpoch, projectId = config.projectId, apiKey = config.apiKey;
      abortRequests(); refreshToken = idToken = uid = null; idTokenExpiresAt = 0; paused = true; lastError = null;
      storage.clear();
      const controller = new AbortController(); authControllers.add(controller);
      try {
        const result = await request(`https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${encodeURIComponent(apiKey)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password, returnSecureToken: true }) }, controller);
        if (!currentAuth(epoch, projectId, apiKey) || controller.signal.aborted) throw Error('Firebase sign-in was superseded.');
        const expires = Number(result.expiresIn);
        if (typeof result.localId !== 'string' || !result.localId || result.localId.length > 128 || typeof result.idToken !== 'string' || !result.idToken || typeof result.refreshToken !== 'string' || !result.refreshToken || !Number.isFinite(expires) || expires < 60 || expires > 86400) throw Error('Firebase returned invalid sign-in credentials.');
        try { storage.save(JSON.stringify({ projectId, apiKey, refreshToken: result.refreshToken, uid: result.localId })); } catch { throw Error('Protected credential storage is unavailable.'); }
        if (!currentAuth(epoch, projectId, apiKey) || controller.signal.aborted) { storage.clear(); throw Error('Firebase sign-in was superseded.'); }
        refreshToken = result.refreshToken; uid = result.localId; idToken = result.idToken; idTokenExpiresAt = now() + expires * 1000; lastError = null; paused = false; quotaStopped = false;
        if (active) await startPublisherNow();
        return { signedIn: true, uid };
      } catch (error) { if (currentAuth(epoch, projectId, apiKey)) { refreshToken = idToken = uid = null; idTokenExpiresAt = 0; try { storage.clear(); } catch {} } throw error; }
      finally { authControllers.delete(controller); }
    },
    async signOut() { authEpoch++; abortRequests(); fence++; if (timer) timers.clearInterval(timer); timer = null; refreshToken = idToken = uid = null; idTokenExpiresAt = 0; storage.clear(); quotaStopped = false; paused = false; lastError = null; },
    async hostingChanged(hosting) {
      if (hosting?.state !== 'available') { if (active) { active = false; hostingIdentity = null; fence++; abortPublication(); } if (timer) timers.clearInterval(timer); timer = null; return; }
      const identity = `${hosting.endpoint || ''}|${hosting.generation || ''}`;
      if (active && identity === hostingIdentity) return;
      active = true; hostingIdentity = identity; fence++; abortPublication(); quotaStopped = false; paused = false; lastError = null; await startPublisherNow();
    },
    async publishNow() { return publish(fence); },
  };
}
function registerDiscoveryIPC(ipcMain, publisher, getWindow) {
  const channels = ['discovery-status','discovery-configure','discovery-sign-in','discovery-sign-out'];
  for (const name of channels) ipcMain.handle(name, async (event, value) => {
    const win = getWindow();
    if (!win || win.isDestroyed() || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) throw Error('Untrusted discovery controller');
    const origin = new URL(event.senderFrame.url).origin;
    if (!['http://localhost:3000','http://localhost:5173','http://localhost:8765'].includes(origin)) throw Error('Untrusted discovery controller');
    if (name === 'discovery-status' && value === undefined) return publisher.status();
    if (name === 'discovery-configure' && value && typeof value.projectId === 'string' && typeof value.apiKey === 'string') return publisher.configure(value).then(() => publisher.status());
    if (name === 'discovery-sign-in' && value && typeof value.email === 'string' && typeof value.password === 'string') return publisher.signIn(value.email, value.password);
    if (name === 'discovery-sign-out' && value === undefined) { await publisher.signOut(); return publisher.status(); }
    throw Error('Invalid discovery request');
  });
}
module.exports = { createDiscoveryPublisher, registerDiscoveryIPC, buildDiscoveryCommit, INTERVAL_MS };
