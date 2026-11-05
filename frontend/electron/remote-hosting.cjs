const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const https = require('https');
const crypto = require('crypto');

const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const LEASE_INTERVAL_MS = 5000;
const ENDPOINT_READY_TIMEOUT_MS = 90000;
const TRY_CLOUDFLARE_HOST = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com$/i;
const STATES = new Set(['off', 'starting_gateway', 'starting_tunnel', 'verifying_endpoint', 'available', 'reconnecting', 'stopping', 'error']);

function validateTunnelPath(value) {
  if (typeof value !== 'string' || !value.trim()) throw Error('Choose the cloudflared executable path.');
  const absolute = path.resolve(value.trim());
  if (path.basename(absolute).toLowerCase() !== 'cloudflared.exe' || !fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
    throw Error('The selected path must be an existing cloudflared.exe file.');
  }
  return absolute;
}

function parseQuickTunnelUrl(line) {
  if (typeof line !== 'string' || line.length > 8192) return null;
  const matches = line.match(/https:\/\/[^\s"'<>]+/g) || [];
  for (const candidate of matches) {
    const trimmed = candidate.replace(/[),.;]+$/, '');
    let url;
    try { url = new URL(trimmed); } catch { continue; }
    const authority = trimmed.match(/^https:\/\/([^/?#]+)/i)?.[1];
    if (url.protocol === 'https:' && authority === url.hostname && !url.username && !url.password && !url.port && url.pathname === '/' && !url.search && !url.hash && TRY_CLOUDFLARE_HOST.test(url.hostname)) {
      return `https://${url.hostname}`;
    }
  }
  return null;
}

function b64url(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) throw Error('Invalid descriptor encoding');
  return Buffer.from(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4), 'base64');
}

function validateDescriptor(descriptor, endpoint, expectedGeneration) {
  if (!descriptor || typeof descriptor !== 'object') throw Error('Missing endpoint descriptor');
  const payloadBytes = b64url(descriptor.payload), signature = b64url(descriptor.signature), publicKey = b64url(descriptor.public_key);
  if (payloadBytes.length > 4096 || signature.length > 256 || publicKey.length > 256) throw Error('Oversized endpoint descriptor');
  const key = crypto.createPublicKey({ key: publicKey, format: 'der', type: 'spki' });
  if (key.asymmetricKeyType !== 'ec' || key.asymmetricKeyDetails?.namedCurve !== 'prime256v1' || !crypto.verify('sha256', payloadBytes, key, signature)) throw Error('Invalid endpoint signature');
  const payload = JSON.parse(payloadBytes.toString('utf8'));
  if (payload.protocol_version !== 1 || payload.endpoint !== endpoint || !/^[0-9a-f-]{36}$/i.test(payload.server_id) ||
      payload.connection_generation !== expectedGeneration || payload.available !== true || !Number.isSafeInteger(payload.expires_at) ||
      payload.expires_at * 1000 <= Date.now() || payload.issued_at * 1000 > Date.now() + 30000) throw Error('Stale or mismatched endpoint descriptor');
  return { payload: descriptor.payload, signature: descriptor.signature, public_key: descriptor.public_key };
}

function taggedError(code, message) { const error = Error(message); error.code = code; return error; }

function probeTunnelEndpoint(endpoint, expectedDescriptor, expectedGeneration, requestFn = https.request, signal = null) {
  const url = new URL(endpoint);
  if (url.protocol !== 'https:' || !TRY_CLOUDFLARE_HOST.test(url.hostname) || url.pathname !== '/' || url.search || url.hash || url.port || url.username || url.password) return Promise.reject(taggedError('IDENTITY_INVALID', 'Invalid tunnel URL'));
  let expected;
  try { expected = validateDescriptor(expectedDescriptor, endpoint, expectedGeneration); }
  catch { return Promise.reject(taggedError('IDENTITY_INVALID', 'Invalid signed endpoint descriptor')); }
  return new Promise((resolve, reject) => {
    let settled = false, size = 0, chunks = [];
    const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(deadline); signal?.removeEventListener('abort', onAbort); error ? reject(error) : resolve(value); };
    const deadline = setTimeout(() => req.destroy(Error('Tunnel probe timed out')), 5000);
    const req = requestFn({ protocol: 'https:', hostname: url.hostname, path: '/api/portable/v1/identity', method: 'GET', agent: false, headers: { Accept: 'application/json', Connection: 'close' } }, response => {
      if (response.statusCode !== 200 || response.headers.location) {
        const transient = [429, 500, 502, 503, 504, 522, 523, 524, 530].includes(response.statusCode);
        response.destroy(); finish(taggedError(transient ? 'TRANSIENT' : 'IDENTITY_INVALID', 'Tunnel identity probe failed')); return;
      }
      const declared = response.headers['content-length'];
      if (declared && (!/^\d+$/.test(declared) || Number(declared) > 8192)) { response.destroy(); finish(taggedError('IDENTITY_INVALID', 'Tunnel identity response too large')); return; }
      response.on('data', chunk => {
        size += chunk.length;
        if (size > 8192) { response.destroy(); finish(taggedError('IDENTITY_INVALID', 'Tunnel identity response too large')); return; }
        chunks.push(chunk);
      });
      response.on('end', () => {
        try {
          const actual = JSON.parse(Buffer.concat(chunks, size).toString('utf8'));
          const checked = validateDescriptor(actual, endpoint, JSON.parse(b64url(expected.payload).toString('utf8')).connection_generation);
          if (checked.public_key !== expected.public_key || checked.payload !== expected.payload || checked.signature !== expected.signature) {
            // The identity endpoint signs fresh timestamps; accept a fresh signature only
            // when the exact pinned key and endpoint/generation identity match.
            const actualPayload = JSON.parse(b64url(checked.payload).toString('utf8'));
            const expectedPayload = JSON.parse(b64url(expected.payload).toString('utf8'));
            if (checked.public_key !== expected.public_key || actualPayload.server_id !== expectedPayload.server_id || actualPayload.endpoint !== expectedPayload.endpoint || actualPayload.connection_generation !== expectedPayload.connection_generation || actualPayload.publication_revision < expectedPayload.publication_revision) throw Error('Tunnel server identity mismatch');
          }
          finish(null, checked);
        } catch { finish(taggedError('IDENTITY_INVALID', 'Tunnel server identity could not be verified')); }
      });
      response.on('error', () => finish(taggedError('TRANSIENT', 'Tunnel identity probe failed')));
    });
    const onAbort = () => req.destroy(taggedError('ABORTED', 'Tunnel probe cancelled'));
    req.on('error', () => finish(signal?.aborted ? taggedError('ABORTED', 'Tunnel probe cancelled') : taggedError('TRANSIENT', 'Tunnel identity probe failed')));
    if (signal?.aborted) return onAbort();
    signal?.addEventListener('abort', onAbort, { once: true });
    req.end();
  });
}

function createRemoteHostingControl({ request, startProcess, stopProcess, probe = async (url, descriptor, generation) => validateDescriptor(descriptor, url, generation), emit = () => {}, timers = globalThis, initialState = {} }) {
  let state = { state: 'off', endpoint: null, error: null, generation: 0, ...initialState };
  let processHandle = null, leaseTimer = null, retryTimer = null, startupTimer = null, generation = 0, connectionGeneration = null, retries = 0, stopping = false, renewal = null, probeAbort = null, admittingFence = null;
  let binaryPath = initialState.binaryPath || '';
  const publish = () => emit({ ...state, binaryPath });
  const setState = (name, extra = {}) => { if (!STATES.has(name)) throw Error('Invalid hosting state'); state = { ...state, state: name, ...extra }; publish(); };
  const clearTimers = () => { if (leaseTimer) timers.clearInterval(leaseTimer); if (retryTimer) timers.clearTimeout(retryTimer); if (startupTimer) timers.clearTimeout(startupTimer); leaseTimer = retryTimer = startupTimer = null; };

  async function closeAdmission(leaseGeneration) {
    if (!Number.isSafeInteger(leaseGeneration)) return;
    try { await request('/remote/stop', { method: 'POST', body: JSON.stringify({ connection_generation: leaseGeneration }) }); } catch (_) {}
  }
  async function stopTunnel() {
    const child = processHandle; processHandle = null;
    if (child) { try { await stopProcess(child); } catch (_) {} }
  }
  function handleExit(child, code) {
    if (processHandle !== child || stopping) return;
    processHandle = null;
    const fence = generation; const leaseGeneration = connectionGeneration; generation++; connectionGeneration = null;
    clearTimers();
    probeAbort?.abort();
    void closeAdmission(leaseGeneration);
    state = { ...state, endpoint: null, error: `cloudflared exited (${code ?? 'unknown'})` };
    scheduleRetry();
  }
  function scheduleRetry() {
    if (stopping) return;
    if (retries >= RETRY_DELAYS_MS.length) { setState('error', { endpoint: null, error: 'Tunnel retry limit reached. Select Retry to try again.' }); return; }
    const delay = RETRY_DELAYS_MS[retries++];
    setState('reconnecting', { endpoint: null, error: `Tunnel disconnected. Retrying in ${Math.round(delay / 1000)} seconds (${retries}/${RETRY_DELAYS_MS.length}).` });
    retryTimer = timers.setTimeout(() => { retryTimer = null; void startAttempt(); }, delay);
  }
  async function renew(fence, leaseGeneration, endpoint) {
    if (stopping || fence !== generation || connectionGeneration !== leaseGeneration || !processHandle || renewal) return;
    renewal = (async () => {
    try {
      const result = await request('/remote/renew', { method: 'POST', body: JSON.stringify({ connection_generation: leaseGeneration }) });
      if (fence !== generation || connectionGeneration !== leaseGeneration || stopping) return;
      if (result?.endpoint && result.endpoint !== endpoint) throw Error('Backend endpoint changed');
    } catch {
      if (fence !== generation || connectionGeneration !== leaseGeneration || stopping) return;
      generation++;
      connectionGeneration = null;
      clearTimers();
      probeAbort?.abort();
      await closeAdmission(leaseGeneration);
      await stopTunnel();
      scheduleRetry();
    }
    })().finally(() => { renewal = null; });
    return renewal;
  }
  async function startAttempt() {
    if (stopping) return;
    const fence = ++generation;
    admittingFence = null;
    setState('starting_gateway', { endpoint: null, error: null, generation: fence });
    try {
      const child = await startProcess(binaryPath);
      if (stopping || fence !== generation) { await stopProcess(child); return; }
      processHandle = child;
      child.onLine?.(line => {
        const endpoint = parseQuickTunnelUrl(line);
        if (endpoint) void admit(fence, endpoint);
      });
      child.onExit?.(code => handleExit(child, code));
      setState('starting_tunnel', { generation: fence });
      startupTimer = timers.setTimeout(() => {
        if (fence !== generation || stopping || admittingFence === fence) return;
        generation++;
        clearTimers();
        void stopTunnel().then(() => scheduleRetry());
      }, ENDPOINT_READY_TIMEOUT_MS);
      child.ready?.then(() => {}, () => handleExit(child, 'startup failed'));
    } catch {
      if (fence === generation && !stopping) scheduleRetry();
    }
  }
  async function admit(fence, endpoint) {
    if (fence !== generation || stopping || state.endpoint || admittingFence === fence) return;
    admittingFence = fence;
    if (startupTimer) { timers.clearTimeout(startupTimer); startupTimer = null; }
    setState('verifying_endpoint', { endpoint: null, error: null });
    try {
      const result = await request('/remote/start', { method: 'POST', body: JSON.stringify({ endpoint }) });
      if (fence !== generation || stopping) { await closeAdmission(result?.connection_generation); return; }
      if (result?.endpoint !== endpoint || !Number.isSafeInteger(result.connection_generation) || result.connection_generation <= 0 || !result.descriptor) throw taggedError('IDENTITY_INVALID', 'Invalid remote hosting response');
      let localDescriptor;
      try { localDescriptor = validateDescriptor(result.descriptor, endpoint, result.connection_generation); }
      catch { throw taggedError('IDENTITY_INVALID', 'Invalid signed endpoint descriptor'); }
      connectionGeneration = result.connection_generation;
      state = { ...state, generation: connectionGeneration, error: null };
      leaseTimer = timers.setInterval(() => void renew(fence, result.connection_generation, endpoint), LEASE_INTERVAL_MS);
      const abortController = new AbortController(); probeAbort = abortController;
      const startedAt = Date.now();
      let verifiedDescriptor = null, delay = 1000;
      while (!stopping && fence === generation && connectionGeneration === result.connection_generation && Date.now() - startedAt < ENDPOINT_READY_TIMEOUT_MS) {
        try {
          verifiedDescriptor = await probe(endpoint, localDescriptor, result.connection_generation, undefined, abortController.signal);
          break;
        } catch (error) {
          if (error?.code === 'ABORTED' || stopping || fence !== generation || connectionGeneration !== result.connection_generation) return;
          if (error?.code !== 'TRANSIENT') throw error;
          const remaining = ENDPOINT_READY_TIMEOUT_MS - (Date.now() - startedAt);
          if (remaining <= 0) break;
          await new Promise(resolve => {
            let settled = false;
            let onAbort;
            const finish = () => { if (settled) return; settled = true; abortController.signal.removeEventListener('abort', onAbort); resolve(); };
            const timer = timers.setTimeout(finish, Math.min(delay, remaining));
            if (abortController.signal.aborted) { timers.clearTimeout(timer); finish(); }
            else { onAbort = () => { timers.clearTimeout(timer); finish(); }; abortController.signal.addEventListener('abort', onAbort, { once: true }); }
          });
          delay = Math.min(5000, delay * 2);
        }
      }
      if (stopping || fence !== generation || connectionGeneration !== result.connection_generation) return;
      if (!verifiedDescriptor) throw Error('Tunnel endpoint did not become reachable within 90 seconds.');
      if (probeAbort === abortController) probeAbort = null;
      state = { ...state, generation: result.connection_generation, endpoint, descriptor: verifiedDescriptor, error: null };
      setState('available');
      retries = 0;
    } catch (error) {
      if (fence === generation && !stopping) {
        const leaseGeneration = connectionGeneration;
        connectionGeneration = null;
        generation++;
        clearTimers(); probeAbort?.abort(); probeAbort = null;
        await closeAdmission(leaseGeneration);
        await stopTunnel();
        if (error?.code === 'IDENTITY_INVALID') setState('error', { endpoint: null, error: 'Tunnel identity verification failed. Review the endpoint and select Retry after resolving the issue.' });
        else scheduleRetry();
      }
    }
  }
  return {
    status: () => ({ ...state, binaryPath }),
    async freshDescriptor() {
      if (state.state !== 'available' || !state.endpoint || !Number.isSafeInteger(state.generation)) throw Error('Remote hosting is not available.');
      try {
      const value = await request('/remote/status', { method: 'GET' });
        if (value?.enabled !== true || value.endpoint !== state.endpoint || value.connection_generation !== state.generation) throw Error();
        const checked = await probe(value.endpoint, value.descriptor, value.connection_generation);
        if (state.state !== 'available' || value.connection_generation !== state.generation) throw Error();
        state = { ...state, descriptor: checked }; publish();
        return checked;
      } catch { throw Error('Could not refresh the signed phone connection update.'); }
    },
    setBinaryPath(value) { binaryPath = validateTunnelPath(value); publish(); return binaryPath; },
    async start() {
      if (!binaryPath) throw Error('Set the cloudflared executable path in Advanced settings first.');
      if (state.state === 'available' || state.state.startsWith('starting_') || state.state === 'verifying_endpoint') return this.status();
      stopping = false; retries = 0; clearTimers(); setState('starting_gateway', { error: null }); await startAttempt(); return this.status();
    },
    async retry() { stopping = false; retries = 0; clearTimers(); await stopTunnel(); await this.start(); return this.status(); },
    async stop() {
      stopping = true; const leaseGeneration = connectionGeneration; generation++; connectionGeneration = null; clearTimers(); probeAbort?.abort(); probeAbort = null; setState('stopping', { endpoint: null, error: null, generation });
      await closeAdmission(leaseGeneration); await stopTunnel(); state = { ...state, state: 'off', endpoint: null, descriptor: null, error: null }; publish();
      return this.status();
    },
    async backendLost() { await this.stop(); },
    setRestoreOnLaunch(value) { state = { ...state, restoreOnLaunch: value === true }; publish(); return state.restoreOnLaunch; },
  };
}

function createWindowsTunnelProcess({ pythonPath, helperPath, parentPid = process.pid, spawnFn = spawn }) {
  return async executable => {
    const fullPath = validateTunnelPath(executable);
    const child = spawnFn(pythonPath, [helperPath, '--parent-pid', String(parentPid), '--', fullPath, 'tunnel', '--url', 'http://127.0.0.1:18766', '--no-autoupdate'], {
      windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    let buffer = '', readyResolve, readyReject;
    const recentLines = [];
    child.ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
    child.onLine = callback => { child._lineHandler = callback; for (const line of recentLines) callback(line); };
    const parse = bytes => {
      buffer += bytes.toString('utf8');
      if (buffer.length > 8192 && !buffer.includes('\n')) { buffer = ''; return; }
      const lines = buffer.split(/\r?\n/); buffer = lines.pop() || '';
      for (const line of lines) {
        if (line === 'RADIO_JOB_READY') readyResolve();
        else if (line.startsWith('RADIO_JOB_ERROR')) readyReject(Error('helper startup failed'));
        recentLines.push(line.slice(0, 8192)); if (recentLines.length > 64) recentLines.shift();
        child._lineHandler?.(line);
      }
    };
    child.stdout.on('data', parse); child.stderr.on('data', parse);
    child.onExit = callback => {
      if (child.exitCode !== null || child.signalCode !== null) queueMicrotask(() => callback(child.exitCode ?? child.signalCode));
      else child.once('exit', callback);
    };
    child.once('error', () => readyReject(Error('helper launch failed')));
    child.once('exit', code => readyReject(Error(`helper exited ${code}`)));
    return child;
  };
}

function registerRemoteHostingIPC(ipcMain, hosting, getWindow) {
  const exposed = new Set(['remote-hosting-status', 'remote-hosting-start', 'remote-hosting-stop', 'remote-hosting-retry', 'remote-hosting-set-path', 'remote-hosting-set-restore', 'remote-hosting-fresh-descriptor']);
  for (const name of exposed) ipcMain.handle(name, async (event, value) => {
    const win = getWindow();
    if (!win || win.isDestroyed() || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) throw Error('Untrusted remote hosting controller');
    const origin = new URL(event.senderFrame.url).origin;
    if (!['http://localhost:3000', 'http://localhost:5173', 'http://localhost:8765'].includes(origin)) throw Error('Untrusted remote hosting controller');
    if (name === 'remote-hosting-status' && value === undefined) return hosting.status();
    if (name === 'remote-hosting-start' && value === undefined) return hosting.start();
    if (name === 'remote-hosting-stop' && value === undefined) return hosting.stop();
    if (name === 'remote-hosting-retry' && value === undefined) return hosting.retry();
    if (name === 'remote-hosting-set-path' && typeof value === 'string') return hosting.setBinaryPath(value);
    if (name === 'remote-hosting-set-restore' && typeof value === 'boolean') return hosting.setRestoreOnLaunch(value);
    if (name === 'remote-hosting-fresh-descriptor' && value === undefined) return hosting.freshDescriptor();
    throw Error('Invalid remote hosting request');
  });
}

module.exports = { createRemoteHostingControl, createWindowsTunnelProcess, parseQuickTunnelUrl, probeTunnelEndpoint, registerRemoteHostingIPC, validateDescriptor, validateTunnelPath, RETRY_DELAYS_MS, LEASE_INTERVAL_MS };
