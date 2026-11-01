const { randomBytes } = require('node:crypto');

const CONTROL_BASE = '/api/mobile-hosting';
const ENDPOINT = 'http://127.0.0.1:18766';
const UNAVAILABLE = 'Mobile pairing is unavailable. Sign in to the owned desktop server and try again.';
const MAX_CONTROL_RESPONSE_BYTES = 64 * 1024;
const validText = (value, max = 256) => typeof value === 'string' && value.length > 0 && value.length <= max && !/[\x00-\x1f\x7f]/.test(value);
const validId = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);

// Bound bytes before decoding or parsing. The same request deadline also fences
// a stalled body reader, including a stream that ignores fetch cancellation.
async function readBoundedControlJSON(response, signal, deadline = performance.now() + 5000) {
  let reader;
  let completed = false;
  let onAbort;
  const checkDeadline = () => {
    signal.throwIfAborted();
    if (performance.now() >= deadline) throw Error(UNAVAILABLE);
  };
  try {
    checkDeadline();
    reader = response.body?.getReader();
    if (!reader) throw Error(UNAVAILABLE);
    const declared = response.headers.get('Content-Length');
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_CONTROL_RESPONSE_BYTES)) throw Error(UNAVAILABLE);
    const aborted = new Promise((_, reject) => {
      onAbort = () => reject(Error(UNAVAILABLE));
      signal.addEventListener('abort', onAbort, { once: true });
    });
    const chunks = [];
    let size = 0;
    while (true) {
      checkDeadline();
      const { value, done } = await Promise.race([reader.read(), aborted]);
      if (done) break;
      if (!(value instanceof Uint8Array) || value.byteLength > MAX_CONTROL_RESPONSE_BYTES - size) throw Error(UNAVAILABLE);
      if (!value.byteLength) continue;
      size += value.byteLength;
      // Copy only bytes admitted by the limit; do not retain a larger backing buffer.
      chunks.push(Buffer.from(value));
    }
    checkDeadline();
    const parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, size)));
    checkDeadline();
    completed = true;
    return parsed;
  } catch { throw Error(UNAVAILABLE); }
  finally {
    if (onAbort) signal.removeEventListener('abort', onAbort);
    if (!completed && reader) {
      // Cancellation may itself stall or reject; it must not extend the deadline.
      try { Promise.resolve(reader.cancel()).catch(() => {}); } catch (_) {}
    }
    try { reader?.releaseLock(); } catch (_) {}
  }
}

function cleanBundle(value, deviceName) {
  if (value?.protocol_version !== 1 || value.endpoint !== ENDPOINT || !validText(value.server_id, 128) ||
      !/^[a-f0-9]{64}$/.test(value.public_key_pin) || !validId(value.ticket_id) ||
      !validText(value.ticket_secret) || value.device_name !== deviceName ||
      !Number.isFinite(value.expires_at) || value.expires_at * 1000 <= Date.now() || value.expires_at * 1000 > Date.now() + 310000) throw Error(UNAVAILABLE);
  return Object.fromEntries(['protocol_version', 'server_id', 'public_key_pin', 'endpoint', 'ticket_id', 'ticket_secret', 'device_name', 'expires_at'].map(key => [key, value[key]]));
}

function cleanCatalogStatus(value) {
  if (!['idle', 'running', 'ready', 'failed'].includes(value?.state) ||
      !(value.revision === null || Number.isSafeInteger(value.revision) && value.revision > 0) ||
      (value.state === 'ready' && value.revision === null) ||
      !Number.isSafeInteger(value.song_count) || value.song_count < 0 ||
      !Number.isSafeInteger(value.vector_count) || value.vector_count < 0 ||
      ![null, 'catalog_prepare_failed', 'catalog_prepare_timeout', 'ingest_active', 'catalog_prepare_cancelled'].includes(value.error)) throw Error(UNAVAILABLE);
  const result = Object.fromEntries(['state', 'revision', 'song_count', 'vector_count', 'error'].map(key => [key, value[key]]));
  if (value.hash_progress != null) {
    const progress = value.hash_progress;
    if (!['completed', 'total', 'hashed', 'skipped'].every(key => Number.isSafeInteger(progress[key]) && progress[key] >= 0) ||
        progress.completed > progress.total || progress.hashed + progress.skipped !== progress.completed) throw Error(UNAVAILABLE);
    result.hash_progress = Object.fromEntries(['completed', 'total', 'hashed', 'skipped'].map(key => [key, progress[key]]));
  }
  return result;
}

