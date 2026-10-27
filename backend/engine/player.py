"""VLC MediaListPlayer for gapless audio playback."""

import logging
import os
import sys
import threading
import time
import queue
from dataclasses import dataclass
from engine.vlc_transport import VLCTransport
try:
    import vlc
except ImportError as _vlc_err:
    raise ImportError(
        "VLC media player not found.\n\n"
        "The Radio Station requires VLC for gapless audio playback.\n"
        "Please install VLC from https://videolan.org, then reinstall\n"
        "the Python binding:\n"
        "  pip install python-vlc"
    ) from _vlc_err
import config
from db import database

_song_logger = logging.getLogger("radio.playback")
_song_logger.setLevel(logging.WARNING)
# Console handler (visible in terminal)
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.WARNING)
_ch.setFormatter(logging.Formatter("[Playback] %(asctime)s.%(msecs)03d [%(threadName)s] %(message)s", datefmt="%H:%M:%S"))
_song_logger.addHandler(_ch)
_song_logger.propagate = False


def _redirect_stderr():
    """Temporarily redirect stderr to suppress VLC init warnings."""
    if not config.VLC_SUPPRESS_STDERR:
        return None
    try:
        old_fd = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        os.close(devnull)
        return old_fd
    except OSError:
        class _DevNull:
            def write(self, *a):
                pass
        sys.stderr = _DevNull()
        return "sys"


def _restore_stderr(saved):
    """Restore stderr after VLC init."""
    if not config.VLC_SUPPRESS_STDERR:
        return
    if saved is None:
        return
    if saved == "sys":
        sys.stderr = sys.__stderr__
    else:
        try:
            os.dup2(saved, 2)
            os.close(saved)
        except OSError:
            pass


@dataclass(frozen=True)
class _PlaybackEnd:
    generation: int
    session_id: int | None
    item_index: int
    song_id: int | None


