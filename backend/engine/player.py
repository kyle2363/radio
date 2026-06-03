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
_handler = logging.FileHandler(config.LOG_PATH, mode="a", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(threadName)s] %(message)s", datefmt="%H:%M:%S"))
_song_logger.addHandler(_handler)
_song_logger.setLevel(logging.DEBUG)
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
            self._vlc_instance = vlc.Instance()
        finally:
            _restore_stderr(saved)
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
            if not cur.get("is_tts"):
                sid = cur.get("song_id")
                if sid:
                    self._current_song_id = sid
                    _song_logger.debug("  -> updated current_song_id=%s from chain", sid)
                if self._talk_playing:
                    self._talk_playing = False
                    _song_logger.debug("  -> talk ended (chain advanced to real song)")

    def _on_end_reached(self, event, user_data):
        """Called when the entire media list playback is done.

        Safety net cleanup.  Also handles the case where a talk-only chain
        (no real song) ends — fires a synthetic song_ended with None so the
        queue manager can advance to the next item.
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
        self._explicitly_stopped = False
        self._stop_in_progress = False
        self._state = "stopped"
        self._current_song_id = None
        self._talk_playing = False
        self._chain_items = []
        self._current_chain_index = 0
        _song_logger.debug("  -> cleaned up: state=stopped song_id=None")
        # If a talk-only chain just ended, nudge the queue to play the next song
        if had_talk_only:
            _song_logger.debug("  -> talk-only chain ended, firing synthetic song_ended to advance queue")
            threading.Thread(
                target=self._run_ended_callbacks, args=(None,), daemon=True,
                name="talk-chain-ended-cb"
            ).start()

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

        Only fires song_ended for the LAST non-TTS item in the chain.
        Mid-chain song completions are handled by _on_next_item which
        fires a callback for the item we are advancing FROM.
        TTS items are silently skipped.
        """
        event_gen = self._play_gen
        _song_logger.debug("VLC_EVENT MediaPlayerEndReached (internal mp)  state=%s  gen=%d  song_id=%s"
                           "  chain_idx=%d/%d",
                           self._state, event_gen, self._current_song_id,
                           self._current_chain_index, len(self._chain_items))
        if self._state == "playing" and self._current_song_id:
            # Ignore TTS items — _on_next_item handles chain advancement
            if (0 <= self._current_chain_index < len(self._chain_items) and
                self._chain_items[self._current_chain_index].get("is_tts")):
                _song_logger.debug("  -> ignored (TTS item ended)")
                return
            # Only fire for the LAST item in the chain.
            # Mid-chain songs are handled by _on_next_item's prev-item check.
            if self._current_chain_index < len(self._chain_items) - 1:
                _song_logger.debug("  -> ignored (not last item, idx=%d of %d)",
                                   self._current_chain_index, len(self._chain_items) - 1)
                return
            song_id = self._current_song_id
            self._state = "stopped"  # prevent double-fire via _on_end_reached
            self._talk_playing = False
            _song_logger.debug("  -> firing ended for song %s (from internal mp event)", song_id)
            self._fire_song_ended(song_id)
            self._current_song_id = None
            self._chain_items = []
            self._current_chain_index = 0

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
                _song_logger.debug("  -> play new media")
                self._list_player.play()
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
                    self._media_list.add_media(media)
                self._chain_items = items
                self._current_chain_index = 0
                # Determine if this chain contains TTS (talk) items
                self._talk_playing = any(it.get("is_tts") for it in items)
                _song_logger.debug("  -> play new chain, talk_playing=%s",
                                   self._talk_playing)
                self._list_player.play()
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
                self._state = "playing"
                self._play_started_at = time.time()
                _song_logger.debug("  -> state=playing song_id=%s (gen=%d, inside lock)", self._current_song_id, gen)

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
        self._chain_items = []
        self._current_chain_index = 0

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
                        return int(max(0, length))
        except Exception as e:
            _song_logger.debug("get_length failed: %s", e)
        return 0

    def set_volume(self, volume):
        """Set volume (0-100)."""
        try:
            with self._lock:
                mp = self._get_mp()
                if mp and mp.get_state() == vlc.State.Playing:
                    mp.audio_set_volume(max(0, min(100, volume)))
        except Exception as e:
            _song_logger.debug("set_volume failed: %s", e)

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
