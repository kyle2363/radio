# Radio — AI-Powered Music Station

An intelligent radio station that listens to your taste, learns your mood, and recommends music from your local MP3 library. Built with FastAPI + React + VLC.

## Features

- **AI music recommendations** via ChromaDB vector search + LLM reranking
- **Mood detection** — 6 mood states with automatic shifting based on skips and completions
- **Vibe lock** — lock onto a song's vibe for up to 30 min / 10 songs
- **Taste constellations** — multi-centroid taste model that forms personal "taste pockets" after 50+ positive signals
- **Gapless TTS announcements** — Piper TTS (CPU) + OmniVoice (GPU voice cloning) DJ chained between songs via VLC
- **Dual LLM providers** — Ollama or OpenAI-compatible (llama.cpp), with auto-fallback
- **Natural language commands** — "more energetic", "play some jazz", "what's next"
- **Listener archetypes** — explorer, comfort listener, deep listener, mood listener
- **Onboarding** — seed your taste on first launch with 15-20 neutral songs
- **Library verification** — compare DB against filesystem, auto-match orphans
- **DJ effects** — apply slowed_reverb, nightcore, and more to songs on-the-fly
- **Lyrics timing** — WhisperX forced alignment with word-level timestamps
- **MuQ-MuLan audio embeddings** — 512-dim vectors for cross-modal text-to-audio search
- **Recovery songs** — automatically picks safe, well-known tracks after a skip streak
- **Electron desktop wrapper** — system tray, mini player, widget, global media keys

## Prerequisites