class Player:
    """Wraps libvlc MediaListPlayer for true gapless playback."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cb_lock = threading.Lock()
        self._state = "stopped"  # stopped, playing, paused
        self._current_song_id = None
        self._current_session_id = None
        self._on_song_ended_callbacks = []
        self._on_song_started_callbacks = []
        self._on_playback_stopped_callbacks = []
        self._explicitly_stopped = False
        self._stop_in_progress = False

        # Generation counter to distinguish stale VLC events from current ones
        self._play_gen = 0
        self._completion_gen = 0
        self._completed_items = set()
        self._stop_gen = -1
        self._play_started_at = 0.0  # time.time() when the current play started
        # Generation at which to ignore the next MediaListPlayerNextItemSet
        # (fired at play-start, not a real advancement)
        self._first_next_gen = -1

        # VLC instance and media list player
        saved = _redirect_stderr()
        try:
            self._vlc_instance = vlc.Instance(["--quiet"])
        finally:
            _restore_stderr(saved)
        # Set VLC log callback to pipe VLC messages through Python logging
        try:
            self._vlc_instance.log_set(
                lambda *a: _song_logger.debug("[VLC] %s", a[-1] if a else "")
            )
        except Exception:
            pass
        self._command_lock = threading.RLock()
        self._transport = None
        self._list_player = None
        self._media_list = None
        self._native_events = queue.SimpleQueue()
        self._closed = False

        # Track chain items and current position
        self._chain_items = []
        self._current_chain_index = 0

        # Talk-playing state: True when current chain contains TTS items
        # (talk segment playing, real song follows).  Cleared when VLC
        # advances past the last TTS item to the real song.
        self._talk_playing = False
        # TTS event counter: tracks TTS end events we expect from the
        # internal MediaPlayer.  Only consumed by _on_media_end_reached
        # to correctly ignore TTS items regardless of VLC event ordering.
        self._tts_events_pending = 0

        # Persistent flag: True when the current chain is ALL TTS (no songs).
        # Set in play_chain, survives independent of _chain_items so that
        # _on_media_end_reached can detect talk-only chain end even if
        # _chain_items was cleared by a concurrent gen advance.
        self._talk_only_chain = False
        # Total duration of all TTS WAVs in current talk-only chain (ms).
        # Used to synthesize position/time during talk playback since VLC
        # reports state=Stopped for talk-only chains even while audio plays.
        self._talk_total_ms = 0

        # Talk volume: saved/restored when talk segments play
        self._pre_talk_volume = None

        # Volume normalization (ReplayGain-style)
        self._base_volume = 70
        self._gain_db = 0.0
        self._normalization_enabled = True

        # EQ preset (None = off)
        self._current_eq = None
        self._last_eq_preset = None

        self._event_worker = threading.Thread(target=self._dispatch_native_events,
                                              daemon=True, name="vlc-events")
        self._event_worker.start()

    def _enqueue_native_event(self, handler, generation):
        self._native_events.put((handler, generation))

    def _dispatch_native_events(self):
        while True:
            item = self._native_events.get()
            if item is None:
                return
            handler, generation = item
            try:
                if generation == self._play_gen:
                    handler(None, generation)
            except Exception:
                _song_logger.exception("Native playback observation failed: gen=%s", generation)

    def _on_next_item(self, event, user_data):
        """Apply an item-advance observation outside the native callback thread."""
        generation = user_data if isinstance(user_data, int) else self._play_gen
        # Direct native-stop callback compatibility: never wait on a state lock
        # while the native caller is stopping. Production callbacks enqueue.
        if self._explicitly_stopped or self._stop_in_progress:
            return
        completion, started_song, callbacks, restore_volume = None, None, [], False
        with self._lock:
            if generation != self._play_gen or self._explicitly_stopped or self._stop_in_progress:
                return
            if self._current_chain_index == 0 and self._first_next_gen == generation:
                self._first_next_gen = -1
                return
            index = self._current_chain_index
            if index < 0 or index + 1 >= len(self._chain_items):
                return  # The final item is completed by its MediaPlayer end event.
            item = self._chain_items[index]
            if not item.get("is_tts") and item.get("song_id"):
                completion = _PlaybackEnd(generation, self._current_session_id, index, item["song_id"])
            self._current_chain_index += 1
            current = self._chain_items[self._current_chain_index]
            if not current.get("is_tts"):
                self._current_song_id = current.get("song_id")
                if self._talk_playing:
                    self._talk_playing = False
                    if self._pre_talk_volume is not None:
                        self._base_volume = self._pre_talk_volume
                        self._pre_talk_volume = None
                        restore_volume = True
                    started_song = self._current_song_id
                    with self._cb_lock:
                        callbacks = list(self._on_song_started_callbacks)
                        self._on_song_started_callbacks.clear()
        if restore_volume:
            self._apply_effective_volume()
        if completion is not None:
            self._fire_song_ended(completion.song_id, completion)
        for callback in callbacks:
            if generation != self._play_gen:
                break
            try:
                callback(started_song)
            except Exception:
                _song_logger.exception("Deferred song-start callback failed")

    def _on_end_reached(self, event, user_data):
        """Handle list-level talk completion without racing playback commands."""
        generation = user_data if isinstance(user_data, int) else self._play_gen
        completion = None
        with self._lock:
            if (generation != self._play_gen or self._explicitly_stopped
                    or self._stop_in_progress):
                return
            if self._chain_items and all(it.get("is_tts") for it in self._chain_items):
                completion = _PlaybackEnd(generation, self._current_session_id, -1, None)
                self._state = "stopped"
                self._current_song_id = None
                self._talk_playing = False
                self._talk_only_chain = False
                self._tts_events_pending = 0
                self._chain_items = []
                self._current_chain_index = 0
        if completion is not None:
            self._fire_song_ended(None, completion)

    def _on_stopped(self, event, user_data):
        """Called when the media list player stops.

        IMPORTANT: This event is UNRELIABLE for natural-end detection.
        VLC may fire MediaListPlayerStopped asynchronously from a prior
        stop() call (inside play_song/stop) long after the flags have been
        reset and the new song is already playing.  Since VLC events carry
        no Python-generation metadata, we cannot distinguish an async stop
        event from a genuine natural end here.

        A generation guard (_stop_gen vs _play_gen) prevents clearing the
        chain items of a newer playback session that has since started.

        THEREFORE: _on_stopped only does cleanup.  Song-end callbacks are
        fired by _on_media_end_reached (attached directly on the internal
        MediaPlayer) and/or _on_end_reached (MediaListPlayer fallback).
        """
        if isinstance(user_data, int) and user_data != self._play_gen:
            return
        _song_logger.debug("VLC_EVENT MediaListPlayerStopped  state=%s  stopped=%s  stop_in_prog=%s  "
                           "stop_gen=%d  play_gen=%d  song_id=%s",
                           self._state, self._explicitly_stopped, self._stop_in_progress,
                           self._stop_gen, self._play_gen, self._current_song_id)

        # Generation guard: if a newer playback has started, don't clear its state
        if self._play_gen > self._stop_gen:
            _song_logger.debug("  -> ignored (stale stop, play_gen=%d > stop_gen=%d)", self._play_gen, self._stop_gen)
            return

        # If _stop_in_progress is True, this is our own explicit stop() call
        # from play_chain/play_song, NOT a natural end.  The stop() call
        # fires this event synchronously on the same thread while _stop_gen
        # still equals _play_gen (both are set before stop()), so the
        # generation guard above does NOT filter it.  Skip cleanup — the
        # caller will set up the new chain immediately after.
        if self._stop_in_progress:
            _song_logger.debug("  -> ignored (explicit stop, _stop_in_progress=True)")
            return

        self._explicitly_stopped = False
        self._stop_in_progress = False
        self._state = "stopped"
        self._current_song_id = None
        self._current_session_id = None
        self._talk_playing = False
        self._chain_items = []
        self._current_chain_index = 0
        _song_logger.debug("  -> cleaned up: state=stopped song_id=None")

    def _fire_song_ended(self, song_id, completion=None):
        """Fire all song_ended callbacks in a background thread.

        Callbacks (which call play_song/play_chain) MUST run outside VLC's
        event thread — VLC cannot safely start new media from within its own
        event callbacks.
        """
        # Claim once under the playback lock, before any persistence or
        # deferred dispatch. Keep only the current generation's item keys.
        with self._lock:
            if completion is None:
                completion = _PlaybackEnd(
                    self._play_gen, self._current_session_id,
                    self._current_chain_index if song_id is not None else -1,
                    song_id,
                )
            if (completion.generation != self._play_gen
                    or self._explicitly_stopped or self._stop_in_progress):
                return
            if self._completion_gen != completion.generation:
                self._completion_gen = completion.generation
                self._completed_items.clear()
            key = (completion.item_index, completion.song_id)
            if key in self._completed_items:
                return
            self._completed_items.add(key)
        _song_logger.debug("_fire_song_ended(song=%s) — recording completion, deferring callbacks", song_id)
        # Record completion event (lightweight DB op, safe in VLC thread)
        try:
            session_id = completion.session_id
            if session_id and song_id is not None:
                database.record_play_event(
                    song_id=song_id,
                    session_id=session_id,
                    signal="completed",
                    completion_pct=100.0,
                )
        except Exception as e:
            _song_logger.error("Error recording completion: %s", e)

        # Defer callbacks to background thread
        _song_logger.debug("  -> starting background thread for callbacks")
        threading.Thread(target=self._run_ended_callbacks,
                         args=(song_id, completion.generation), daemon=True,
                         name="song-ended-cb").start()

    def _run_ended_callbacks(self, song_id, generation=None):
        """Run song-ended callbacks in a background thread."""
        with self._cb_lock:
            cbs = list(self._on_song_ended_callbacks)
        _song_logger.debug("_run_ended_callbacks(song=%s) — %d callbacks registered", song_id, len(cbs))
        # Skip DB-recording callbacks when song_id is None (talk-only chain ended)
        for i, cb in enumerate(cbs):
            with self._lock:
                if (generation is not None and generation != self._play_gen
                        or self._explicitly_stopped or self._stop_in_progress):
                    return
            # Never hold the player lock while invoking application code:
            # callbacks can start playback. The coordinator must still
            # protect commands racing after this check.
            cb_name = getattr(cb, '__name__', repr(cb))
            try:
                _song_logger.debug("  -> running callback %d/%d: %s", i + 1, len(cbs), cb_name)
                cb(song_id)
                _song_logger.debug("  <- callback %s done", cb_name)
            except Exception as e:
                _song_logger.error("Error in on_song_ended callback %s: %s", cb_name, e)
        _song_logger.debug("_run_ended_callbacks done")

    def _on_media_end_reached(self, event, user_data):
        """Called when the internal MediaPlayer reaches end of media.

        This is the most reliable song-end signal (attached directly on
        the MediaPlayer, not through MediaListPlayer).  It fires when
        the currently playing media completes playback.

        Three cases handled:
        1. Last non-TTS item in a mixed chain → fire song_ended(song_id)
        2. TTS item in a normal chain → decrement counter, skip
        3. Talk-only chain (all items are TTS) → fire song_ended(None) to
           trigger pop_next() in main.py, advancing to the next real song.

        State reads/writes are protected by self._lock to avoid races
        with play_song/play_chain which modify the same fields under
        the lock.
        """
        event_gen = user_data if isinstance(user_data, int) else self._play_gen
        _song_logger.debug("VLC_EVENT MediaPlayerEndReached (internal mp)  state=%s  gen=%d  song_id=%s"
                           "  chain_idx=%d/%d  talk_playing=%s  talk_only=%s",
                           self._state, event_gen, self._current_song_id,
                           self._current_chain_index, len(self._chain_items),
                           self._talk_playing, self._talk_only_chain)
        talk_only_ended = False
        song_id = None
        completion = None
        _song_logger.debug("  -> waiting for player._lock (event_gen=%d)...", event_gen)
        if not self._lock.acquire(timeout=5.0):
            _song_logger.error("  -> DEADLOCK: could not acquire player._lock within 5s! Dumping threads:")
            import traceback as _tb
            import sys as _sys
            _cur_frames = _sys._current_frames()
            for th in threading.enumerate():
                _tid = th.ident
                _frame = _cur_frames.get(_tid)
                if _frame:
                    _song_logger.error("  -> Thread %s (id=%s):", th.name, _tid)
                    for _f, _fl in _tb.walk_stack(_frame):
                        _song_logger.error("    %s:%d %s", _f.f_code.co_filename, _fl, _f.f_code.co_name)
            _song_logger.error("  -> DEADLOCK: bailing out of _on_media_end_reached")
            return
        try:
            _song_logger.debug("  -> acquired player._lock, event_gen=%d play_gen=%d state=%s talk_only=%s",
                               event_gen, self._play_gen, self._state, self._talk_only_chain)
            # Generation guard: if play_gen advanced since this event was
            # generated, playback was stopped and restarted by play_song/
            # play_chain while this VLC event was queued.  The state now
            # belongs to a different playback session — ignore this event
            # to prevent firing song_ended for the wrong song.
            if (self._play_gen != event_gen or self._explicitly_stopped
                    or self._stop_in_progress):
                _song_logger.debug("  -> stale event (gen %d != current %d), ignoring",
                                   event_gen, self._play_gen)
                return
            if self._state == "playing" and self._current_song_id:
                _song_logger.debug("  -> branch1: state=playing song_id=%s", self._current_song_id)
                # Ignore TTS items — _on_next_item handles chain advancement.
                #
                # IMPORTANT: _on_next_item may fire BEFORE or AFTER this
                # event (VLC platform-dependent).  Use a counter-based
                # approach (_tts_events_pending) that tracks ALL remaining
                # TTS events regardless of chain-index ordering.  This
                # avoids the race where ci points to the wrong item.
                # Only _on_media_end_reached decrements the counter, so
                # it works correctly in both event orderings.
                if self._tts_events_pending > 0:
                    self._tts_events_pending -= 1
                    _song_logger.debug("  -> ignored (TTS item ended, %d remaining)",
                                       self._tts_events_pending)
                    return
                # Original index-based check — fires when _on_media_end_reached
                # runs before _on_next_item, before ci advances past the TTS.
                if (0 <= self._current_chain_index < len(self._chain_items) and
                    self._chain_items[self._current_chain_index].get("is_tts")):
                    _song_logger.debug("  -> ignored (TTS item ended via idx, ci=%d)",
                                       self._current_chain_index)
                    return
                # Only fire for the LAST item in the chain.
                # Mid-chain songs are handled by _on_next_item's prev-item check.
                if self._current_chain_index < len(self._chain_items) - 1:
                    _song_logger.debug("  -> ignored (not last item, idx=%d of %d)",
                                       self._current_chain_index, len(self._chain_items) - 1)
                    return
                _song_logger.debug("  -> branch1: firing ended for song %s", self._current_song_id)
                song_id = self._current_song_id
                completion = _PlaybackEnd(event_gen, self._current_session_id,
                                          self._current_chain_index, song_id)
                self._state = "stopped"
                self._talk_playing = False
                self._current_song_id = None
                self._chain_items = []
                self._current_chain_index = 0
            else:
                _song_logger.debug("  -> branch2: state=%s song_id=%s talk_only=%s",
                                   self._state, self._current_song_id, self._talk_only_chain)
                # Detect talk-only chain end using persistent _talk_only_chain
                # flag (set in play_chain).  This flag survives even if
                # _chain_items was cleared by a concurrent gen advance
                # (e.g. _on_stopped or stop() during long TTS playback).
                if self._talk_only_chain:
                    if self._tts_events_pending > 1:
                        self._tts_events_pending -= 1
                        return
                    completion = _PlaybackEnd(event_gen, self._current_session_id, -1, None)
                    talk_only_ended = True
                    self._state = "stopped"
                    self._talk_playing = False
                    self._talk_only_chain = False
                    self._current_song_id = None
                    self._chain_items = []
                    self._current_chain_index = 0
                    self._tts_events_pending = 0
                    _song_logger.debug("  -> talk-only chain ended, will fire synthetic song_ended(None)")
                if not talk_only_ended:
                    _song_logger.debug("  -> ignored (state=%s, song_id=%s)", self._state, self._current_song_id)
        finally:
            self._lock.release()
        if song_id:
            _song_logger.debug("  -> firing ended for song %s (from internal mp event)", song_id)
            self._fire_song_ended(song_id, completion)
        elif talk_only_ended:
            # Talk-only chain ended after the lock is released — fire
            # synthetic song_ended(None) to trigger pop_next() in main.py,
            # advancing to the next real song from queue.
            _song_logger.debug("  -> talk-only chain ended, firing synthetic song_ended to advance queue")
            self._fire_song_ended(None, completion)

    def play_song(self, song_id, filepath, session_id=None):
        """Play one song using a dedicated native playback lifecycle."""
        return self._start_items([{"song_id": song_id, "filepath": filepath, "is_tts": False}], session_id)

    def play_chain(self, items, session_id=None):
        """Play prepared speech/song items using VLC's media list chaining."""
        return self._start_items(items, session_id)

    def _start_items(self, items, session_id=None):
        if not items:
            return False
        with self._command_lock:
            if self._closed:
                return False
            with self._lock:
                self._play_gen += 1
                generation = self._play_gen
                old = self._transport
                prior_session = self._current_session_id
                self._explicitly_stopped = True
                self._stop_in_progress = True
                self._transport = None
                self._list_player = None
                self._media_list = None
                self._state = "stopped"
            # No playback state lock may be held while native stop runs.
            # Its callbacks only enqueue observations with the old identity.
            if old is not None:
                old.retire()
            transport = None
            try:
                transport = VLCTransport(self._vlc_instance, vlc.EventType, generation, {
                    "next": self._on_next_item, "ended": self._on_end_reached,
                    "stopped": self._on_stopped, "media_end": self._on_media_end_reached,
                }, self._enqueue_native_event)
                prepared = []
                for item in items:
                    media = self._vlc_instance.media_new(item["filepath"])
                    if media is None:
                        raise RuntimeError("VLC could not open prepared media")
                    transport.media_list.add_media(media)
                    media.release()
                    prepared.append(dict(item))
                first_song = next((it.get("song_id") for it in prepared if not it.get("is_tts")), None)
                song = database.get_song(first_song) if first_song else None
                gain = (song.get("gain_db") or 0.0) if song else 0.0
                talk_total_ms = 0
                import wave
                for item in prepared:
                    if item.get("is_tts"):
                        try:
                            with wave.open(item["filepath"], "rb") as wav:
                                talk_total_ms += int(wav.getnframes() / wav.getframerate() * 1000)
                        except (OSError, EOFError, wave.Error):
                            _song_logger.debug("Could not read prepared speech duration")
                with self._lock:
                    self._transport = transport
                    self._list_player = transport.list_player
                    self._media_list = transport.media_list
                    self._chain_items = prepared
                    self._current_chain_index = 0
                    self._first_next_gen = generation
                    self._current_song_id = first_song
                    self._current_session_id = session_id if session_id is not None else prior_session
                    self._talk_playing = any(it.get("is_tts") for it in prepared)
                    self._talk_only_chain = all(it.get("is_tts") for it in prepared)
                    self._tts_events_pending = sum(bool(it.get("is_tts")) for it in prepared)
                    self._talk_total_ms = talk_total_ms
                    self._gain_db = gain
                    self._play_started_at = time.time()
                    self._explicitly_stopped = False
                    self._stop_in_progress = False
                    self._stop_gen = -1
                    self._state = "playing"
                    with self._cb_lock:
                        self._on_song_started_callbacks.clear()
                # Carry output settings across native lifecycles.
                if self._pre_talk_volume is not None:
                    self._base_volume = self._pre_talk_volume
                    self._pre_talk_volume = None
                if self._talk_playing:
                    talk_percent = database.get_setting_int("talk_volume", 100)
                    if talk_percent != 100:
                        self._pre_talk_volume = self._base_volume
                        self._base_volume = max(0, min(100, int(self._base_volume * talk_percent / 100)))
                self._apply_effective_volume()
                if self._current_eq:
                    self.set_eq_preset(self._current_eq)
                finished = threading.Event()
                failure = []
                def start_native():
                    try:
                        if transport.play() == -1:
                            failure.append("VLC refused playback")
                        if self._play_gen != generation:
                            transport.retire()
                    except Exception as exc:
                        failure.append(str(exc))
                    finally:
                        finished.set()
                threading.Thread(target=start_native, daemon=True, name="vlc-play-thread").start()
                if not finished.wait(3.0):
                    _song_logger.warning("VLC start still pending: generation=%s", generation)
                if failure:
                    raise RuntimeError(failure[0])
                return True
            except Exception:
                _song_logger.exception("VLC playback preparation failed: generation=%s", generation)
                with self._lock:
                    self._state = "stopped"
                    self._current_song_id = None
                    self._transport = None
                    self._list_player = None
                    self._media_list = None
                    self._stop_in_progress = False
                    self._explicitly_stopped = True
                if transport is not None:
                    transport.retire()
                return False

    def stop(self):
        """Retire the playback identity before stopping its native emitter."""
        with self._command_lock:
            with self._lock:
                self._play_gen += 1
                self._stop_gen = self._play_gen
                self._explicitly_stopped = True
                self._stop_in_progress = True
                transport, native = self._transport, self._list_player
                self._transport = None
                self._list_player = None
                self._current_song_id = None
                self._current_session_id = None
                self._state = "stopped"
                self._talk_playing = False
                self._talk_only_chain = False
                self._chain_items = []
                self._current_chain_index = 0
                self._tts_events_pending = 0
            try:
                if transport is not None:
                    transport.retire()
                elif native is not None:
                    native.stop()
            finally:
                self._stop_in_progress = False
                self._stop_gen = -1
            if self._pre_talk_volume is not None:
                self.set_volume(self._pre_talk_volume)
                self._pre_talk_volume = None
            with self._cb_lock:
                self._on_song_started_callbacks.clear()

    def shutdown(self):
        """Permanently reject starts and stop the native observation dispatcher."""
        with self._command_lock:
            self._closed = True
            self.stop()
        self._native_events.put(None)
        worker = getattr(self, "_event_worker", None)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)

    def pause(self):
        """Pause playback."""
        with self._command_lock:
            if self._list_player is not None and self._state == "playing":
                self._transport.set_pause(1)
                self._state = "paused"

    def resume(self):
        """Resume playback."""
        with self._command_lock:
            if self._list_player is not None and self._state == "paused" and not self._closed:
                self._transport.set_pause(0)
                self._state = "playing"

    def toggle_pause(self):
        """Toggle pause/resume."""
        if self._state == "playing":
            self.pause()
        elif self._state == "paused":
            self.resume()

    def get_state(self):
        """Return current player state: 'stopped', 'playing', 'paused'."""
        return self._state

    def is_playing(self):
        """Return True if currently playing."""
        return self._state == "playing"

    def get_current_song_id(self):
        """Return the currently playing song ID."""
        return self._current_song_id

    def is_talk_playing(self):
        """Return True if a talk segment is currently playing (TTS in chain)."""
        return self._talk_playing

    def _get_mp(self):
        """Return the internal MediaPlayer, or None."""
        transport = self._transport
        return transport.media_view if transport is not None else None

    def get_position(self):
        """Return current playback position as fraction (0.0 to 1.0).

        Returns 0.0 when stopped, ended, or unavailable.
        """
        try:
            with self._lock:
                mp = self._get_mp()
                if mp:
                    state = mp.get_state()
                    if state in (vlc.State.Playing, vlc.State.Paused):
                        pos = mp.get_position()
                        return float(max(0.0, pos))
                # Talk-only chain: VLC reports Stopped — synthesize from clock
                if self._talk_only_chain and self._talk_total_ms > 0 and self._play_started_at is not None:
                    elapsed = (time.time() - self._play_started_at) * 1000
                    return min(1.0, max(0.0, float(elapsed) / self._talk_total_ms))
        except Exception as e:
            _song_logger.debug("get_position failed: %s", e)
        return 0.0

    def get_time(self):
        """Return current playback position in milliseconds."""
        try:
            with self._lock:
                mp = self._get_mp()
                if mp:
                    state = mp.get_state()
                    if state in (vlc.State.Playing, vlc.State.Paused):
                        t = mp.get_time()
                        return int(max(0, t))
                # Talk-only chain: VLC reports Stopped — use elapsed wall clock
                if self._talk_only_chain and self._play_started_at is not None:
                    return int((time.time() - self._play_started_at) * 1000)
        except Exception as e:
            _song_logger.debug("get_time failed: %s", e)
        return 0

    def get_length(self):
        """Return total length of current media in milliseconds."""
        try:
            with self._lock:
                mp = self._get_mp()
                if mp:
                    state = mp.get_state()
                    if state in (vlc.State.Playing, vlc.State.Paused):
                        length = mp.get_length()
                        if length > 0:
                            return int(max(0, length))
                # Talk-only chain: VLC reports Stopped — use stored TTS duration
                if self._talk_only_chain and self._talk_total_ms > 0:
                    return self._talk_total_ms
        except Exception as e:
            _song_logger.debug("get_length failed: %s", e)
        return 0

    def set_volume(self, volume):
        """Set base volume (0-100). Recalculates effective volume with gain."""
        self._base_volume = max(0, min(100, volume))
        self._apply_effective_volume()

    def _apply_effective_volume(self):
        """Apply effective volume (no lock — caller must hold or it's safe)."""
        target = self._base_volume
        if self._normalization_enabled and self._gain_db:
            linear = 10 ** (self._gain_db / 20.0)
            target = int(round(self._base_volume * linear))
            target = max(0, min(100, target))
        try:
            mp = self._get_mp()
            if mp:
                mp.audio_set_volume(target)
        except Exception as e:
            _song_logger.debug("_apply_effective_volume failed: %s", e)

    def get_volume(self):
        """Get current base volume (0-100)."""
        return self._base_volume

    def set_normalization_enabled(self, enabled):
        """Enable or disable volume normalization."""
        self._normalization_enabled = enabled
        with self._lock:
            self._apply_effective_volume()

    def set_gain_db(self, gain_db):
        """Set per-song gain offset and recalculate volume."""
        self._gain_db = gain_db if gain_db else 0.0
        with self._lock:
            self._apply_effective_volume()

    EQ_PRESETS = {
        "off": None,
        "bass_boost": [0.0, 0.0, 0.6, 0.8, 0.6, 0.4, 0.2, 0.0, 0.0, 0.0],
        "vocal_boost": [0.0, 0.0, 0.2, 0.4, 0.6, 0.8, 0.6, 0.4, 0.2, 0.0],
        "night_mode": [-0.2, -0.1, 0.0, 0.0, 0.0, -0.1, -0.2, -0.4, -0.6, -0.8],
        "flat": [0.0] * 10,
        "loudness": [0.4, 0.3, 0.2, 0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        "clarity": [0.2, 0.3, 0.0, 0.2, 0.5, 0.7, 0.5, 0.3, 0.2, 0.1],
    }

    def set_eq_preset(self, preset_name):
        """Set equalizer preset by name. 'off' or None to disable."""
        if preset_name in (None, "off", ""):
            self._current_eq = None
            self._last_eq_preset = None
            try:
                mp = self._get_mp()
                if mp:
                    mp.set_equalizer_enable(False)
            except Exception as e:
                _song_logger.debug("set_eq_preset disable failed: %s", e)
            return

        bands = self.EQ_PRESETS.get(preset_name)
        if not bands:
            _song_logger.warning("Unknown EQ preset: %s", preset_name)
            return

        try:
            eq = vlc.AudioEqualizer()
            for i, amp in enumerate(bands):
                eq.set_amp_at_index(amp, i)
            mp = self._get_mp()
            if mp:
                mp.set_equalizer(eq)
                mp.set_equalizer_enable(True)
                self._current_eq = preset_name
                self._last_eq_preset = preset_name
                _song_logger.info("EQ set to: %s", preset_name)
        except Exception as e:
            _song_logger.error("Failed to set EQ preset '%s': %s", preset_name, e)

    def get_eq_preset(self):
        """Return current EQ preset name, or None if disabled."""
        return self._current_eq

    def seek(self, ms):
        """Seek to a position in milliseconds."""
        try:
            with self._lock:
                mp = self._get_mp()
                state = mp.get_state() if mp else None
                if mp and state in (vlc.State.Playing, vlc.State.Paused):
                    ms_int = int(ms)
                    mp.set_time(ms_int)
                    _song_logger.debug("seek(%d) state=%s OK", ms_int, state)
                else:
                    _song_logger.debug("seek(%d) skipped: mp=%s state=%s", ms, mp, state)
        except Exception as e:
            _song_logger.error("seek(%s) FAILED: %s", ms, e)

    def on_song_ended(self, callback):
        """Register a callback for when a song ends."""
        with self._cb_lock:
            self._on_song_ended_callbacks.append(callback)

    def register_on_song_started(self, callback):
        """Register a callback fired when VLC advances TO a real song.

        Used by talk segment playback to defer on_song_started until
        the actual song begins (after TTS WAVs finish).
        """
        with self._cb_lock:
            self._on_song_started_callbacks.append(callback)

    def on_stopped(self, callback):
        """Register a callback for when playback stops."""
        with self._cb_lock:
            self._on_playback_stopped_callbacks.append(callback)


# Global singleton
_player = None
_player_lock = threading.Lock()


def get_player():
    """Get or create the global Player singleton."""
    global _player
    if _player is None:
        with _player_lock:
            if _player is None:
                _player = Player()
    return _player
