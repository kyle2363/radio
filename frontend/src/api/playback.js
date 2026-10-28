import { BrowserAudioEngine } from '../audio/BrowserAudioEngine.js';

const STORAGE_KEY = 'radio.playback.session';
const identity = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function createPlaybackApi({ baseUrl = '', fetchImpl = globalThis.fetch, onUnauthorized = () => {} } = {}) {
  return {
    async request(method, path, body, owner, signal) {
      const headers = { 'Content-Type': 'application/json' };
      if (owner) headers['X-Radio-Playback-Owner'] = owner;
      const response = await fetchImpl(`${baseUrl}/api/playback/sessions${path}`, {
        method, headers, credentials: 'include', signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (response.status === 401) onUnauthorized();
      if (!response.ok) {
        const error = new Error('The station could not complete this playback request.');
        error.status = response.status;
        throw error;
      }
      return response.json();
    },
  };
}

/** One controller per page. The station alone advances completed plans. */
export class PlaybackSessionController {
  constructor({ api = createPlaybackApi(), apiBase = '', wsUrl, clientId = identity(),
    electron = Boolean(globalThis.window?.electron), storage = globalThis.sessionStorage,
    socketFactory = url => new WebSocket(url), engineFactory = options => new BrowserAudioEngine(options),
    onChange = () => {}, reconnectDelay = 1500, eventTarget = globalThis.window } = {}) {
    Object.assign(this, { api, clientId, electron, storage, socketFactory, onChange, reconnectDelay, eventTarget });
    const base = new URL(apiBase || globalThis.location?.origin || 'http://localhost:8765');
    this.wsUrl = wsUrl || `${base.href.replace(/\/$/, '').replace(/^http/, 'ws')}/api/playback/sessions/events`;
    this.disposed = false;
    this.state = { session: null, audio: { status: 'idle' }, connected: false, loading: true, error: null, pendingHandoff: false, needsReconnect: false, retryNeeded: false };
    this.engine = engineFactory({ clientId, apiBase,
      postEvent: body => this.request('events', body),
      postPosition: body => this.request('position', body),
      onState: audio => this.update({ audio }), onError: error => this.audioError(error),
      onPlanComplete: () => this.refresh().catch(error => this.fail(error)),
      onQuiesceAck: async body => {
        await this.request('ack', body);
        if (this.pendingHandoff) await this.retryHandoff();
      },
    });
    this.offline = () => {
      this.suspendLocal('Network connection lost. Reconnect when ready.');
      this.refresh().catch(error => this.fail(error));
    };
    this.eventTarget?.addEventListener?.('offline', this.offline);
  }

  update(patch) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.onChange(this.state);
  }
  fail(error) { this.update({ error: error.message || 'Playback connection interrupted.', loading: false }); }
  suspendLocal(message) {
    if (this.disposed) return;
    this.gesture = false;
    this.suspended = this.state.session?.mode === 'browser';
    // A revoked local view retires DOM audio immediately without sending a
    // fabricated producer event through a broken connection.
    if (this.state.session) this.engine.loadSnapshot({ ...this.state.session, output_client_id: null });
    this.update({ needsReconnect: this.suspended, audio: { ...this.state.audio, status: 'interrupted', recoverable: true }, ...(message ? { error: message } : {}) });
  }
  async audioError(error) {
    this.update({ error: error.message });
    if (['media_network', 'connection', 'media'].includes(error.code)) {
      this.suspendLocal(error.message);
      try { await this.refresh(); } catch (failure) { this.fail(failure); }
    }
  }
  getState() { return this.state; }
  requestHeaders() {
    return this.sessionId && this.owner ? { 'X-Radio-Playback-Session': this.sessionId, 'X-Radio-Playback-Owner': this.owner } : {};
  }
  async start() {
    if (this.disposed) return;
    if (this.starting) return this.starting;
    this.starting = this.initialize().catch(error => { this.fail(error); throw error; });
    return this.starting;
  }
  async retry() {
    if (this.disposed) return;
    this.starting = null;
    this.update({ loading: true, error: null, retryNeeded: false });
    clearInterval(this.poll);
    return this.start();
  }
  async initialize() {
    let saved;
    try { saved = JSON.parse(this.storage?.getItem(STORAGE_KEY) || 'null'); } catch { /* unavailable storage */ }
    if (saved?.session_id && saved?.owner_key) {
      this.sessionId = saved.session_id;
      this.owner = saved.owner_key;
      try { await this.refresh(); }
      catch (error) {
        if (!this.disposed && [403, 404].includes(error.status)) {
          try {
            const stored = JSON.parse(this.storage?.getItem(STORAGE_KEY) || 'null');
            if (stored?.session_id === saved.session_id && stored?.owner_key === saved.owner_key) {
              if (this.storage?.removeItem) this.storage.removeItem(STORAGE_KEY);
              else this.storage?.setItem(STORAGE_KEY, '');
            }
          } catch { /* inaccessible storage */ }
          this.sessionId = this.owner = null;
          this.update({ session: null, retryNeeded: true });
        }
        throw error;
      }
    } else {
      const result = await this.api.request('POST', this.electron ? '/desktop/claim' : '', this.electron ? undefined : { client_id: this.clientId });
      if (this.disposed) {
        // Only browser creation is known to allocate a new isolated session.
        // A desktop claim can refer to an already adopted shared output.
        if (!this.electron && result?.owner_key && result?.session?.id) {
          try { await this.api.request('POST', `/${encodeURIComponent(result.session.id)}/close`,
            { command_id: identity(), expected_revision: result.session.revision }, result.owner_key); } catch { /* exact isolated cleanup attempted */ }
        }
        return;
      }
      this.sessionId = result.session.id || result.session.session_id;
      this.owner = result.owner_key;
      try { this.storage?.setItem(STORAGE_KEY, JSON.stringify({ session_id: this.sessionId, owner_key: this.owner })); } catch { /* session remains usable */ }
      this.accept(result);
    }
    if (this.disposed) return;
    this.update({ loading: false });
    this.connect();
    this.poll = setInterval(() => { if (!this.state.connected) this.refresh().catch(error => this.fail(error)); }, 5000);
    this.poll.unref?.();
  }
  async request(action, body, method = 'POST') {
    if (this.disposed) return null;
    if (!this.sessionId || !this.owner) throw new Error('Playback session is not ready.');
    const result = await this.api.request(method, `/${encodeURIComponent(this.sessionId)}${action ? `/${action}` : ''}`, body, this.owner);
    this.accept(result);
    return result;
  }
  accept(result) {
    const session = result?.session || (result?.id || result?.session_id ? result : null);
    if (!session || this.disposed) return;
    if (this.state.session && session.revision < this.state.session.revision) return;
    const active = session.items?.[session.active_index || 0];
    const needsReconnect = (this.suspended && session.mode === 'browser') || ['interrupted', 'awaiting_output'].includes(session.status) ||
      (session.mode === 'browser' && session.output_client_id !== this.clientId);
    this.update({ session, needsReconnect });
    // A restored or handed-over browser waits for an explicit play gesture.
    if (session.mode === 'browser' && session.output_client_id === this.clientId && !this.gesture && active && !active.terminal) {
      this.update({ audio: { ...this.state.audio, status: 'awaiting_user_gesture' } });
      return;
    }
    this.engine.loadSnapshot(session);
  }
  refresh() { return this.request('', undefined, 'GET'); }
  connect() {
    if (this.disposed || this.socket || !this.sessionId) return;
    // Native controllers subscribe using their owner capability; browser
    // producers additionally require the current page's output grant.
    if (this.state.session?.mode !== 'vlc' &&
        (this.state.session?.mode !== 'browser' || this.state.session.output_client_id !== this.clientId)) return;
    const socket = this.socketFactory(this.wsUrl);
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket || this.disposed) return;
      socket.send(JSON.stringify({ session_id: this.sessionId, owner_key: this.owner, client_id: this.clientId }));
      this.update({ connected: true });
    };
    socket.onmessage = message => {
      if (this.socket !== socket || this.disposed) return;
      try {
        const event = JSON.parse(message.data);
        if (event.session_id !== this.sessionId) return;
        if (event.event === 'playback_control') this.engine.applyControl(event).catch(error => this.fail(error));
        else if (event.event === 'playback_state') this.accept(event.session);
        else this.refresh().catch(error => this.fail(error));
      } catch { this.fail(new Error('Invalid playback update.')); }
    };
    socket.onclose = () => {
      if (this.socket !== socket || this.disposed) return;
      this.socket = null;
      this.suspendLocal('Playback connection lost. Reconnect when ready.');
      this.update({ connected: false });
      if (this.reconnectDelay > 0) this.reconnectTimer = setTimeout(async () => {
        try { await this.refresh(); this.connect(); } catch (error) { this.fail(error); }
      }, this.reconnectDelay);
    };
    socket.onerror = () => socket.close();
  }
  command() { return { command_id: identity(), expected_revision: this.state.session?.revision || 0 }; }
  async activate() {
    if (this.state.needsReconnect) return this.reactivate();
    this.gesture = true;
    if (this.state.session) this.engine.loadSnapshot(this.state.session);
    return this.engine.activate();
  }
  async play(songId) {
    if (this.state.needsReconnect) return this.reactivate();
    this.gesture = true;
    if (songId != null) return this.request('select', { ...this.command(), song_id: songId });
    const active = this.state.session?.items?.[this.state.session.active_index || 0];
    if (active && !active.terminal) {
      await this.activate();
      return this.request('resume');
    }
    return this.request('next', this.command());
  }
  async reactivate() {
    this.gesture = false;
    await this.refresh();
    if (this.disposed) return null;
    this.gesture = true;
    this.suspended = false;
    this.update({ error: null, needsReconnect: false });
    const session = this.state.session;
    const active = session?.items?.[session.active_index || 0];
    if (!active || active.terminal || ['idle', 'stopped'].includes(session?.status)) return this.switchOutput('browser');
    if (!['interrupted', 'awaiting_output'].includes(session?.status)) return this.switchOutput('browser');
    this.pendingHandoff = null;
    this.update({ pendingHandoff: false });
    const result = await this.request('reactivate', { ...this.command(), client_id: this.clientId });
    this.connect();
    return result;
  }
  async switchOutput(mode) {
    if (!['vlc', 'browser'].includes(mode)) throw new Error('Unknown playback output.');
    if (this.pendingHandoff) return this.retryHandoff();
    this.pendingHandoff = { ...this.command(), target_mode: mode, target_client_id: mode === 'browser' ? this.clientId : null };
    this.update({ pendingHandoff: true });
    return this.retryHandoff();
  }
  async retryHandoff() {
    const pending = this.pendingHandoff;
    if (!pending) return null;
    const result = await this.request('handoff', pending);
    if (!result.awaiting_confirmation) {
      this.pendingHandoff = null;
      this.update({ pendingHandoff: false });
      this.connect();
    }
    return result;
  }

  async routeLegacy(path, options = {}) {
    const method = options.method || 'GET';
    let body = options.body;
    if (typeof body === 'string') body = JSON.parse(body);
    const current = this.state.session;
    const match = path.match(/^\/api\/play\/(\d+)$/);
    if (match) return this.play(Number(match[1]));
    if (path === '/api/play/status') return this.legacyStatus();
    if (path === '/api/play/pause') return this.request('pause');
    if (path === '/api/play/resume') return this.play();
    if (path === '/api/play/seek') return this.request('seek', { position_ms: Math.round(body.ms) });
    if (path === '/api/play/volume') return this.request('volume', body);
    if (path === '/api/play/volume-normalization') return this.request('normalization', body);
    if (path === '/api/play/eq') return method === 'GET' ? { preset: current?.preferences?.eq || 'flat' } : this.request('eq', body);
    if (path === '/api/play/stop') return this.request('stop', this.command());
    if (['/api/play/next', '/api/queue/skip', '/api/queue/play-next', '/api/queue/pop-next'].includes(path)) return this.request('next', this.command());
    if (path === '/api/queue') return current?.queue || [];
    if (path === '/api/queue/status') return { queue: current?.queue || [], queue_size: current?.queue?.length || 0, rebuilding: false };
    if (path === '/api/queue/clear') return this.request('queue/clear');
    if (path === '/api/queue/reorder') return this.request('queue/reorder', body);
    const add = path.match(/^\/api\/queue\/add\/(\d+)$/);
    if (add) return this.request('queue/add', { song_ids: [Number(add[1])], position: 'back' });
    const remove = path.match(/^\/api\/queue\/remove\/(\d+)$/);
    if (remove) return this.request('queue/remove', { song_id: Number(remove[1]) });
    if (path === '/api/mood') return { mood: current?.mood, vibe_lock: current?.vibe_lock };
    if (path === '/api/mood/set') return this.request('mood', { mood: body.state });
    if (path === '/api/mood/vibe-lock/activate') return this.request('vibe', body);
    if (path === '/api/mood/vibe-lock/deactivate') return this.request('vibe', { song_id: null });
    if (path === '/api/sleep/timer') return this.request('timer', body, method);
    throw new Error('This control is not available for the selected listening session.');
  }
  legacyStatus() {
    const session = this.state.session || {}, item = session.items?.[session.active_index || 0];
    return { ...session, state: session.status, is_playing: session.status === 'playing',
      song: item?.song || (item?.song_id ? { id: item.song_id, ...item } : null),
      position_ms: session.position_ms || 0, duration_ms: item?.duration_ms || 0,
      volume: session.preferences?.volume ?? 70, eq: session.preferences?.eq || 'flat',
      mood: session.mood, queue: session.queue || [] };
  }
  dispose() {
    this.disposed = true;
    this.eventTarget?.removeEventListener?.('offline', this.offline);
    clearTimeout(this.reconnectTimer);
    clearInterval(this.poll);
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.engine.dispose();
  }
}