- **Python 3.11+** (the project includes `backend/venv/`; if missing, create it with `python -m venv backend/venv`)
- **Node.js 20+** with npm
- **[VLC](https://www.videolan.org/vlc/)** media player (required for audio playback)
- **llama.cpp** — must be installed separately (see [External Dependencies](#external-dependencies))
- **acestep.cpp** — must be installed separately (only needed for the "understand" ingest phase)

### Optional

- **NVIDIA GPU + CUDA 12.1+** — for MuQ-MuLan audio embeddings and OmniVoice voice cloning (CPU fallbacks available)

## External Dependencies

This project does **not** bundle llama.cpp or acestep.cpp. You must build and install them yourself:

| Dependency | Purpose | Port | Source |
|------------|---------|------|--------|
| **llama.cpp** (`llama-server.exe`) | LLM chat (reranking, tagging, DJ lines) | 8081 | [ggerganov/llama.cpp](https://github.com/ggerganov/llama.cpp) |
| **llama.cpp** (`llama-server.exe`) | Text embeddings (vector search) | 8082 | Same as above — run as a separate instance with `--embedding` |
| **acestep.cpp** (`server.cmd`) | Audio understanding (caption, BPM, key, lyrics skeleton) | 8085 | acestep project |
| **GGUF model files** | Qwen3.5 9B (chat), Qwen3 4B Embedding (embeddings) | — | HuggingFace |

The **Process Manager** in the backend can auto-start llama.cpp and acestep when the backend boots — configure their launch commands in `.env` (see [Configuration](#configuration)). If you already have Ollama running externally, set `CHAT_PROVIDER=ollama` and skip the llama.cpp builds.

## Quick Start

### 1. Clone and set up the backend

```bash
git clone <repo-url> radio
cd radio

# Create .env from the example
copy .env.example .env

# Edit .env — set LLAMA_CHAT_WORK_DIR, LLAMA_EMBED_WORK_DIR, and ACESTEP_WORK_DIR
# to point at your llama.cpp and acestep.cpp installations

cd backend
```
The project expects `backend/venv/` to already exist. If it was stripped from git:
```bash
python -m venv venv
```

Install Python dependencies:
```bash
venv\Scripts\pip install -r requirements.txt     # Windows
```

### 2. Set up the frontend

```bash
cd frontend
npm install
```

### 3. Run

```bash
# From frontend/ — starts Vite + Electron (Electron spawns the FastAPI backend automatically)
npm run dev
```
Electron app should auto start with 'npm run dev'

For browser verions open `http://localhost:3000` in your browser. The backend runs on `http://localhost:8765`.

## Configuration

Edit `backend/config.py` for path/model/threshold settings, or override via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://192.168.1.20:5002` | Ollama server URL |
| `CHAT_PROVIDER` | `openai` | `ollama`, `openai`, or `auto` |
| `EMBED_PROVIDER` | `openai` | `ollama`, `openai`, or `auto` |
| `OPENAI_BASE_URL_CHAT` | `http://127.0.0.1:8081/v1` | OpenAI-compatible chat endpoint |
| `OPENAI_BASE_URL_EMBED` | `http://127.0.0.1:8082/v1` | OpenAI-compatible embed endpoint |
| `OPENAI_API_KEY` | `""` | API key (if required) |

The `.env` file controls the **Process Manager** — external subprocesses the backend can start/stop:

| Variable | Description |
|----------|-------------|
| `LLAMA_CHAT_CMD` | Full launch command for the llama.cpp chat server |
| `LLAMA_CHAT_WORK_DIR` | Working directory (e.g. `C:/path/to/llama.cpp`) |
| `LLAMA_CHAT_AUTO_START` | `true` to auto-start on backend boot |
| `LLAMA_EMBED_CMD` | Full launch command for the llama.cpp embeddings server |
| `LLAMA_EMBED_WORK_DIR` | Working directory |
| `LLAMA_EMBED_AUTO_START` | `true` to auto-start (disabled by default) |
| `ACESTEP_CMD` | Launch command for acestep.cpp (manual trigger only) |
| `ACESTEP_WORK_DIR` | Working directory |
| `ACESTEP_AUTO_START` | `false` — acestep is only started during "understand" phase |

Copy `.env.example` to `.env` to get started. All 3 commands are pre-filled with sensible defaults for llama.cpp — you only need to update the `*_WORK_DIR` paths.

## Manual Ingest Phases

Ingest phases can be run individually **without starting the server**. Each phase processes only songs that haven't completed it yet, so they are safe to resume.

### From the `backend/ingest.py` shortcut

Edit the last line of `backend/ingest.py` to call the phase you want:

```python
from ingest.pipeline import *
results = run_phase_raw()              # change this line
print(results)
```

Then run it:

```bash
cd backend
venv\Scripts\python ingest.py
```

### Available phases

| Function | What it does |
|----------|-------------|
| `run_phase_understand()` | Submit MP3s to acestep.cpp for understanding (req. acestep running) |
| `run_phase_raw()` | Scan music dir, add new songs to DB, extract album art |
| `run_phase_analyze()` | librosa per-song audio feature extraction (RMS, crest, spectral) |
| `run_phase_normalize()` | Library-wide percentile normalization → energy/valence (0-1) |
| `run_phase_describe()` | Load acestep JSON metadata, generate LLM emotional/genre/instrument tags |
| `run_phase_embed()` | Build embedding text, upsert to ChromaDB (starts/stops llama.cpp embed server on-demand) |
| `run_phase_embed_audio()` | MuQ-MuLan audio embeddings → `songs_audio` collection (GPU required) |
| `run_phase_cluster()` | KMeans with 28 clusters on embedding + audio features |
| `run_phase_lyrics_timing()` | WhisperX forced alignment for songs with lyrics |

Phases can also be triggered via the HTTP API when the server is running:

```
POST /api/ingest/run-phase/{name}
```

Where `{name}` is one of: `understand`, `raw`, `analyze`, `normalize`, `describe`, `embed`, `audio_embed`, `cluster`, `lyrics_timing`.

## Lyrics Process

**Note: Lyrics are a manual process.** After acestep generates metadata, it leaves `lyrics: "TODO"` in each JSON file. You must populate lyrics yourself using the helper scripts, then re-run the describe phase to pull them into the database:

```bash
# Step 1 — Process MP3s through acestep (leaves lyrics as "TODO")
python batch_understand.py audio_files/

# Step 2 — Fill lyrics from lyrics.ovh API
python batch_fill_lyrics.py --resume

# Alternative: scrape Genius.com (no API key needed)
python batch_fill_genius.py --resume

# Alternative: general web scrape
python batch_scrape_lyrics.py --resume

# Alternative: Use a ai agent like opencode to scrape the web for you

# Step 3 — Re-run describe phase (pulls updated lyrics + tags into DB)
# From backend/ — edit ingest.py to call run_phase_describe(), then:
venv\Scripts\python ingest.py

# Step 4 (optional) — WhisperX forced alignment for word-level timing
python batch_lyrics_timing_smart.py --resume
```

Songs that can't be matched are marked as `"lyrics not found"` and skipped from timing. Instrumental tracks should be marked manually (`"lyrics": "instrumental"`).

## Acestep Metadata Pipeline

The app uses acestep.cpp for audio understanding. The `batch_understand.py` script processes MP3s in batches, saves JSON metadata to `docs/understand/`, and supports resume via `--resume`:

```bash
# Process all MP3s (supports --resume to skip already-done files)
python batch_understand.py audio_files/ --concurrency 2

# Use a different LM model
python batch_understand.py audio_files/ --lm-model acestep-5Hz-lm-4B-Q8_0
```

Once JSONs are generated, run the ingest pipeline (see [Manual Ingest Phases](#manual-ingest-phases)).

## Project Structure

```
radio/
├── backend/                 # FastAPI Python backend (port 8765)
│   ├── main.py              # ~130 API endpoints + WebSocket
│   ├── config.py            # Paths, model names, thresholds, provider config
│   ├── ingest.py            # Manual phase execution shortcut
│   ├── db/                  # SQLite schema (12 tables) + CRUD helpers
│   ├── ingest/               # 7-phase ingest pipeline (scanner → clusterer)
│   ├── engine/               # Recommendation engine
│   │   ├── player.py         # VLC MediaListPlayer wrapper, gapless chaining
│   │   ├── queue.py          # threading.Condition-protected song queue
│   │   ├── taste.py          # 4-layer memory system (immediate → longterm)
│   │   ├── retriever.py      # ChromaDB + SQLite two-stage retrieval
│   │   ├── reranker.py       # LLM reranking with deterministic fallback
│   │   ├── slots.py          # 5-slot allocator (exploit/explore/wildcard/context)
│   │   ├── transitions.py    # Song-pair transition scoring (rolling average)
│   │   ├── mood.py           # Mood state machine (6 states) + vibe lock
│   │   ├── energy.py         # Energy trajectory tracking (last 5 songs)
│   │   ├── recovery.py       # Recovery song selection after skip streaks
│   │   ├── archetype.py      # Listener archetype detection (4 archetypes)
│   │   ├── constellations.py # Multi-centroid taste model ("taste pockets")
│   │   ├── audio_separator.py# UVR-MDX vocal separation for lyrics alignment
│   │   ├── process_manager.py# Spawn/monitor llama.cpp + acestep subprocesses
│   │   ├── dj_effects.py     # DJ audio effects (slowed_reverb, nightcore, etc.)
│   │   └── sleep_timer.py    # Sleep timer with fade-out
│   ├── announcer/            # TTS, DJ announcements, talk show
│   │   ├── voice_manager.py  # Unified OmniVoice + Piper interface
│   │   ├── announcements.py  # LLM-generated DJ lines with trigger conditions
│   │   ├── talk_segments.py  # Caller, monologue, guest takeover segments
│   │   ├── personas.py       # 20+ DJ personas with voice clips
│   │   └── voices/           # 22 MP3 voice prompt clips
│   ├── commands/             # NL command parser (regex + LLM tool-calling)
│   └── llm/                  # LLM provider abstraction (Ollama + OpenAI)
│       └── muq_engine.py     # MuQ-MuLan audio embedding engine
│
├── frontend/                 # Vite + React 18 + Tailwind CSS v4
│   ├── electron/             # Electron main process (tray, mini player, widget)
│   └── src/
│       ├── components/       # ~50 React components
│       ├── hooks/            # 8 custom hooks (state, playback, mood, UI, etc.)
│       ├── api/              # HTTP + WebSocket client
│       └── styles/           # Tailwind v4 + dark/light theme
│
├── audio_files/              # MP3 library
├── docs/understand/          # Acestep JSON metadata files
├── data/                     # Runtime data (SQLite DB, ChromaDB, TTS cache, art)
├── .env.example              # Process manager configuration template
├── batch_understand.py       # Submit MP3s to acestep /understand
├── batch_fill_lyrics.py      # Fetch lyrics from lyrics.ovh API
├── batch_fill_genius.py      # Scrape lyrics from Genius.com
├── batch_scrape_lyrics.py    # Scrape lyrics from web
├── batch_lyrics_timing_smart.py  # WhisperX forced alignment (vocals-first)
└── reembed_all.py            # Re-embed all songs with updated text
```

## License

MIT
