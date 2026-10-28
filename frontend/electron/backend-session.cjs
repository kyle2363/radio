const BACKEND_URL = 'http://localhost:8765';

async function backendFetch(backendSession, apiPath, options = {}) {
  // Keep main-process credentials scoped to the one backend we control.
  if (!/^\/api\/[a-zA-Z0-9_/-]+$/.test(apiPath) || apiPath.includes('..')) {
    throw Error('Invalid backend path');
  }
  return backendSession.fetch(`${BACKEND_URL}${apiPath}`, { ...options, credentials: 'include' });
}

async function backendCookieHeader(backendSession) {
  const cookies = await backendSession.cookies.get({ url: BACKEND_URL });
  return cookies.map(({ name, value }) => `${name}=${value}`).join('; ');
}

async function waitForBackendReady(backendSession, {
  isCancelled = () => false,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  while (!isCancelled()) {
    try {
      const response = await backendFetch(backendSession, '/api/auth/status', {
        signal: AbortSignal.timeout(3000), cache: 'no-store',
      });
      if (response.ok) {
        const data = await response.json();
        if (typeof data.configured === 'boolean' && typeof data.authenticated === 'boolean') return !isCancelled();
      }
    } catch { /* Startup can take a while; keep waiting for public auth status. */ }
    if (!isCancelled()) await sleep(500);
  }
  return false;
}

function createPlaybackBinding() {
  let active = null;
  return {
    set(value) {
      if (value == null) { active = null; return; }
      if (typeof value.session_id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(value.session_id) ||
          typeof value.owner_key !== 'string' || !/^[a-zA-Z0-9_-]{1,256}$/.test(value.owner_key)) {
        throw Error('Invalid listening session binding');
      }
      active = { session_id: value.session_id, owner_key: value.owner_key };
    },
    headers() {
      return active ? { 'X-Radio-Playback-Session': active.session_id, 'X-Radio-Playback-Owner': active.owner_key } : {};
    },
  };
}

module.exports = { BACKEND_URL, backendFetch, backendCookieHeader, waitForBackendReady, createPlaybackBinding };
