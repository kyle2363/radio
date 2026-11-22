# ZONOS3-Mini TTS trial

A third speech engine (`zonos3`) beside OmniVoice and Piper, for comparing
Zyphra's ZONOS3-Mini (released November 12, 2026) by ear and by speed. OmniVoice
stays the default; Piper stays the fallback. Branch: `zonos3-tts-trial`.

## How it fits

- The model runs out of process in Zyphra's C++/ggml `zonos3-server`, using the
  quantized GGUF weights. The backend only talks HTTP to it
  (`backend/announcer/zonos3_engine.py`), so it adds no Python packages and no
  GPU memory to the backend process.
- Voices:
  - Your persona reference clips in `backend/announcer/voices/` (same names as
    OmniVoice). Each clip is registered with the server once
    (`POST /v2/speakers`, session `radio-backend`) and reused by id in
    embedding mode. If the server restarts, the engine registers again; if
    registration fails, the clip is sent inline.
  - The server's 27 bundled voices, listed by label (for example
    `Thaddeus en announcervoice M 1`, `Savannah en American F 1`).
- Steering: persona speed maps to `delivery.tempo`; everything else comes from
  the engine-neutral speech direction below. Ids and fields the server does not
  offer are dropped, so a stale id can never break synthesis.
- When the server is down or returns no audio, speech falls back to Piper.

## Speech direction (engine-neutral, November 21, 2026)

The radio describes *how* a line should sound, and each engine's adapter turns
that into its own request. Changing or upgrading the model means writing one
adapter; nothing in the talk show or announcements changes.

| File | Role |
|---|---|
| `backend/announcer/speech_direction.py` | Settings, emotion vocabulary, mood/next-song/hour delivery, persona traits, inline marks, builders, `ADAPTERS` registry, plain and OmniVoice adapters |
| `backend/announcer/zonos3_direction.py` | ZONOS3 adapter: steering ids, doses, studio safe ranges |
| `backend/announcer/voice_manager.py` | Engine-neutral parts: pause splitting, loudness matching, cache key |

Settings (one row each, all on by default; Desktop → Settings → Talk → Speech direction):

| Setting | ZONOS3 | OmniVoice | Piper / unknown engine |
|---|---|---|---|
| `speech_direction_enabled` (master) | — | — | — |
| `speech_emotion_enabled`: trigger emotions, an emotion word per caller line from the LLM, monologue type | steering | ignored | ignored |
| `speech_mood_delivery_enabled`: mood, next song energy/valence, late hours | steering + pace | pace | pace (where the engine takes a speed) |
| `speech_loudness_match_enabled` + `speech_loudness_target_lufs` (-16) | condition hint + normalisation | normalisation | normalisation |
| `speech_tight_start_enabled` | no lead-in silence | ignored | ignored |
| `speech_word_marks_enabled`: whispered asides, shouted words, emphasis; `[laughter]`/`[sigh]` become a delivery | word-level spans | marks stripped, tags kept | stripped |
| `speech_pauses_enabled`: real pauses at `...` and spaced dashes (two per line at most) | line split, silence inserted | `.,.` | stripped |
| `speech_persona_traits_enabled`: accent and texture per persona | embedding-mode voices only | ignored | ignored |

Notes from the live server (released checkpoint, `steer.gguf`):

- Accents, texture, identity, pitch and room are speaker-embedding ("raw")
  controls. The server refuses them next to a reference prefix, so with the
  recommended reference-mode persona voices they are left out; the cloned clip
  already carries the persona's accent. They apply to bundled voices and
  `ZONOS3_SPEAKER_MODE=embedding`.
- The checkpoint has no pause tokens, no start token and no steer-then-hold, so
  pauses are done by splitting the line, and the emotion dose is constant.
- Word spans use the rate schedule (`X-Control-Span-Follow: rate`); there are
  no alignment heads loaded for the reading schedule.
- Sample renders (plain, energetic, late-night chill, caller with marks and a
  pause, laughter tag, persona line) are in
  `.test-runtime/speech-direction-samples/` of the `zonos3-trial` worktree; all
  directed lines measured -16.0 to -16.2 LUFS against -21.6 for the plain one.

To support a new model: add `announcer/<model>_direction.py` with a
`DirectionAdapter` subclass (`render(text, direction)` returns plain text and
request fields; set `native_pauses` if the model has its own pause syntax), add
one line to `ADAPTERS`, and accept `request=` in the engine's `synthesize`.
Tables of doses live in the adapter, so tuning by ear never touches the radio.

## Measured on the RTX 5080 Laptop GPU (November 21, 2026)

