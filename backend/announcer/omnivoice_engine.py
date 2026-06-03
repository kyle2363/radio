"""OmniVoice TTS engine — voice cloning via reference audio.

Uses the k2-fsa/OmniVoice model from HuggingFace for high-quality
voice-cloned TTS. Requires CUDA GPU for reasonable performance.

Reference audio files live in backend/announcer/voices/.
"""

import json
import logging
import os
import random
import threading
import time
from pathlib import Path

from announcer.tts_engine import run_with_timeout

import config

logger = logging.getLogger(__name__)

# Module-level GPU detection cache — shared across all engine instances
_GLOBAL_GPU_AVAILABLE: bool | None = None
_GLOBAL_GPU_LOG_ONCE = False


def _detect_gpu_once() -> bool:
    """Detect CUDA GPU availability once at module level.
    
    All OmniVoiceTTSEngine instances share this result so the
    CUDA availability log is emitted exactly once per process.
    """
    global _GLOBAL_GPU_AVAILABLE, _GLOBAL_GPU_LOG_ONCE
    if _GLOBAL_GPU_AVAILABLE is not None:
        return _GLOBAL_GPU_AVAILABLE
    try:
        import torch
        _GLOBAL_GPU_AVAILABLE = torch.cuda.is_available()
        if _GLOBAL_GPU_AVAILABLE and not _GLOBAL_GPU_LOG_ONCE:
            _GLOBAL_GPU_LOG_ONCE = True
            device_name = torch.cuda.get_device_name(0)
            logger.info(f"OmniVoice: CUDA available — {device_name}")
        elif not _GLOBAL_GPU_AVAILABLE:
            logger.warning("OmniVoice: CUDA not available, will fall back to Piper")
    except ImportError:
        _GLOBAL_GPU_AVAILABLE = False
        logger.warning("OmniVoice: torch not installed")
    except Exception as e:
        _GLOBAL_GPU_AVAILABLE = False
        logger.error(f"OmniVoice: GPU detection failed: {e}")
    return _GLOBAL_GPU_AVAILABLE


# Directory containing reference audio MP3s
VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

# Path to transcripts JSON (maps voice filenames to their reference text)
TRANSCRIPTS_PATH = os.path.join(VOICES_DIR, "transcripts.json")


def _load_transcripts() -> dict[str, str]:
    """Load voice transcript mappings from JSON file.

    Returns dict mapping voice_id (stem of MP3 filename) to reference text.
    """
    if not os.path.exists(TRANSCRIPTS_PATH):
        logger.warning(f"OmniVoice: transcripts file not found at {TRANSCRIPTS_PATH}")
        return {}
    try:
        with open(TRANSCRIPTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"OmniVoice: failed to load transcripts: {e}")
        return {}


def _list_voice_mp3s() -> dict[str, str]:
    """Scan voices/ directory and return {voice_id: mp3_path} for all MP3 files.

    Scans the main voices/ directory. All files there are already truncated
    to 12s by the processing script.
    """
    voices = {}
    if not os.path.isdir(VOICES_DIR):
        return voices
    for fname in os.listdir(VOICES_DIR):
        if fname.lower().endswith(".mp3"):
            voice_id = os.path.splitext(fname)[0]
            voices[voice_id] = os.path.join(VOICES_DIR, fname)
    return voices


