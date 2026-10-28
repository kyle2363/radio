// Passwords stay in the submitted body. Session credentials belong to the
// browser's HttpOnly cookie jar; never persist them in renderer storage.
export function createAuthClient(fetchImpl, apiBase = '') {
  async function request(path, body) {
    const response = await fetchImpl(`${apiBase}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'include',
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
      ...(body === undefined ? {} : {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    });
    if (!response.ok) {
      if (path.endsWith('/login') && response.status === 401) throw Error('Incorrect password. Try again.');
      throw Error('Unable to check sign-in. Try again.');
    }
    return response;
  }

  async function status() {
    const data = await (await request('/api/auth/status')).json();
    if (typeof data.configured !== 'boolean' || typeof data.authenticated !== 'boolean') {
      throw Error('Unable to check sign-in. Try again.');
    }
    return data;
  }

  return {
    status,
    async login(password) {
      await request('/api/auth/login', { password });
      const state = await status();
      if (state.configured && !state.authenticated) throw Error('Sign-in could not be verified. Try again.');
      return state;
    },
    async logout() {
      await request('/api/auth/logout', {});
      return status();
    },
  };
}

export function watchAuthentication(client, onState, {
  schedule = setTimeout, cancel = clearTimeout,
} = {}) {
  let active = true;
  let timer;
  async function check() {
    try {
      const status = await client.status();
      if (active) onState({ phase: status.configured && !status.authenticated ? 'locked' : 'ready' });
    } catch {
      if (!active) return;
      onState({ phase: 'connecting' });
      timer = schedule(check, 1000);
    }
  }
  check();
  return () => { active = false; cancel(timer); };
}
