"""QueueManager - thread-safe queue with rebuild support.

Uses threading.Condition for efficient waiting on queue rebuild.
"""

import json
import threading
import time
import traceback
import logging
import os
import config
from dataclasses import dataclass, field
from datetime import datetime
from engine import taste, retriever, slots, reranker, transitions, mood, energy, recovery


def _song_effective_energy(song):
    """Compute effective energy from a song dict (avoids extra DB query)."""
    base = song.get("energy", 0.5)
    user = song.get("user_energy")
    n = song.get("user_energy_count", 0) or 0
    return round(0.7 * base + 0.3 * user, 4) if n > 0 and user is not None else base
from engine.constellations import TasteConstellations
from engine import player as player_engine
from db import database
from announcer import talk_segments
from announcer.voice_manager import _fade_in_first_second, _concatenate_wavs

logger = logging.getLogger(__name__)


@dataclass
class PlaybackContext:
    """Immutable snapshot of playback state at rebuild start.

    All async TTS/talk generation reads from this snapshot, never from
    live state like self._current_song_id.  This ensures that a stale
    rebuild cannot generate contextually-incoherent announcements.
    """
    generation: int
    current_song_id: int | None
    next_song_id: int | None = None  # filled after queue is built
    mood_state: str = "neutral"
    session_id: int | None = None
    recent_context: list[dict] = field(default_factory=list)
    talk_counter_snapshot: int = 0

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
        self._max_recent_history = 50
        # Seed from DB so the buffer survives restarts
        try:
            recent_ids = database.get_recent_queue_song_ids(self._max_recent_history)
            self._recently_recommended_ids = set(recent_ids)
        except Exception:
            pass
        self._pregen_cache = {}
        self._pregen_lock = threading.Lock()
        self._dj_effect_cache = {}
        self._dj_effect_cache_lock = threading.Lock()
        self._pending_talk_segments = []  # separate queue — survives song queue rebuilds
        self._talk_segment_playing = False  # True while a talk segment TTS is playing
        self._manual_queue = []  # user/LLM additions — survives song queue rebuilds
        self._rebuild_gen = 0  # incremented each rebuild; used to discard stale results
        self._broadcast_gen = 0  # monotonically increasing counter for broadcast events
        # Queue preview cache — pre-built queue from the last rebuild, tagged
        # with the song that was playing when it was generated.  When a song
        # ends, pop_next() checks if the preview is still valid (same song)
        # and uses it instantly instead of triggering a fresh rebuild.
        self._preview_queue = []
        self._preview_song_id = None
        self._preview_lock = threading.Lock()
        self._last_ended_song_id = None  # set by on_song_ended for preview validation
        self._tts_checker_stop = threading.Event()  # signals staleness checker to stop
        self._preview_stop = threading.Event()  # signals preview watchdog to stop
        # Undo tracking (single-deep)
        self._last_action = None  # {"type": "skip"|"thumbs_down", "song_id": int, "timestamp": float}
        self._last_skipped_song = None  # song dict of most recently skipped song
        self._prev_mood_state = None  # previous mood state for MOOD_CHANGE trigger detection
        self._constellations = TasteConstellations()  # multi-centroid taste model
        # Persistent preview watchdog — regenerates the preview batch
        # whenever it diverges from the main queue.
        self._start_preview_watchdog()

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
        # Keep _current_song_id set so the next on_song_started can
        # detect that this is NOT the first play in session, enabling
        # the preview consumption path.  pop_next() validates the preview
        # against the just-ended song, not the next one.
        self._last_ended_song_id = song_id
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
        # Clear talk segment flag — if this on_song_started was triggered by
        # pop_next() after a talk-only chain ended, we're now on a real song.
        self._talk_segment_playing = False
        prev_song_id = self._current_song_id  # save for first-play detection
        self._current_song_id = song_id
        if session_id:
            self._current_session_id = session_id

        # Stop any running TTS staleness checker (will restart after new rebuild)
        self._tts_checker_stop.set()

        # Only clear this song's TTS cache entry — preserve pre-generated
        # TTS for upcoming songs so transitions work when the queue advances.
        with self._pregen_lock:
            self._pregen_cache.pop(song_id, None)

        # Check if preview cache is valid and seeded for THIS song.
        # If valid: swap preview into main queue instantly (no rebuild wait).
        # If stale/missing: clear main queue, fall back to normal rebuild.
        preview_valid = False
        with self._preview_lock:
            logger.info("on_song_started: preview check prev_song_id=%s song_id=%s preview_song_id=%s preview_queue_len=%d",
                        prev_song_id, song_id, self._preview_song_id, len(self._preview_queue))
            if (prev_song_id is not None  # skip on first play in session
                    and self._preview_queue
                    and self._preview_song_id is not None
                    and self._preview_song_id == song_id):
                logger.info(f"on_song_started: using preview queue (seeded for song {song_id}, "
                            f"{len(self._preview_queue)} items) queue_size_before=%d", len(self._queue))
                self._queue = list(self._preview_queue)
                self._preview_queue = []
                self._preview_song_id = None
                preview_valid = True
                # If current song is at the front of the queue (manual play via
                # play_song endpoint bypasses pop_next), remove it so it doesn't
                # replay after the current one ends.
                if self._queue and self._queue[0].get("song_id") == song_id:
                    self._queue.pop(0)
                    logger.info("on_song_started: popped front song %s from preview, queue now %d items",
                                song_id, len(self._queue))

        if not preview_valid:
            logger.info(f"on_song_started: preview missing/stale for song {song_id}, "
                        f"clearing queue (was %d items)", len(self._queue))
            with self._condition:
                self._queue.clear()

        # Broadcast queue state so frontend shows it immediately
        self._broadcast("queue_updated", {"queue": self._get_display_queue()})

        # Start queue rebuild in background
        with self._condition:
            self._rebuilding = True
            self._rebuild_gen += 1
        threading.Thread(target=self._rebuild_queue, kwargs={"advance_talk_counter": True}, daemon=True).start()

        # Save current mood state for MOOD_CHANGE detection on next song.
        # Placed at the end so it captures the mood after any shifts from
        # on_song_ended() / _check_mood_shift(), ensuring the next rebuild
        # compares against the correct previous state.
        self._prev_mood_state = self._mood.get_state()

    def pop_next(self):
        """Pop the next item to play.

        Priority:
          1. Talk segment queue (pre-generated talk show audio)
          2. Manual queue (user/LLM additions — survives AI rebuilds)
          3. AI song queue (recommendation engine picks)
        """
        pick = None

        # 1. Talk segments always have priority — they're pre-generated
        # All queues checked under the same lock for atomicity
        # NOTE: Generation check removed — talk segments are pre-rendered
        # audio that doesn't depend on queue state.  The old check caused
        # valid segments to be silently discarded when the preview watchdog
        # incremented _rebuild_gen between segment generation and pop_next.
        with self._condition:
            if self._pending_talk_segments:
                pick = self._pending_talk_segments.pop(0)
                logger.info(f"pop_next: returning pending talk segment ({pick.get('segment_type')})")

            # 2. Manual queue (user/LLM additions — survives rebuilds)
            if pick is None and self._manual_queue:
                pick = self._manual_queue.pop(0)
                logger.info(f"pop_next: returning manual queue item (song_id={pick.get('song_id')})")

            # 3. AI song queue
            if pick is None:
                # Preview cache is handled by on_song_started() — it swaps
                # the preview into _queue before pop_next ever runs.
                # Just pop directly from _queue.
                if not self._queue and not self._rebuilding:
                    self._rebuilding = True
                    self._rebuild_gen += 1
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

    def _get_display_queue(self, _locked=False):
        """Return merged view of all queue tiers for display.

        Order: talk segments → manual queue → AI song queue.
        If _locked is True, caller already holds self._condition.
        """
        if _locked:
            return list(self._pending_talk_segments) + list(self._manual_queue) + list(self._queue)
        with self._condition:
            return list(self._pending_talk_segments) + list(self._manual_queue) + list(self._queue)

    def reorder_display_queue(self, order: list[int]) -> list[dict]:
        """Reorder the AI queue based on user drag-and-drop.

        Only reorders items within _queue (the AI recommendation queue).
        _manual_queue (explicitly added songs) is left untouched so it
        does not accumulate items from reorder operations.
        On reorder, the stale preview is cleared and regenerated with the
        new first item as the recommendation seed (full pipeline).
        """
        clean_order = [sid for sid in order if sid is not None]
        if not clean_order:
            return self._get_display_queue()

        with self._condition:
            ai_items = list(self._queue)
            ai_map = {}
            for item in ai_items:
                song_id = item.get("song_id")
                if song_id is not None and song_id not in ai_map:
                    ai_map[song_id] = item

            seen = set()
            ordered = []
            for song_id in clean_order:
                item = ai_map.get(song_id)
                if item and song_id not in seen:
                    ordered.append(item)
                    seen.add(song_id)

            remaining = [item for item in ai_items if item.get("song_id") not in seen]
            self._queue = ordered + remaining

            # Clear stale preview — it was seeded for the old first item
            with self._preview_lock:
                self._preview_queue = []
                self._preview_song_id = None

            # Regenerate preview with new first item as seed (full pipeline)
            if self._queue:
                first_id = self._queue[0].get("song_id")
                if first_id:
                    threading.Thread(
                        target=self._rebuild_queue,
                        args=(first_id,),
                        daemon=True,
                    ).start()

            self._condition.notify_all()

            return list(self._pending_talk_segments) + list(self._manual_queue) + list(self._queue)

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

    def _rebuild_queue(self, seed_id=None, advance_talk_counter=False):
        """Build the full recommendation queue (called in background thread).

        Generates the PREVIEW queue — the next batch of 5 options that will
        be shown after the current song's transition.

        If seed_id is provided, uses that song as the "current song" context
        for the recommendation pipeline (retriever → slots → reranker).
        Otherwise, uses _queue[0] (the first queued option) as seed, falling
        back to self._current_song_id if the queue is empty.

        Stores the result in _preview_queue, tagged with the seed ID for
        staleness validation in on_song_started(). If _queue is empty
        (startup/fallback), also populates _queue directly.
        """
        my_gen = self._rebuild_gen
        # Determine the context seed: explicit seed → first queue item → current song
        if seed_id is None:
            with self._condition:
                if self._queue:
                    seed_id = self._queue[0].get("song_id")
        if seed_id is None:
            seed_id = self._current_song_id
        context_id = seed_id
        _log_step(f"=== REBUILD START (gen={my_gen}, context_id={context_id}) ===")
        # DEBUG: log queue state at rebuild start
        with self._condition:
            _queue_first = self._queue[0].get("song_id") if self._queue else "empty"
            _log_step(f"Rebuild queue state: _queue={len(self._queue)} items, manual={len(self._manual_queue)}, talk={len(self._pending_talk_segments)}, first_id={_queue_first}")
        # Notify frontend that background preview generation has started
        with self._condition:
            self._broadcast_gen += 1
            broadcast_gen = self._broadcast_gen
        self._broadcast("queue_rebuilding", {"rebuilding": True, "gen": broadcast_gen})
        _log_step("Broadcast queue_rebuilding=True")

        try:
            current_song = self._db.get_song(context_id) if context_id else None
            _log_step(f"Context song: {context_id}")

            # --- Create frozen playback context snapshot ---
            # All async TTS/talk generation reads from this, never from live
            # state.  This prevents a stale rebuild from generating contextually
            # incoherent announcements.
            import announcer.talk_segments as _ts_mod
            snapshot = PlaybackContext(
                generation=my_gen,
                current_song_id=self._current_song_id,
                mood_state=self._mood.get_state(),
                session_id=self._current_session_id,
                recent_context=self._get_recent_played_context(3),
                talk_counter_snapshot=_ts_mod._song_counter,
            )
            _log_step(f"Snapshot: gen={snapshot.generation}, current={snapshot.current_song_id}, "
                      f"mood={snapshot.mood_state}, recent={len(snapshot.recent_context)} songs")

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

            # Build taste vectors (text + audio)
            taste_vectors = self._memory.get_taste_vectors(
                session_id=self._current_session_id,
                skip_streak=self._skip_streak,
            )
            taste_vector = taste_vectors["text"]  # legacy fallback
            drift_score = taste_vectors["drift"]
            _log_step(f"Taste vectors built: text={len(taste_vectors['text']) if taste_vectors['text'] else 'None'}, "
                      f"audio={len(taste_vectors['audio']) if taste_vectors['audio'] else 'None'}, drift={drift_score:.2f}")

            # Emit taste drift event if significant divergence detected
            if drift_score > 0.6:
                self._broadcast("taste_drift", {"drift": drift_score, "state": self._mood.get_state()})
                _log_step("Broadcast taste_drift")

            # Get vibe lock info
            vibe_lock = None
            if self._mood.is_vibe_lock_active():
                vibe_info = self._mood.get_vibe_lock_info()
                if vibe_info.get("embedding") is not None:
                    vibe_lock = {
                        "active": True,
                        "embedding": vibe_info.get("embedding"),
                        "audio_embedding": vibe_info.get("audio_embedding"),
                        "songs_remaining": vibe_info.get("songs_remaining", 0),
                    }
                    _log_step(f"Vibe lock active, {vibe_info.get('songs_remaining')} songs remaining")
                else:
                    _log_step("Vibe lock active but embedding is None — deactivating")
                    self._mood.deactivate_vibe_lock()

            # Get repulsion vectors (text + audio)
            text_repulsion = self._memory.get_repulsion_vector()
            audio_repulsion = self._memory.get_audio_repulsion_vector()
            _log_step(f"Repulsion vectors: text={type(text_repulsion).__name__} len={len(text_repulsion) if text_repulsion else 0}, "
                      f"audio={type(audio_repulsion).__name__} len={len(audio_repulsion) if audio_repulsion else 0}")

            # Build candidate list - exclude queued + recently recommended to prevent cycling
            exclude_ids = ([s.get("song_id") for s in self._queue]
                          + [s.get("song_id") for s in self._manual_queue]
                          + list(self._recently_recommended_ids))
            mood_energy = self._mood.get_energy_range()
            mood_valence = self._mood.get_valence_range()
            logger.debug("Queue rebuild: mood=%r -> energy=%r (type=%s), valence=%r (type=%s)",
                         self._mood.get_state(), mood_energy, type(mood_energy).__name__,
                         mood_valence, type(mood_valence).__name__)

            # Parse recommendation filters (folders, mtime range, artist mode)
            subfolder_filter = None
            mtime_min = None
            mtime_max = None
            artist_mode = None
            artists = None
            filters_raw = self._db.get_setting("recommendation_filters") or self._db.get_setting("folder_filter") or ""
            if filters_raw and filters_raw.strip():
                try:
                    parsed = json.loads(filters_raw)
                    if isinstance(parsed, list):
                        subfolder_filter = parsed
                    elif isinstance(parsed, dict):
                        subfolder_filter = parsed.get("folders") or None
                        raw_min = parsed.get("mtime_min_days")
                        raw_max = parsed.get("mtime_max_days")
                        if raw_min is not None:
                            mtime_min = int(raw_min)
                        if raw_max is not None:
                            mtime_max = int(raw_max)
                        am = parsed.get("artist_mode")
                        if am in ("include", "exclude"):
                            artist_mode = am
                            artists = parsed.get("artists") or None
                except (json.JSONDecodeError, TypeError):
                    pass

            _log_step(f"Building candidate list (exclude={len(exclude_ids)})")
            # Recent skip feedback for energy bias
            recent_feedback = database.get_recent_skip_feedback(self._current_session_id, limit=3)
           # logger.debug("Queue rebuild: taste_vector type=%s len=%s, mood_energy=%r (type=%s), mood_valence=%r (type=%s), recent_feedback=%r (type=%s, len=%s)",
            #              type(taste_vector).__name__, len(taste_vector) if taste_vector else None,
            #              mood_energy, type(mood_energy).__name__,
            #              mood_valence, type(mood_valence).__name__,
            #              recent_feedback, type(recent_feedback).__name__, len(recent_feedback) if recent_feedback else 0)
            # logger.debug("Queue rebuild: repulsion_vector type=%s len=%s, subfolder_filter=%r, exclude_ids type=%s len=%s",
            #              type(repulsion_vector).__name__, len(repulsion_vector) if repulsion_vector else None,
            #              subfolder_filter, type(exclude_ids).__name__, len(exclude_ids))
            candidates = retriever.build_candidate_list(
                text_taste_vector=taste_vector,
                audio_taste_vector=taste_vectors["audio"],
                current_song_id=context_id,
                exclude_ids=exclude_ids,
                mood_energy_range=mood_energy,
                mood_valence_range=mood_valence,
                text_repulsion_vector=text_repulsion,
                audio_repulsion_vector=audio_repulsion,
                skip_feedback=recent_feedback,
                subfolder_filter=subfolder_filter,
                vibe_lock=vibe_lock,
                mood_state=self._mood.get_state(),
                mtime_min=mtime_min, mtime_max=mtime_max,
                artist_mode=artist_mode, artists=artists,
            )
            _log_step(f"Candidates: {len(candidates)}")

            # Merge transition candidates into the pool
            if context_id:
                from engine import transitions
                trans_candidates = transitions.get_transition_candidates(context_id, n=15)
                seen_ids = {c["song_id"] for c in candidates}
                for tc in trans_candidates:
                    if tc["song_id"] not in seen_ids and tc["song_id"] not in exclude_ids:
                        song = self._db.get_song(tc["song_id"])
                        if song:
                            candidates.append({
                                "song_id": tc["song_id"],
                                "score": tc["score"] * 0.8,
                                "energy": _song_effective_energy(song),
                                "valence": song.get("valence", 0.5),
                                "cluster_id": song.get("cluster_id", -1),
                                "artist": song.get("artist", ""),
                                "title": song.get("title", ""),
                                "source": "transition",
                            })
                            seen_ids.add(tc["song_id"])
                _log_step(f"Transition candidates merged: {len(candidates)} total")

            # Apply recommendation filters to transition candidates added post-hoc
            import time as _time
            now_ts = _time.time()
            filtered_candidates = []
            for c in candidates:
                cid = c["song_id"]
                if subfolder_filter:
                    allowed_ids = set(database.get_song_ids_in_subfolders(subfolder_filter))
                    if cid not in allowed_ids:
                        continue
                if mtime_min is not None or mtime_max is not None:
                    song = database.get_song(cid)
                    if song:
                        m = song.get("file_mtime")
                        if m is None:
                            # Fallback: derive from subfolder
                            subf = song.get("subfolder", "")
                            all_subfolders = database.get_subfolders()
                            if all_subfolders and len(all_subfolders) > 1:
                                rank = all_subfolders.index(subf) if subf in all_subfolders else 0
                                known_mtimes = database.get_file_mtime_range()
                                min_known, max_known = known_mtimes if known_mtimes[0] else (0, 1)
                                if max_known > min_known:
                                    frac = rank / (len(all_subfolders) - 1)
                                    m = min_known + frac * (max_known - min_known)
                        if m is not None:
                            age_days = (now_ts - m) / 86400.0
                            if mtime_min is not None and age_days < mtime_min:
                                continue
                            if mtime_max is not None and age_days > mtime_max:
                                continue
                if artist_mode and artists:
                    lower_artists = [a.lower() for a in artists if a.strip()]
                    if lower_artists:
                        c_artist = c.get("artist", "").lower()
                        if artist_mode == "include" and not any(a in c_artist for a in lower_artists):
                            continue
                        if artist_mode == "exclude" and any(a in c_artist for a in lower_artists):
                            continue
                filtered_candidates.append(c)
            candidates = filtered_candidates
            _log_step(f"Recommendation filters applied: {len(candidates)} remaining")

            # Apply transition boost to candidates (batched)
            if context_id and candidates:
                from engine import transitions
                candidate_ids = [c["song_id"] for c in candidates]
                boost_map = transitions.get_transition_boosts(context_id, candidate_ids)
                for c in candidates:
                    boost = boost_map.get(c["song_id"], 0.0)
                    if boost > 0:
                        c["score"] = min(1.0, c.get("score", 0.5) + boost)

            if not candidates:
                # Fallback: neutral centroid
                _log_step("No candidates, using neutral centroid fallback")
                candidates = retriever.search_by_vector(
                    taste_vector=[0.0] * retriever.get_embedding_dimension(),
                    n=40,
                    exclude_ids=exclude_ids,
                    subfolder_filter=subfolder_filter,
                    mtime_min=mtime_min, mtime_max=mtime_max,
                    artist_mode=artist_mode, artists=artists,
                )

            # --- Taste Constellations: reweight candidates + taste hopping ---
            hop_pocket = None
            if (self._db.get_setting("constellations_enabled") or "true") == "true":
                try:
                    self._constellations.build()
                    if self._constellations.is_active:
                        active_pocket = self._constellations.select_active_pocket(
                            current_song=current_song,
                            mood_state=self._mood.get_state(),
                            hour=datetime.now().hour,
                        )
                        if active_pocket:
                            candidates = self._constellations.reweight_candidates(
                                candidates, active_pocket
                            )
                            _log_step(f"Constellation reweighted: active_pocket={active_pocket.constellation_id} "
                                      f"(mood={active_pocket.primary_mood}, weight={active_pocket.weight:.2f})")

                            # Taste hopping: occasionally inject candidates from neighboring pocket
                            exploration_level_val = self._db.get_setting_float("exploration_level", 0.3)
                            hop_pocket = self._constellations.should_hop(
                                active_pocket, exploration_level_val
                            )
                            if hop_pocket:
                                hop_emb = hop_pocket.centroid_text
                                if hop_emb:
                                    hop_candidates = retriever.search_by_vector(
                                        taste_vector=hop_emb,
                                        n=10,
                                        exclude_ids=exclude_ids + [c["song_id"] for c in candidates],
                                        subfolder_filter=subfolder_filter,
                                        mtime_min=mtime_min, mtime_max=mtime_max,
                                        artist_mode=artist_mode, artists=artists,
                                    )
                                    for hc in hop_candidates:
                                        hc["source"] = "constellation_hop"
                                    candidates.extend(hop_candidates)
                                    _log_step(f"Taste hop: {len(hop_candidates)} candidates from pocket {hop_pocket.constellation_id}")
                except Exception as e:
                    logger.debug("Constellation reweighting skipped: %s", e)

            # Defensive: coerce None artist/title to "" — SQLite returns NULL for
            # missing ID3 tags, and dict.get("artist","") returns None (not the
            # default) when the key exists with value None.
            for c in candidates:
                if c.get("artist") is None:
                    c["artist"] = ""
                if c.get("title") is None:
                    c["title"] = ""

            # Apply genre filter if set (batched)
            genre_filter = self._db.get_setting("genre_filter")
            if genre_filter and genre_filter.strip().lower() not in ("none", ""):
                _log_step(f"Applying genre filter: {genre_filter}")
                candidate_ids = [c["song_id"] for c in candidates]
                song_map = database.get_songs_by_ids(candidate_ids)
                filtered = []
                for c in candidates:
                    song = song_map.get(c["song_id"])
                    if song:
                        genres = json.loads(song.get("genre_tags") or "[]")
                        if genre_filter.lower() in [g.lower() for g in genres]:
                            filtered.append(c)
                if filtered:
                    candidates = filtered
                    _log_step(f"Genre filter: {len(candidates)} remaining")

            # Apply artist filter if set
            artist_filter = self._db.get_setting("artist_filter")
            if artist_filter and artist_filter.strip().lower() not in ("none", ""):
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
            exploration_level = self._db.get_setting_float("exploration_level", 0.3)
            from engine import archetype as archetype_engine
            archetype_name, _ = archetype_engine.detect_archetype()
            guest_bias = talk_segments.get_active_guest_slot_bias()
            # Pass constellation pockets if available
            _active_pocket = self._constellations.active_pocket if self._constellations.is_active else None
            _log_step(f"Allocating slots (archetype={archetype_name}, exploration={exploration_level})")
            slots_result = slots.allocate_slots(
                candidates, current_song, vibe_lock=vibe_lock,
                exploration_level=exploration_level, archetype=archetype_name,
                slot_bias=guest_bias,
                active_pocket=_active_pocket,
                hop_pocket=hop_pocket,
            )
            _log_step(f"Slots allocated: {slots_result}")

            # Enrich candidates with lyrics for reranker
            _log_step("Enriching candidates with lyrics")
            for c in candidates:
                if not c.get("lyrics"):
                    cs = self._db.get_song(c["song_id"])
                    if cs and cs.get("lyrics"):
                        c["lyrics"] = cs["lyrics"]

            # Check play_mode (ai/shuffle/sequential) — bypasses full recommendation engine
            mode = self._db.get_setting("current_mode") or "radio"
            play_mode = self._db.get_setting("play_mode") or "ai"
            _log_step(f"Mode: {mode}, Play mode: {play_mode}")
            if play_mode in ("shuffle", "sequential"):
                _log_step(f"Play mode '{play_mode}' — building queue directly from library")
                conn = self._db.get_connection()
                try:
                    exclude_sql = ""
                    if self._current_song_id:
                        exclude_sql = f"AND s.id != {self._current_song_id}"
                    eff_expr = "(0.7 * s.energy + 0.3 * COALESCE(s.user_energy, s.energy))"
                    order_by = "RANDOM()" if play_mode == "shuffle" else f"{eff_expr} DESC"
                    reason = "Random pick" if play_mode == "shuffle" else "Sequential order"
                    rows = conn.execute(f"""
                        SELECT s.id, s.artist, s.title, {eff_expr} as energy, s.valence, s.bpm
                        FROM songs s LEFT JOIN blacklist b ON s.id = b.song_id
                        WHERE s.ingest_phase = 'clustered' AND b.song_id IS NULL {exclude_sql}
                        ORDER BY {order_by} LIMIT 5
                    """).fetchall()
                finally:
                    conn.close()
                picks = [{"id": r["id"], "slot": "manual", "reason": reason} for r in rows]
                _log_step(f"Built {len(picks)} picks from {play_mode} mode")
            elif mode == "normal":
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
                        "energy": _song_effective_energy(song),
                        "valence": song.get("valence", 0.5),
                        "bpm": song.get("bpm", 0),
                        "vibe_affected": is_vibe_affect,
                    })

            # Ensure we have at least 4 items
            if len(new_queue) < 4:
                _log_step(f"Queue only {len(new_queue)}, adding fillers")
                additional = retriever.build_candidate_list(
                    text_taste_vector=taste_vector,
                    audio_taste_vector=taste_vectors["audio"],
                    current_song_id=self._current_song_id,
                    exclude_ids=[p["song_id"] for p in new_queue],
                    subfolder_filter=subfolder_filter,
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
                            "energy": _song_effective_energy(song),
                            "valence": song.get("valence", 0.5),
                            "bpm": song.get("bpm", 0),
                        })

            # --- Phase 1: Validate rebuild is current BEFORE talk scheduling ---
            with self._condition:
                if self._rebuild_gen != my_gen:
                    _log_step(f"Stale rebuild (gen {my_gen} != {self._rebuild_gen}), discarding (pre-talk)")
                    self._condition.notify_all()
                    return

            # --- Phase 2: Schedule talk segment (LLM calls, no lock held) ---
            # Use the actual next-to-play song (first item of _queue), not the preview
            _next_song_for_talk = None
            with self._condition:
                if self._queue:
                    first = self._queue[0]
                    if first.get("song_id"):
                        _next_song_for_talk = {"artist": first.get("artist", ""), "title": first.get("title", "")}
                        snapshot.next_song_id = first.get("song_id")
            _log_step("Scheduling talk segment")
            _talk_suppress = self._maybe_schedule_talk_segment(next_song=_next_song_for_talk, snapshot=snapshot, advance_counter=advance_talk_counter)

            # --- Phase 3: Write preview with second staleness check ---
            _log_step("Setting preview queue and notifying")
            was_empty = False
            with self._condition:
                if self._rebuild_gen != my_gen:
                    _log_step(f"Stale rebuild (gen {my_gen} != {self._rebuild_gen}), discarding (post-talk)")
                    # Talk segment was appended but rebuild is stale — remove it
                    self._discard_stale_talk_segment(my_gen)
                    self._condition.notify_all()
                    return
                # Store as preview, tagged with the seed it was generated for
                with self._preview_lock:
                    self._preview_queue = new_queue
                    self._preview_song_id = context_id
                # If main queue is empty (startup/fallback), populate it directly
                was_empty = not self._queue
                if was_empty:
                    self._queue = list(new_queue)
                    # Clear preview so the watchdog detects it as stale and
                    # generates a distinct batch (preview != main queue).
                    with self._preview_lock:
                        self._preview_queue = []
                # Ensure preview tag points to the first queued song so that
                # on_song_started() validation succeeds when that song plays.
                with self._preview_lock:
                    if self._queue:
                        _old_preview_id = self._preview_song_id
                        self._preview_song_id = self._queue[0].get("song_id")
                        logger.info("_rebuild_queue: overwrote preview_song_id from %s to %s (queue[0]=%s)",
                                    _old_preview_id, self._preview_song_id, self._queue[0].get("song_id"))
                self._rebuilding = False
                self._condition.notify_all()

            # Notify frontend that rebuilding has finished
            # Always broadcast so talk segments (appended during rebuild) and
            # other changes are reflected in the frontend queue preview.
            _log_step("Broadcasting queue_updated")
            self._broadcast("queue_updated", {"queue": self._get_display_queue(), "gen": broadcast_gen})
            _log_step("Broadcasting queue_rebuilding=False")
            self._broadcast("queue_rebuilding", {"rebuilding": False, "gen": broadcast_gen})

            # Start staleness checker to keep TTS in sync with queue changes
            self._start_tts_staleness_checker()

            # Pre-generate TTS for the next song (background, non-blocking)
            _log_step("Starting TTS pre-gen thread")
            threading.Thread(target=self._pregen_tts_for_next_song, args=(snapshot, _talk_suppress), daemon=True).start()

            # Pre-generate DJ effects for upcoming songs (non-blocking, checks cache + spawns bg gen)
            # Use _queue (actual upcoming songs), not new_queue (preview batch)
            _log_step("Starting DJ effect pre-gen thread")
            queue_for_dj = list(self._queue) if self._queue else new_queue
            threading.Thread(target=self._pregen_dj_effects, args=(queue_for_dj, snapshot), daemon=True).start()
            _log_step("=== REBUILD DONE ===")

        except Exception as e:
            logger.error(f"Queue rebuild failed: {e}\n{traceback.format_exc()}")
            with self._condition:
                is_current_rebuild = self._rebuild_gen == my_gen
                if is_current_rebuild:
                    self._rebuilding = False
                self._condition.notify_all()
                display_queue = self._get_display_queue(_locked=True)
            # Notify frontend that rebuilding has stopped only for the active
            # generation. Stale failures should not hide a newer rebuild.
            if is_current_rebuild:
                self._broadcast("queue_updated", {"queue": display_queue, "gen": broadcast_gen})
                self._broadcast("queue_rebuilding", {"rebuilding": False, "gen": broadcast_gen})
            else:
                self._broadcast("queue_updated", {"queue": display_queue, "gen": broadcast_gen})

    def _get_single_recommendation(self):
        """Get a single song recommendation (fallback when queue is empty)."""
        try:
            current_song = self._db.get_song(self._current_song_id) if self._current_song_id else None
            taste_vectors = self._memory.get_taste_vectors(
                session_id=self._current_session_id,
                skip_streak=self._skip_streak,
            )
            taste_vector = taste_vectors["text"]

            # Parse recommendation filters
            subfolder_filter = None
            mtime_min = None
            mtime_max = None
            artist_mode = None
            artists = None
            filters_raw = self._db.get_setting("recommendation_filters") or self._db.get_setting("folder_filter") or ""
            if filters_raw and filters_raw.strip():
                try:
                    parsed = json.loads(filters_raw)
                    if isinstance(parsed, list):
                        subfolder_filter = parsed
                    elif isinstance(parsed, dict):
                        subfolder_filter = parsed.get("folders") or None
                        raw_min = parsed.get("mtime_min_days")
                        raw_max = parsed.get("mtime_max_days")
                        if raw_min is not None:
                            mtime_min = int(raw_min)
                        if raw_max is not None:
                            mtime_max = int(raw_max)
                        am = parsed.get("artist_mode")
                        if am in ("include", "exclude"):
                            artist_mode = am
                            artists = parsed.get("artists") or None
                except (json.JSONDecodeError, TypeError):
                    pass

            candidates = retriever.build_candidate_list(
                text_taste_vector=taste_vector or [0.0] * retriever.get_embedding_dimension(),
                audio_taste_vector=taste_vectors["audio"],
                current_song_id=self._current_song_id or 0,
                exclude_ids=[],
                subfolder_filter=subfolder_filter,
                mtime_min=mtime_min, mtime_max=mtime_max,
                artist_mode=artist_mode, artists=artists,
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

    def _get_recent_played_context(self, count=3):
        """Get recently played songs from play_events for talk segment context.

        Uses play_events with signal IN ('playing', 'completed') ordered by
        played_at DESC — NOT queue_history (which records pop order, not
        actual play signals).  This gives talk segments accurate awareness
        of what the listener actually heard.

        Returns the list in chronological order (oldest first) so the LLM
        can reference songs in the right temporal sequence.
        """
        try:
            signals = self._db.get_recent_signals(
                signal_types=("playing", "completed"),
                limit=count,
            )
            context = []
            for sig in signals:
                song = self._db.get_song(sig["song_id"])
                if song:
                    lyrics_snippet = ""
                    if song.get("lyrics"):
                        words = song["lyrics"].split()[:config.LYRICS_TALK_WORDS]
                        lyrics_snippet = " ".join(words)
                    context.append({
                        "artist": song.get("artist", "Unknown"),
                        "title": song.get("title", "Unknown"),
                        "lyrics_snippet": lyrics_snippet,
                    })
            context.reverse()
            return context
        except Exception as e:
            logger.debug(f"Failed to get recent played context: {e}")
            return []

    def _discard_stale_talk_segment(self, stale_gen: int):
        """Remove talk segments created by a stale rebuild.

        Called when the post-talk staleness check fails.  Removes any
        segments in _pending_talk_segments that were tagged with the
        stale generation number.
        """
        before = len(self._pending_talk_segments)
        self._pending_talk_segments = [
            seg for seg in self._pending_talk_segments
            if seg.get("generation") != stale_gen
        ]
        discarded = before - len(self._pending_talk_segments)
        if discarded:
            _log_step(f"Discarded {discarded} stale talk segment(s) from gen {stale_gen}")

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
        self.clear_undo()

    def clear_queue(self):
        """Clear all queue tiers: AI recommendations, manual additions, and talk segments."""
        with self._condition:
            self._queue = []
            self._manual_queue = []
            self._pending_talk_segments.clear()
            self._condition.notify_all()

    def remove_manual(self, song_id: int) -> bool:
        """Remove a song from the manual queue by song_id.

        Args:
            song_id: The song ID to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._condition:
                for i, item in enumerate(self._manual_queue):
                    if item.get("song_id") == song_id:
                        self._manual_queue.pop(i)
                        self._condition.notify_all()
                        logger.info(f"remove_manual: song_id={song_id} manual_queue_size={len(self._manual_queue)}")
                        self._broadcast("queue_updated", {"queue": self._get_display_queue(_locked=True), "gen": self._broadcast_gen})
                        return True
                return False

    def add_manual(self, song_ids: list[int], position: str = "back") -> dict:
        """Add songs to the manual queue (survives AI rebuilds).

        Args:
            song_ids: List of song IDs to add.
            position: 'front' (play next) or 'back' (add to end).

        Returns:
            Dict with 'added' (list of added IDs) and 'skipped' (list of skipped IDs).
        """
        added = []
        skipped = []
        with self._condition:
            existing = {e["song_id"] for e in self._manual_queue}
            if self._current_song_id:
                existing.add(self._current_song_id)
            if len(self._manual_queue) <= 3:
                existing |= {e["song_id"] for e in self._queue}

            ids = list(reversed(song_ids)) if position == "front" else song_ids
            for sid in ids:
                if sid in existing:
                    skipped.append(sid)
                    logger.info(f"add_manual: skipping song_id={sid} (already in queue)")
                    continue
                song = self._db.get_song(sid)
                if song:
                    entry = {
                        "song_id": sid,
                        "slot": "manual",
                        "reason": "Added by you.",
                        "artist": song.get("artist", ""),
                        "title": song.get("title", ""),
                    }
                    if position == "front":
                        self._manual_queue.insert(0, entry)
                    else:
                        self._manual_queue.append(entry)
                    added.append(sid)
                    existing.add(sid)
                    logger.info(f"add_manual: song_id={sid} artist={song.get('artist','')} title={song.get('title','')} position={position} manual_queue_size={len(self._manual_queue)}")
            self._condition.notify_all()
        if added:
            logger.info(f"add_manual: total added={len(added)} songs ({position}), manual_queue_size={len(self._manual_queue)}")
            self._broadcast("queue_updated", {"queue": self._get_display_queue(), "gen": self._broadcast_gen})
        return {"added": added, "skipped": skipped}

    def record_replay(self):
        """Record a replay signal - reset skip streak, boost taste."""
        self.reset_skip_streak()
        self.clear_undo()
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

    def record_action(self, action_type, song_id, song_info=None, signal_type=None):
        """Record an action for potential undo.

        Args:
            action_type: "skip" or "thumbs_down"
            song_id: The song ID the action was taken on
            song_info: Full song dict (for undo-skip to replay)
            signal_type: The actual play event signal (e.g. "skip_early", "skip_late")
        """
        import time
        self._last_action = {
            "type": action_type,
            "song_id": song_id,
            "timestamp": time.time(),
            "signal_type": signal_type,
        }
        self._last_skipped_song = song_info if action_type == "skip" else None

    def clear_undo(self):
        """Clear the undo stack (called after positive signals)."""
        self._last_action = None
        self._last_skipped_song = None

    def get_undo_state(self):
        """Return the current undo state for the frontend."""
        if not self._last_action:
            return {"can_undo": False}
        import time
        elapsed = time.time() - self._last_action["timestamp"]
        remaining = max(0, 10000 - int(elapsed * 1000))  # 10-second window
        if remaining <= 0:
            self._last_action = None
            self._last_skipped_song = None
            return {"can_undo": False}
        song = self._db.get_song(self._last_action["song_id"])
        return {
            "can_undo": True,
            "action_type": self._last_action["type"],
            "song_id": self._last_action["song_id"],
            "song_title": song.get("title", "") if song else "",
            "song_artist": song.get("artist", "") if song else "",
            "expires_in_ms": remaining,
        }

    def undo_skip(self):
        """Undo the last skip: replay the skipped song."""
        if not self._last_action or self._last_action["type"] != "skip":
            return {"success": False, "error": "No skip to undo"}
        import time
        if time.time() - self._last_action["timestamp"] > 10:
            self._last_action = None
            self._last_skipped_song = None
            return {"success": False, "error": "Undo window expired"}
        song = self._last_skipped_song or self._db.get_song(self._last_action["song_id"])
        if not song:
            return {"success": False, "error": "Song not found"}
        filepath = song.get("filepath")
        if not filepath or not os.path.exists(filepath):
            return {"success": False, "error": "File not found"}
        p = player_engine.get_player()
        session_id = self._current_session_id
        # Stop current playback, play the skipped song
        p.stop()
        p.play_song(song["id"], filepath, session_id)
        # Mark the skip event as undone in DB
        signal_type = self._last_action.get("signal_type", "skip_early")
        database.undo_play_event(self._last_action["song_id"], signal_type)
        import time
        self.reset_skip_streak()
        self._last_action = None
        self._last_skipped_song = None
        return {"success": True, "song_id": song["id"]}

    def undo_thumbs_down(self):
        """Undo the last thumbs-down."""
        if not self._last_action or self._last_action["type"] != "thumbs_down":
            return {"success": False, "error": "No thumbs-down to undo"}
        import time
        if time.time() - self._last_action["timestamp"] > 10:
            self._last_action = None
            self._last_skipped_song = None
            return {"success": False, "error": "Undo window expired"}
        # Mark the thumbs-down event as undone in DB
        song_id = self._last_action["song_id"]
        database.undo_play_event(song_id, "thumbed_down")
        self._last_action = None
        return {"success": True, "song_id": song_id}

    def _maybe_schedule_talk_segment(self, next_song: dict | None = None, snapshot: PlaybackContext | None = None, advance_counter=True):
        """Schedule a talk segment if the counter triggers and none is pending.

        Uses a two-phase approach: saves talk_segments module-level state
        before the LLM call, then restores it if the rebuild turned stale
        during generation.  The appended segment is tagged with the generation
        number so pop_next() and _discard_stale_talk_segment() can validate it.

        Args:
            next_song: Dict with 'artist' and 'title' of the first song in the
                       new queue, so the talk segment can reference it.
            snapshot: PlaybackContext for staleness validation and pre-fetched
                      recent context.
            advance_counter: If True, advance the talk song counter (should be
                             True when triggered by on_song_started, False for
                             pop_next-backed rebuilds to prevent double-counting).
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

            # Save module-level state for rollback if rebuild turns stale
            import announcer.talk_segments as _ts_mod
            saved_counter = _ts_mod._song_counter
            saved_guest = _ts_mod._current_guest_takeover
            saved_callers = list(_ts_mod._recent_callers)
            saved_guests = list(_ts_mod._recent_guests)
            saved_types = list(_ts_mod._recent_segment_types)

            # Advance talk counter for this song.  If the rebuild turns
            # stale, the saved state is restored below — so manual queue
            # refreshes and stale rebuilds never advance the schedule.
            if advance_counter:
                _ts_mod._song_counter += 1

            # Build last_song context from current song
            last_song = None
            if self._current_song_id:
                cs = self._db.get_song(self._current_song_id)
                if cs:
                    last_song = {"artist": cs.get("artist", "Unknown"), "title": cs.get("title", "Unknown")}

            # Build session_state for weighted call reasons
            session_state = {
                "skip_streak": self._skip_streak,
                "mood_changed_recently": False,
                "hour": datetime.now().hour,
                "session_duration_minutes": 0,
            }
            if self._current_session_id:
                sess = self._db.get_session(self._current_session_id)
                if sess and sess.get("started_at"):
                    try:
                        started = datetime.fromisoformat(sess["started_at"].replace("Z", "+00:00"))
                        session_state["session_duration_minutes"] = int((datetime.now() - started).total_seconds() / 60)
                    except Exception:
                        pass

            caller_mode = self._db.get_setting("talk_caller_mode") or "influences_queue"
            segment = talk_segments.get_next_talk_segment(
                mood_state=snapshot.mood_state if snapshot else self._mood.get_state(),
                caller_mode=caller_mode,
                next_song=next_song,
                recent_context=snapshot.recent_context if snapshot else None,
                last_song=last_song,
                session_state=session_state,
                increment=False,
            )

            if segment:
                # Check if rebuild turned stale during LLM call
                if snapshot and self._rebuild_gen != snapshot.generation:
                    _log_step(f"Talk segment generated but rebuild is stale (gen {snapshot.generation} -> {self._rebuild_gen}), "
                              f"discarding and restoring counters")
                    if advance_counter:
                        _ts_mod._song_counter = saved_counter
                    _ts_mod._current_guest_takeover = saved_guest
                    _ts_mod._recent_callers = saved_callers
                    _ts_mod._recent_guests = saved_guests
                    _ts_mod._recent_segment_types = saved_types
                    return

                queue_item = segment.to_queue_item()
                # Tag segment with generation metadata for downstream validation
                if snapshot:
                    queue_item["generation"] = snapshot.generation
                    queue_item["target_next_song_id"] = snapshot.next_song_id
                queue_item["recent_context"] = snapshot.recent_context
                with self._condition:
                    self._pending_talk_segments.append(queue_item)
                logger.info(f"Talk segment scheduled: {segment.type.value} "
                            f"(gen={snapshot.generation if snapshot else '?'})")
                return True
            else:
                logger.debug("_maybe_schedule_talk_segment: counter not reached, no segment")
        except Exception as e:
            logger.warning(f"Talk segment scheduling failed: {e}")
        return False

    def _pregen_tts_for_next_song(self, snapshot: PlaybackContext | None = None, talk_suppress: bool = False):
        """Pre-generate TTS announcement for the next song in the queue.

        Called from _rebuild_queue() after the queue is populated.
        Runs in a background thread — the result is cached so play_next()
        can skip both the LLM call and TTS synthesis.

        The cache value includes prev_song_id and generation so play_next()
        and the staleness checker can validate that the context used to
        generate the TTS still matches the actual playback context.

        Args:
            snapshot: PlaybackContext from the rebuild.  When provided, TTS
                      context is read from the snapshot (frozen state) rather
                      than live reads of self._current_song_id etc.
            talk_suppress: True if a talk segment is scheduled for this song,
                          so TTS should be skipped entirely.
        """
        logger.info("_pregen_tts_for_next_song START")
        try:
            # Use snapshot's next_song_id if available; otherwise peek queue
            next_song_id = snapshot.next_song_id if snapshot else None
            if not next_song_id:
                with self._condition:
                    if not self._queue:
                        return
                    next_song_id = self._queue[0].get("song_id")

            if not next_song_id:
                return

            # Bail out early if rebuild generation changed (stale context)
            if snapshot and self._rebuild_gen != snapshot.generation:
                logger.info("_pregen_tts: stale rebuild, skipping")
                return

            tts_enabled = self._db.get_setting("tts_enabled") or "true"
            if not (tts_enabled.lower() == "true" and config.TTS_ENABLED):
                logger.info("_pregen_tts: TTS disabled")
                with self._pregen_lock:
                    self._pregen_cache[next_song_id] = {"no_announcement": True}
                return

            next_song = self._db.get_song(next_song_id)
            if not next_song:
                return

            filepath = next_song.get("filepath")
            if not filepath or not os.path.exists(filepath):
                return

            from announcer import get_announcement, play_announcement, AnnounceTrigger, should_announce
            from datetime import datetime

            # Use snapshot for context (frozen) rather than live reads
            current_song_id = snapshot.current_song_id if snapshot else self._current_song_id
            current_song = self._db.get_song(current_song_id) if current_song_id else None
            mood_state = snapshot.mood_state if snapshot else self._mood.get_state()

            context = {
                "current_song": dict(current_song) if current_song else {},
                "next_song": dict(next_song) if next_song else {},
                "mood_state": mood_state,
                "skip_streak": self._skip_streak,
                "hour": datetime.now().hour,
            }

            # Skip TTS if a talk segment is scheduled for this song
            if talk_suppress:
                logger.info(f"_pregen_tts: talk segment pending for song {next_song_id}, skipping TTS")
                gen = snapshot.generation if snapshot else 0
                with self._pregen_lock:
                    self._pregen_cache[next_song_id] = {
                        "wav_path": None,
                        "text": None,
                        "song_id": next_song_id,
                        "prev_song_id": current_song_id,
                        "generation": gen,
                        "no_announcement": True,
                    }
                return

            # Determine trigger type with priority:
            # SKIP_STREAK > MOOD_CHANGE > WILDCARD > RECOVERY > RADIO_INTRO
            trigger = AnnounceTrigger.RADIO_INTRO
            trigger_type = "radio_intro"

            if self._skip_streak >= 1:
                trigger = AnnounceTrigger.SKIP_STREAK
                trigger_type = "skip_streak"
            else:
                # Check for mood change
                if self._prev_mood_state is not None and self._prev_mood_state != mood_state:
                    trigger = AnnounceTrigger.MOOD_CHANGE
                    trigger_type = "mood_change"

                # Check for wildcard slot (if mood didn't change)
                if trigger == AnnounceTrigger.RADIO_INTRO:
                    with self._condition:
                        first_queue_item = self._queue[0] if self._queue else None
                    if first_queue_item and first_queue_item.get("slot") == "wildcard":
                        trigger = AnnounceTrigger.WILDCARD
                        trigger_type = "wildcard"

                # Check for recovery song (if nothing else matched)
                if trigger == AnnounceTrigger.RADIO_INTRO:
                    recovery_score = next_song.get("recovery_score", 0)
                    if recovery_score is not None and recovery_score > 0.75:
                        trigger = AnnounceTrigger.RECOVERY
                        trigger_type = "recovery"

            # Skip TTS if should_announce says no (radio intro frequency gate)
            if not should_announce({"trigger": trigger, **context}):
                logger.info(f"_pregen_tts: should_announce returned False for song {next_song_id}, skipping TTS")
                gen = snapshot.generation if snapshot else 0
                with self._pregen_lock:
                    self._pregen_cache[next_song_id] = {
                        "wav_path": None,
                        "text": None,
                        "song_id": next_song_id,
                        "prev_song_id": current_song_id,
                        "generation": gen,
                        "no_announcement": True,
                    }
                return

            text = get_announcement(context, trigger)
            if not text:
                gen = snapshot.generation if snapshot else 0
                with self._pregen_lock:
                    self._pregen_cache[next_song_id] = {
                        "wav_path": None,
                        "text": None,
                        "song_id": next_song_id,
                        "prev_song_id": current_song_id,
                        "generation": gen,
                        "no_announcement": True,
                    }
                return

            wav_path = play_announcement(text, filepath, trigger_type=trigger_type)
            if not wav_path:
                return

            gen = snapshot.generation if snapshot else 0
            with self._pregen_lock:
                self._pregen_cache[next_song_id] = {
                    "wav_path": wav_path,
                    "text": text,
                    "song_id": next_song_id,
                    "prev_song_id": current_song_id,
                    "generation": gen,
                }
            logger.info(f"_pregen_tts: cached TTS for song {next_song_id} (prev={current_song_id}, gen={gen})")
        except Exception as e:
            logger.warning(f"TTS pre-generation failed: {e}")
        logger.info("_pregen_tts_for_next_song DONE")

    def _pregen_dj_effects(self, new_queue, snapshot):
        """Pre-generate DJ effects for songs in the queue.

        Checks the currently active guest persona for a DJ effect preset.
        For each queue item: if the effect is already cached, sets
        modified_filepath immediately. If not cached, spawns a background
        generation thread. The background thread stores the result in
        _dj_effect_cache so play_next and _play_talk_segment can use it.

        Runs in a background thread after queue rebuild.
        """
        try:
            from announcer.talk_segments import get_active_dj_effect_preset
            preset_name = get_active_dj_effect_preset()
            if not preset_name:
                # Guest takeover ended or never started — clear stale cache
                with self._dj_effect_cache_lock:
                    if self._dj_effect_cache:
                        logger.info(f"DJ effect cache cleared (guest ended, had {len(self._dj_effect_cache)} entries)")
                        self._dj_effect_cache.clear()
                return

            enabled = self._db.get_setting("dj_effect_enabled") or "true"
            if enabled.lower() != "true":
                return

            _log_step(f"DJ effect pre-gen: preset={preset_name}, queue_size={len(new_queue)}")

            from engine.dj_effects import get_cached
            for item in new_queue:
                song_id = item.get("song_id")
                if not song_id:
                    continue
                song = self._db.get_song(song_id)
                if not song or not song.get("dj_effect_eligible", 1):
                    continue
                filepath = song.get("filepath")
                if not filepath or not os.path.exists(filepath):
                    continue

                cached = get_cached(song_id, preset_name)
                if cached:
                    item["modified_filepath"] = cached
                    with self._dj_effect_cache_lock:
                        self._dj_effect_cache[song_id] = cached
                    _log_step(f"DJ effect cache HIT for song {song_id}")
                else:
                    threading.Thread(
                        target=self._gen_dj_effect_background,
                        args=(filepath, song_id, preset_name),
                        daemon=True,
                    ).start()
                    _log_step(f"DJ effect cache MISS for song {song_id}, spawned bg gen")
        except Exception as e:
            logger.warning(f"DJ effect pre-gen failed: {e}")

    def _gen_dj_effect_background(self, filepath, song_id, preset_name):
        """Generate DJ effect in background thread and store in cache."""
        try:
            from engine.dj_effects import apply_effect
            effect_path = apply_effect(filepath, song_id, preset_name)
            if effect_path and os.path.exists(effect_path):
                with self._dj_effect_cache_lock:
                    self._dj_effect_cache[song_id] = effect_path
                _log_step(f"DJ effect background gen completed for song {song_id}")
        except Exception as e:
            logger.warning(f"DJ effect background gen failed for song {song_id}: {e}")

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

                    # Check if we have cached TTS for this song (with context validation)
                    with self._pregen_lock:
                        cached = self._pregen_cache.get(next_song_id)
                        if cached and cached.get("wav_path") and os.path.exists(cached["wav_path"]):
                            cached_prev = cached.get("prev_song_id")
                            # If the cached TTS was generated for a different previous song,
                            # it's contextually stale — treat as cache miss so it gets regenerated.
                            if cached_prev is None or cached_prev == self._current_song_id:
                                continue  # TTS is up to date
                            logger.info(f"TTS staleness checker: prev_song mismatch "
                                        f"(cached={cached_prev} actual={self._current_song_id}), will regen")
                        elif cached and cached.get("no_announcement"):
                            # Announcement was blocked by interval gate; don't retry until next song
                            continue
                        elif cached and cached.get("wav_path") and not os.path.exists(cached["wav_path"]):
                            # TTS file was deleted since caching — clear stale entry
                            self._pregen_cache.pop(next_song_id, None)

                    # Cache miss or stale — regenerate
                    # Check if a talk segment is pending for this song
                    _staleness_talk_suppress = False
                    with self._condition:
                        for seg in self._pending_talk_segments:
                            if seg.get("target_next_song_id") == next_song_id:
                                _staleness_talk_suppress = True
                                break
                    # Build a minimal snapshot so TTS context is correct
                    # (prevents generating "that was Song X" when current is Song Y)
                    with self._condition:
                        current_gen = self._rebuild_gen
                    staleness_snapshot = PlaybackContext(
                        generation=current_gen,
                    current_song_id=self._current_song_id,
                        next_song_id=next_song_id,
                        mood_state=self._mood.get_state(),
                        session_id=self._current_session_id,
                    )
                    logger.info(f"TTS staleness checker: song {next_song_id} not cached or stale, regenerating")
                    self._pregen_tts_for_next_song(staleness_snapshot, talk_suppress=_staleness_talk_suppress)
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

            # Check for DJ effect modified version (only if guest takeover is active)
            from announcer.talk_segments import get_active_dj_effect_preset
            with self._dj_effect_cache_lock:
                modified_fp = self._dj_effect_cache.get(song_id)
                if modified_fp and os.path.exists(modified_fp):
                    if get_active_dj_effect_preset():
                        filepath = modified_fp
                        logger.info(f"play_next: using DJ effect version for song {song_id}")
                    else:
                        # Guest ended — remove stale entry so we don't re-check
                        self._dj_effect_cache.pop(song_id, None)
                        logger.info(f"play_next: DJ effect cache stale for song {song_id} (guest ended), removed")

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
                    trigger_type = "radio_intro"

                    if self._skip_streak >= 1:
                        trigger = AnnounceTrigger.SKIP_STREAK
                        trigger_type = "skip_streak"
                    else:
                        # Check for mood change
                        current_mood = self._mood.get_state()
                        if self._prev_mood_state is not None and self._prev_mood_state != current_mood:
                            trigger = AnnounceTrigger.MOOD_CHANGE
                            trigger_type = "mood_change"

                        # Check for wildcard slot (if mood didn't change)
                        if trigger == AnnounceTrigger.RADIO_INTRO:
                            slot = pick.get("slot", "context")
                            if slot == "wildcard":
                                trigger = AnnounceTrigger.WILDCARD
                                trigger_type = "wildcard"

                        # Check for recovery song (if nothing else matched)
                        if trigger == AnnounceTrigger.RADIO_INTRO:
                            recovery_score = song.get("recovery_score", 0)
                            if recovery_score is not None and recovery_score > 0.75:
                                trigger = AnnounceTrigger.RECOVERY
                                trigger_type = "recovery"

                    if not should_announce({"trigger": trigger, **context}):
                        # No announcement needed, play song directly
                        # NOTE: on_song_started() is called by main.py after play_next() returns
                        logger.info(f"play_next: playing song directly (no TTS needed)")
                        p.play_song(song_id, filepath, session_id)
                        return

                    # Check pre-generation cache (works when predictions match)
                    use_cache = (self._skip_streak == 0)
                    cached = None
                    if use_cache:
                        with self._pregen_lock:
                            cached = self._pregen_cache.pop(song_id, None)

                    # Validate cached TTS matches current playback context.
                    # The TTS was pre-generated with a specific "previous song" —
                    # if that doesn't match the actual current song, discard the
                    # cache entry to avoid stale context (e.g. "that was Song
                    # X" when the listener just heard Song Y).
                    if cached and cached.get("wav_path") and os.path.exists(cached["wav_path"]):
                        cached_prev = cached.get("prev_song_id")
                        if cached_prev is not None and cached_prev != current_song_id:
                            logger.info(f"play_next: TTS cache stale (prev {cached_prev} != actual {current_song_id}), "
                                        f"playing song directly")
                            cached = None
                            # Remove stale entry from cache entirely
                            with self._pregen_lock:
                                self._pregen_cache.pop(song_id, None)

                    if cached and cached.get("wav_path") and os.path.exists(cached["wav_path"]):
                        logger.info(f"play_next: TTS cache hit, playing chain (TTS + song)")
                        items = [
                            {"filepath": cached["wav_path"], "is_tts": True},
                            {"filepath": filepath, "song_id": song_id, "is_tts": False},
                        ]
                        p.play_chain(items)
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

        except Exception as e:
            logger.error(f"play_next failed: {e}")

    def _play_talk_segment(self, pick: dict):
        """Play a talk segment (caller, monologue, guest takeover).

        Plays TTS WAV(s) as a talk-only chain (no song appended).
        VLC's _on_end_reached detects the talk-only chain end and fires
        synthetic song_ended(None), which triggers pop_next() →
        the next real song from queue plays naturally.

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

            # Fade in the first WAV for a smooth talk segment entrance
            if valid_wavs:
                faded = _fade_in_first_second(valid_wavs[0])
                if faded:
                    valid_wavs[0] = faded

            # Concatenate multiple WAVs into one — VLC's media_list_player
            # has trouble chaining 5+ items reliably.  A single concatenated
            # WAV + song (2 items) always works.
            if len(valid_wavs) > 1:
                concat_path = _concatenate_wavs(valid_wavs)
                if concat_path:
                    logger.info("_play_talk_segment: concatenated %d WAVs -> %s", len(valid_wavs), concat_path)
                    valid_wavs = [concat_path]
                else:
                    logger.warning("_play_talk_segment: concatenation failed, using %d individual WAVs", len(valid_wavs))

            p = player_engine.get_player()
            self._talk_segment_playing = True

            # Look ahead in queue for the next song (peek only — don't pop,
            # so on_song_started can handle the normal preview swap when the
            # real song actually starts after TTS finishes).
            next_song_item = None
            with self._condition:
                for item in self._queue:
                    if item.get("type") != "talk_segment":
                        next_song_item = item
                        break

            # Validate the target song matches what was teased
            if next_song_item:
                target_nsid = pick.get("target_next_song_id")
                actual_nsid = next_song_item.get("song_id")
                if target_nsid is not None and actual_nsid != target_nsid:
                    logger.info(f"_play_talk_segment: target_next_song_id mismatch "
                                f"(teased={target_nsid}, actual={actual_nsid}) — "
                                f"talk segment text may reference wrong song")

            if next_song_item:
                song_id = next_song_item.get("song_id")
                song = self._db.get_song(song_id)
                if song and song.get("filepath") and os.path.exists(song["filepath"]):
                    # Check for DJ effect modified version (only if guest takeover is active)
                    from announcer.talk_segments import get_active_dj_effect_preset
                    with self._dj_effect_cache_lock:
                        modified_fp = self._dj_effect_cache.get(song_id)
                        if modified_fp and os.path.exists(modified_fp):
                            if get_active_dj_effect_preset():
                                logger.info(f"_play_talk_segment: using DJ effect version for song {song_id}")
                            else:
                                self._dj_effect_cache.pop(song_id, None)
                                logger.info(f"_play_talk_segment: DJ effect cache stale for song {song_id} (guest ended), removed")

                    # Apply mood_shift BEFORE play_chain so the background
                    # rebuild uses the new mood when picking next songs.
                    mood_shift = pick.get("mood_shift")
                    if mood_shift:
                        try:
                            logger.info(f"Applying mood shift from talk segment: {mood_shift}")
                            self._mood.set_state(mood_shift)
                            self._broadcast("mood_changed", {"mood": mood_shift, "source": "talk_segment"})
                        except Exception as e:
                            logger.warning(f"Mood shift from talk segment failed: {e}")

                # Play TTS WAVs as a talk-only chain — VLC's media_list_player
                # does NOT reliably fire transition events between items, so
                # chaining TTS + song together never advances past the TTS.
                # Instead: play TTS alone, let _on_end_reached detect talk-only
                # chain end and fire synthetic song_ended(None), which triggers
                # pop_next() → next real song from queue.
                logger.info(f"_play_talk_segment: playing {len(valid_wavs)} TTS WAV(s) as talk-only chain")
                p.play_chain([{"filepath": w, "is_tts": True} for w in valid_wavs])

            else:
                # No song in queue at all — play talk alone; _on_end_reached
                # fires synthetic song_ended(None), pop_next handles the rest.
                self._talk_segment_playing = False
                logger.info(f"_play_talk_segment: no next song item in queue, playing {len(valid_wavs)} WAVs only")
                p.play_chain([{"filepath": w, "is_tts": True} for w in valid_wavs])


            # Broadcast talk segment start to frontend
            # Include next_song info so frontend knows what will play after
            _next_id = next_song_item.get("song_id") if next_song_item else None
            _next_artist = next_song_item.get("artist", "") if next_song_item else ""
            _next_title = next_song_item.get("title", "") if next_song_item else ""
            self._broadcast("talk_segment_started", {
                "segment_type": pick.get("segment_type"),
                "reason": pick.get("reason", ""),
                "persona_id": pick.get("secondary_persona_id") or pick.get("persona_id"),
                "duration_secs": pick.get("duration_secs", 30),
                "next_song_id": _next_id,
                "next_song_artist": _next_artist,
                "next_song_title": _next_title,
            })

            logger.info(f"Talk segment played: {pick.get('segment_type')} ({pick.get('reason', '')})")

        except Exception as e:
            logger.error(f"_play_talk_segment failed: {e}", exc_info=True)
            self._talk_segment_playing = False
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

    def _start_preview_watchdog(self):
        """Start a persistent daemon thread that checks if the preview queue
        is stale (empty or diverged from the main queue).  Every ~3 s:

          - Skip while a rebuild is already running.
          - Skip if the main queue is empty (nothing to consume).
          - If the preview queue is empty or its tag doesn't match the
            first queued song, trigger a fresh rebuild to generate a
            distinct "next-next" batch.

        Dies on process exit (daemon thread).
        """
        self._preview_stop.set()   # stop any existing watchdog
        self._preview_stop.clear()

        def _watchdog():
            logger.info("Preview watchdog started")
            while not self._preview_stop.is_set():
                try:
                    self._preview_stop.wait(timeout=3.0)
                    if self._preview_stop.is_set():
                        break

                    # Don't interfere with an active rebuild
                    with self._condition:
                        if self._rebuilding:
                            continue
                        if not self._queue:
                            continue
                        # Don't invalidate pending talk segments — they take
                        # priority over the preview and a rebuild would
                        # increment _rebuild_gen, making their generation
                        # stale and causing pop_next() to discard them.
                        if self._pending_talk_segments:
                            continue
                        # Skip while a talk segment is actively playing —
                        # the deferred on_song_started hasn't fired yet,
                        # so the preview is stale by design.
                        if self._talk_segment_playing:
                            continue
                        first_song_id = self._queue[0].get("song_id")

                    if not first_song_id:
                        continue

                    # Check if the preview is stale
                    with self._preview_lock:
                        preview_valid = bool(
                            self._preview_queue
                            and self._preview_song_id == first_song_id
                        )

                    if not preview_valid:
                        logger.info(f"Preview watchdog: preview stale/missing for "
                                    f"context={first_song_id}, triggering rebuild")
                        with self._condition:
                            # Re-check: a talk segment may have been appended
                            # between our earlier check and this lock acquisition
                            # (race window).  Don't invalidate pending talks.
                            if self._pending_talk_segments:
                                continue
                            if self._talk_segment_playing:
                                continue
                            self._rebuilding = True
                            self._rebuild_gen += 1
                        threading.Thread(
                            target=self._rebuild_queue,
                            daemon=True,
                        ).start()

                except Exception as e:
                    logger.debug(f"Preview watchdog error: {e}")

        threading.Thread(
            target=_watchdog, daemon=True, name="preview-watchdog"
        ).start()