class OmniVoiceTTSEngine:
    """OmniVoice TTS engine — voice cloning with reference audio.

    Voice IDs correspond to MP3 filenames in the voices/ directory.
    Each voice requires a reference transcript entry in transcripts.json.
    """

    ENGINE_ID = "omnivoice"
    MODEL_ID = "k2-fsa/OmniVoice"

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        self._device = None
        self._dtype = None
        self._voices_dir = VOICES_DIR
        self._transcripts: dict[str, str] = {}
        self._voice_mp3s: dict[str, str] = {}
        self._gpu_available: bool | None = None  # None = not checked yet
        self._load_lock = threading.Lock()

    def _detect_gpu(self) -> bool:
        """Check if CUDA GPU is available.

        Delegates to module-level _detect_gpu_once() which caches the
        result and logs the GPU message exactly once per process.
        """
        if self._gpu_available is not None:
            return self._gpu_available
        self._gpu_available = _detect_gpu_once()
        return self._gpu_available

    def _load_model(self) -> bool:
        """Lazy-load the OmniVoice model. Thread-safe, loads once."""
        if self._model is not None:
            return True
        with self._load_lock:
            if self._model is not None:
                return True
            if not self._detect_gpu():
                return False
            try:
                import torch
                from omnivoice import OmniVoice

                device = getattr(config, "OMNIVOICE_DEVICE", "cuda:0")
                dtype_str = getattr(config, "OMNIVOICE_DTYPE", "float16")
                dtype = torch.float16 if dtype_str == "float16" else torch.float32

                logger.info(f"OmniVoice: loading model {self.MODEL_ID} on {device} ({dtype_str})...")
                start = time.time()
                self._model = OmniVoice.from_pretrained(
                    self.MODEL_ID,
                    device_map=device,
                    dtype=dtype,
                )
                self._device = device
                self._dtype = dtype
                elapsed = time.time() - start
                logger.info(f"OmniVoice: model loaded in {elapsed:.1f}s")
                return True
            except ImportError as e:
                logger.error(f"OmniVoice: dependency not installed: {e}")
                return False
            except Exception as e:
                logger.error(f"OmniVoice: model load failed: {e}")
                return False

    def is_ready(self) -> bool:
        """True if GPU is available and model can be loaded."""
        return self._detect_gpu()

    def get_available_voices(self) -> list[str]:
        """Return list of voice IDs (from MP3 filenames in voices/)."""
        if not self._voice_mp3s:
            self._voice_mp3s = _list_voice_mp3s()
        return list(self._voice_mp3s.keys())

    def load_voice(self, voice_id: str) -> bool:
        """No-op for OmniVoice — voices are reference files, not model weights."""
        return voice_id in self.get_available_voices()

    def voice_downloaded(self, voice_id: str) -> bool:
        """Check if the reference audio MP3 exists for this voice."""
        if not self._voice_mp3s:
            self._voice_mp3s = _list_voice_mp3s()
        return voice_id in self._voice_mp3s

    def _ensure_transcripts(self):
        """Load transcripts on first use."""
        if not self._transcripts:
            self._transcripts = _load_transcripts()

    def synthesize(self, text: str, voice_id: str, output_path: str, speed: float = 0.90) -> str | None:
        """Synthesize text using voice cloning with reference audio.

        Args:
            text: Text to speak
            voice_id: Voice identifier (MP3 filename stem, e.g. "The Trusted Morning Host")
            output_path: Where to write the WAV file
            speed: Speech speed baseline from persona (0.75-1.05). Randomized ±0.1 at synth time.

        Returns:
            output_path on success, None on failure
        """
        # Preprocess text: replace periods with .,. for better pausing
        text = text.replace(".", ".,.")
        # Collapse any stacked .,. markers (from existing .,. in text) back to single
        while ".,.," in text:
            text = text.replace(".,.,", ".,.")

        # Randomize speed around the persona baseline
        actual_speed = speed + random.uniform(-0.1, 0.1)
        actual_speed = max(0.5, min(1.5, actual_speed))

        logger.info(f"OmniVoiceTTSEngine.synthesize: voice={voice_id}, text_len={len(text)}, speed={actual_speed:.2f}")

        # Load model if not already loaded
        if not self._load_model():
            logger.warning("OmniVoice: model not available, cannot synthesize")
            return None

        # Resolve reference audio
        if not self._voice_mp3s:
            self._voice_mp3s = _list_voice_mp3s()
        ref_audio = self._voice_mp3s.get(voice_id)
        if not ref_audio or not os.path.exists(ref_audio):
            logger.error(f"OmniVoice: reference audio not found for voice '{voice_id}'")
            return None

        # Resolve reference text (transcript)
        self._ensure_transcripts()
        ref_text = self._transcripts.get(voice_id)
        if not ref_text:
            logger.warning(f"OmniVoice: no transcript for voice '{voice_id}', using voice description as fallback")
            # Fallback: use the voice prompt descriptions
            ref_text = self._get_fallback_ref_text(voice_id)
            if not ref_text:
                logger.error(f"OmniVoice: no reference text available for voice '{voice_id}'")
                return None

        try:
            import soundfile as sf

            logger.info(f"OmniVoice: generating speech (ref={os.path.basename(ref_audio)}, text_len={len(text)})")
            start = time.time()

            # Run inference in a thread with timeout
            def _do_generate():
                return self._model.generate(
                    text=text,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    speed=actual_speed,
                )

            ok, result_or_err = run_with_timeout(_do_generate, timeout=30)
            if not ok:
                if isinstance(result_or_err, TimeoutError):
                    logger.error("OmniVoice: synthesis timed out after 30s")
                else:
                    logger.error(f"OmniVoice: synthesis failed: {result_or_err}")
                return None

            # result_or_err is a list of np.ndarray at 24kHz
            audio_array = result_or_err
            if audio_array is None:
                logger.error("OmniVoice: synthesis returned None")
                return None
            sf.write(output_path, audio_array[0], 24000)
            elapsed = time.time() - start
            logger.info(f"OmniVoice: synthesis done in {elapsed:.1f}s -> {output_path}")
            return output_path

        except ImportError as e:
            logger.error(f"OmniVoice: missing dependency (soundfile): {e}")
            return None
        except Exception as e:
            logger.error(f"OmniVoice: synthesis error: {e}")
            return None

    def _get_fallback_ref_text(self, voice_id: str) -> str | None:
        """Get fallback reference text from voice descriptions when no transcript exists."""
        descriptions = {
            "deep radio voice": "A standard typical radio host voice.",
            "The Trusted Morning Host": "A warm, confident male radio host in his late 30s with a smooth baritone voice and neutral American accent. Speaking at a conversational pace with perfect audio quality.",
            "The Energetic Night Companion": "An energetic female radio personality in her early 30s with a bright, clear voice and slight midwestern accent. High-quality studio recording. She speaks at a quick, lively pace with infectious enthusiasm.",
            "The Southern Storyteller": "A seasoned male radio veteran in his late 50s with a deep, resonant voice and a thick, warm Southern drawl. Studio-quality audio. He speaks at a deliberate, unhurried pace that commands attention.",
            "The Classical Connoisseur": "A sophisticated female radio host in her mid-40s with a velvety smooth voice and crisp British accent. Perfect audio quality with exceptional clarity. She speaks at a measured, elegant pace with impeccable diction.",
            "The Golden Age Starlet": "A glamorous female starlet in her late 20s to early 30s with a breathy, velvety voice that exudes old Hollywood charm. She speaks with a mid-Atlantic accent, blending American and British elements with refined enunciation.",
            "The Rising Heartthrob": "A young male actor in his early 20s with a modern Hollywood heartthrob quality. His voice is smooth and charming with a slight rasp that adds character. He speaks with a standard American accent, casual and approachable.",
            "The International Icon": "An elegant female starlet in her 40s with a sophisticated, smoky voice that carries the weight of experience. She has a slight French accent that colors her English beautifully, adding an air of international mystique.",
            "The Distinguished Leading Man": "A charismatic male leading man in his 50s with a deep, resonant voice that commands attention. He speaks with a cultured British accent, refined but not pretentious. His delivery is theatrical yet genuine.",
        }
        return descriptions.get(voice_id)
