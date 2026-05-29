# Radio — AI-Powered Music Station

An intelligent radio station that listens to your taste, learns your mood, and recommends music from your local MP3 library. Built with FastAPI + React + VLC.

## Features

- **AI music recommendations** via ChromaDB vector search + LLM reranking
- **Mood detection** — 6 mood states with automatic shifting
- **Vibe lock** — lock onto a song's vibe for up to 30 min
- **Gapless TTS announcements** — Piper TTS DJ chained between songs via VLC
- **Natural language commands** — "more energetic", "play some jazz", "what's next"
- **Listener archetypes** — explorer, comfort listener, deep listener, mood listener
- **Onboarding** — seed your taste on first launch
- **WebSocket real-time events** — 12 event types for responsive UI
- **Electron desktop wrapper** — system tray, mini player, global media keys

## Quick Start

### Prerequisites

- Python 3.11+ with `venv`
- Node.js 20+ with npm
- [VLC](https://www.videolan.org/vlc/) media player (required for audio playback)
- One of: [Ollama](https://ollama.ai/) or an OpenAI-compatible server (llama.cpp) for LLM + embeddings

### Backend Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### Frontend Setup

```bash
cd frontend
npm install
```

### Running

```bash
# Terminal 1 — Start the backend
cd backend
venv\Scripts\activate
python main.py

# Terminal 2 — Start the frontend
cd frontend
npm run dev
```

Open http://localhost:3000 in your browser.

### Electron (Desktop App)

```bash
cd frontend
npm run dev        # Runs Vite + Electron concurrently
npm run build      # Build production bundle
npm run electron:build  # Build Windows installer (requires electron-builder)
```

## Configuration

Edit `backend/config.py` or use environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://192.168.1.20:5002` | Ollama server URL |
| `CHAT_PROVIDER` | `openai` | `ollama`, `openai`, or `auto` |
| `EMBED_PROVIDER` | `openai` | `ollama`, `openai`, or `auto` |
| `OPENAI_BASE_URL_CHAT` | `http://127.0.0.1:8081/v1` | OpenAI-compatible chat endpoint |
| `OPENAI_BASE_URL_EMBED` | `http://127.0.0.1:8082/v1` | OpenAI-compatible embed endpoint |
| `OPENAI_API_KEY` | `""` | API key (if required) |

## Acestep Metadata Pipeline

The app uses [acestep.cpp](https://github.com/acestep/acestep) for audio understanding:

```bash
# 1. Process MP3s through acestep server
python batch_understand.py audio_files/

# 2. (Optional) Fill lyrics from lyrics.ovh
python batch_fill_lyrics.py

# 3. Ingest into the radio database
# POST to /api/ingest/run or use the Library UI
```

## Project Structure

```
radio/
├── backend/          # FastAPI Python backend
│   ├── main.py       # 42 API endpoints + WebSocket
│   ├── db/           # SQLite schema + helpers
│   ├── ingest/       # Music ingestion pipeline
│   ├── engine/       # Recommendation engine
│   ├── announcer/    # TTS + DJ announcements
│   ├── commands/     # NL command parser
│   └── llm/          # LLM provider abstraction
├── frontend/         # Vite + React + Electron UI
│   └── src/
│       ├── components/  # 25 React components
│       ├── hooks/       # State, polling, transitions
│       └── styles/      # Tailwind v4 + dark/light theme
├── audio_files/      # MP3 library
├── docs/understand/  # Acestep JSON metadata
└── data/             # Runtime data (DB, ChromaDB, TTS cache)
```

## License

MIT