// The launch capability stays inside this closure. Only the one-time bootstrap
// ticket, never a parent capability or a device bearer, can leave this module.
function createMobilePairingControl({ request, isBackendAlive, enabled }) {
  const capability = randomBytes(32).toString('base64url');
  let pending = Promise.resolve();
  let stopping = false;
  let attached = false;
  const rendererSafe = value => {
    if (JSON.stringify(value).includes(capability)) throw Error(UNAVAILABLE);
    return value;
  };
  const enqueue = task => {
    if (stopping || !enabled || !isBackendAlive()) return Promise.reject(Error(UNAVAILABLE));
    const result = pending.then(task);
    pending = result.catch(() => {});
    return result;
  };
  const call = async (suffix, method, body) => {
    try {
      if (!isBackendAlive()) throw Error(UNAVAILABLE);
      const deadline = performance.now() + 5000;
      const signal = AbortSignal.timeout(5000);
      const response = await request(`${CONTROL_BASE}${suffix}`, {
        method, redirect: 'error', cache: 'no-store', signal,
        headers: { Origin: 'http://localhost:8765', 'X-Radio-Mobile-Owner': capability, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) throw Error(UNAVAILABLE);
      return await readBoundedControlJSON(response, signal, deadline);
    } catch { throw Error(UNAVAILABLE); }
  };
  return {
    isAvailable: () => Boolean(enabled),
    launchOptions: () => ({ stdio: ['pipe', 'pipe', 'pipe'], env: { RADIO_MOBILE_CONTROL_PIPE: '1' } }),
    attach(child) {
      if (attached || !child?.stdin) throw Error('Private backend pipe unavailable');
      attached = true;
      // Never forward pipe errors or raw process objects to the renderer.
      child.stdin.on('error', () => {});
      child.stdin.end(`${JSON.stringify({ protocol_version: 1, capability })}\n`);
    },
    async createBundle(name) {
      if (!validText(name, 80) || !name.trim() || name !== name.trim()) throw Error('Enter a device name (1–80 characters).');
      return enqueue(async () => {
        await call('/enable', 'POST');
        return rendererSafe(cleanBundle(await call('/pairing-tickets', 'POST', { device_name: name }), name));
      });
    },
    async listDevices() {
      return enqueue(async () => {
        const value = await call('/devices', 'GET');
        if (!Array.isArray(value?.devices) || value.devices.length > 1000) throw Error(UNAVAILABLE);
        return rendererSafe({ devices: value.devices.map(device => {
          if (!validId(device.device_id) || !validText(device.name, 80) || !Array.isArray(device.scopes) ||
              !device.scopes.every(scope => typeof scope === 'string' && scope.length <= 64) ||
              typeof device.revoked !== 'boolean' || (device.expires_at !== null && !Number.isFinite(device.expires_at))) throw Error(UNAVAILABLE);
          return { device_id: device.device_id, name: device.name, scopes: [...device.scopes], expires_at: device.expires_at, revoked: device.revoked };
        }) });
      });
    },
    async revokeDevice(id) {
      if (!validId(id)) throw Error('Invalid paired device.');
      return enqueue(async () => {
        const value = await call(`/devices/${id}`, 'DELETE');
        if (value?.revoked !== true) throw Error(UNAVAILABLE);
        return { revoked: true };
      });
    },
    prepareCatalog() {
      return enqueue(async () => {
        await call('/enable', 'POST');
        return rendererSafe(cleanCatalogStatus(await call('/catalog/prepare', 'POST')));
      });
    },
    cancelCatalog() {
      return enqueue(async () => rendererSafe(cleanCatalogStatus(await call('/catalog/cancel', 'POST'))));
    },
    catalogStatus() {
      return enqueue(async () => rendererSafe(cleanCatalogStatus(await call('/catalog/status', 'GET'))));
    },
    shutdown() {
      if (stopping) return pending;
      stopping = true;
      pending = pending.then(async () => {
        if (enabled && isBackendAlive()) await call('/disable', 'POST');
      });
      return pending;
    },
  };
}

function registerMobilePairingIPC(ipcMain, control, getMainWindow) {
  const guard = event => {
    const window = getMainWindow();
    if (!window || window.isDestroyed() || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw Error('Untrusted mobile pairing request');
    let origin;
    try { origin = new URL(event.senderFrame.url).origin; } catch { throw Error('Untrusted mobile pairing request'); }
    if (!['http://localhost:3000', 'http://localhost:8765'].includes(origin)) throw Error('Untrusted mobile pairing request');
  };
  for (const [channel, count, method] of [
    ['mobile-pairing-available', 0, 'isAvailable'],
    ['mobile-pairing-create', 1, 'createBundle'], ['mobile-pairing-devices', 0, 'listDevices'], ['mobile-pairing-revoke', 1, 'revokeDevice'],
    ['mobile-catalog-prepare', 0, 'prepareCatalog'], ['mobile-catalog-status', 0, 'catalogStatus'], ['mobile-catalog-cancel', 0, 'cancelCatalog'],
  ]) ipcMain.handle(channel, (event, ...args) => {
    guard(event);
    if (args.length !== count) throw Error('Invalid mobile pairing request');
    return control[method](...args);
  });
}

module.exports = { createMobilePairingControl, registerMobilePairingIPC, readBoundedControlJSON };
