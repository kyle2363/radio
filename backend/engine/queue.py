"""QueueManager - thread-safe queue with rebuild support.

Uses threading.Condition for efficient waiting on queue rebuild.
"""

import json
import threading
import logging
import os
import config
from datetime import datetime
from engine import taste, retriever, slots, reranker, transitions, mood, energy, recovery
from engine import player as player_engine
from db import database
from announcer import talk_segments

logger = logging.getLogger(__name__)

def _log_step(msg):
    """Log a rebuild step with thread name for debugging."""
    logger.info(f"[REBUILD-{threading.current_thread().name}] {msg}")


class QueueManager:
    """Manages the recommendation queue with thread-safe access.

    On song start, triggers background rebuild.
    pop_next() waits up to 5s for rebuild, falls back to single recommendation.
    """

    def __init__(self, player, db_handler, broadcast_callback=None, mood_machine=None):
        self._queue = []
        self._condition = threading.Condition()
        self._rebuilding = False
        self._player = player
        self._db = db_handler
        self._broadcast = broadcast_callback or (lambda *a, **kw: None)
        self._mood = mood_machine or mood.MoodStateMachine()
        self._memory = taste.MemorySystem()
        self._current_song_id = None
        self._current_session_id = None
        self._skip_streak = 0
        self._archetype = "mood_listener"
        self._recently_recommended_ids = set()
        self._max_recent_history = 20
        self._pregen_cache = {}
        self._pregen_lock = threading.Lock()
        self._pending_talk_segments = []  # separate queue — survives song queue rebuilds
        self._rebuild_gen = 0  # incremented each rebuild; used to discard stale results
        self._tts_checker_stop = threading.Event()  # signals staleness checker to stop

    def on_song_ended(self, song_id):
        """Called when a song ends. Updates state, does NOT rebuild.
        
        Queue was already pre-built by on_song_started(). 
        The main.py handler will pop+play the next song.
        """
        # Skip if user manually skipped (stop was explicit)
        if self._player._explicitly_stopped:
            self._player._explicitly_stopped = False
            self._current_song_id = None
            return
        self._current_song_id = None
        # Record transition
        if self._queue:
            next_song_id = self._queue[0].get("song_id")
            if next_song_id:
                transitions.record_transition(song_id, next_song_id, "completed")

        # Tick vibe lock
        self._mood.tick_vibe_lock()

        # Check for auto mood shift
        self._check_mood_shift()

    def on_song_started(self, song_id, session_id=None):
        """Called when a song starts playing."""
        self._current_song_id = song_id
        if session_id:
            self._current_session_id = session_id

        # Stop any running TTS staleness checker (will restart after new rebuild)
        self._tts_checker_stop.set()

        # Clear stale TTS cache entries (will be regenerated for new queue)
        with self._pregen_lock:
            self._pregen_cache.clear()

        # Start queue rebuild in background
        with self._condition:
            self._rebuilding = True
            self._rebuild_gen += 1
        threading.Thread(target=self._rebuild_queue, daemon=True).start()

    def pop_next(self):
        """Pop the next item to play.

        Priority: talk segment queue first, then song queue.
        Talk segments are checked first because they're pre-generated
        and should play immediately when their turn comes, regardless
        of whether a song queue rebuild is in progress.
        """
        pick = None

        # 1. Talk segments always have priority — they're pre-generated
        # Both queues checked under the same lock for atomicity
        with self._condition:
            if self._pending_talk_segments:
                pick = self._pending_talk_segments.pop(0)
                logger.info(f"pop_next: returning pending talk segment ({pick.get('segment_type')})")

            # 2. Song queue (only if no talk segment pending)
            if pick is None:
                if not self._queue and not self._rebuilding:
                    self._rebuilding = True
                    threading.Thread(target=self._rebuild_queue, daemon=True).start()

                # Wait for rebuild
                if not self._queue:
                    self._condition.wait(timeout=5.0)

                if self._queue:
                    pick = self._queue.pop(0)
                    # Track recently recommended to prevent cycling
                    self._recently_recommended_ids.add(pick["song_id"])
                    trim_count = len(self._recently_recommended_ids) - self._max_recent_history
                    if trim_count > 0:
                        trimmed = set(list(self._recently_recommended_ids)[trim_count:])
                        self._recently_recommended_ids = trimmed

        # DB write (queue_history INSERT) happens outside the condition
        # to avoid blocking other threads that need _condition.
        if pick:
            self._record_queue_history(pick)
            return pick

        # Fallback: single recommendation (outside condition to avoid blocking
        # all other threads that need _condition — like /api/play/status)
        return self._get_single_recommendation()

    def _get_display_queue(self):
        """Return merged view of pending talk segments + song queue for display.

        Talk segments appear first so the frontend can show "Up Next: Caller on the line"
        without actually mixing them into the song queue that pop_next() consumes.
        """
        with self._condition:
            return list(self._pending_talk_segments) + list(self._queue)

    def _record_queue_history(self, pick):
        """Record a popped queue item to the queue_history table."""
        try:
            conn = self._db.get_connection()
            try:
                conn.execute(
                    """INSERT INTO queue_history (session_id, song_id, position, slot, reason)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        self._current_session_id,
                        pick.get("song_id"),
                        0,
                        pick.get("slot", "context"),
                        pick.get("reason", ""),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Failed to record queue history: %s", e)

    def _rebuild_queue(self):
        """Build the full recommendation queue (called in background thread)."""
        my_gen = self._rebuild_gen
        _log_step(f"=== REBUILD START (gen={my_gen}) ===")
        # Notify frontend that rebuilding has started
        self._broadcast("queue_rebuilding", {"rebuilding": True})
        _log_step("Broadcast queue_rebuilding=True")

        try:
            current_song = self._db.get_song(self._current_song_id) if self._current_song_id else None
            _log_step(f"Current song: {self._current_song_id}")

            # Get energy trajectory (use singleton)
            traj = energy.get_trajectory()
            energy_dir = traj.get_direction()
            _log_step(f"Energy dir: {energy_dir}")

            if current_song:
                target_energy = traj.get_target_energy(
                    self._mood.get_state(),
                    datetime.now().hour,
                )
            else:
                target_energy = None

            # Build taste vector (now returns tuple: vector, drift_score)
            taste_vector, drift_score = self._memory.get_blended_taste_vector(
                session_id=self._current_session_id,
                skip_streak=self._skip_streak,
            )
            _log_step(f"Taste vector built, drift={drift_score:.2f}")

            # Emit taste drift event if significant divergence detected
            if drift_score > 0.6:
                self._broadcast("taste_drift", {"drift": drift_score, "state": self._mood.get_state()})
                _log_step("Broadcast taste_drift")

            # Get vibe lock info
            vibe_lock = None
            if self._mood.is_vibe_lock_active():
                vibe_info = self._mood.get_vibe_lock_info()
                vibe_lock = {
                    "active": True,
                    "embedding": vibe_info.get("embedding"),
                    "songs_remaining": vibe_info.get("songs_remaining", 0),
                }
                _log_step(f"Vibe lock active, {vibe_info.get('songs_remaining')} songs remaining")

            # Get repulsion vector (negative feedback embedding)
            repulsion_vector = self._memory.get_repulsion_vector()
            _log_step("Repulsion vector built")

            # Build candidate list - exclude queued + recently recommended to prevent cycling
            exclude_ids = [s.get("song_id") for s in self._queue] + list(self._recently_recommended_ids)
            mood_energy = self._mood.get_energy_range()
            mood_valence = self._mood.get_valence_range()

            _log_step(f"Building candidate list (exclude={len(exclude_ids)})")
            candidates = retriever.build_candidate_list(
                taste_vector=taste_vector,
                current_song_id=self._current_song_id,
                exclude_ids=exclude_ids,
                mood_energy_range=mood_energy,
                mood_valence_range=mood_valence,
                repulsion_vector=repulsion_vector,
            )
            _log_step(f"Candidates: {len(candidates)}")

            # Merge transition candidates into the pool
            if self._current_song_id:
                from engine import transitions
                trans_candidates = transitions.get_transition_candidates(self._current_song_id, n=15)
                seen_ids = {c["song_id"] for c in candidates}
                for tc in trans_candidates:
                    if tc["song_id"] not in seen_ids and tc["song_id"] not in exclude_ids:
                        song = self._db.get_song(tc["song_id"])
                        if song:
                            candidates.append({
                                "song_id": tc["song_id"],
                                "score": tc["score"] * 0.8,
                                "energy": song.get("energy", 0.5),
                                "valence": song.get("valence", 0.5),
                                "cluster_id": song.get("cluster_id", -1),
                                "artist": song.get("artist", ""),
                                "title": song.get("title", ""),
                                "source": "transition",
                            })
                            seen_ids.add(tc["song_id"])
                _log_step(f"Transition candidates merged: {len(candidates)} total")

            # Apply transition boost to candidates
            if self._current_song_id and candidates:
                from engine import transitions
                for c in candidates:
                    boost = transitions.get_transition_boost(self._current_song_id, c["song_id"])
                    if boost > 0:
                        c["score"] = min(1.0, c.get("score", 0.5) + boost)

            if not candidates:
                # Fallback: neutral centroid
                _log_step("No candidates, using neutral centroid fallback")
                candidates = retriever.search_by_vector(
                    taste_vector=[0.0] * 1024,
                    n=40,
                    exclude_ids=exclude_ids,
                )

            # Apply genre filter if set
            genre_filter = self._db.get_setting("genre_filter")
            if genre_filter:
                _log_step(f"Applying genre filter: {genre_filter}")
                filtered = []
                for c in candidates:
                    song = self._db.get_song(c["song_id"])
                    if song:
                        genres = json.loads(song.get("genre_tags") or "[]")
                        if genre_filter.lower() in [g.lower() for g in genres]:
                            filtered.append(c)
                if filtered:
                    candidates = filtered
                    _log_step(f"Genre filter: {len(candidates)} remaining")

            # Apply artist filter if set
            artist_filter = self._db.get_setting("artist_filter")
            if artist_filter:
                _log_step(f"Applying artist filter: {artist_filter}")
                filtered = []
                for c in candidates:
                    artist = c.get("artist", "").lower()
                    if artist_filter.lower() in artist:
                        filtered.append(c)
                if filtered:
                    candidates = filtered
                    _log_step(f"Artist filter: {len(candidates)} remaining")

            # Allocate slots (with optional guest persona slot bias)
            exploration_level = float(self._db.get_setting("exploration_level") or "0.3")
            from engine import archetype as archetype_engine
            archetype_name, _ = archetype_engine.detect_archetype()
            guest_bias = talk_segments.get_active_guest_slot_bias()
            _log_step(f"Allocating slots (archetype={archetype_name}, exploration={exploration_level})")
            slots_result = slots.allocate_slots(
                candidates, current_song, vibe_lock=vibe_lock,
                exploration_level=exploration_level, archetype=archetype_name,
                slot_bias=guest_bias,
            )
            _log_step(f"Slots allocated: {slots_result}")

            # Enrich candidates with lyrics for reranker
            _log_step("Enriching candidates with lyrics")
            for c in candidates:
                if not c.get("lyrics"):
                    cs = self._db.get_song(c["song_id"])
                    if cs and cs.get("lyrics"):
                        c["lyrics"] = cs["lyrics"]

            # Check if in normal mode (skip LLM)
            mode = self._db.get_setting("current_mode") or "radio"
            _log_step(f"Mode: {mode}")
            if mode == "normal":
                _log_step("Normal mode - deterministic assignment")
                picks = reranker.deterministic_assignment(candidates, slots_result)
            else:
                # Rerank with LLM
                _log_step("Calling reranker.rerank()...")
                picks = reranker.rerank(
                    candidates=candidates,
                    current_song=current_song,
                    mood_state=self._mood.get_state(),
                    energy_trajectory=energy_dir,
                    hour=datetime.now().hour,
                    skip_streak=self._skip_streak,
                    vibe_lock=vibe_lock,
                )
                _log_step(f"Reranker returned: {len(picks) if picks else None} picks")
                if picks is None:
                    _log_step("Reranker returned None, using fallback")
                    picks = reranker.fallback_assignment(candidates, slots_result)

            if picks is None:
                # Fallback: deterministic assignment
                _log_step("Picks still None, using fallback_assignment")
                picks = reranker.fallback_assignment(candidates, slots_result)

            # Build queue from picks
            _log_step(f"Building queue from {len(picks)} picks")
            vibe_active = vibe_lock and vibe_lock.get("active")
            new_queue = []
            for pick in picks:
                song = self._db.get_song(pick["id"])
                if song:
                    is_vibe_affect = bool(vibe_active and pick.get("slot") in ("exploit", "explore"))
                    new_queue.append({
                        "song_id": pick["id"],
                        "slot": pick.get("slot", "context"),
                        "reason": pick.get("reason", ""),
                        "artist": song.get("artist", ""),
                        "title": song.get("title", ""),
                        "vibe_affected": is_vibe_affect,
                    })

            # Ensure we have at least 4 items
            if len(new_queue) < 4:
                _log_step(f"Queue only {len(new_queue)}, adding fillers")
                additional = retriever.build_candidate_list(
                    taste_vector=taste_vector,
                    current_song_id=self._current_song_id,
                    exclude_ids=[p["song_id"] for p in new_queue],
                )
                for c in additional:
                    song = self._db.get_song(c["song_id"])
                    if song and len(new_queue) < 4:
                        new_queue.append({
                            "song_id": c["song_id"],
                            "slot": "context",
                            "reason": "Recommended for you.",
                            "artist": song.get("artist", ""),
                            "title": song.get("title", ""),
                        })

            # Schedule talk segment (stored separately, survives queue rebuilds)
            # Peek at the first song so the talk segment can reference it
            _next_song_for_talk = None
            if new_queue:
                first = new_queue[0]
                if first.get("song_id"):
                    _next_song_for_talk = {"artist": first.get("artist", ""), "title": first.get("title", "")}
            _log_step("Scheduling talk segment")
            self._maybe_schedule_talk_segment(next_song=_next_song_for_talk)

            _log_step("Setting queue and notifying")
            with self._condition:
                # Discard stale rebuilds — if gen changed, a newer rebuild superseded us
                if self._rebuild_gen != my_gen:
                    _log_step(f"Stale rebuild (gen {my_gen} != {self._rebuild_gen}), discarding")
                    self._rebuilding = False
                    self._condition.notify_all()
                    return
                self._queue = new_queue
                self._rebuilding = False
                self._condition.notify_all()

            # Notify frontend that rebuilding has finished
            _log_step("Broadcasting queue_rebuilding=False")
            self._broadcast("queue_rebuilding", {"rebuilding": False})
            _log_step("Broadcasting queue_updated")
            self._broadcast("queue_updated", {"queue": self._get_display_queue()})

            # Pre-generate TTS for the next song (background, non-blocking)
            _log_step("Starting TTS pre-gen thread")
            threading.Thread(target=self._pregen_tts_for_next_song, daemon=True).start()
            _log_step("=== REBUILD DONE ===")

        except Exception as e:
            logger.error(f"Queue rebuild failed: {e}")
            with self._condition:
                self._rebuilding = False
                self._condition.notify_all()
            # Notify frontend that rebuilding has stopped (with error)
            self._broadcast("queue_rebuilding", {"rebuilding": False})
            self._broadcast("queue_updated", {"queue": []})

    def _get_single_recommendation(self):
        """Get a single song recommendation (fallback when queue is empty)."""
        try:
            current_song = self._db.get_song(self._current_song_id) if self._current_song_id else None
            tv, _ = self._memory.get_blended_taste_vector(
                session_id=self._current_session_id,
                skip_streak=self._skip_streak,
            )
            taste_vector = tv

            candidates = retriever.build_candidate_list(
                taste_vector=taste_vector or [0.0] * 1024,
                current_song_id=self._current_song_id or 0,
                exclude_ids=[],
            )

            if candidates:
                song = self._db.get_song(candidates[0]["song_id"])
                return {
                    "song_id": candidates[0]["song_id"],
                    "slot": "fallback",
                    "reason": "Recommended for you.",
                    "artist": song.get("artist", ""),
                    "title": song.get("title", ""),
                }

            # Ultimate fallback: neutral song
            neutral = self._db.get_neutral_centroid_songs(1)
            if neutral:
                s = neutral[0]
                return {
                    "song_id": s["id"],
                    "slot": "fallback",
                    "reason": "Starting your session.",
                    "artist": s.get("artist", ""),
                    "title": s.get("title", ""),
                }

            return None
        except Exception as e:
            logger.error(f"Single recommendation failed: {e}")
            return None

    def _check_mood_shift(self):
        """Check if mood should auto-shift based on recent behavior."""
        recent = self._db.get_recent_signals(
            signal_types=["hearted", "replayed", "skip_early", "skip_late"],
            limit=10,
        )
        new_state = self._mood.check_auto_mood_shift(recent, self._skip_streak)
        if new_state:
            self._mood.set_state(new_state)

    def reset_skip_streak(self):
        """Reset skip streak (called on positive signals)."""
        if self._skip_streak > 0:
            self._skip_streak = 0

    def record_heart(self):
        """Record a heart signal."""
        self.reset_skip_streak()

    def clear_queue(self):
        """Clear the recommendation queue."""
        with self._condition:
            self._queue = []
            self._condition.notify_all()

    def record_replay(self):
        """Record a replay signal - reset skip streak, boost taste."""
        self.reset_skip_streak()
        self._memory.record_signal(
            song_id=self._current_song_id,
            signal="replayed",
            session_id=self._current_session_id,
        )

    def record_completion(self):
        """Record a positive completion signal.
        
        Note: Player._fire_song_ended already records the DB "completed" event.
        This method only updates in-memory state and derived scores.
        """
        self.reset_skip_streak()
        if self._current_song_id:
            database.update_avg_completion(self._current_song_id, 100.0)
            database.calculate_recovery_score(self._current_song_id)

    def _maybe_schedule_talk_segment(self, next_song: dict | None = None):
        """Schedule a talk segment if the counter triggers and none is pending.

        Talk segments are stored in _pending_talk_segments (separate from the
        song queue) so they survive song queue rebuilds.  Only generates a new
        segment when the pending list is empty AND the counter has reached the
        configured frequency.

        Args:
            next_song: Dict with 'artist' and 'title' of the first song in the
                       new queue, so the talk segment can reference it.
        """
        # Already have one queued — don't duplicate
        if self._pending_talk_segments:
            logger.debug("_maybe_schedule_talk_segment: segment already pending, skipping")
            return

        try:
            mode = self._db.get_setting("current_mode") or "radio"
            if mode != "radio":
                return
            tts_enabled = self._db.get_setting("tts_enabled") or "true"
            talk_enabled = self._db.get_setting("talk_segments_enabled") or "false"
            if not (tts_enabled.lower() == "true" and config.TTS_ENABLED
                    and talk_enabled.lower() == "true"):
                logger.debug("_maybe_schedule_talk_segment: talk disabled")
                return

            caller_mode = self._db.get_setting("talk_caller_mode") or "influences_queue"
            segment = talk_segments.get_next_talk_segment(
                mood_state=self._mood.get_state(),
                caller_mode=caller_mode,
                next_song=next_song,
            )
            if segment:
                with self._condition:
                    self._pending_talk_segments.append(segment.to_queue_item())
                logger.info(f"Talk segment scheduled: {segment.segment_type} (pending={len(self._pending_talk_segments)})")
            else:
                logger.debug("_maybe_schedule_talk_segment: counter not reached, no segment")
        except Exception as e:
            logger.warning(f"Talk segment scheduling failed: {e}")

    def _pregen_tts_for_next_song(self):
        """Pre-generate TTS announcement for the next song in the queue.

        Called from _rebuild_queue() after the queue is populated.
        Runs in a background thread — the result is cached so play_next()
        can skip both the LLM call and TTS synthesis.

        The cache is keyed by song_id and the WAV filename includes the
        song_id, so a staleness checker can detect queue changes and
        regenerate for the correct song.
        """
        logger.info("_pregen_tts_for_next_song START")
        try:
            tts_enabled = self._db.get_setting("tts_enabled") or "true"
            if not (tts_enabled.lower() == "true" and config.TTS_ENABLED):
                logger.info("_pregen_tts: TTS disabled")
                return

            with self._condition:
                if not self._queue:
                    return
                # Talk segments are now in a separate queue, so first item is always a song
                next_song_id = self._queue[0].get("song_id")

            if not next_song_id:
                return

            next_song = self._db.get_song(next_song_id)
            if not next_song:
                return

            filepath = next_song.get("filepath")
            if not filepath or not os.path.exists(filepath):
                return

            from announcer import get_announcement, play_announcement, AnnounceTrigger
            from datetime import datetime

            current_song_id = self._current_song_id
            current_song = self._db.get_song(current_song_id) if current_song_id else None

            context = {
                "current_song": dict(current_song) if current_song else {},
                "next_song": dict(next_song) if next_song else {},
                "mood_state": self._mood.get_state(),
                "skip_streak": 0,
                "hour": datetime.now().hour,
            }

            text = get_announcement(context, AnnounceTrigger.RADIO_INTRO)
            if not text:
                return

            wav_path = play_announcement(text, filepath, trigger_type="radio_intro")
            if not wav_path:
                return

            with self._pregen_lock:
                self._pregen_cache[next_song_id] = {
                    "wav_path": wav_path,
                    "text": text,
                    "song_id": next_song_id,
                }
            logger.info(f"_pregen_tts: cached TTS for song {next_song_id}")
            # Start staleness checker to keep TTS in sync with queue changes
            self._start_tts_staleness_checker()
        except Exception as e:
            logger.warning(f"TTS pre-generation failed: {e}")
        logger.info("_pregen_tts_for_next_song DONE")

    def _start_tts_staleness_checker(self):
        """Start a background thread that checks if TTS cache is still valid.

        Runs every 3 seconds. If the next song in the queue changed,
        regenerates TTS for the new song. Stops when a new rebuild starts
        (via _tts_checker_stop event).
        """
        self._tts_checker_stop.set()  # stop any existing checker
        self._tts_checker_stop.clear()

        def _checker():
            logger.info("TTS staleness checker started")
            while not self._tts_checker_stop.is_set():
                try:
                    self._tts_checker_stop.wait(timeout=3.0)
                    if self._tts_checker_stop.is_set():
                        break

                    # Get the current next song from queue
                    with self._condition:
                        if not self._queue:
                            continue
                        next_song_id = self._queue[0].get("song_id")

                    if not next_song_id:
                        continue

                    # Check if we have cached TTS for this song
                    with self._pregen_lock:
                        cached = self._pregen_cache.get(next_song_id)
                        if cached and cached.get("wav_path") and os.path.exists(cached["wav_path"]):
                            continue  # TTS is up to date

                    # Cache miss or stale — regenerate
                    logger.info(f"TTS staleness checker: song {next_song_id} not cached, regenerating")
                    self._pregen_tts_for_next_song()
                except Exception as e:
                    logger.debug(f"TTS staleness checker error: {e}")

        threading.Thread(target=_checker, daemon=True, name="tts-staleness-checker").start()

    def play_next(self, pick):
        """Play the next item (song or talk segment) with optional TTS.

        Args:
            pick: Dict from pop_next() with keys:
                  For songs: song_id, slot, reason, artist, title
                  For talk segments: type="talk_segment", segment_type, wav_paths, ...
        """
        try:
            logger.info(f"play_next START: pick_type={pick.get('type', 'song')}, song_id={pick.get('song_id')}")
            # Handle talk segment items
            if pick.get("type") == "talk_segment":
                logger.info("play_next: dispatching to _play_talk_segment")
                self._play_talk_segment(pick)
                return

            song_id = pick.get("song_id")
            song = self._db.get_song(song_id)
            if not song:
                logger.warning(f"play_next: song {song_id} not found in DB")
                return

            filepath = song.get("filepath")
            if not filepath or not os.path.exists(filepath):
                logger.warning(f"play_next: file not found: {filepath}")
                return

            session_id = self._current_session_id
            p = player_engine.get_player()

            # Try TTS announcement
            tts_enabled = self._db.get_setting("tts_enabled") or "true"
            should_play_tts = tts_enabled.lower() == "true" and config.TTS_ENABLED

            if should_play_tts:
                try:
                    from announcer import should_announce, AnnounceTrigger
                    from datetime import datetime
                    current_song_id = p.get_current_song_id()
                    current_song = self._db.get_song(current_song_id) if current_song_id else None

                    context = {
                        "current_song": dict(current_song) if current_song else {},
                        "next_song": dict(song) if song else {},
                        "mood_state": self._mood.get_state(),
                        "skip_streak": self._skip_streak,
                        "hour": datetime.now().hour,
                    }

                    trigger = AnnounceTrigger.RADIO_INTRO
                    if self._skip_streak >= 1:
                        trigger = AnnounceTrigger.SKIP_STREAK
                    elif context.get("mood_state") == "chill":
                        trigger = AnnounceTrigger.RADIO_INTRO

                    if not should_announce({"trigger": trigger, **context}):
                        # No announcement needed, play song directly
                        logger.info(f"play_next: playing song directly (no TTS needed)")
                        p.play_song(song_id, filepath, session_id)
                        self.on_song_started(song_id, session_id)
                        return

                    # Check pre-generation cache (works when predictions match)
                    use_cache = (self._skip_streak == 0)
                    cached = None
                    if use_cache:
                        with self._pregen_lock:
                            cached = self._pregen_cache.pop(song_id, None)

                    if cached and cached.get("wav_path") and os.path.exists(cached["wav_path"]):
                        logger.info(f"play_next: TTS cache hit, playing chain (TTS + song)")
                        items = [
                            {"filepath": cached["wav_path"], "is_tts": True},
                            {"filepath": filepath, "song_id": song_id, "is_tts": False},
                        ]
                        p.play_chain(items)
                        self.on_song_started(song_id, session_id)
                        return

                    # Cache miss — play song directly. TTS pre-generation in the background
                    # thread handles async generation so the next song will have TTS.
                    # NEVER generate TTS synchronously here — it blocks the request thread
                    # for 5-15 seconds (LLM call + Piper synthesis), freezing the API.
                    logger.info(f"play_next: TTS cache miss, playing song directly")
                except Exception as e:
                    logger.warning(f"TTS announcement failed, playing song directly: {e}")

            # Play song directly (no TTS or TTS failed)
            logger.info(f"play_next: playing song directly (no TTS)")
            p.play_song(song_id, filepath, session_id)
            self.on_song_started(song_id, session_id)

        except Exception as e:
            logger.error(f"play_next failed: {e}")

    def _play_talk_segment(self, pick: dict):
        """Play a talk segment (caller, monologue, guest takeover).

        Chains talk WAV(s) with the following song in the queue.
        Applies mood_shift / energy_direction if present.

        On any failure, falls back to playing the next available song
        so playback never stalls.
        """
        try:
            logger.info(f"_play_talk_segment START: segment_type={pick.get('segment_type')}, wav_paths={pick.get('wav_paths')}")
            wav_paths = pick.get("wav_paths", [])
            if not wav_paths:
                logger.warning("_play_talk_segment: no WAV paths — falling back to next song")
                self._fallback_play_next_song()
                return

            # Verify WAV files exist
            valid_wavs = [w for w in wav_paths if os.path.exists(w)]
            if not valid_wavs:
                logger.warning(f"_play_talk_segment: no valid WAV files (checked {len(wav_paths)} paths) — falling back to next song")
                for w in wav_paths:
                    logger.warning(f"  missing: {w}")
                self._fallback_play_next_song()
                return

            logger.info(f"_play_talk_segment: {len(valid_wavs)}/{len(wav_paths)} valid WAVs, getting player...")
            p = player_engine.get_player()
            session_id = self._current_session_id

            # Look ahead in queue for the next song
            next_song_item = None
            with self._condition:
                # Find first song in queue (talk segments may have been inserted)
                song_idx = None
                for i, item in enumerate(self._queue):
                    if item.get("type") != "talk_segment":
                        song_idx = i
                        break
                if song_idx is not None:
                    next_song_item = self._queue.pop(song_idx)

            if next_song_item:
                song_id = next_song_item.get("song_id")
                song = self._db.get_song(song_id)
                if song and song.get("filepath") and os.path.exists(song["filepath"]):
                    # Chain: talk WAVs + song back-to-back
                    logger.info(f"_play_talk_segment: playing {len(valid_wavs)} WAVs + song chain ({len(valid_wavs) + 1} items)")
                    items = []
                    for w in valid_wavs:
                        items.append({"filepath": w, "is_tts": True})
                    items.append({
                        "filepath": song["filepath"],
                        "song_id": song_id,
                        "is_tts": False,
                    })

                    # Apply mood_shift BEFORE play_chain/on_song_started so the
                    # background rebuild uses the new mood when picking next songs.
                    mood_shift = pick.get("mood_shift")
                    energy_dir = pick.get("energy_direction")
                    if mood_shift:
                        try:
                            logger.info(f"Applying mood shift from talk segment: {mood_shift}")
                            self._mood.set_state(mood_shift)
                            self._broadcast("mood_changed", {"mood": mood_shift, "source": "talk_segment"})
                        except Exception as e:
                            logger.warning(f"Mood shift from talk segment failed: {e}")

                    logger.info("_play_talk_segment: calling p.play_chain()...")
                    p.play_chain(items)
                    logger.info("_play_talk_segment: play_chain returned, calling on_song_started")
                    self.on_song_started(song_id, session_id)
                    logger.info("_play_talk_segment: on_song_started done")
                else:
                    # Song not available, try to play talk alone, then fallback
                    logger.info(f"_play_talk_segment: no valid next song, playing {len(valid_wavs)} WAVs only")
                    p.play_chain([{"filepath": w, "is_tts": True} for w in valid_wavs])
            else:
                # No song in queue at all — play talk alone, then fallback will trigger on end
                logger.info(f"_play_talk_segment: no next song item in queue, playing {len(valid_wavs)} WAVs only")
                p.play_chain([{"filepath": w, "is_tts": True} for w in valid_wavs])

            # Broadcast talk segment start to frontend
            self._broadcast("talk_segment_started", {
                "segment_type": pick.get("segment_type"),
                "reason": pick.get("reason", ""),
                "persona_id": pick.get("secondary_persona_id") or pick.get("persona_id"),
                "duration_secs": pick.get("duration_secs", 30),
            })

            logger.info(f"Talk segment played: {pick.get('segment_type')} ({pick.get('reason', '')})")

        except Exception as e:
            logger.error(f"_play_talk_segment failed: {e}", exc_info=True)
            self._fallback_play_next_song()

    def _fallback_play_next_song(self):
        """Fallback: pop the next song from the queue and play it directly.

        Called when a talk segment fails to play so playback never stalls.
        """
        try:
            logger.info("_fallback_play_next_song: looking for next song in queue")
            p = player_engine.get_player()
            session_id = self._current_session_id

            next_song_item = None
            with self._condition:
                for i, item in enumerate(self._queue):
                    if item.get("type") != "talk_segment":
                        next_song_item = self._queue.pop(i)
                        break

            if not next_song_item:
                logger.warning("_fallback_play_next_song: no song available in queue")
                return

            song_id = next_song_item.get("song_id")
            song = self._db.get_song(song_id)
            if not song or not song.get("filepath") or not os.path.exists(song.get("filepath", "")):
                logger.warning(f"_fallback_play_next_song: song {song_id} not found or file missing")
                return

            filepath = song["filepath"]
            logger.info(f"_fallback_play_next_song: playing {song.get('artist')} - {song.get('title')}")
            p.play_song(song_id, filepath, session_id)
            self.on_song_started(song_id, session_id)

        except Exception as e:
            logger.error(f"_fallback_play_next_song failed: {e}", exc_info=True)
