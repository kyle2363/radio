"""Read existing embeddings without depending on playback or creating collections."""
import logging

import config
from db.chroma import get_chroma_client

logger = logging.getLogger(__name__)


def fetch_song_embeddings(song_id, *, client_factory=None):
    """Return (text, audio); failure of either collection leaves the other usable."""
    factory = client_factory or get_chroma_client

    def fetch(name):
        try:
            result = factory().get_collection(name).get(
                ids=[str(song_id)], include=["embeddings"],
            )
            embeddings = result.get("embeddings")
            return embeddings[0] if embeddings is not None and len(embeddings) else None
        except Exception:
            logger.warning("Embedding lookup failed: song=%s collection=%s", song_id, name, exc_info=True)
            return None

    text = fetch("songs")
    audio = fetch(config.MUQ_AUDIO_COLLECTION) if config.MUQ_ENABLED else None
    return text, audio
