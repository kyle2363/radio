import { BrowserAudioEngine } from '../audio/BrowserAudioEngine.js';

const STORAGE_KEY = 'radio.playback.session';
const identity = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
// A duplicated tab may inherit sessionStorage byte-for-byte. Keep producer
// identity in the page's JS realm, never in copied storage.
const DOCUMENT_CLIENT_ID = identity();

function storedSession(storage) {
  try { return JSON.parse(storage?.getItem(STORAGE_KEY) || 'null'); } catch { return null; }
}

export function createPlaybackApi({ baseUrl = '', fetchImpl = globalThis.fetch, onUnauthorized = () => {}, timeoutMs = 10000 } = {}) {
  return {
    async request(method, path, body, owner, signal) {
      const headers = { 'Content-Type': 'application/json' };
      if (owner) headers['X-Radio-Playback-Owner'] = owner;
      const abort = new AbortController();
      const cancel = () => abort.abort(signal.reason);
      if (signal?.aborted) cancel();
      else signal?.addEventListener('abort', cancel, { once: true });
      const timer = setTimeout(() => abort.abort(new DOMException('Playback request timed out.', 'TimeoutError')), timeoutMs);
      try {
        const response = await fetchImpl(`${baseUrl}/api/playback/sessions${path}`, {
          method, headers, credentials: 'include', signal: abort.signal,
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        if (response.status === 401) onUnauthorized();
        if (!response.ok) {
          const error = new Error('The station could not complete this playback request.');
          error.status = response.status;
          throw error;
        }
        return await response.json();
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', cancel);
      }
    },
  };
}

/** One controller per page. The station alone advances completed plans. */
export class PlaybackSessionController {
  constructor({ api = createPlaybackApi(), apiBase = '', wsUrl, clientId = null,
    electron = Boolean(globalThis.window?.electron), storage = globalThis.sessionStorage,
    socketFactory = url => new WebSocket(url), engineFactory = options => new BrowserAudioEngine(options),
    onChange = () => {}, reconnectDelay = 1500, connectTimeout = 8000, handoffTimeout = 20000, handoffRetryDelay = 1000,
    eventTarget = globalThis.window } = {}) {
    // Capabilities restore the logical session, while each document gets a
    // distinct producer identity so duplicated tabs cannot emit concurrently.
    const pageClient = clientId || DOCUMENT_CLIENT_ID;
    Object.assign(this, { api, clientId: pageClient, electron, storage, socketFactory, onChange,
      reconnectDelay, connectTimeout, handoffTimeout, handoffRetryDelay, eventTarget });
    const base = new URL(apiBase || globalThis.location?.origin || 'http://localhost:8765');
    this.wsUrl = wsUrl || `${base.href.replace(/\/$/, '').replace(/^http/, 'ws')}/api/playback/sessions/events`;
    this.disposed = false;
    this.leaseTimer = null;
    this.leasePromise = null;
    this.leaseInstanceId = null;
    this.state = { session: null, audio: { status: 'idle' }, connected: false, loading: true, error: null, pendingHandoff: false, needsReconnect: false, retryNeeded: false };
    this.engine = engineFactory({ clientId: pageClient, apiBase, requireLease: true,
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
      this.controlDisconnected('Network connection lost. Reconnecting to the station.');
      this.scheduleReconnect();
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
  controlDisconnected(message) {
    if (this.disposed) return;
    // Keep the current media element and its buffered bytes alive while only
    // the control channel is unavailable. The engine owns observed events and
    // local controls until the station can reconcile them.
    this.update({ connected: false, ...(message ? { error: message } : {}) });
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
    const saved = storedSession(this.storage);
    if (saved?.session_id && saved?.owner_key) {
      this.sessionId = saved.session_id;
      this.owner = saved.owner_key;
      try { await this.refresh(); }
      catch (error) {
        if (!this.disposed && [403, 404].includes(error.status)) {
          try {
            const stored = storedSession(this.storage);
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
      try { this.storage?.setItem(STORAGE_KEY, JSON.stringify({ session_id: this.sessionId, owner_key: this.owner, client_id: this.clientId })); } catch { /* session remains usable */ }
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
    if (session.mode === 'browser' && session.output_client_id === this.clientId && session.instance_id &&
        !['idle', 'stopped', 'interrupted', 'awaiting_output'].includes(session.status)) {
      this.ensureLease(session).then(() => {
        if (!this.disposed && this.state.session?.revision === session.revision && this.state.session?.instance_id === session.instance_id) {
          this.engine.loadSnapshot(session);
          if (this.gesture && session.status === 'playing' && this.engine.getState?.().reason === 'lease_expired') {
            this.engine.activate().catch(error => this.fail(error));
          }
        }
      }).catch(error => this.fail(error));
    } else this.engine.loadSnapshot(session);
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
    // An unanswered upgrade (stale dev proxy, sleeping host) never closes on
    // its own. Without a deadline the page would wait on it forever and never
    // retry, so the station would stay silent until a reload.
    if (this.connectTimeout > 0) {
      this.connectTimer = setTimeout(() => {
        if (this.socket !== socket || socket.readyState !== 0) return;
        try { socket.close(); } catch { /* already closing */ }
      }, this.connectTimeout);
      this.connectTimer.unref?.();
    }
    socket.onopen = () => {
      if (this.socket !== socket || this.disposed) return;
      this.clearConnectTimer();
      socket.send(JSON.stringify({ session_id: this.sessionId, owner_key: this.owner, client_id: this.clientId }));
      this.update({ connected: true, error: null });
      this.recoverControl().catch(error => this.fail(error));
      // A transport drop retires DOM audio for safety. When the control
      // channel is genuinely back and the grant is still ours, resume it
      // without making the listener press a reconnect button.
      if (this.suspended) this.resume().catch(error => this.fail(error));
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
      this.clearConnectTimer();
      this.socket = null;
      this.controlDisconnected('Playback connection lost. Reconnecting to the station.');
      this.update({ connected: false });
      this.scheduleReconnect();
    };
    socket.onerror = () => socket.close();
  }
  clearConnectTimer() {
    clearTimeout(this.connectTimer);
    this.connectTimer = null;
  }
  scheduleReconnect(delay = this.reconnectDelay) {
    if (this.disposed || this.reconnectDelay <= 0) return;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(async () => {
      if (this.disposed) return;
      // A failed probe (backend restarting) must not end the retry chain.
      try { await this.refresh(); } catch (error) { this.fail(error); }
      if (this.disposed) return;
      this.connect();
      if (!this.socket) this.scheduleReconnect(Math.min(Math.max(delay, 1500) * 2, 15000));
    }, delay);
    this.reconnectTimer.unref?.();
  }
  async resume() {
    if (this.disposed || !this.suspended) return null;
    // Browser media can have queued completion/skip receipts while offline.
    // Persist them before reading state so a stale snapshot cannot revive a
    // terminal item or overwrite the resulting queue position.
    await this.engine.flushPendingEvents?.();
    await this.refresh();
    if (this.disposed || !this.suspended) return null;
    const session = this.state.session;
    const active = session?.items?.[session.active_index || 0];
    if (!session || session.mode !== 'browser' || session.output_client_id !== this.clientId) return null;
    // Interrupted grants need the explicit server-side reactivation path; a
    // local transport failure is the only case this method recovers.
    if (!active || active.terminal || ['idle', 'stopped', 'interrupted', 'awaiting_output'].includes(session.status)) return null;
    // Reuse the gesture this page had before the drop. If the browser refuses,
    // the engine reports awaiting_user_gesture and offers its own button.
    this.suspended = false;
    this.gesture = true;
    this.update({ error: null, needsReconnect: false });
    await this.ensureLease(session);
    this.engine.loadSnapshot(session);
    if (session.status === 'paused') return session;
    return this.engine.activate();
  }
  command() { return { command_id: identity(), expected_revision: this.state.session?.revision || 0 }; }
  async activate() {
    if (this.state.needsReconnect) return this.reactivate();
    this.gesture = true;
    if (this.state.session?.mode === 'browser' && this.state.session.output_client_id === this.clientId) await this.ensureLease(this.state.session);
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
    const current = await this.refresh();
    if (this.disposed) return null;
    this.gesture = true;
    this.suspended = false;
    this.update({ error: null, needsReconnect: false });
    const session = this.state.session;
    const active = session?.items?.[session.active_index || 0];
    if (!active || active.terminal || ['idle', 'stopped'].includes(session?.status)) return this.switchOutput('browser');
    if (!['interrupted', 'awaiting_output'].includes(session?.status)) {
      // The grant is still ours and the plan is live, so the loss was the
      // transport rather than the output. Switching to ourselves would need a
      // quiesce ACK over the channel that just failed; resync locally instead.
      if (session.mode === 'browser' && session.output_client_id === this.clientId) {
        this.clearPendingHandoff();
        await this.ensureLease(session);
        this.engine.loadSnapshot(session);
        this.connect();
        await this.engine.activate().catch(error => this.fail(error));
        return current;
      }
      return this.switchOutput('browser');
    }
    this.clearPendingHandoff();
    const result = await this.request('reactivate', { ...this.command(), client_id: this.clientId });
    if (this.state.session?.instance_id) await this.ensureLease(this.state.session);
    this.connect();
    return result;
  }
  async ensureLease(session = this.state.session, force = false) {
    if (!session?.instance_id || session.mode !== 'browser' || session.output_client_id !== this.clientId) return null;
    if (!force && this.leaseInstanceId === session.instance_id && this.leaseExpiresAt > performance.now() + 1000) return true;
    if (this.leasePromise && this.leasePromiseInstanceId === session.instance_id) return this.leasePromise;
    const instanceId = session.instance_id;
    const requestStartedAt = performance.now();
    this.leasePromiseInstanceId = instanceId;
    const pending = this.api.request('POST', `/${encodeURIComponent(this.sessionId)}/lease`,
      { instance_id: instanceId, client_id: this.clientId }, this.owner).then(lease => {
      if (this.disposed || this.state.session?.instance_id !== instanceId || this.state.session?.output_client_id !== this.clientId) return null;
      const granted = this.engine.grantLease?.(lease, requestStartedAt, instanceId);
      if (granted === false) throw new Error('The browser playback lease was rejected.');
      this.leaseInstanceId = instanceId;
      this.leaseExpiresAt = requestStartedAt + Math.max(0, Number(lease.lease_remaining_ms) - 1000);
      this.armLeaseRenewal();
      return true;
    }).finally(() => {
      if (this.leasePromise === pending) {
        this.leasePromise = null;
        this.leasePromiseInstanceId = null;
      }
    });
    this.leasePromise = pending;
    return pending;
  }
  armLeaseRenewal() {
    clearTimeout(this.leaseTimer);
    if (this.disposed || !this.leaseInstanceId) return;
    this.leaseTimer = setTimeout(() => {
      const session = this.state.session;
      if (!session || session.instance_id !== this.leaseInstanceId || session.mode !== 'browser' || session.output_client_id !== this.clientId) return;
      this.ensureLease(session, true).catch(error => {
        this.fail(error);
        if (!this.disposed && this.state.session?.instance_id === session.instance_id &&
            this.state.session?.output_client_id === this.clientId) this.armLeaseRenewal();
      });
    }, 3000);
    this.leaseTimer.unref?.();
  }
  async recoverControl() {
    // Event receipts are ordered and durable at the client. Flush them before
    // reading a snapshot that could still reflect pre-command state.
    await this.engine.flushPendingEvents?.();
    if (this.pendingVolume != null) {
      const volume = this.pendingVolume;
      try {
        await this.request('volume', { volume });
        if (this.pendingVolume === volume) this.pendingVolume = null;
      } catch (error) { this.fail(error); }
    }
    const current = this.state.session;
    if (current?.mode === 'browser' && current.output_client_id === this.clientId && current.instance_id) {
      try { await this.ensureLease(current, true); }
      catch (error) { if (![404, 409].includes(error.status)) throw error; }
    }
    return this.refresh();
  }
  async switchOutput(mode) {
    if (!['vlc', 'browser'].includes(mode)) throw new Error('Unknown playback output.');
    if (this.pendingHandoff) return this.retryHandoff();
    this.pendingHandoff = { ...this.command(), target_mode: mode, target_client_id: mode === 'browser' ? this.clientId : null };
    this.update({ pendingHandoff: true });
    this.armHandoffTimer();
    return this.retryHandoff();
  }
  async retryHandoff() {
    const pending = this.pendingHandoff;
    if (!pending) return null;
    if (this.handoffInFlight) return this.handoffInFlight;
    this.handoffInFlight = this.request('handoff', pending).then(result => {
      if (this.pendingHandoff !== pending) return result;
      if (!result.awaiting_confirmation) {
        this.clearPendingHandoff();
        this.connect();
      } else this.scheduleHandoffRetry();
      return result;
    }).catch(error => {
      if (this.pendingHandoff === pending) this.scheduleHandoffRetry();
      throw error;
    }).finally(() => { this.handoffInFlight = null; });
    return this.handoffInFlight;
  }
  async cancelOrReconcileHandoff() {
    const pending = this.pendingHandoff;
    if (!pending || this.disposed) return null;
    try {
      const result = await this.request('handoff/cancel', pending);
      if (this.pendingHandoff !== pending) return result;
      if (result?.cancelled || result?.duplicate) {
        this.clearPendingHandoff();
        this.connect();
        return result;
      }
    } catch (error) {
      this.fail(error);
    }
    // The cancellation can race a successful commit. Retry the exact receipt
    // body so the server returns the accepted handoff instead of minting one.
    return this.retryHandoff().catch(error => this.fail(error));
  }
  clearPendingHandoff() {
    clearTimeout(this.handoffTimer);
    clearTimeout(this.handoffRetryTimer);
    this.handoffTimer = null;
    this.pendingHandoff = null;
    this.pendingHandoffAt = 0;
    if (this.state.pendingHandoff) this.update({ pendingHandoff: false });
  }
  armHandoffTimer() {
    clearTimeout(this.handoffTimer);
    if (!this.pendingHandoffAt) this.pendingHandoffAt = Date.now();
    if (!(this.handoffTimeout > 0)) return;
    // A quiesce confirmation travels over the very socket a silent page may
    // have lost. Without a deadline the selector would wait on it forever.
    this.handoffTimer = setTimeout(() => {
      if (this.disposed || !this.pendingHandoff) return;
      // The server may have committed the reservation while its acknowledgement
      // was lost. Cancel/reconcile with the original command identity.
      this.cancelOrReconcileHandoff();
    }, Math.max(0, this.handoffTimeout - (Date.now() - this.pendingHandoffAt)));
    this.handoffTimer.unref?.();
  }
  scheduleHandoffRetry() {
    clearTimeout(this.handoffRetryTimer);
    if (this.disposed || !this.pendingHandoff || !(this.handoffRetryDelay > 0)) return;
    this.handoffRetryTimer = setTimeout(() => {
      if (!this.pendingHandoff || this.disposed) return;
      this.retryHandoff().catch(error => this.fail(error));
    }, this.handoffRetryDelay);
    this.handoffRetryTimer.unref?.();
  }

  async routeLegacy(path, options = {}) {
    const method = options.method || 'GET';
    let body = options.body;
    if (typeof body === 'string') body = JSON.parse(body);
    const current = this.state.session;
    const match = path.match(/^\/api\/play\/(\d+)$/);
    if (match) return this.play(Number(match[1]));
    if (path === '/api/play/status') return this.legacyStatus();
    if (path === '/api/play/pause') return this.localControl('pause');
    if (path === '/api/play/resume') return this.localControl('resume');
    if (path === '/api/play/seek') return this.localControl('seek', { position_ms: Math.round(body.ms) });
    if (path === '/api/play/volume') return this.localControl('volume', body);
    if (path === '/api/play/volume-normalization') return this.request('normalization', body);
    if (path === '/api/play/eq') return method === 'GET' ? { preset: current?.preferences?.eq || 'flat' } : this.request('eq', body);
    if (path === '/api/play/stop') return this.request('stop', this.command());
    if (path === '/api/queue/replay') return this.request('replay', this.command());
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
  async localControl(action, body = {}) {
    const session = this.state.session;
    if (action === 'resume' && (!session || ['idle', 'stopped'].includes(session.status) ||
        session.items?.[session.active_index || 0]?.terminal)) {
      // Resume is also the UI's Play action. With no live plan, ask the server
      // to reserve the next item instead of calling play() on an empty engine.
      this.gesture = true;
      return this.play();
    }
    if (session?.mode !== 'browser' || session.output_client_id !== this.clientId || this.state.needsReconnect) {
      if (action === 'resume') return this.play();
      return this.request(action, body);
    }
    // Pausing must remain immediate during a transport outage. Seek and volume
    // use the engine's existing permit; only resume needs a fresh network grant.
    if (action === 'resume') await this.ensureLease(session);
    if (this.disposed || this.state.session?.instance_id !== session.instance_id) return null;
    switch (action) {
      case 'pause': this.engine.pause(); break;
      case 'resume': await this.engine.play(); break;
      case 'seek': this.engine.seek?.(body.position_ms); break;
      case 'volume': {
        this.engine.setVolume?.(body.volume);
        this.pendingVolume = body.volume;
        if (this.state.connected) {
          try {
            await this.request('volume', body);
            if (this.pendingVolume === body.volume) this.pendingVolume = null;
          } catch { /* retain the latest local setting for reconnect */ }
        }
        break;
      }
      default: return this.request(action, body);
    }
    return { local: true };
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
    clearTimeout(this.handoffTimer);
    clearTimeout(this.handoffRetryTimer);
    clearTimeout(this.leaseTimer);
    this.clearConnectTimer();
    clearInterval(this.poll);
    clearTimeout(this.leaseTimer);
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.engine.dispose();
  }
}
