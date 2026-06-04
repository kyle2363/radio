"""MuQ-MuLan audio embedding engine.

Wraps the OpenMuQ/MuQ-MuLan model for audio and text embedding.
Model is loaded lazily on first use to avoid import-time GPU init.

Requires: torch, muq (install separately — see requirements.txt)
"""

import logging
import os
import threading

import config

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()
_device = None


def _check_deps():
    """Check if torch and muq are importable."""
    try:
        import torch
        from muq import MuQMuLan
        return True
    except ImportError:
        return False


def _get_device():
    """Determine the best available device."""
    global _device
    if _device is not None:
        return _device
    try:
        import torch
        _device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        _device = "cpu"
    return _device


def _load_model():
    """Load MuQ-MuLan model (lazy, thread-safe)."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        # Double-check after acquiring lock
        if _model is not None:
            return _model

        if not _check_deps():
            raise RuntimeError(
                "MuQ-MuLan requires torch and muq packages. "
                "Install with: pip install torch muq"
            )

        import torch
        from muq import MuQMuLan

        device = _get_device()
        logger.info("Loading MuQ-MuLan model on %s (this may take a moment)...", device)

        try:
            _model = MuQMuLan.from_pretrained(config.MUQ_MODEL_NAME)
            _model = _model.to(device).eval()
            logger.info("MuQ-MuLan loaded successfully on %s", device)
        except Exception as e:
            _model = None
            raise RuntimeError(f"Failed to load MuQ-MuLan: {e}") from e

    return _model


def is_available():
    """Check if MuQ-MuLan can be used (deps installed, model loadable)."""
    if not config.MUQ_ENABLED:
        return False
    return _check_deps()


def get_embedding_dimension():
    """Return the embedding dimension of MuQ-MuLan (512)."""
    return 512


def embed_audio(filepath: str) -> list:
    """Embed an audio file using MuQ-MuLan.

    Args:
        filepath: Path to an audio file (MP3, WAV, etc.)

    Returns:
        list of floats (512-dim embedding vector)

    Raises:
        RuntimeError: If model not available or embedding fails
    """
    import torch
    import librosa

    model = _load_model()
    device = _get_device()

    try:
        wav, sr = librosa.load(filepath, sr=config.MUQ_SAMPLE_RATE)
    except Exception as e:
        raise RuntimeError(f"Could not load audio {filepath}: {e}") from e

    wavs = torch.tensor(wav).unsqueeze(0).to(device)

    with _inference_lock:
        with torch.no_grad():
            audio_embeds = model(wavs=wavs)

    embedding = audio_embeds.cpu().numpy().flatten().tolist()
    return embedding


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed text descriptions using MuQ-MuLan's text encoder.

    Supports English and Chinese text.

    Args:
        texts: list of text strings to embed

    Returns:
        list of embedding vectors (one per input text)
    """
    import torch

    if not texts:
        return []

    model = _load_model()
    device = _get_device()

    with _inference_lock:
        with torch.no_grad():
            text_embeds = model(texts=texts)

    return text_embeds.cpu().numpy().tolist()


def calc_similarity(audio_embedding: list, text_embeddings: list[list[float]]) -> list[float]:
    """Compute dot-product similarity between an audio embedding and text embeddings.

    Args:
        audio_embedding: single audio embedding vector
        text_embeddings: list of text embedding vectors

    Returns:
        list of similarity scores
    """
    import torch

    audio_t = torch.tensor(audio_embedding)
    text_t = torch.tensor(text_embeddings)

    # Dot product similarity (matching MuQ-MuLan's calc_similarity)
    sims = torch.mm(audio_t.unsqueeze(0), text_t.t()).squeeze(0)
    return sims.cpu().tolist()
