"""Multi-voice TTS manager with dynamic engine switching.

Manages which TTS engine is active and allows runtime engine swaps
(e.g. Piper -> OmniVoice) without refactoring callers.
"""

import hashlib
import json
import logging
import os
import random
import struct
import threading
import wave

import numpy as np
import soundfile as sf

import config
from announcer import speech_direction as sd
from announcer.tts_engine import TTSEngine, AVAILABLE_ENGINES, PiperTTSEngine, run_with_timeout

logger = logging.getLogger(__name__)

# Voice selection modes
VOICE_MODE_USER_SELECT = "user_select"  # Use the user's chosen voice for all announcements
VOICE_MODE_RANDOM = "random"            # Pick a random voice per synthesis call
VOICE_MODE_CONTEXT = "context"          # Map trigger context to voice

# Between-song announcements when no voice is chosen (tts_voice empty, user_select mode).
DEFAULT_ANNOUNCER_VOICE = "deep radio voice"

TTS_CACHE_DIR = config.TTS_CACHE_DIR
TTS_SILENCE_PADDING_SECS = 0.25


def _append_silence(path: str, seconds: float = TTS_SILENCE_PADDING_SECS) -> bool:
    """Append silence to the end of a WAV file.

    Re-opens the WAV, reads its params, appends zero-filled frames, rewrites.
    Returns True on success, False on failure.
    """
    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            orig_data = wf.readframes(nframes)

        silence_frames = int(framerate * seconds)
        silence_bytes = b'\x00' * (channels * sampwidth * silence_frames)

        with wave.open(path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(framerate)
            wf.writeframes(orig_data)
            wf.writeframes(silence_bytes)

        return True
    except Exception as e:
        logger.warning(f"Failed to append silence to {path}: {e}")
        return False


def _prepend_silence(path: str, seconds: float = TTS_SILENCE_PADDING_SECS) -> bool:
    """Prepend silence to the start of a WAV file.

    Re-opens the WAV, reads its params, prepends zero-filled frames, rewrites.
    Returns True on success, False on failure.
    """
    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            orig_data = wf.readframes(nframes)

        silence_frames = int(framerate * seconds)
        silence_bytes = b'\x00' * (channels * sampwidth * silence_frames)

        with wave.open(path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(framerate)
            wf.writeframes(silence_bytes)
            wf.writeframes(orig_data)

        return True
    except Exception as e:
        logger.warning(f"Failed to prepend silence to {path}: {e}")
        return False


def _fade_in_first_second(path: str) -> str | None:
    """Apply a 1-second linear fade-in at the start of a WAV file.

    Ramps volume from 0 to 100% over the first second for a smooth entrance.
    Used on the first WAV of a talk show segment.

    Writes to a temp file alongside the original so the cached original is left intact.

    Returns the path to the faded copy on success, None on failure.
    """
    faded_path = path + ".faded.wav"
    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            raw = bytearray(wf.readframes(nframes))

        fade_frames = min(framerate, nframes)

        for i in range(fade_frames):
            gain = i / fade_frames
            offset = i * channels * sampwidth
            for ch in range(channels):
                sample_offset = offset + ch * sampwidth
                if sampwidth == 1:
                    sample = raw[sample_offset] - 128
                    sample = int(sample * gain)
                    raw[sample_offset] = max(0, min(255, sample + 128))
                elif sampwidth == 2:
                    sample = struct.unpack_from('<h', raw, sample_offset)[0]
                    sample = int(sample * gain)
                    struct.pack_into('<h', raw, sample_offset, max(-32768, min(32767, sample)))

        with wave.open(faded_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(framerate)
            wf.writeframes(bytes(raw))

        logger.info(f"_fade_in_first_second: faded 1s on {path} -> {faded_path}")
        return faded_path
    except Exception as e:
        logger.warning(f"Failed to fade in {path}: {e}")
        # Clean up partial output if any
        try:
            if os.path.exists(faded_path):
                os.remove(faded_path)
        except Exception:
            pass
        return None


def talk_pauses(count, rng=random):
    """Extra silence (ms) between ``count`` speech lines, on top of each line's own trailing pad.

    Speakers don't answer instantly: ordinary replies get 250-750 ms, sometimes a longer
    beat as if thinking. The first and last boundaries are handoffs (the DJ putting a
    caller on air, then taking the mic back) and get a longer 600-1200 ms.
    """
    pauses = []
    boundaries = max(count - 1, 0)
    for index in range(boundaries):
        if index == 0 or index == boundaries - 1:
            pause = rng.uniform(600, 1200)
        else:
            pause = rng.uniform(250, 750)
            if rng.random() < 0.15:
                pause += rng.uniform(400, 800)
        pauses.append(int(pause))
    return pauses


LOUDNESS_MAX_GAIN_DB = 24.0
LOUDNESS_PEAK_LIMIT = 0.89  # about -1 dBFS


def _limit_peaks(data, rate: int, limit: float):
    """Peak limiter: a 5 ms look-around gain envelope, then a hard ceiling."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    level = np.abs(data) if data.ndim == 1 else np.max(np.abs(data), axis=1)
    win = max(1, int(rate * 0.005))
    envelope = uniform_filter1d(maximum_filter1d(level, size=2 * win + 1), size=win)
    gain = np.minimum(1.0, limit / np.maximum(envelope, 1e-9))
    data = data * (gain if data.ndim == 1 else gain[:, None])
    return np.clip(data, -limit, limit)


def _match_loudness(path: str, target_lufs: float) -> bool:
    """Scale a WAV to ``target_lufs`` integrated loudness (engine-neutral).

    Gain is limited to +/-24 dB; transients over about -1 dBFS are limited. Clips too short
    to measure are left alone.
    """
    try:
        import pyloudnorm
        data, rate = sf.read(path)
        if len(data) < rate * 0.5:
            return False
        measured = pyloudnorm.Meter(rate).integrated_loudness(data)
        if not np.isfinite(measured):
            return False
        gain_db = max(-LOUDNESS_MAX_GAIN_DB, min(LOUDNESS_MAX_GAIN_DB, target_lufs - measured))
        meter = pyloudnorm.Meter(rate)
        # Gain, then limit only the transients, a few times: scaling the whole clip
        # down to its loudest peak would leave speech well under the target.
        for _ in range(3):
            data = _limit_peaks(data * (10 ** (gain_db / 20)), rate, LOUDNESS_PEAK_LIMIT)
            now = meter.integrated_loudness(data)
            if not np.isfinite(now) or abs(target_lufs - now) < 0.3:
                break
            gain_db = max(0.0, min(LOUDNESS_MAX_GAIN_DB, target_lufs - now))
            if gain_db == 0.0:
                break
        subtype = sf.info(path).subtype
        sf.write(path, data, rate, subtype=subtype)
        logger.info(f"TTS loudness: {measured:.1f} LUFS -> {now:.1f} (target {target_lufs:.1f})")
        return True
    except Exception as e:
        logger.warning(f"TTS loudness matching skipped for {path}: {e}")
        return False


def _join_chunks(paths, pauses_ms, out_path):
    """Join chunk WAVs into ``out_path`` with the given silence after each chunk."""
    pieces, rate = [], None
    subtype = sf.info(paths[0]).subtype
    for path, pause in zip(paths, pauses_ms):
        data, sr = sf.read(path)
        if rate is None:
            rate = sr
        elif sr != rate:
            import librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=rate)
        pieces.append(data)
        if pause:
            shape = (int(rate * pause / 1000),) + data.shape[1:]
            pieces.append(np.zeros(shape, dtype=data.dtype))
    sf.write(out_path, np.concatenate(pieces, axis=0), rate, subtype=subtype)
    return out_path


def _concatenate_wavs(wav_paths, pauses_ms=None, lead_ms=0):
    """Concatenate multiple WAV files into a single WAV with natural pauses between lines.

    ``pauses_ms`` gives the silence inserted between consecutive files (randomized by
    ``talk_pauses`` when omitted); ``lead_ms`` adds silence before the first line.
    All WAVs must share the same sample rate and channel count.
    Returns the path to the concatenated (and cached) file.
    """
    if not wav_paths:
        return None
    if len(wav_paths) == 1 and not lead_ms:
        return wav_paths[0]
    if pauses_ms is None:
        pauses_ms = talk_pauses(len(wav_paths))

    key_text = "|".join(wav_paths) + "#" + ",".join(str(int(p)) for p in pauses_ms) + f"#{int(lead_ms)}"
    key = hashlib.md5(key_text.encode()).hexdigest()
    out_path = os.path.join(TTS_CACHE_DIR, f"concat_{key}.wav")
    if os.path.exists(out_path):
        return out_path

    segments = []
    target_sr = None
    target_channels = None
    for path in wav_paths:
        data, sr = sf.read(path)
        if target_sr is None:
            target_sr = sr
        elif sr != target_sr:
            logger.warning(f"_concatenate_wavs: sample rate mismatch {sr} != {target_sr}, resampling")
            import librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=target_sr)
        if data.ndim == 1:
            channels = 1
        else:
            channels = data.shape[1]
        if target_channels is None:
            target_channels = channels
        segments.append(data)

    def silence(ms):
        frames = int(target_sr * max(ms, 0) / 1000)
        shape = (frames,) if segments[0].ndim == 1 else (frames, segments[0].shape[1])
        return np.zeros(shape, dtype=segments[0].dtype)

    spaced = [silence(lead_ms)] if lead_ms else []
    for index, data in enumerate(segments):
        if index > 0:
            spaced.append(silence(pauses_ms[index - 1] if index - 1 < len(pauses_ms) else 0))
        spaced.append(data)
    combined = np.concatenate(spaced, axis=0)
    sf.write(out_path, combined, target_sr)
    logger.info("_concatenate_wavs: %d WAVs -> %s (%.1fs, %dHz, %dch)",
                len(wav_paths), out_path, len(combined) / target_sr, target_sr, target_channels)
    return out_path


class VoiceManager:
    """Manages TTS engines and voices with runtime switching.

    Supports multiple engines (OmniVoice, Piper) with automatic fallback.
    Voice selection modes: user_select (default), random, context.
    """

    _MAX_SYNTH_LOCKS = 128  # prevent unbounded growth of _synth_locks

    def __init__(self):
        self._lock = threading.RLock()
        self._engines: dict[str, TTSEngine | "OmniVoiceTTSEngine"] = {}
        self._active_engine_id: str | None = None
        self._active_voice: str | None = None
        self._settings_db = None  # lazy reference to database for persisted engine choice
        self._voice_mode: str = VOICE_MODE_USER_SELECT
        self._synth_locks: dict[str, threading.Lock] = {}
        self._synth_locks_lock = threading.Lock()

    def _get_synth_lock(self, output_path):
        """Get or create a per-path lock with LRU eviction to prevent unbounded growth."""
        with self._synth_locks_lock:
            if output_path not in self._synth_locks:
                if len(self._synth_locks) >= self._MAX_SYNTH_LOCKS:
                    # Evict the first (oldest) entry
                    oldest = next(iter(self._synth_locks))
                    del self._synth_locks[oldest]
                self._synth_locks[output_path] = threading.Lock()
            return self._synth_locks[output_path]

    def set_database(self, db):
        """Inject database reference for persisting engine/voice choices."""
        self._settings_db = db

    def initialize(self):
        """Load persisted engine/voice settings from DB on startup."""
        if not self._settings_db:
            return
        try:
            engine_id = self._settings_db.get_setting("tts_engine")
            voice_id = self._settings_db.get_setting("tts_voice")
            if engine_id:
                # Don't use set_engine() here — it would persist back to DB again.
                # Just set the internal state directly.
                with self._lock:
                    self._active_engine_id = engine_id
            if voice_id:
                with self._lock:
                    self._active_voice = voice_id
        except Exception:
            pass

    def _resolve_default_engine(self) -> str:
        """Determine the best default engine: ZONOS3, then OmniVoice, then Piper."""
        if "zonos3" in AVAILABLE_ENGINES:
            try:
                if self.get_engine("zonos3").is_ready():
                    return "zonos3"
            except Exception:
                pass
        if "omnivoice" in AVAILABLE_ENGINES:
            try:
                from announcer.omnivoice_engine import OmniVoiceTTSEngine
                test = OmniVoiceTTSEngine()
                if test.is_ready():
                    return "omnivoice"
            except Exception:
                pass
        return "piper"

    # ---- Engine management ----

    def register_engine(self, engine_id: str, engine: TTSEngine):
        """Manually register a TTS engine instance."""
        with self._lock:
            self._engines[engine_id] = engine

    def get_engine(self, engine_id: str | None = None) -> TTSEngine:
        """Get an engine instance, creating it if needed."""
        if engine_id is None:
            engine_id = self._active_engine_id or self._resolve_default_engine()
        with self._lock:
            if engine_id not in self._engines:
                engine_cls = AVAILABLE_ENGINES.get(engine_id)
                if not engine_cls:
                    raise ValueError(f"Unknown TTS engine: {engine_id}. Available: {list(AVAILABLE_ENGINES.keys())}")
                self._engines[engine_id] = engine_cls()
            return self._engines[engine_id]

    def set_engine(self, engine_id: str, voice_id: str | None = None):
        """Switch the active TTS engine at runtime.
        
        If the requested engine is OmniVoice but GPU is unavailable,
        automatically falls back to Piper.
        """
        with self._lock:
            if engine_id not in AVAILABLE_ENGINES and engine_id not in self._engines:
                raise ValueError(f"Unknown TTS engine: {engine_id}")

            # Auto-fallback: if OmniVoice requested but GPU unavailable, use Piper
            if engine_id == "omnivoice":
                try:
                    from announcer.omnivoice_engine import OmniVoiceTTSEngine
                    test = self._engines.get("omnivoice")
                    if test is None:
                        test = OmniVoiceTTSEngine()
                    if not test.is_ready():
                        logger.warning("OmniVoice GPU not available, falling back to Piper")
                        engine_id = "piper"
                        if voice_id is None:
                            voice_id = "amy"
                except Exception as e:
                    logger.warning(f"OmniVoice not available ({e}), falling back to Piper")
                    engine_id = "piper"
                    if voice_id is None:
                        voice_id = "amy"

            self._active_engine_id = engine_id
            if voice_id:
                self._active_voice = voice_id

            # Persist
            if self._settings_db:
                try:
                    self._settings_db.set_setting("tts_engine", engine_id)
                    self._settings_db.set_setting("tts_voice", self._active_voice or "")
                except Exception:
                    pass

    def get_active_engine(self) -> tuple[str, TTSEngine]:
        """Get the currently active engine ID and instance."""
        with self._lock:
            eid = self._active_engine_id or self._resolve_default_engine()
            eng = self.get_engine(eid)
            return eid, eng

    def get_engine_ids(self) -> list[str]:
        """Return list of all registered engine IDs."""
        with self._lock:
            return list(AVAILABLE_ENGINES.keys())

    # ---- Voice management ----

    def set_voice(self, voice_id: str):
        """Set the default voice for the active engine."""
        with self._lock:
            self._active_voice = voice_id
            if self._settings_db:
                try:
                    self._settings_db.set_setting("tts_voice", voice_id)
                except Exception:
                    pass

    def get_voice(self) -> str | None:
        """Get the currently active voice ID."""
        with self._lock:
            return self._active_voice

    def set_voice_mode(self, mode: str):
        """Set voice selection mode: user_select, random, or context."""
        if mode not in (VOICE_MODE_USER_SELECT, VOICE_MODE_RANDOM, VOICE_MODE_CONTEXT):
            logger.warning(f"Invalid voice mode: {mode}")
            return
        with self._lock:
            self._voice_mode = mode
            if self._settings_db:
                try:
                    self._settings_db.set_setting("tts_voice_mode", mode)
                except Exception:
                    pass

    def get_voice_mode(self) -> str:
        """Get current voice selection mode."""
        with self._lock:
            return self._voice_mode

    def _resolve_voice(self, voice_id: str | None, trigger_type: str | None = None) -> str | None:
        """Resolve which voice to use based on mode and context.

        Note: callers must NOT hold self._lock — all sub-methods acquire it themselves.
        """
        # Explicit voice_id always wins (no lock needed)
        if voice_id:
            return voice_id

        mode = self._voice_mode

        if mode == VOICE_MODE_RANDOM:
            # Pick a random voice from the active engine
            _, eng = self.get_active_engine()
            voices = eng.get_available_voices()
            if voices:
                return random.choice(voices)
            return self._active_voice

        elif mode == VOICE_MODE_CONTEXT:
            return self._context_voice(trigger_type)

        # VOICE_MODE_USER_SELECT — use stored voice
        if self._active_voice:
            return self._active_voice
        # No voice chosen: the station announcer if this engine has it, else its first voice.
        # Not stored, so adding a clip that sorts first (or switching engine) never changes it.
        _, eng = self.get_active_engine()
        voices = eng.get_available_voices()
        if DEFAULT_ANNOUNCER_VOICE in voices:
            return DEFAULT_ANNOUNCER_VOICE
        return voices[0] if voices else None

    def _context_voice(self, trigger_type: str | None) -> str | None:
        """Map trigger context to a voice for context mode."""
        # Trigger-to-voice mapping
        TRIGGER_VOICES = {
            "mood_change": ["The Energetic Night Companion", "The Rising Heartthrob"],
            "skip_streak": ["The Trusted Morning Host", "The Southern Storyteller"],
            "wildcard": ["The Classical Connoisseur", "The International Icon"],
            "recovery": ["The Trusted Morning Host", "The Southern Storyteller"],
            "radio_intro": ["deep radio voice", "The Trusted Morning Host"],
            "song_intro": ["The Golden Age Starlet", "The Distinguished Leading Man"],
            "discovery": ["The International Icon", "The Classical Connoisseur"],
            "vibe_lock": ["The Energetic Night Companion", "The Golden Age Starlet"],
        }
        candidates = TRIGGER_VOICES.get(trigger_type, [])
        # Filter to available voices
        _, eng = self.get_active_engine()
        available = eng.get_available_voices()
        valid = [v for v in candidates if v in available]
        if valid:
            return random.choice(valid)
        # Fallback to any available voice
        if available:
            return random.choice(available)
        return self._active_voice

    def get_available_voices(self) -> list[str]:
        """Get available voices from the active engine."""
        _, eng = self.get_active_engine()
        return eng.get_available_voices()

    def get_all_voices(self) -> dict[str, list[str]]:
        """Get available voices from ALL engines, grouped by engine ID."""
        result = {}
        for engine_id in AVAILABLE_ENGINES:
            try:
                eng = self.get_engine(engine_id)
                result[engine_id] = eng.get_available_voices()
            except Exception:
                result[engine_id] = []
        return result

    def load_voice(self, voice_id: str) -> bool:
        """Load a specific voice on the active engine."""
        _, eng = self.get_active_engine()
        return eng.load_voice(voice_id)

    def voice_downloaded(self, voice_id: str) -> bool:
        """Check if a voice is downloaded on any engine."""
        for engine_id in AVAILABLE_ENGINES:
            try:
                eng = self.get_engine(engine_id)
                if hasattr(eng, "voice_downloaded") and eng.voice_downloaded(voice_id):
                    return True
            except Exception:
                continue
        return False

    def download_all_voices(self, progress_callback=None) -> dict[str, bool]:
        """Download all voice models for the Piper engine."""
        eng = self.get_engine("piper")
        if isinstance(eng, PiperTTSEngine):
            return eng.download_all_voices(progress_callback)
        return {}

    # ---- Synthesis ----

    def synthesize(self, text: str, voice_id: str | None = None, output_dir: str | None = None, song_id: int | None = None, trigger_type: str | None = None, speed: float | None = None, direction: "sd.SpeechDirection | None" = None) -> str | None:
        """Synthesize text to WAV file.
        
        Args:
            text: Text to synthesize
            voice_id: Voice to use (defaults to active voice via voice mode). 
                      Can include engine prefix: "piper:amy" or "omnivoice:The Trusted Morning Host"
            output_dir: Output directory (defaults to cache)
            song_id: Optional song ID to include in the WAV filename for cache tracking
            trigger_type: Announcement trigger type for context voice mode
            direction: Engine-neutral delivery (``announcer.speech_direction``). The
                   engine's adapter renders it; without one, a trigger still gets a
                   trigger direction. Ignored when speech direction is switched off.
            speed: Speech speed baseline (0.75-1.05). Only used by OmniVoice engine.
                   Randomized ±0.1 at synth time for natural variety.
            
        Returns:
            Path to WAV file, or None on failure
        """
        if not text or not text.strip():
            return None

        if output_dir is None:
            output_dir = TTS_CACHE_DIR
        os.makedirs(output_dir, exist_ok=True)

        # Parse voice_id for engine prefix
        engine_id = None
        if voice_id and ":" in voice_id:
            engine_id, voice_id = voice_id.split(":", 1)

        # Resolve voice via mode if no explicit voice_id
        if voice_id is None:
            voice_id = self._resolve_voice(None, trigger_type)

        active_id, active_engine = self.get_active_engine()
        if engine_id is None:
            engine_id = active_id
            eng = active_engine
        else:
            eng = self.get_engine(engine_id)

        if not eng.is_ready():
            logger.warning(f"VoiceManager: engine {engine_id} not ready, attempting fallback")
            # Auto-fallback to Piper
            if engine_id != "piper" and "piper" in AVAILABLE_ENGINES:
                engine_id = "piper"
                eng = self.get_engine("piper")
                if voice_id is None:
                    voice_id = "amy"
            else:
                return None

        # Resolve speed with default
        if speed is None:
            speed = 0.90

        # Engine-neutral direction, rendered by this engine's adapter.
        flags = sd.current_flags()
        if not flags.enabled:
            direction = sd.SpeechDirection.off()
        elif direction is None:
            direction = (sd.direction_for_announcement(trigger_type, flags=flags) if trigger_type
                         else sd.SpeechDirection(word_marks=flags.word_marks, pauses=flags.pauses))
        adapter = sd.get_adapter(engine_id)
        if direction.pace:
            speed = max(0.5, min(1.5, speed * (1 + 0.08 * direction.pace)))
        if not direction.pauses:
            text = sd.remove_pauses(text)
        if direction.pauses and not adapter.native_pauses:
            chunks = sd.split_pauses(text)
        else:
            chunks = [(text, 0)]
        rendered = [adapter.render(chunk, direction) for chunk, _ in chunks]
        if not any(piece.text.strip() for piece in rendered):
            return None

        # OmniVoice understands ".,." as a prosody pause and gets "... " up front;
        # Piper gets silence prepended to the WAV instead (after synthesis).
        if engine_id == "omnivoice":
            rendered[0].text = "... " + rendered[0].text

        # Cache key: engine + voice + speed + hash of what is sent + optional song id.
        # A neutral line without request fields keeps the plain text hash.
        if len(rendered) == 1:
            key_text = rendered[0].text
        else:
            key_text = "|".join(f"{piece.text}~{ms}" for piece, (_, ms) in zip(rendered, chunks))
        if not direction.is_neutral:
            key_text += "#" + direction.key()
        if any(piece.request for piece in rendered):
            key_text += "#" + json.dumps([piece.request for piece in rendered], sort_keys=True)
        text_hash = hashlib.md5(key_text.encode()).hexdigest()[:12]
        if song_id is not None:
            output_path = os.path.join(output_dir, f"{engine_id}_{voice_id}_{speed:.2f}_{text_hash}_song{song_id}.wav")
        else:
            output_path = os.path.join(output_dir, f"{engine_id}_{voice_id}_{speed:.2f}_{text_hash}.wav")
        plain_text = " ".join(piece.text for piece in rendered)

        # Per-path lock to prevent duplicate concurrent synthesis (same text,
        # same voice, same speed → multiple threads racing before any finishes).
        # The first thread to acquire writes the file; subsequent threads wait
        # and then return the cache hit.
        path_lock = self._get_synth_lock(output_path)
        try:
            with path_lock:
                if os.path.exists(output_path):
                    logger.info(f"TTS cache hit: {os.path.basename(output_path)}")
                    return output_path

                logger.info(f"TTS synthesize: engine={engine_id}, voice={voice_id}, text_len={len(plain_text)}, "
                            f"chunks={len(rendered)}, emotion={direction.emotion or '-'}, path={output_path}")

                def _say(piece, path):
                    if engine_id == "omnivoice":
                        return eng.synthesize(piece.text, voice_id, path, speed=speed)
                    if engine_id == "zonos3" or piece.request:
                        return eng.synthesize(piece.text, voice_id, path, speed=speed, request=piece.request or None)
                    return eng.synthesize(piece.text, voice_id, path)

                def _do_synthesize():
                    if len(rendered) == 1:
                        return _say(rendered[0], output_path)
                    parts = []
                    try:
                        for index, piece in enumerate(rendered):
                            part = f"{output_path}.part{index}.wav"
                            if not _say(piece, part):
                                return None
                            parts.append(part)
                        return _join_chunks(parts, [ms for _, ms in chunks], output_path)
                    finally:
                        for part in parts:
                            try:
                                os.remove(part)
                            except OSError:
                                pass

                timeout = eng.synthesis_timeout if engine_id in ("omnivoice", "zonos3") else 30
                ok, result_or_err = run_with_timeout(_do_synthesize, timeout=timeout * len(rendered))
                if not ok:
                    if isinstance(result_or_err, TimeoutError):
                        logger.error(f"TTS synthesis timeout ({timeout}s) for engine={engine_id}")
                    else:
                        logger.error(f"TTS synthesis error for engine={engine_id}: {result_or_err}")
                    if engine_id != "piper":
                        logger.info("VoiceManager: attempting Piper fallback")
                        return self._fallback_synthesize(plain_text, voice_id, output_dir, song_id)
                    return None
                if engine_id == "zonos3" and not result_or_err:
                    logger.info("VoiceManager: ZONOS3 returned no audio, attempting Piper fallback")
                    return self._fallback_synthesize(plain_text, voice_id, output_dir, song_id)

                # Match loudness (any engine), prepend silence for Piper (it doesn't
                # understand ".,." markers) and append silence for all engines to
                # prevent truncation.
                if result_or_err and os.path.exists(result_or_err):
                    if direction.loudness_lufs is not None:
                        _match_loudness(result_or_err, direction.loudness_lufs)
                    # OmniVoice gets "... " in its text; every other engine gets
                    # a short silence so the first word never starts abruptly.
                    if engine_id != "omnivoice":
                        _prepend_silence(result_or_err)
                    _append_silence(result_or_err)
                logger.info(f"TTS synthesize done: {'found' if result_or_err and os.path.exists(result_or_err) else 'FAILED'}")
                return result_or_err
        finally:
            with self._synth_locks_lock:
                self._synth_locks.pop(output_path, None)

    def _fallback_synthesize(self, text: str, original_voice_id: str | None, output_dir: str, song_id: int | None = None) -> str | None:
        """Fallback to Piper TTS when primary engine fails."""
        try:
            from announcer.nonverbal import strip_nonverbal_tags
            piper_eng = self.get_engine("piper")
            fallback_voice = "amy"  # Piper default voice
            text = sd.strip_marks(strip_nonverbal_tags(text))
            text = text.replace(".,.", "")
            text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            if song_id is not None:
                output_path = os.path.join(output_dir, f"piper_{fallback_voice}_{text_hash}_song{song_id}.wav")
            else:
                output_path = os.path.join(output_dir, f"piper_{fallback_voice}_{text_hash}.wav")
            if os.path.exists(output_path):
                return output_path
            logger.info(f"VoiceManager: Piper fallback synthesizing with voice={fallback_voice}")
            result = piper_eng.synthesize(text, fallback_voice, output_path)
            if result and os.path.exists(result):
                _prepend_silence(result)
                _append_silence(result)
            return result
        except Exception as e:
            logger.error(f"VoiceManager: Piper fallback failed: {e}")
            return None

    def get_status(self) -> dict:
        """Return diagnostic status."""
        _, eng = self.get_active_engine()
        gpu_available = None
        omnivoice_ready = False
        if "omnivoice" in AVAILABLE_ENGINES:
            try:
                from announcer.omnivoice_engine import OmniVoiceTTSEngine
                test = OmniVoiceTTSEngine()
                omnivoice_ready = test.is_ready()
                gpu_available = omnivoice_ready
            except Exception:
                pass
        zonos3_ready = False
        if "zonos3" in AVAILABLE_ENGINES:
            try:
                zonos3_ready = self.get_engine("zonos3").is_ready()  # health is cached briefly
            except Exception:
                pass
        return {
            "zonos3_ready": zonos3_ready,
            "active_engine": self._active_engine_id or self._resolve_default_engine(),
            "active_voice": self._active_voice or "",
            "voice_mode": self._voice_mode,
            "available_voices": self.get_available_voices(),
            "all_voices": self.get_all_voices(),
            "engine_ready": eng.is_ready(),
            "engine_ids": self.get_engine_ids(),
            "gpu_available": gpu_available,
            "omnivoice_ready": omnivoice_ready,
        }


# Global singleton
_voice_manager: VoiceManager | None = None
_vm_lock = threading.Lock()


def get_voice_manager() -> VoiceManager:
    """Get the global VoiceManager singleton."""
    global _voice_manager
    with _vm_lock:
        if _voice_manager is None:
            _voice_manager = VoiceManager()
        return _voice_manager


def set_tts_engine(engine_id: str, voice_id: str | None = None):
    """Convenience: switch TTS engine at runtime."""
    vm = get_voice_manager()
    vm.set_engine(engine_id, voice_id)


def set_tts_voice(voice_id: str):
    """Convenience: switch default voice."""
    vm = get_voice_manager()
    vm.set_voice(voice_id)
