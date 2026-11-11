# Radio — AI music station and local Android player

Radio turns your personal MP3 collection into a station that learns what you enjoy. Listen on your computer, or use the Android app to play the music stored on your phone—even offline.

| Surface | What it does |
| --- | --- |
| **Android** | Plays music stored on the phone; local library, playlists, offline Radio and optional desktop connections |
| **Desktop + backend** | Library analysis, AI recommendations, listening sessions, browser or VLC playback, and optional DJ speech |
| **Browser demo** | Standalone showcase with fictional tracks, deterministic recommendations and browser-synthesized audio |

Android playback stays local and works without the server. Connecting adds catalog matching, explicit profile sync and optional desktop recommendations; it does not stream the desktop music library or take over another playback session.

## Choose where to start

- **Listen on your phone:** follow [Android setup](#android). You only need music on the phone; a desktop connection is optional.
- **Listen on your computer:** follow [desktop setup](#desktop-and-backend). This requires Python, Node.js and configured AI services.
- **Try the interface:** run the [standalone browser demo](#standalone-browser-demo), which uses fictional tracks and synthesized audio.

The setup commands below are for Windows PowerShell and build/run the apps from this repository.

## Available today

Android includes offline Radio, manual profile sync, secure remote connections, automatic desktop address discovery and optional Desktop picks. Radio has an ON AIR station view, mood and exploration controls, Vibe lock, New mix and Not for me with Undo.

Android lyrics, statistics, speech, crossfade, downloads and automatic profile sync are not available yet. Phone playback uses music already stored on the device.

## Android

### Features

- Recursive, read-only MP3 scanning through a folder grant; source audio and tags are never modified.
- Local playback, background and lock-screen controls, playlists, queue editing and shuffle.
- Paused restoration of the last song, position and queue.
- Listening history, likes/dislikes and Replay.
- Offline Radio with local learning, mood/exploration tuning, Vibe lock and automatic queue refill.
- Sleep timer and optional volume normalization that lowers loud tracks without boosting quiet ones.
- Ocean Blue / Deep Teal palettes, light/dark appearance and optional Now Playing backdrop.
- Optional USB pairing, catalog import/content matching, manual profile sync, secure remote hosting and address discovery.
- Optional Desktop picks and phone-only connected tuning; local playback continues independently.

### Build and install

Use Android Studio or a local Android SDK/toolchain compatible with the checked-in Gradle project. The app requires **Android 15 (API 35) or newer**. Building requires Android SDK 37. You do not need Python, Node.js or a running desktop server to listen on the phone.

From the repository root in PowerShell:

```powershell
cd android
.\gradlew.bat assembleDebug
.\gradlew.bat installDebug
```

Installation requires an attached device with USB debugging enabled, or an emulator. The debug APK is generated at `android/app/build/outputs/apk/debug/app-debug.apk`.

### First listen

1. Open the app, grant access to a folder containing your MP3s and scan it.
2. Open **Library** and choose a song for normal playback.
3. Open **Radio** to start a station with **Pick for me** or **Start from a song**. Adjust mood and exploration in Radio, or open **Tune** for more controls.
4. Use **New mix** to replace upcoming Radio picks while keeping songs you added. Use **End** to finish the queue or stop now.

The app restores your last song and position paused. Scanning and playback leave your original audio files and tags unchanged.

For optional desktop connections, start with [USB pairing and matching](docs/MOBILE_PAIRING.md), then [manual profile sync](docs/MOBILE_SYNC.md). Debug USB forwarding uses `127.0.0.1:18766`; release connections require HTTPS. [Remote hosting](docs/REMOTE_HOSTING.md) and [automatic discovery](docs/AUTOMATIC_DISCOVERY.md) have separate setup guides.

## Desktop and backend

### Features

- ChromaDB retrieval, LLM reranking, mood/energy tuning, Vibe lock and taste constellations.
- Authenticated browser audio or server-side VLC output, with independent listening sessions and coordinated output transfer.
- Library browsing, collections, search, verification, feedback and listening analytics.
- Optional Piper / OmniVoice DJ announcements, talk segments and audio effects.
- Optional MuQ-MuLan audio embeddings and WhisperX lyrics timing.
- Electron tray, mini-player, widget and media keys.

### Requirements

- Python 3.11+ and the project virtual environment at `backend/venv/`.
- Node.js 20+ with npm.
- VLC for server-side VLC output; browser audio is also available.
- An Ollama or OpenAI-compatible service for chat and embeddings. llama.cpp can serve both as separate processes.
- Optional external tools/models for audio understanding, GPU embeddings, voice cloning and lyrics alignment. They are not bundled; some optional Python packages are commented out in `backend/requirements.txt`.

### Set up

Run these commands from the repository root in PowerShell. Create the virtual environment only if it is missing:

```powershell
Copy-Item .env.example .env
python -m venv backend/venv
backend\venv\Scripts\python -m pip install -r backend/requirements.txt
cd frontend
npm install
```

Review `.env` before launching. Replace the example model paths and working directories, or disable managed processes and configure an existing provider. Configure both `CHAT_PROVIDER` and `EMBED_PROVIDER` when using Ollama. Always use the project virtual environment for subsequent Python commands.

From `frontend/`, launch the desktop app:

```powershell
npm run dev
```

Electron starts the backend. To listen in a browser, start these in separate terminals from the repository root:

```powershell
cd backend
.\venv\Scripts\python main.py
```

```powershell
cd frontend
npm run electron:dev
```

Open [the development frontend](http://localhost:3000). The backend binds to `127.0.0.1:8765` by default. See [server access](docs/SERVER_ACCESS.md) for authentication and hosting boundaries.

Normal backend startup migrates the database, performs maintenance, clears generated caches and may start managed processes. Use the isolated launcher below for verification.

### Configuration

The backend loads the root `.env` before reading environment variables. See [the example](.env.example) and [backend configuration](backend/config.py) for the full settings.

| Variables | Purpose |
| --- | --- |
| `RADIO_DATA_DIR`, `RADIO_MUSIC_DIR`, `RADIO_METADATA_DIR` | Override runtime data, MP3 library and metadata paths |
| `CHAT_PROVIDER`, `EMBED_PROVIDER` | Select `ollama`, `openai` or `auto` |
| `OLLAMA_BASE_URL` | Existing Ollama service |
| `OPENAI_BASE_URL_CHAT`, `OPENAI_BASE_URL_EMBED`, `OPENAI_API_KEY` | OpenAI-compatible endpoints and credentials |
| `LLAMA_CHAT_CMD`, `LLAMA_CHAT_WORK_DIR`, `LLAMA_CHAT_AUTO_START` | Managed chat service, normally port 8081 |
| `LLAMA_EMBED_CMD`, `LLAMA_EMBED_WORK_DIR`, `LLAMA_EMBED_AUTO_START` | Managed embedding service, normally port 8082 |
| `ACESTEP_CMD`, `ACESTEP_WORK_DIR`, `ACESTEP_AUTO_START` | Optional audio-understanding service, normally port 8085; manual by default |
| `RADIO_TTS_ENABLED`, `RADIO_PROC_MGR_ENABLED` | Speech and process-manager master toggles |

Desktop defaults use `audio_files/` for music, `docs/understand/` for acestep metadata and `data/` for runtime storage. Keep credentials and personal library data out of commits.

### Ingest and maintenance

The main ingest pipeline runs sequentially:

`raw → analyzed → normalized → described → embedded → audio_embedded → clustered`

Audio understanding supplies captions/metadata; the describe stage imports that metadata and adds LLM tags. Text embeddings, optional audio embeddings and clustering support recommendations. Export a portable catalog after ingest has settled.

Use the app's ingest controls or the phase functions in [the pipeline](backend/ingest/pipeline.py). The [manual shortcut](backend/ingest.py) selects a phase in source. Lyrics helpers and `batch_*.py` files are operational scripts that can modify metadata or runtime state; inspect their options and targets before running them. They are not test fixtures.

### Packaging

From `frontend/`, `npm run electron:build` packages the Electron UI, frontend build and backend source. It does **not** bundle Python, installed backend dependencies, VLC or model servers. Packaged installs need a compatible Python runtime with dependencies, or a compatible `backend/venv` beside the copied backend.

Packaged runtime data defaults to Electron's per-user application-data directory. Use the path overrides above for an existing music library and metadata.

## Standalone browser demo

The [demo](demo/README.md) runs independently of the backend and Android app. It uses fictional catalog data and original synthesized audio, runs no live AI and never accesses visitor music.

Preview from the repository root:

```powershell
backend\venv\Scripts\python -B -m http.server 4173 --bind 127.0.0.1 --directory demo
```

Open [the local demo](http://127.0.0.1:4173) and click Play to enable audio. The repository includes a [GitHub Pages workflow](.github/workflows/demo-pages.yml); publishing setup is covered in the demo guide.

## Development and verification

Run checks for the surface you change. See [TESTING.md](docs/TESTING.md) for isolation, fixtures and manual checks.

| Surface | Commands | Run from |
| --- | --- | --- |
| Backend | `backend\venv\Scripts\python -B scripts/run_tests.py -q` | Repository root |
| Frontend | `npm test`, `npm run build` | `frontend/` |
| Android | `.\gradlew.bat testDebugUnitTest`, `.\gradlew.bat lintDebug`, `.\gradlew.bat assembleDebug` | `android/` |
| Android device/emulator | `.\gradlew.bat connectedDebugAndroidTest` | `android/` |
| Demo | `node --test demo/tests/*.test.mjs` | Repository root |

For a disposable desktop test environment, run from the repository root:

```powershell
backend\venv\Scripts\python -B scripts/run_isolated_app.py --port 18765
```

Generated media, fixtures and test output belong in `.test-runtime/`. Automated checks must not write to the original music, operational metadata, production database or production Chroma store. Root `test_*.py` scripts are not the backend test suite. Emulator checks do not replace phone acceptance for Bluetooth, battery and playback ergonomics.

## Repository map and documentation

| Path | Contents |
| --- | --- |
| `android/` | Standalone Kotlin/Compose app, Room schemas, JVM and instrumented tests |
| `backend/` | FastAPI, playback sessions, recommendation engine, ingest, speech and portable APIs |
| `frontend/` | React/Vite UI, browser audio engine and Electron shell |
| `demo/` | Standalone public browser showcase and tests |
| `scripts/` | Isolated test runners, launcher and fixture generators |
| `docs/` | Architecture, operation guides, portable contracts and checkpoint evidence |
| `.test-runtime/` | Disposable generated test artifacts |

Start with the [documentation index](docs/README.md). Key guides include [architecture](docs/ARCHITECTURE.md), [desktop usage](docs/APP_USAGE_CHECKLIST.md), [Android checkpoints](docs/ANDROID_APP_PLAN.md), [offline recommendations](docs/OFFLINE_RECOMMENDATIONS.md), [connected recommendations](docs/CONNECTED_RECOMMENDATIONS_PLAN.md) and [connected tuning](docs/CONNECTED_TUNING_PLAN.md).

`main` is the integration branch. Repository workflow and implementation constraints are in [AGENTS.md](AGENTS.md).

Checkpoint history: `main` includes Android checkpoints through 4.2, phone-accepted and merged November 11, 2026. The [checkpoint log](docs/ANDROID_APP_PLAN.md) and [redesign plan](docs/RADIO_REDESIGN_PLAN.md) contain verification evidence; their pre-merge headers have not yet been updated.