| | Result |
|---|---|
| Server start | About 7 s, including a 6 s warm-up |
| GPU memory | About 2.2 GB loaded idle; about 3–5 GB while generating (batch 8) |
| Bundled voice | 0.3–0.5 s per line (13× faster than real time) |
| Persona clip, first line | About 1.4 s (includes one-time registration) |
| Persona clip, later lines | 0.3–0.5 s per line |
| 15 sample lines | 77 s of speech generated in 9.1 s |
| Completeness | Whisper transcription of every sample matched the script word for word |
| Output | 44.1 kHz 16-bit mono WAV |

Noticed: bundled voices come out quieter (RMS about 0.04) than cloned persona
clips (about 0.07–0.17), so loudness may need matching against the music.

## Quality settings (tested November 21, 2026)

The engine asks the server for its capabilities and uses the best it offers:

| Setting | Default | Falls back to | Needs on the server |
|---|---|---|---|
| `ZONOS3_SPEAKER_MODE` | `reference` (clone from the clip's audio) | `embedding` (192-number voice fingerprint) | `zac-encoder.gguf` + `ssl-teacher.gguf` (5.6 GB) |
| `ZONOS3_DECODER` | `full_mr` (full decoder) | the server's default (`keep2_plain`) | `zac.gguf` (2.2 GB) |
| `ZONOS3_SPEAKER_CFG` | off | — | nothing extra; try `1.5` |

Four personas × four lines, Whisper large-v3 for word accuracy:

| Setup | Clips with a wrong word | Time for 16 lines | Speech length | Loudness spread |
|---|---|---|---|---|
| OmniVoice | 7 of 16 | 32.7 s | 150 s | 3.0 dB |
| ZONOS3 embedding + light decoder | 1 of 16 | 8.8 s | 125 s | 10.0 dB |
| ZONOS3 embedding + full decoder | 2 of 16 | 11.0 s | 125 s | 11.3 dB |
| **ZONOS3 reference + full decoder** | **0 of 16** | 12.8 s | 147 s | 6.5 dB |
| ZONOS3 reference + full + guidance 1.5 | 1 of 16 | 13.0 s | 141 s | 4.0 dB |

Reference mode is what closes the gap: it follows each persona's own pacing (speech length
close to OmniVoice's) where embedding mode rushed. The full decoder alone changed little.
OmniVoice's 7 errors are a stray word at the start of every line for two personas, the tail of
their reference transcript ("me", "comedy") leaking into new speech.

Costs: the first line for each persona takes about 7 s while the server encodes its clip, then
0.4–2 s per line. The server unloads the encoders after 60 s idle; GPU use peaked near 12.8 GB
alongside llama.cpp. The listening page (blind A–D mode) is
`.test-runtime/zonos3-compare/compare.html` in the main checkout.

## Pre-encoded persona voices (recommended)

Reference mode normally makes the server encode each persona clip with its 5.6 GB encoders
(about 7 s the first time per persona, and up to about 5 GB extra GPU memory). Encoding once and
storing the result avoids both:

```powershell
backend\venv\Scripts\python scripts/zonos3_encode_voices.py --zonos-dir C:/Users/kyleh/Desktop/AI/audio/ZONOS3-Mini/servers/ggml
```

- Writes `data/zonos3_voices/<persona>.<fingerprint>.codes.npy` (about 8 KB each; 18 personas = 144 KB).
  `--zonos-dir` defaults to `ZONOS3_WORK_DIR`; `--cpu` encodes without the GPU.
- Run it again after adding or re-recording a clip: unchanged clips are skipped, a changed clip is
  re-encoded and its old codes removed (the fingerprint is a hash of the clip).
- The engine sends the stored codes with each request (`speaker_reference_codes`), so the server never
  loads its encoders while the radio runs. A clip without codes still works through the slower path.
- Measured: first line per persona 0.3–0.5 s (was about 7 s); the server log showed no encoder load.

## GPU memory (measured November 21, 2026, nothing else on the GPU)

| Engine / server setup | Idle | Peak while speaking | Per line |
|---|---|---|---|
| OmniVoice (in the backend process) | 2.1 GB | 2.7 GB | about 2 s |
| ZONOS3 default server (both decoders, 4 decode lanes, batch 8) | 4.9 GB | 5.4 GB | 0.67 s |
| ZONOS3 full decoder only | 3.8 GB | 4.3 GB | 0.62 s |
| ZONOS3 full decoder only, 1 lane | 3.3 GB | 3.7 GB | 0.65 s |
| **ZONOS3 full decoder only, 1 lane, batch 2 (recommended)** | **3.1 GB** | **3.6 GB** | 0.66 s |
| ZONOS3 encoding a clip on the server (no stored codes) | — | 12.4 GB | about 7 s once |

The radio speaks one line at a time, so the extra decode lanes and request slots only cost memory.
Keep `--batch 2` (speaker guidance needs at least 2). With stored codes the encoders never load;
the remaining memory is mostly the full decoder (`zac.gguf`, 2.2 GB on disk). Recommended command:

```powershell
.\build-cuda\Release\zonos3-server.exe gguf\backbone-ptq.gguf --gpu --zac gguf\zac.gguf --zac-threads 1 --batch 2 --tts-default-voices-dir gguf\voices --port 1919
```

## Files to download (GGUF quants: 1.2 GB minimum, 8.4 GB with the quality files)

| File | Size | Why |
|---|---|---|
| `gguf/backbone-ptq.gguf` | 0.66 GB | Speech model (quantized) |
| `gguf/zac-keep2.gguf` | 0.45 GB | Light codec decoder (WAV output) |
| `gguf/spk-encoder.gguf` | 0.05 GB | Speaker encoder, for persona clips |
| `gguf/steer.gguf` | <0.01 GB | Steering controls (65 directions) |
| `gguf/voices/*` | 0.02 GB | 27 bundled voices |

For the quality settings above, also download `gguf/zac.gguf` (2.2 GB, full decoder) and
`gguf/zac-encoder.gguf` + `gguf/ssl-teacher.gguf` (5.6 GB, reference-mode cloning); the server
loads them automatically when they sit beside the backbone. Not needed: the full-precision
PyTorch weights (`lm/`, `autoencoder/`, about 10–14 GB). Do not download the whole repository
(29 GB+).

## Setup (Windows, CUDA) — as done on this machine

Prerequisites: CMake, Visual Studio 2022 C++ build tools, CUDA toolkit (13.1
here), Rust (`winget install Rustlang.Rustup`; the text normalizer is a Rust
library and the build will not configure without `cargo`), ffmpeg on `PATH`
(decodes MP3 persona clips), and the `hf` CLI.

```powershell
cd C:\Users\kyleh\Desktop\AI\audio
git clone --depth 1 https://github.com/Zyphra/ZONOS3-Mini.git
cd ZONOS3-Mini
git submodule update --init --depth 1 servers/ggml/ggml third_party/zyphra-textnorm
cd servers\ggml
# RTX 50-series (Blackwell) is sm_120; Zyphra's default targets only the H100 (sm_90).
cmake -B build-cuda -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 -DCMAKE_BUILD_TYPE=Release
cmake --build build-cuda --config Release -j
# Windows quirk: the exe lands in build-cuda\Release but the ggml DLLs in build-cuda\bin\Release.
copy build-cuda\bin\Release\*.dll build-cuda\Release\
hf download Zyphra/ZONOS3-Mini gguf/backbone-ptq.gguf gguf/zac-keep2.gguf gguf/spk-encoder.gguf gguf/steer.gguf --local-dir .
hf download Zyphra/ZONOS3-Mini --include "gguf/voices/*" --local-dir .
.\build-cuda\Release\zonos3-server.exe gguf\backbone-ptq.gguf --gpu --tts-default-voices-dir gguf\voices --port 1919
```

The startup log should show `backend = CUDA0 | NVIDIA GeForce RTX 5080 Laptop GPU`
and `listening on http://127.0.0.1:1919`. The server also hosts Zyphra's studio
UI at that address for trying controls by hand.

To let the backend start it, add to `.env` (see `.env.example`):

```
ZONOS3_CMD=build-cuda/Release/zonos3-server.exe gguf/backbone-ptq.gguf --gpu --tts-default-voices-dir gguf/voices --port 1919
ZONOS3_WORK_DIR=C:/Users/kyleh/Desktop/AI/audio/ZONOS3-Mini/servers/ggml
ZONOS3_AUTO_START=true
```

`ZONOS3_BASE_URL` defaults to `http://127.0.0.1:1919`.

## Trying it

1. Desktop → Settings → Talk → TTS engine → **ZONOS3**. Pick a voice under
   "ZONOS3 (trial)".
2. Compare the same persona on OmniVoice and ZONOS3: a song intro, a mood
   change, a DJ monologue and a caller segment. Listen for naturalness and
   likeness; compare `Zonos3: synthesized ... in N.NNs` log lines with
   OmniVoice's `synthesis done in N.Ns`.
3. Watch `nvidia-smi` with llama.cpp loaded too.
4. Tune the tables in `zonos3_direction.py` by ear (the studio UI at `http://127.0.0.1:1919`
   shows each control's safe range).

Sample clips from the trial run are in `.test-runtime/zonos3-samples/`.

## Not done in this trial

- Streaming playback (the engine asks for a buffered WAV).
- Speech editing, voice blending and the 42 other languages (`ZONOS3_LANGUAGE`
  defaults to `en_us`).
- On-phone speech.
