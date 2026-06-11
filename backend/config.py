import os

# Disable ChromaDB telemetry before any chromadb import
os.environ["ANONYMIZED_TELEMETRY"] = "False"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MUSIC_DIR = os.path.join(BASE_DIR, "audio_files")
DB_PATH = os.path.join(DATA_DIR, "radio.db")
CHROMA_PATH = os.path.join(DATA_DIR, "chroma")
ART_DIR = os.path.join(DATA_DIR, "art")
ACESTEP_JSON_DIR = os.path.join(BASE_DIR, "docs", "understand")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://192.168.1.20:5002")
OLLAMA_EMBED_MODEL = "qwen3-embedding:0.6b"
OLLAMA_CHAT_MODEL = "qwen3.5:9b"
TTS_VOICE = "en_US-amy-medium"

# Master TTS toggle — set False to completely disable TTS announcements
TTS_ENABLED = True

# OmniVoice TTS settings (voice cloning engine)
OMNIVOICE_MODEL = "k2-fsa/OmniVoice"
OMNIVOICE_DEVICE = os.environ.get("OMNIVOICE_DEVICE", "cuda:0")
OMNIVOICE_DTYPE = os.environ.get("OMNIVOICE_DTYPE", "float16")

# Lyrics truncation lengths (word counts for different contexts)
LYRICS_EMBED_WORDS = 150       # Words in embedding text (reduced from 300 to make room for folder/collection)
LYRICS_RERANKER_WORDS = 40   # Words in reranker candidate preview
LYRICS_CURRENT_WORDS = 60    # Words for "current song" in reranker prompt
LYRICS_TALK_WORDS = 75       # Words in talk segment prompts
LYRICS_TAG_WORDS = 200       # Words for LLM tag generation
LYRICS_DISPLAY_WORDS = None  # None = full lyrics in UI, no truncation

# Embedding text fields (what goes into the embedding)
AI_DESCRIPTION_MAX_CHARS = 850  # Max chars of ai_description in embedding text
EMBED_INCLUDE_FOLDER = True     # Include subfolder in embedding text
EMBED_INCLUDE_COLLECTIONS = True  # Include collection names in embedding text

# Radio Talk Show defaults (overridden by DB settings)
TALK_SEGMENTS_ENABLED = False
TALK_CALLER_ENABLED = True
TALK_CALLER_MODE = "influences_queue"  # or "narrative_only"
TALK_MONOLOGUE_ENABLED = True
TALK_GUEST_TAKEOVER_ENABLED = True
TALK_FREQUENCY = 4        # songs between talk segments
TALK_GUEST_SONGS_PER_SET = 4
TALK_VOLUME = 100  # percent of main volume for talk segments

# Talk show debug logging (set True to log OmniVoice inputs + talk segment prompts/responses)
TALK_SHOW_DEBUG = True
TALK_DEBUG_LOG_PATH = os.path.join(BASE_DIR, "data", "talk_show_debug.log")

# Song-end debug log
LOG_PATH = os.path.join(BASE_DIR, "data", "playback.log")
API_PORT = 8765
N_CLUSTERS = 21

# VLC stderr suppression (set True to hide libvlc init warnings, False to debug VLC issues)
VLC_SUPPRESS_STDERR = False

# Ollama chat options (passed via ollama.Client options= parameter)
# think=False must be passed as a separate keyword arg to ollama.Client.chat()
OLLAMA_CHAT_OPTIONS = {
    "temperature": 0.3,
    "top_k": 50,
    "top_p": 0.9,
    "num_ctx": 4096,
    "num_predict": -1,  # -1 = unlimited
}

# Timeout for LLM calls (seconds) — prevents hangs from blocking queue rebuild
LLM_CHAT_TIMEOUT = 45
LLM_EMBED_TIMEOUT = 30

# --- Provider selection ---
# Which provider to use for chat and embedding.
# Options: "ollama" (force), "openai" (force), "auto" (try first listed, fall back to second)
CHAT_PROVIDER = os.environ.get("CHAT_PROVIDER", "openai")
EMBED_PROVIDER = os.environ.get("EMBED_PROVIDER", "openai")

