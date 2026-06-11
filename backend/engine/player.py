"""VLC MediaListPlayer for gapless audio playback."""

import logging
import os
import sys
import threading
import time
import vlc
import config
from db import database

_song_logger = logging.getLogger("radio.playback")
_song_logger.setLevel(logging.WARNING)
# Console handler (visible in terminal)
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.WARNING)
_ch.setFormatter(logging.Formatter("[Playback] %(asctime)s.%(msecs)03d [%(threadName)s] %(message)s", datefmt="%H:%M:%S"))
_song_logger.addHandler(_ch)
# File handler (persistent log)
_fh = logging.FileHandler(config.LOG_PATH, mode="a", encoding="utf-8")
_fh.setLevel(logging.WARNING)
_fh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(threadName)s] %(message)s", datefmt="%H:%M:%S"))
_song_logger.addHandler(_fh)
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


class Player:
    """Wraps libvlc MediaListPlayer for true gapless playback."""

    def __init__(self):
        self._lock = threading.Lock()
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
        self._media_list = self._vlc_instance.media_list_new([])
        self._list_player = self._vlc_instance.media_list_player_new()
        self._list_player.set_media_list(self._media_list)

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

        # Internal MediaPlayer end-reached handler is re-attached each play()
        self._mp_event_manager = None

        # Event manager for list-level events
        self._event_manager = self._list_player.event_manager()
        self._event_manager.event_attach(
            vlc.EventType.MediaListPlayerNextItemSet,
            self._on_next_item,
            None,
        )
        self._event_manager.event_attach(
            vlc.EventType.MediaPlayerEndReached,
            self._on_end_reached,
            None,
        )
        self._event_manager.event_attach(
            vlc.EventType.MediaListPlayerStopped,
            self._on_stopped,
            None,
        )

    def _on_next_item(self, event, user_data):
        """Called when MediaListPlayer advances to next item.

        VLC fires MediaListPlayerNextItemSet BOTH at play-start (setting
        the first item) AND when advancing from one item to the next.
        We skip the play-start event using a generation flag so that
        _current_chain_index only advances on real transitions — this
        is critical for _on_media_end_reached to correctly identify
        TTS items.

        When this is a real advancement, fires song_ended for the
        previous item if it was a non-TTS song.  Also updates
        _current_song_id when advancing TO a real song.
        """
        _song_logger.debug("VLC_EVENT MediaListPlayerNextItemSet  index=%d  chain_len=%d  state=%s  gen=%d  first_next_gen=%d",
                           self._current_chain_index, len(self._chain_items), self._state, self._play_gen,
                           self._first_next_gen)

        # Skip play-start event (VLC fires this once when the first item starts)
        if self._current_chain_index == 0 and self._first_next_gen == self._play_gen:
            self._first_next_gen = -1
            _song_logger.debug("  -> first next item (play start) — ignored")
            return

        # Real advancement: fire callback for the item we're leaving
        if self._current_chain_index >= 0 and self._current_chain_index < len(self._chain_items):
            item = self._chain_items[self._current_chain_index]
            if not item.get("is_tts"):
                song_id = item.get("song_id")
                if song_id:
                    _song_logger.debug("  -> firing ended for song %s", song_id)
                    self._fire_song_ended(song_id)
            else:
                _song_logger.debug("  -> skipped (TTS item)")

        self._current_chain_index += 1

        # Clear talk-playing when we advance from TTS to the real song
        if self._current_chain_index < len(self._chain_items):
            cur = self._chain_items[self._current_chain_index]
            _song_logger.debug("  -> advanced to chain[%d]: is_tts=%s song_id=%s filepath=%s",
                               self._current_chain_index, cur.get("is_tts"), cur.get("song_id"),
                               os.path.basename(cur.get("filepath", "")))
            if not cur.get("is_tts"):
                sid = cur.get("song_id")
                if sid:
                    self._current_song_id = sid
                    _song_logger.debug("  -> updated current_song_id=%s from chain", sid)
                if self._talk_playing:
                    self._talk_playing = False
                    _song_logger.debug("  -> talk ended (chain advanced to real song)")
                    # Restore volume after talk segment
                    if self._pre_talk_volume is not None:
                        self.set_volume(self._pre_talk_volume)
                        _song_logger.debug("  -> restored volume to %d after talk segment", self._pre_talk_volume)
                        self._pre_talk_volume = None
                    # Fire registered callbacks (e.g. on_song_started deferred from talk segment)
                    for cb in self._on_song_started_callbacks:
                        try:
                            cb(sid)
                        except Exception as e:
                            _song_logger.error("Error in on_song_started callback: %s", e)
                    self._on_song_started_callbacks.clear()
        else:
            _song_logger.debug("  -> chain fully advanced (idx=%d >= len=%d), no more items",
                               self._current_chain_index, len(self._chain_items))

    def _on_end_reached(self, event, user_data):
        """Called when the entire media list playback is done.

        Safety net cleanup.  Also handles the case where a talk-only chain
        (no real song) ends — fires a synthetic song_ended with None so the
        queue manager can advance to the next item.

        IMPORTANT: For normal (non-talk-only) chains, we do NOT clear
        _state, _current_song_id, _chain_items, or _current_chain_index
        here.  Those fields are managed by _on_media_end_reached (the
        primary end handler) and play_song/play_chain.  Clearing them
        here races with _on_media_end_reached — if _on_end_reached fires
        first (which VLC can do), it sets _state="stopped" and
        _current_song_id=None, which causes _on_media_end_reached's
        guard (if state=="playing") to bail, silently dropping the
        song_ended notification and freezing playback.
        """
        saved_gen = self._play_gen
        had_talk_only = (
            self._chain_items
            and all(it.get("is_tts") for it in self._chain_items)
        )
        _song_logger.debug("VLC_EVENT MediaPlayerEndReached  chain_len=%d  state=%s  stopped=%s  gen=%d"
                           "  chain_idx=%d/%d  talk_only=%s  — cleanup",
                           len(self._chain_items), self._state, self._explicitly_stopped, saved_gen,
                           self._current_chain_index, len(self._chain_items), had_talk_only)
        # Reset boolean flags unconditionally (safe for any ordering)
        self._explicitly_stopped = False
        self._stop_in_progress = False
        # Restore volume if talk segment was playing when chain ended
        if self._pre_talk_volume is not None:
            self.set_volume(self._pre_talk_volume)
            _song_logger.debug("  -> restored volume to %d after talk segment (end_reached)", self._pre_talk_volume)
            self._pre_talk_volume = None

        if had_talk_only:
            # Talk-only chain: safe to clear state — there is no real song
            # that _on_media_end_reached needs to process.
            self._state = "stopped"
            self._current_song_id = None
            self._talk_playing = False
            self._talk_only_chain = False
            self._tts_events_pending = 0
            self._chain_items = []
            self._current_chain_index = 0
            _song_logger.debug("  -> talk-only chain ended, firing synthetic song_ended to advance queue")
            threading.Thread(
                target=self._run_ended_callbacks, args=(None,), daemon=True,
                name="talk-chain-ended-cb"
            ).start()
        else:
            # Normal chain: leave _state, _current_song_id, _chain_items,
            # _current_chain_index untouched — _on_media_end_reached (or
            # stop()/play_song()) will clear them when it processes the
            # song end.  This prevents _on_end_reached from racing ahead
            # and suppressing the song_ended callback.
            _song_logger.debug("  -> normal chain end, skipping state clear (deferred to _on_media_end_reached)")

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

    def _fire_song_ended(self, song_id):
        """Fire all song_ended callbacks in a background thread.

        Callbacks (which call play_song/play_chain) MUST run outside VLC's
        event thread — VLC cannot safely start new media from within its own
        event callbacks.
        """
        if song_id is None:
            return
        _song_logger.debug("_fire_song_ended(song=%s) — recording completion, deferring callbacks", song_id)
        # Record completion event (lightweight DB op, safe in VLC thread)
        try:
            session_id = self._current_session_id
            if session_id:
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
        threading.Thread(target=self._run_ended_callbacks, args=(song_id,), daemon=True, name="song-ended-cb").start()

    def _run_ended_callbacks(self, song_id):
        """Run song-ended callbacks in a background thread."""
        _song_logger.debug("_run_ended_callbacks(song=%s) — %d callbacks registered", song_id, len(self._on_song_ended_callbacks))
        # Skip DB-recording callbacks when song_id is None (talk-only chain ended)
        for i, cb in enumerate(self._on_song_ended_callbacks):
            cb_name = getattr(cb, '__name__', repr(cb))
            try:
                _song_logger.debug("  -> running callback %d/%d: %s", i + 1, len(self._on_song_ended_callbacks), cb_name)
                cb(song_id)
                _song_logger.debug("  <- callback %s done", cb_name)
            except Exception as e:
                _song_logger.error("Error in on_song_ended callback %s: %s", cb_name, e)
        _song_logger.debug("_run_ended_callbacks done")

    def _attach_mp_end_reached(self):
        """Attach MediaPlayerEndReached on the internal MediaPlayer.

        MediaListPlayer does NOT reliably forward this event in all VLC
        versions, so we attach directly on the internal player instance.
        Must be called after play() so the internal player is valid.
        """
        try:
            mp = self._list_player.get_media_player()
            if mp:
                self._mp_event_manager = mp.event_manager()
                self._mp_event_manager.event_attach(
                    vlc.EventType.MediaPlayerEndReached,
                    self._on_media_end_reached,
                    None,
                )
                _song_logger.debug("  -> attached MediaPlayerEndReached on internal player")
        except Exception as e:
            _song_logger.debug("  -> could not attach internal player end event: %s", e)

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
        event_gen = self._play_gen
        _song_logger.debug("VLC_EVENT MediaPlayerEndReached (internal mp)  state=%s  gen=%d  song_id=%s"
                           "  chain_idx=%d/%d  talk_playing=%s  talk_only=%s",
                           self._state, event_gen, self._current_song_id,
                           self._current_chain_index, len(self._chain_items),
                           self._talk_playing, self._talk_only_chain)
        talk_only_ended = False
        song_id = None
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
            if self._play_gen != event_gen:
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
            self._fire_song_ended(song_id)
        elif talk_only_ended:
            # Talk-only chain ended after the lock is released — fire
            # synthetic song_ended(None) to trigger pop_next() in main.py,
            # advancing to the next real song from queue.
            _song_logger.debug("  -> talk-only chain ended, firing synthetic song_ended to advance queue")
            threading.Thread(
                target=self._run_ended_callbacks, args=(None,), daemon=True,
                name="talk-chain-ended-cb"
            ).start()

    def play_song(self, song_id, filepath, session_id=None):
        """Play a single song. Returns True if playback started."""
        _song_logger.debug("play_song(id=%s, file=%s) entering", song_id, os.path.basename(filepath) if filepath else None)
        try:
            with self._lock:
                self._play_gen += 1
                gen = self._play_gen
                self._first_next_gen = gen
                self._explicitly_stopped = True
                self._stop_in_progress = True
                self._stop_gen = gen
                _song_logger.debug("  -> stop old playback (gen=%d)", gen)
                self._list_player.stop()
                self._stop_in_progress = False
                self._explicitly_stopped = False
                self._stop_gen = -1
                self._media_list = self._vlc_instance.media_list_new([])
                self._list_player.set_media_list(self._media_list)
                media = self._vlc_instance.media_new(filepath)
                self._media_list.add_media(media)
                self._chain_items = [{"song_id": song_id, "is_tts": False}]
                self._current_chain_index = 0
                self._talk_playing = False
                self._talk_only_chain = False
                self._talk_total_ms = 0
                self._tts_events_pending = 0
                self._on_song_started_callbacks.clear()
                _song_logger.debug("  -> play new media")
                # HACK: VLC's media_list_player.play() can block indefinitely if
                # the stop/play cycle happens too fast.  Run play() inside a
                # daemon thread with a 3-second timeout so we never hang the
                # background thread (same pattern as play_chain).
                _play_ok = threading.Event()
                def _do_play():
                    self._list_player.play()
                    _play_ok.set()
                threading.Thread(target=_do_play, daemon=True, name="vlc-play-song-thread").start()
                if not _play_ok.wait(timeout=3.0):
                    _song_logger.error("  -> VLC play() TIMED OUT after 3s in play_song! "
                                       "VLC may be stuck. Continuing anyway.")
                else:
                    _song_logger.debug("  -> VLC play() completed normally")
                # Attach MediaPlayerEndReached directly on internal player
                self._attach_mp_end_reached()
                # Set state inside lock so async event handlers see "playing"
                self._current_song_id = song_id
                self._current_session_id = session_id
                self._state = "playing"
                self._play_started_at = time.time()
                _song_logger.debug("  -> state=playing song_id=%s (gen=%d, inside lock)", song_id, gen)

            return True
        except Exception as e:
            _song_logger.error("play_song FAILED: %s", e)
            return False

    def play_chain(self, items):
        """Play a chain of media items back-to-back (no gap).

        items: list of dicts with 'filepath' key (and optionally 'song_id', 'is_tts')
        """
        _song_logger.debug("play_chain(%d items) entering", len(items))
        try:
            with self._lock:
                self._play_gen += 1
                gen = self._play_gen
                self._first_next_gen = gen
                self._explicitly_stopped = True
                self._stop_in_progress = True
                self._stop_gen = gen
                _song_logger.debug("  -> stop old playback (gen=%d)", gen)
                self._list_player.stop()
                self._stop_in_progress = False
                self._explicitly_stopped = False
                self._stop_gen = -1
                self._media_list = self._vlc_instance.media_list_new([])
                self._list_player.set_media_list(self._media_list)
                for item in items:
                    media = self._vlc_instance.media_new(item["filepath"])
                    if not media:
                        _song_logger.error("  -> VLC media_new FAILED for: %s", item["filepath"])
                        continue
                    self._media_list.add_media(media)
                _song_logger.debug("  -> built media list with %d items", self._media_list.count())
                self._chain_items = items
                self._current_chain_index = 0
                # Determine if this chain contains TTS (talk) items
                self._talk_playing = any(it.get("is_tts") for it in items)
                self._tts_events_pending = sum(1 for it in items if it.get("is_tts"))
                self._talk_only_chain = self._talk_playing and all(it.get("is_tts") for it in items)
                self._talk_total_ms = 0
                if self._talk_playing:
                    import wave as _wave
                    for _w_item in items:
                        _w_path = _w_item.get("filepath")
                        if _w_item.get("is_tts") and _w_path:
                            try:
                                with _wave.open(_w_path, 'rb') as _wf:
                                    _frames = _wf.getnframes()
                                    _rate = _wf.getframerate()
                                    if _rate > 0:
                                        self._talk_total_ms += int(_frames / _rate * 1000)
                            except Exception:
                                pass
                self._on_song_started_callbacks.clear()
                _song_logger.debug("  -> play new chain, talk_playing=%s, tts_pending=%d",
                                   self._talk_playing, self._tts_events_pending)

                # Apply talk volume boost if chain has TTS items
                if self._talk_playing:
                    try:
                        talk_vol_pct = int(database.get_setting("talk_volume") or "100")
                    except Exception:
                        talk_vol_pct = 100
                    if talk_vol_pct != 100:
                        # Use _base_volume directly — we already hold self._lock,
                        # and get_volume() would deadlock trying to acquire it.
                        current_vol = self._base_volume
                        adjusted_vol = max(0, min(100, int(current_vol * talk_vol_pct / 100)))
                        self._pre_talk_volume = current_vol
                        self.set_volume(adjusted_vol)
                        _song_logger.debug("  -> talk volume: %d%% of %d = %d", talk_vol_pct, current_vol, adjusted_vol)
                    else:
                        self._pre_talk_volume = None

                _song_logger.debug("  -> built media list with %d items, calling _list_player.play() for chain gen=%d", self._media_list.count(), gen)
                # HACK: VLC's media_list_player.play() can block indefinitely if
                # the stop/play cycle happens too fast.  Run play() inside a
                # daemon thread with a 3-second timeout so we never hang the
                # background thread.
                import threading as _thr
                _play_ok = threading.Event()
                def _do_play():
                    self._list_player.play()
                    _play_ok.set()
                _t = _thr.Thread(target=_do_play, daemon=True, name="vlc-play-thread")
                _t.start()
                if not _play_ok.wait(timeout=3.0):
                    _song_logger.error("  -> VLC play() TIMED OUT after 3s! "
                                       "VLC may be stuck. Continuing anyway.")
                else:
                    _song_logger.debug("  -> VLC play() completed normally")
                _song_logger.debug("  -> post-play: is_playing=%s, state=%s",
                                   self._list_player.is_playing(),
                                   self._state)
                # Attach MediaPlayerEndReached directly on internal player
                self._attach_mp_end_reached()
                # Set state inside lock so async event handlers see "playing"
                # Find first non-TTS item for current_song_id
                first_song_id = None
                if items:
                    for it in items:
                        if not it.get("is_tts"):
                            first_song_id = it.get("song_id")
                            break
                self._current_song_id = first_song_id
                # Look up gain_db for volume normalization
                # NOTE: set _gain_db directly + call _apply_effective_volume instead
                # of set_gain_db() — we already hold self._lock (non-reentrant),
                # and set_gain_db would try to acquire it again → self-deadlock.
                if first_song_id:
                    try:
                        song = database.get_song(first_song_id)
                        if song and song.get("gain_db") is not None:
                            self._gain_db = song["gain_db"]
                        else:
                            self._gain_db = 0.0
                    except Exception:
                        self._gain_db = 0.0
                else:
                    self._gain_db = 0.0
                self._apply_effective_volume()
                self._state = "playing"
                self._play_started_at = time.time()
                _song_logger.debug("  -> state=playing song_id=%s (gen=%d, inside lock)", self._current_song_id, gen)

            _song_logger.debug("  <- play_chain returning True (chain started)")
            return True
        except Exception as e:
            _song_logger.error("play_chain FAILED: %s", e)
            return False

    def stop(self):
        """Stop playback."""
        self._play_gen += 1
        gen = self._play_gen
        self._explicitly_stopped = True
        with self._lock:
            self._stop_in_progress = True
            self._stop_gen = gen
            self._list_player.stop()
            self._stop_in_progress = False
            self._stop_gen = -1
        self._current_song_id = None
        self._current_session_id = None
        self._state = "stopped"
        self._talk_playing = False
        # Restore volume if talk segment was playing when stopped
        if self._pre_talk_volume is not None:
            self.set_volume(self._pre_talk_volume)
            self._pre_talk_volume = None
        self._chain_items = []
        self._current_chain_index = 0
        self._tts_events_pending = 0
        self._on_song_started_callbacks.clear()

    def pause(self):
        """Pause playback."""
        with self._lock:
            self._list_player.pause()
        self._state = "paused"

    def resume(self):
        """Resume playback."""
        with self._lock:
            self._list_player.play()
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
        return self._list_player.get_media_player() if self._list_player else None

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

    def get_volume(self):
        """Get current volume (0-100)."""
        try:
            with self._lock:
                mp = self._get_mp()
                if mp:
                    return mp.audio_get_volume()
        except Exception as e:
            _song_logger.debug("get_volume failed: %s", e)
        return 70

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
        self._on_song_ended_callbacks.append(callback)

    def register_on_song_started(self, callback):
        """Register a callback fired when VLC advances TO a real song.

        Used by talk segment playback to defer on_song_started until
        the actual song begins (after TTS WAVs finish).
        """
        self._on_song_started_callbacks.append(callback)

    def on_stopped(self, callback):
        """Register a callback for when playback stops."""
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
