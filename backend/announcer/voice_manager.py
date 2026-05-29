"""Multi-voice TTS manager with dynamic engine switching.

Manages which TTS engine is active and allows runtime engine swaps
(e.g. Piper -> OmniVoice) without refactoring callers.
"""

import hashlib
import logging
import os
import random
import threading
import wave
from announcer.tts_engine import TTSEngine, AVAILABLE_ENGINES, PiperTTSEngine

logger = logging.getLogger(__name__)

# Voice selection modes
VOICE_MODE_USER_SELECT = "user_select"  # Use the user's chosen voice for all announcements
VOICE_MODE_RANDOM = "random"            # Pick a random voice per synthesis call
VOICE_MODE_CONTEXT = "context"          # Map trigger context to voice

TTS_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tts_cache")
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


class VoiceManager:
    """Manages TTS engines and voices with runtime switching.

    Supports multiple engines (OmniVoice, Piper) with automatic fallback.
    Voice selection modes: user_select (default), random, context.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._engines: dict[str, TTSEngine | "OmniVoiceTTSEngine"] = {}
        self._active_engine_id: str | None = None
        self._active_voice: str | None = None
        self._settings_db = None  # lazy reference to database for persisted engine choice
        self._voice_mode: str = VOICE_MODE_USER_SELECT

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
        """Determine the best default engine based on available hardware."""
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

        Args:
            voice_id: Explicit voice override (bypasses mode)
            trigger_type: Announcement trigger type for context mode
        """
        with self._lock:
            # Explicit voice_id always wins
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
            # Fallback: pick first available voice from active engine
            _, eng = self.get_active_engine()
            voices = eng.get_available_voices()
            if voices:
                with self._lock:
                    self._active_voice = voices[0]
                return self._active_voice
            return None

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

    def synthesize(self, text: str, voice_id: str | None = None, output_dir: str | None = None, song_id: int | None = None, trigger_type: str | None = None, speed: float | None = None) -> str | None:
        """Synthesize text to WAV file.
        
        Args:
            text: Text to synthesize
            voice_id: Voice to use (defaults to active voice via voice mode). 
                      Can include engine prefix: "piper:amy" or "omnivoice:The Trusted Morning Host"
            output_dir: Output directory (defaults to cache)
            song_id: Optional song ID to include in the WAV filename for cache tracking
            trigger_type: Announcement trigger type for context voice mode
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

        # Cache key includes engine + voice + speed + text hash + optional song_id
        # Strip non-verbal tags for Piper (it would read them as literal text)
        if engine_id == "piper":
            from announcer.nonverbal import strip_nonverbal_tags
            text = strip_nonverbal_tags(text)
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        if song_id is not None:
            output_path = os.path.join(output_dir, f"{engine_id}_{voice_id}_{speed:.2f}_{text_hash}_song{song_id}.wav")
        else:
            output_path = os.path.join(output_dir, f"{engine_id}_{voice_id}_{speed:.2f}_{text_hash}.wav")

        if os.path.exists(output_path):
            logger.info(f"TTS cache hit: {os.path.basename(output_path)}")
            return output_path

        logger.info(f"TTS synthesize: engine={engine_id}, voice={voice_id}, text_len={len(text)}, path={output_path}")
        # Wrap entire synthesis in thread with timeout as safety net
        result_val = [None]
        err_val = [None]

        def _do_synthesize():
            try:
                if engine_id == "omnivoice":
                    result_val[0] = eng.synthesize(text, voice_id, output_path, speed=speed)
                else:
                    result_val[0] = eng.synthesize(text, voice_id, output_path)
            except Exception as e:
                err_val[0] = e

        t = threading.Thread(target=_do_synthesize, daemon=True)
        t.start()
        t.join(timeout=30)
        if t.is_alive():
            logger.warning(f"VoiceManager: synthesize timed out after 30s")
            # Auto-fallback to Piper on timeout
            if engine_id != "piper":
                logger.info("VoiceManager: attempting Piper fallback after timeout")
                return self._fallback_synthesize(text, voice_id, output_dir, song_id)
            return None
        if err_val[0]:
            logger.error(f"VoiceManager: synthesize failed: {err_val[0]}")
            # Auto-fallback to Piper on error
            if engine_id != "piper":
                logger.info("VoiceManager: attempting Piper fallback after error")
                return self._fallback_synthesize(text, voice_id, output_dir, song_id)
            return None
        # Append silence padding to prevent truncation in chained playback
        if result_val[0] and os.path.exists(result_val[0]):
            _append_silence(result_val[0])
        logger.info(f"TTS synthesize done: {'found' if result_val[0] and os.path.exists(result_val[0]) else 'FAILED'}")
        return result_val[0]

    def _fallback_synthesize(self, text: str, original_voice_id: str | None, output_dir: str, song_id: int | None = None) -> str | None:
        """Fallback to Piper TTS when primary engine fails."""
        try:
            from announcer.nonverbal import strip_nonverbal_tags
            piper_eng = self.get_engine("piper")
            fallback_voice = "amy"  # Piper default voice
            text = strip_nonverbal_tags(text)
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
        return {
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
