"""Abstract base class for TTS engines + Piper implementation.

To add a new TTS engine:
1. Subclass TTSEngine
2. Implement synthesize(text, voice_id, output_path) -> str | None
3. Register it in VoiceManager
"""

import abc
import hashlib
import logging
import os
import wave
import math
import struct
import threading
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class TTSEngine(abc.ABC):
    """Abstract base for a TTS engine.
    
    Each engine can support one or more voice IDs.
    """

    @abc.abstractmethod
    def synthesize(self, text: str, voice_id: str, output_path: str) -> str | None:
        """Synthesize text with the given voice.
        
        Args:
            text: Text to speak
            voice_id: Voice identifier (e.g. "amy", "ryan", "qwen3")
            output_path: Where to write the WAV file
            
        Returns:
            output_path on success, None on failure
        """
        ...

    @abc.abstractmethod
    def get_available_voices(self) -> list[str]:
        """Return list of voice IDs this engine supports."""
        ...

    @abc.abstractmethod
    def is_ready(self) -> bool:
        """Return True if the engine is loaded and usable."""
        ...

    @abc.abstractmethod
    def load_voice(self, voice_id: str) -> bool:
        """Load/prepare a voice. Returns True on success."""
        ...


class PiperTTSEngine(TTSEngine):
    """Piper TTS engine - supports multiple ONNX voice models."""

    ENGINE_ID = "piper"

    # Default voice IDs this engine provides
    VOICE_IDS = {
        "amy": "en_US-amy-medium",
        "ryan": "en_US-ryan-medium",
        "kusal": "en_US-kusal-medium",
        "arctic": "en_US-arctic-medium",
    }

    # HuggingFace URLs for download
    VOICE_URLS = {
        "amy": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium",
        "ryan": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/medium",
        "kusal": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/kusal/medium",
        "arctic": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/arctic/medium",
    }

    def __init__(self, voice_dir: str | None = None):
        self._voice_dir = voice_dir or os.path.join(config.DATA_DIR, "piper_voice")
        self._voices: dict[str, object] = {}  # voice_id -> PiperVoice
        self._lock = threading.Lock()
        os.makedirs(self._voice_dir, exist_ok=True)

    def _voice_path(self, voice_id: str) -> str:
        """Get filesystem path for a Piper voice model."""
        piper_name = self.VOICE_IDS.get(voice_id, voice_id)
        return os.path.join(self._voice_dir, f"{piper_name}.onnx")

    def _voice_json_path(self, voice_id: str) -> str:
        piper_name = self.VOICE_IDS.get(voice_id, voice_id)
        return os.path.join(self._voice_dir, f"{piper_name}.onnx.json")

    def is_ready(self) -> bool:
        return True

    def get_available_voices(self) -> list[str]:
        ready = []
        for vid in self.VOICE_IDS:
            if os.path.exists(self._voice_path(vid)):
                ready.append(vid)
        return ready

    def voice_downloaded(self, voice_id: str) -> bool:
        return (
            os.path.exists(self._voice_path(voice_id))
            and os.path.exists(self._voice_json_path(voice_id))
        )

    def load_voice(self, voice_id: str) -> bool:
        """Load a Piper voice into cache."""
        if voice_id in self._voices:
            return True
        if not self.voice_downloaded(voice_id):
            ok = self._download_voice(voice_id)
            if not ok:
                return False
        try:
            import piper
            # Use thread with timeout to prevent ONNX model loading from blocking forever
            result = [False]

            def _do_load():
                try:
                    with self._lock:
                        if voice_id not in self._voices:
                            voice = piper.PiperVoice.load(self._voice_path(voice_id))
                            self._voices[voice_id] = voice
                    result[0] = True
                except Exception as e:
                    print(f"PiperTTSEngine: failed to load voice {voice_id}: {e}")

            t = threading.Thread(target=_do_load, daemon=True)
            t.start()
            t.join(timeout=15)
            if t.is_alive():
                logger.warning(f"PiperTTSEngine: load_voice timed out for {voice_id}")
                return False
            return result[0]
        except Exception as e:
            print(f"PiperTTSEngine: load_voice exception: {e}")
            return False

    def _download_voice(self, voice_id: str) -> bool:
        """Download a Piper voice model from HuggingFace."""
        import requests
        base_url = self.VOICE_URLS.get(voice_id)
        if not base_url:
            print(f"PiperTTSEngine: unknown voice {voice_id}")
            return False

        piper_name = self.VOICE_IDS.get(voice_id, voice_id)
        onnx_url = f"{base_url}/{piper_name}.onnx"
        json_url = f"{base_url}/{piper_name}.onnx.json"

        print(f"Downloading Piper voice: {piper_name}")
        try:
            for url, path in [(onnx_url, self._voice_path(voice_id)),
                              (json_url, self._voice_json_path(voice_id))]:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)
            return True
        except Exception as e:
            print(f"Failed to download voice {voice_id}: {e}")
            return False

    def download_all_voices(self, progress_callback=None) -> dict[str, bool]:
        """Download all voice models. Returns {voice_id: success}."""
        results = {}
        for vid in self.VOICE_IDS:
            if self.voice_downloaded(vid):
                results[vid] = True
                continue
            results[vid] = self._download_voice(vid)
            if progress_callback:
                progress_callback(vid, results[vid])
        return results

    def synthesize(self, text: str, voice_id: str, output_path: str) -> str | None:
        """Synthesize text with Piper voice."""
        logger.info(f"PiperTTSEngine.synthesize START: voice={voice_id}, text_len={len(text)}")
        # Ensure voice is loaded
        if voice_id not in self._voices:
            logger.info(f"PiperTTSEngine: loading voice {voice_id}")
            ok = self.load_voice(voice_id)
            if not ok:
                logger.warning(f"PiperTTSEngine: voice load failed, using fallback")
                return self._fallback_synth(text, output_path)

        voice = self._voices.get(voice_id)
        if not voice:
            logger.warning(f"PiperTTSEngine: voice not in cache, using fallback")
            return self._fallback_synth(text, output_path)

        try:
            import piper
            logger.info(f"PiperTTSEngine: calling voice.synthesize_wav...")
            # Run synthesis in a thread with timeout to prevent indefinite blocking
            result_path = [None]
            error = [None]

            def _do_synthesize():
                try:
                    with wave.open(output_path, "wb") as wav_file:
                        voice.synthesize_wav(text, wav_file)
                    result_path[0] = output_path
                except Exception as e:
                    error[0] = e

            t = threading.Thread(target=_do_synthesize, daemon=True)
            t.start()
            t.join(timeout=10)
            if t.is_alive():
                logger.warning(f"PiperTTSEngine: synthesize timed out after 10s, falling back")
                return self._fallback_synth(text, output_path)
            if error[0]:
                logger.error(f"PiperTTSEngine: synthesize failed: {error[0]}")
                return self._fallback_synth(text, output_path)
            logger.info(f"PiperTTSEngine: synthesize SUCCESS -> {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"PiperTTSEngine: synthesize failed: {e}")
            return self._fallback_synth(text, output_path)

    def _fallback_synth(self, text: str, output_path: str) -> str | None:
        """Fallback tone generation when voice is unavailable.
        
        Produces a multi-tone chime with voice-like amplitude envelope
        so it's clearly audible as a "TTS placeholder" sound.
        """
        try:
            duration_secs = max(1.0, len(text) * 0.08)
            sample_rate = 44100
            num_samples = int(duration_secs * sample_rate)
            samples = []
            for i in range(num_samples):
                ti = i / sample_rate
                # Attack-sustain-decay envelope
                attack = min(1.0, ti / 0.05)
                if ti > duration_secs - 0.1:
                    decay = max(0.0, (duration_secs - ti) / 0.1)
                else:
                    decay = 1.0
                env = attack * decay * 0.5
                # Multi-tone: fundamental at 440Hz (A4) + harmonics
                fund = 440.0
                tone = (
                    math.sin(2 * math.pi * fund * ti) * 0.4 +
                    math.sin(2 * math.pi * fund * 2 * ti) * 0.2 +
                    math.sin(2 * math.pi * fund * 3 * ti) * 0.1 +
                    math.sin(2 * math.pi * fund * 4 * ti) * 0.05
                )
                # Slow tremolo to sound more natural
                trem = 1.0 + 0.1 * math.sin(2 * math.pi * 4 * ti)
                sample = env * tone * trem
                sample = max(-1.0, min(1.0, sample))
                samples.append(int(sample * 32767))
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(struct.pack(f"{len(samples)}h", *samples))
            return output_path
        except Exception as e:
            print(f"Fallback synth failed: {e}")
            return None


def _try_import_omnivoice():
    """Try to import OmniVoice engine. Returns class or None."""
    try:
        from announcer.omnivoice_engine import OmniVoiceTTSEngine
        return OmniVoiceTTSEngine
    except Exception as e:
        logger.debug(f"OmniVoice engine not available: {e}")
        return None


# Built-in registry of engine classes
AVAILABLE_ENGINES: dict[str, type] = {
    "piper": PiperTTSEngine,
}

# Try to register OmniVoice
_omnivoice_cls = _try_import_omnivoice()
if _omnivoice_cls is not None:
    AVAILABLE_ENGINES["omnivoice"] = _omnivoice_cls
