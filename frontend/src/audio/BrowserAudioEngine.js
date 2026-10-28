const VLC_FREQUENCIES = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000];
const AUTOPLAY_STATUSES = new Set(['ready', 'playing', 'awaiting_output']);
const INACTIVE_STATUSES = new Set(['stopped', 'idle']);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

/** Browser decoding only. The server owns recommendation, queue and handoff. */
export class BrowserAudioEngine {
  constructor({
    clientId, apiBase = '', audioFactory = () => new Audio(),
    audioContextFactory = undefined, postEvent, postPosition = async () => {},
    onState = () => {}, onError = () => {}, onPlanComplete = () => {},
    onQuiesceAck = async () => {}, now = () => Date.now(),
  } = {}) {
    if (!clientId || typeof postEvent !== 'function') throw Error('Browser audio requires a client and event transport');
    const base = new URL(apiBase || globalThis.location?.origin || 'http://localhost:8765');
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
      throw Error('Invalid audio API base');
    }
    this.apiBase = base.href.replace(/\/+$/, '');
    this.clientId = clientId;
    this.audioFactory = audioFactory;
    this.contextFactory = audioContextFactory === undefined ? (() => {
      const AudioContext = globalThis.AudioContext || globalThis.webkitAudioContext;
      return AudioContext ? new AudioContext() : null;
    }) : audioContextFactory;
    Object.assign(this, { postEvent, postPosition, onState, onError, onPlanComplete, onQuiesceAck, now });
    this.snapshot = null;
    this.context = {};
    this.revision = -1;
    this.contextRevision = -1;
    this.active = null;
    this.preload = null;
    this.audioContext = null;
    this.webAudioUnavailable = false;
    this.disposed = false;
    this.generation = 0;
    this.eventSequence = 0;
    this.eventNamespace = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    this.positionSequence = 0;
    this.lastPositionAt = -Infinity;
    this.eventTail = Promise.resolve();
    this.controls = new Set();
    this.completedInstances = new Set();
    this.completion = null;
    this.deferredSnapshot = null;
    this.pendingFailure = null;
    this.eqApplied = true;
    this.state = { status: 'idle', recoverable: false, capabilities: { webAudio: false, eq: false }, eqApplied: true };
  }

  getState() { return { ...this.state, capabilities: { ...this.state.capabilities } }; }

  loadSnapshot(incoming) {
    if (this.disposed || !incoming || typeof incoming !== 'object') return false;
    const revision = number(incoming.revision, -1);
    const sameInstance = incoming.instance_id === this.snapshot?.instance_id;
    const contextRevision = number(incoming.context_revision, -1);
    if (sameInstance && contextRevision > this.contextRevision) {
      this.contextRevision = contextRevision;
      this.context = { ...(incoming.context || {}) };
      this._applyAudioSettings();
    }
    if (revision < this.revision) return false;
    if (this.completedInstances.has(incoming.instance_id) && !INACTIVE_STATUSES.has(incoming.status)) return false;

    // Even during a completion request, a revoked grant must immediately stop
    // output. A next-item WS snapshot waits for the completion HTTP ACK.
    const granted = incoming.mode === 'browser' && incoming.output_client_id === this.clientId;
    if (this.completion && sameInstance && granted && !INACTIVE_STATUSES.has(incoming.status) &&
        incoming.items?.[incoming.active_index]?.item_id !== this.active?.item.item_id) {
      if (!this.deferredSnapshot || revision >= number(this.deferredSnapshot.revision, -1)) this.deferredSnapshot = incoming;
      return true;
    }

    const previous = this.snapshot;
    const changedInstance = incoming.instance_id !== previous?.instance_id;
    this.revision = revision;
    this.snapshot = incoming;
    if (changedInstance) {
      this.positionSequence = 0;
      this.lastPositionAt = -Infinity;
      this.controls.clear();
      this.contextRevision = contextRevision;
    }
    if (contextRevision < 0 || contextRevision >= this.contextRevision || changedInstance) {
      this.context = { ...(incoming.context || {}) };
      this.contextRevision = Math.max(this.contextRevision, contextRevision);
    }

    if (!granted || INACTIVE_STATUSES.has(incoming.status) || !incoming.instance_id) {
      this._retireAll();
      this._setState({ status: granted ? incoming.status : 'idle', recoverable: false });
      return true;
    }

    const item = incoming.items?.[incoming.active_index];
    if (!item) {
      this._retireAll();
      this._setState({ status: 'idle', recoverable: false });
      return true;
    }

    if (sameInstance && this.active?.item.item_id === item.item_id && this.active.item.url === item.url) {
      this.active.item = item;
      this._applyAudioSettings();
      // This page produces pause/resume observations. Their delayed HTTP/WS
      // echoes acknowledge state; only explicit controls change this output.
      // Otherwise an old pause ACK can cancel a newer local resume or seek.
      if (incoming.status === 'interrupted') {
        this._silence(this.active);
        this._setState({ status: incoming.status, recoverable: incoming.status === 'interrupted' });
      }
      this._ensurePreload();
      return true;
    }

    // Validate all media URLs that may be loaded before allocating DOM nodes.
    try {
      this._mediaUrl(item.url);
      const next = incoming.items[incoming.active_index + 1];
      if (next) this._mediaUrl(next.url);
    } catch {
      this._retireAll();
      this._setState({ status: 'interrupted', recoverable: false });
      this.onError({ code: 'invalid_media_url', message: 'The station returned an invalid audio resource.', recoverable: false });
      return false;
    }

    const promoted = sameInstance && this.preload?.item.item_id === item.item_id && this.preload.item.url === item.url
      ? this.preload : null;
    if (promoted) this.preload = null;
    this._retireAll();
    this.active = promoted || this._createRecord(item, incoming.instance_id);
    this.active.item = item;
    this.active.generation = this.generation;
    this._attach(this.active);
    this._connectGraph(this.active);
    this._seek(number(incoming.position_ms));
    this._applyAudioSettings();
    this._ensurePreload();
    this._setState({ status: incoming.status, recoverable: incoming.status === 'interrupted' });
    if (AUTOPLAY_STATUSES.has(incoming.status)) this.play();
    return true;
  }

  _mediaUrl(path) {
    if (typeof path !== 'string' || !path.startsWith('/api/media/') || /[?#%\\\s]/.test(path) || path.split('/').includes('..')) {
      throw Error('Invalid media URL');
    }
    const url = new URL(`${this.apiBase}${path}`);
    const base = new URL(this.apiBase);
    if (url.origin !== base.origin || !url.pathname.startsWith(`${base.pathname.replace(/\/$/, '')}/api/media/`)) throw Error('Invalid media URL');
    return url.href;
  }

  _createRecord(item, instanceId) {
    const audio = this.audioFactory();
    const record = { audio, item, instanceId, generation: this.generation, listeners: [], graph: [], started: false, playing: false, completed: false, failed: false, suppressPause: false, retired: false, playAttempt: 0 };
    audio.crossOrigin = 'use-credentials';
    audio.preload = 'auto';
    audio.src = this._mediaUrl(item.url);
    audio.load();
    return record;
  }

  _ensurePreload() {
    const next = this.snapshot?.items?.[this.snapshot.active_index + 1];
    if (!next || this.preload?.item.item_id !== next.item_id || this.preload.item.url !== next.url) {
      this._retire(this.preload);
      this.preload = null;
    }
    if (next && !this.preload) {
      try { this.preload = this._createRecord(next, this.snapshot.instance_id); }
      catch {
        this.onError({ code: 'invalid_media_url', message: 'The next audio resource could not be loaded.', recoverable: true });
      }
    }
  }

  _current(record) {
    return !this.disposed && !record.retired && record === this.active && record.generation === this.generation &&
      record.instanceId === this.snapshot?.instance_id && this.snapshot?.mode === 'browser' && this.snapshot.output_client_id === this.clientId;
  }

  _attach(record) {
    const listen = (name, callback) => {
      const guarded = () => { if (this._current(record)) callback(); };
      record.audio.addEventListener(name, guarded);
      record.listeners.push([name, guarded]);
    };
    listen('loadedmetadata', () => {
      if (record.pendingSeek != null) this._seek(record.pendingSeek);
    });
    listen('playing', () => {
      if (record.audio.paused || record.playing || record.completed || record.failed) return;
      const kind = record.started ? 'resumed' : 'started';
      record.started = true;
      record.playing = true;
      this._setState({ status: 'playing', recoverable: false });
      this._sendEvent(record, this._event(record, kind));
    });
    listen('pause', () => {
      if (record.audio.paused === false || record.suppressPause || !record.playing || record.completed) return;
      record.playing = false;
      this._setState({ status: 'paused', recoverable: false });
      this._sendEvent(record, this._event(record, 'paused'));
    });
    listen('ended', () => {
      if (record.completed || record.failed) return;
      record.completed = true;
      record.playing = false;
      const completion = { record, event: this._event(record, 'completed') };
      this.completion = completion;
      this._setState({ status: 'awaiting_output', recoverable: false });
      this._complete(completion);
    });
    listen('error', () => {
      if (record.audio.error?.code === 2) {
        this._interrupt(record, 'media_network', 'Playback paused while reconnecting to the audio resource.');
      } else this._fail(record);
    });
    listen('timeupdate', () => this._position(record));
  }

  _event(record, kind) {
    return { event_id: `${this.eventNamespace}:${++this.eventSequence}`, instance_id: record.instanceId,
      item_id: record.item.item_id, client_id: this.clientId, kind, position_ms: this._positionMs(record) };
  }

  _sendEvent(record, event, { completion = false } = {}) {
    const pending = this.eventTail.catch(() => {}).then(async () => {
      if (!this._current(record)) return null;
      try {
        const response = await this.postEvent(event);
        if (!this._current(record)) return null;
        if (!response || (!response.accepted && !response.duplicate)) throw Error('Playback event was not accepted');
        if (this.pendingFailure?.event.event_id === event.event_id) this.pendingFailure = null;
        if (response.session && !completion) this.loadSnapshot(response.session);
        return response;
      } catch {
        if (this._current(record)) {
          this.pendingFailure = { record, event, completion };
          this._interrupt(record, 'connection', 'Playback paused while reconnecting to the station.');
        }
        return null;
      }
    });
    this.eventTail = pending;
    return pending;
  }

  async _complete(completion) {
    const { record, event } = completion;
    const response = await this._sendEvent(record, event, { completion: true });
    if (!response || !this._current(record) || this.completion !== completion) return;
    const queued = this.deferredSnapshot;
    const completedPlan = this.snapshot;
    const wasFinalItem = completedPlan.active_index >= completedPlan.items.length - 1;
    this.completion = null;
    this.deferredSnapshot = null;
    const session = queued && (!response.session || number(queued.revision) >= number(response.session.revision)) ? queued : response.session;
    if (session) this.loadSnapshot(session);
    if (wasFinalItem) {
      this.completedInstances.add(completedPlan.instance_id);
      if (this._current(record)) {
        this._retireAll();
        this._setState({ status: 'idle', recoverable: false });
      }
      this.onPlanComplete(completedPlan);
      return;
    }
    if (!this._current(record)) return;
    // Acknowledged event transports may omit the session; advance only through
    // the server-provided immutable items already in the plan.
    const nextIndex = this.snapshot.active_index + 1;
    if (nextIndex < this.snapshot.items.length) {
      this.loadSnapshot({ ...this.snapshot, active_index: nextIndex, position_ms: 0, status: 'playing' });
    } else {
      this._retireAll();
      this._setState({ status: 'idle', recoverable: false });
      this.onPlanComplete(completedPlan);
    }
  }

  _positionMs(record = this.active) {
    return Math.max(0, Math.round(number(record?.audio.currentTime) * 1000));
  }

  _position(record) {
    this._setState({ position_ms: this._positionMs(record) });
    if (this.now() - this.lastPositionAt < 1000 || record.completed || record.failed) return;
    this.lastPositionAt = this.now();
    const payload = { instance_id: record.instanceId, item_id: record.item.item_id, client_id: this.clientId,
      position_ms: this._positionMs(record), sequence: ++this.positionSequence };
    Promise.resolve().then(() => this._current(record) ? this.postPosition(payload) : null).catch(() => {
      if (this._current(record)) this._interrupt(record, 'connection', 'Playback paused while reconnecting to the station.');
    });
  }

  async activate() { return this.play(); }

  async play() {
    const record = this.active;
    if (!record || !this._current(record)) return false;
    if (this.pendingFailure?.record === record) {
      const pending = this.pendingFailure;
      if (pending.completion) {
        await this._complete(this.completion);
        return !this.pendingFailure;
      }
      if (!await this._sendEvent(record, pending.event)) return false;
    }
    if (!this._current(record) || record.completed || record.failed) return false;
    const attempt = ++record.playAttempt;
    try {
      // Invoke play and resume synchronously within the caller's user gesture.
      const resumed = this.audioContext?.state === 'suspended' ? this.audioContext.resume() : null;
      const playing = record.audio.play();
      await Promise.all([resumed, playing]);
      return this._current(record) && record.playAttempt === attempt;
    } catch (error) {
      if (!this._current(record) || record.playAttempt !== attempt) return false;
      this._silence(record);
      if (error?.name === 'NotAllowedError') {
        this._setState({ status: 'awaiting_user_gesture', recoverable: true });
      } else if (error?.name !== 'AbortError') {
        this._fail(record);
      }
      return false;
    }
  }

  pause() {
    const record = this.active;
    if (!record || !this._current(record)) return false;
    const shouldReport = record.playing && !record.completed;
    this._silence(record);
    this._setState({ status: 'paused', recoverable: false });
    if (shouldReport) this._sendEvent(record, this._event(record, 'paused'));
    return true;
  }

  _silence(record) {
    if (!record) return;
    record.suppressPause = true;
    record.playAttempt++;
    record.playing = false;
    try { record.audio.pause(); } finally { record.suppressPause = false; }
  }

  _interrupt(record, code, message) {
    this._silence(record);
    this._setState({ status: 'interrupted', recoverable: true });
    this.onError({ code, message, recoverable: true });
  }

  _fail(record) {
    if (!this._current(record) || record.failed || record.completed) return;
    record.failed = true;
    this._interrupt(record, 'media', 'This audio resource could not be played.');
    this._sendEvent(record, this._event(record, 'failed'));
  }

  _seek(positionMs) {
    const record = this.active;
    if (!record) return;
    const limit = number(record.item.duration_ms, Infinity) || Infinity;
    const target = clamp(number(positionMs), 0, limit);
    try { record.audio.currentTime = target / 1000; record.pendingSeek = null; }
    catch { record.pendingSeek = target; }
    this._setState({ position_ms: target });
  }

  async applyControl(command) {
    if (this.disposed || !command?.id || command.output_client_id !== this.clientId ||
        this.controls.has(command.id)) return false;
    if (!['pause', 'resume', 'seek', 'volume', 'eq', 'quiesce', 'stop'].includes(command.action)) return false;
    // Handoff confirmations must be answered even after this page retired its
    // audio (reload, transport drop, blocked autoplay), otherwise the station
    // waits on a quiesce that can never arrive and the selector wedges.
    const unavoidable = command.action === 'quiesce' || command.action === 'stop';
    const instanceId = this.snapshot?.instance_id;
    if (instanceId ? command.instance_id !== instanceId : !unavoidable) return false;
    const record = this.active && this._current(this.active) ? this.active : null;
    if (!record && !unavoidable) return false;
    this.controls.add(command.id);
    switch (command.action) {
      case 'pause': this.pause(); break;
      case 'resume': await this.play(); break;
      case 'seek': this._seek(command.position_ms); break;
      case 'volume': this.context = { ...this.context, volume: command.volume }; this._applyAudioSettings(); break;
      case 'eq': this.context = { ...this.context, eq: command.eq, eq_bands: command.eq_bands }; this._applyAudioSettings(); break;
      case 'quiesce': {
        this._silence(record);
        this._setState({ status: 'paused', recoverable: false });
        try {
          await this.onQuiesceAck({ command_id: command.id, instance_id: command.instance_id, client_id: this.clientId,
            position_ms: record ? this._positionMs(record) : number(this.snapshot?.position_ms) });
        } catch {
          this.controls.delete(command.id);
          if (record && this._current(record)) this._interrupt(record, 'connection', 'Output is paused while the station confirms the handoff.');
          return false;
        }
        break;
      }
      case 'stop': this._retireAll(); this._setState({ status: 'stopped', recoverable: false }); break;
    }
    return true;
  }

  _connectGraph(record) {
    if (this.webAudioUnavailable || !this.contextFactory) return;
    try {
      this.audioContext ||= this.contextFactory();
      if (!this.audioContext) { this.webAudioUnavailable = true; return; }
      const source = this.audioContext.createMediaElementSource(record.audio);
      record.graph.push(source);
      let previous = source;
      record.filters = VLC_FREQUENCIES.map((frequency) => {
        const filter = this.audioContext.createBiquadFilter();
        filter.type = 'peaking';
        filter.frequency.value = frequency;
        filter.Q.value = 1;
        previous.connect(filter);
        previous = filter;
        record.graph.push(filter);
        return filter;
      });
      record.gain = this.audioContext.createGain();
      record.graph.push(record.gain);
      previous.connect(record.gain);
      record.gain.connect(this.audioContext.destination);
    } catch {
      // If source creation succeeded, preserve its output route when possible.
      for (const node of record.graph) { try { node.disconnect(); } catch {} }
      try { record.graph[0]?.connect(this.audioContext.destination); } catch {}
      record.gain = null;
      record.filters = null;
      this.webAudioUnavailable = true;
    }
  }

  _eq() {
    const raw = this.context.eq_bands ?? this.context.eq;
    let bands = Array.isArray(raw) ? raw : raw?.bands ?? raw?.gains;
    let frequencies = raw?.frequencies ?? VLC_FREQUENCIES;
    if (!bands && raw && typeof raw === 'object') {
      const keys = Object.keys(raw).filter((key) => /^\d+(\.\d+)?$/.test(key)).map(Number).sort((a, b) => a - b);
      if (keys.length === 10) { frequencies = keys; bands = keys.map((key) => raw[key]); }
    }
    const recognized = raw == null || ['flat', 'off', 'none'].includes(raw) || (Array.isArray(bands) && bands.length === 10);
    return { bands: Array.from({ length: 10 }, (_, i) => clamp(number(bands?.[i]), -20, 20)),
      frequencies: Array.from({ length: 10 }, (_, i) => clamp(number(frequencies[i], VLC_FREQUENCIES[i]), 20, 20000)), recognized };
  }

  _applyAudioSettings() {
    const record = this.active;
    if (!record) return;
    const { bands, frequencies, recognized } = this._eq();
    const webAudio = Boolean(record.gain && record.filters);
    const normalization = this.context.normalization === true || this.context.normalization === 'true';
    const normalizedGain = normalization ? 10 ** (clamp(number(record.item.gain_db), -60, 24) / 20) : 1;
    const volume = clamp(number(this.context.volume, 70), 0, 100) / 100;
    const speech = record.item.kind === 'speech' ? clamp(number(this.context.talk_volume, 100), 0, 100) / 100 : 1;
    // Conservatively reserve headroom for every positive EQ band, and never
    // amplify above unity. This cannot repair clipping already in the source.
    const headroom = webAudio ? 10 ** (-bands.reduce((sum, gain) => sum + Math.max(0, gain), 0) / 20) : 1;
    const gain = Math.min(headroom, volume * speech * normalizedGain);
    if (webAudio) {
      record.audio.volume = 1;
      record.gain.gain.value = gain;
      record.filters.forEach((filter, i) => { filter.frequency.value = frequencies[i]; filter.gain.value = bands[i]; });
    } else record.audio.volume = gain;
    this.eqApplied = recognized && (webAudio || bands.every((gain) => gain === 0));
    this._setState({ capabilities: { webAudio, eq: webAudio }, eqApplied: this.eqApplied });
  }

  _setState(change) {
    this.state = { ...this.state, ...change, instance_id: this.snapshot?.instance_id ?? null,
      item_id: this.active?.item.item_id ?? null,
      ...(change.position_ms === undefined ? { position_ms: this._positionMs() } : {}) };
    if (!this.disposed) this.onState(this.getState());
  }

  _retire(record) {
    if (!record || record.retired) return;
    record.retired = true;
    for (const [name, callback] of record.listeners) record.audio.removeEventListener(name, callback);
    record.listeners = [];
    this._silence(record);
    for (const node of record.graph) { try { node.disconnect(); } catch {} }
    record.audio.removeAttribute('src');
    record.audio.load();
  }

  _retireAll() {
    this.generation++;
    this._retire(this.active);
    this._retire(this.preload);
    this.active = null;
    this.preload = null;
    this.completion = null;
    this.deferredSnapshot = null;
    this.pendingFailure = null;
  }

  dispose() {
    if (this.disposed) return;
    this._retireAll();
    this.disposed = true;
    this.controls.clear();
    this.completedInstances.clear();
    this.state = { ...this.state, status: 'idle', item_id: null, position_ms: 0 };
    try { Promise.resolve(this.audioContext?.close()).catch(() => {}); } catch {}
  }
}