# OpenAI-compatible server URL (used when provider is "openai" or "auto" with ollama fallback)
# llama.cpp server: http://127.0.0.1:8081/v1 (chat) and http://127.0.0.1:8082/v1 (embed)
# Can point to a single server that handles both, or use separate URLs per purpose.
OPENAI_BASE_URL_CHAT = os.environ.get("OPENAI_BASE_URL_CHAT", "http://127.0.0.1:8081/v1")
OPENAI_BASE_URL_EMBED = os.environ.get("OPENAI_BASE_URL_EMBED", "http://127.0.0.1:8082/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Model names for OpenAI-compatible endpoints (used when provider forces "openai")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "llama3.2:3b")
OPENAI_EMBED_MODEL = os.environ.get("OPENAI_EMBED_MODEL", "qwen3-embedding:0.6b")

# TTS cache directory — shared by voice_manager.py, tts.py, and main.py
TTS_CACHE_DIR = os.path.join(DATA_DIR, "tts_cache")

# Lyrics timing directory — per-song JSON files with word/line timestamps from WhisperX
LYRICS_TIMING_DIR = os.path.join(DATA_DIR, "lyrics_timing")

# --- WhisperX forced alignment tuning ---
# Beam search: keep top N candidate sequences (higher = better accuracy, slower)
WHISPERX_BEAM_SIZE = 5
# VAD onset: voice activity detection start threshold (lower = more sensitive to vocal onset)
WHISPERX_VAD_ONSET = 0.15
# VAD offset: voice activity detection end threshold (lower = more patient about vocal endings)
WHISPERX_VAD_OFFSET = 0.05
# Patience: beam search patience factor (higher = explore more candidates before deciding)
WHISPERX_PATIENCE = 2.0
# No-speech threshold: segments below this are treated as silence (lower = more aggressive filtering)
WHISPERX_NO_SPEECH_THRESHOLD = 0.6
# Compression ratio threshold: segments above this ratio are flagged as repetition/hallucination
WHISPERX_COMPRESSION_RATIO_THRESHOLD = 2.4
# Vocal separation before WhisperX (Demucs via audio-separator) — helps with lyrics alignment
WHISPERX_SEPARATE_VOCALS = True
WHISPERX_SEPARATOR_MODEL = "UVR-MDX-NET-Voc_FT.onnx"
WHISPERX_KEEP_SEPARATED = False          # delete temp WAV after processing
WHISPERX_SMART_SELECT = True             # run both separated & original, pick best
SEPARATOR_MODEL_DIR = os.path.join(DATA_DIR, "separator_models")
SEPARATOR_OUTPUT_DIR = os.path.join(DATA_DIR, "separator_output")

# --- MuQ-MuLan audio embedding settings ---
MUQ_ENABLED = os.environ.get("MUQ_ENABLED", "true").lower() == "true"
MUQ_MODEL_NAME = os.environ.get("MUQ_MODEL_NAME", "OpenMuQ/MuQ-MuLan-large")
MUQ_SAMPLE_RATE = 24000  # MuQ-MuLan expects 24kHz audio
MUQ_AUDIO_BLEND = float(os.environ.get("MUQ_AUDIO_BLEND", "0.5"))  # 0.0=text only, 1.0=audio only
MUQ_AUDIO_COLLECTION = "songs_audio"  # ChromaDB collection name for audio embeddings

# --- Taste Constellations (multi-centroid taste model) ---
CONSTELLATION_MIN_SIGNALS = 50       # Minimum positive signals to activate constellations
CONSTELLATION_MIN_CLUSTER_SIZE = 5   # Min songs per taste cluster
CONSTELLATION_MAX_CLUSTERS = 8       # Upper bound for adaptive KMeans
CONSTELLATION_HOP_CHANCE = 0.20      # Probability of sampling from neighboring cluster
CONSTELLATION_WEIGHT_BOOST = 0.15    # Max score boost for constellation-aligned candidates
CONSTELLATION_CACHE_TTL = 300        # Seconds to cache constellation state (5 min)
CONSTELLATION_ARTIST_BLEND = 0.0     # Disabled — artist is already prominent in embedding text
