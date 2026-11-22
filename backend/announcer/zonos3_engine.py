"""ZONOS3-Mini TTS engine (trial) — HTTP client for a local ``zonos3-server``.

The model runs out of process in Zyphra's C++/ggml server using the quantized
GGUF weights (``gguf/backbone-ptq.gguf`` + ``gguf/zac-keep2.gguf`` decoder +
``gguf/spk-encoder.gguf``, about 1.2 GB). This module only speaks HTTP, so it
adds no Python dependencies and no GPU memory to the backend process.

Voices:
- Server voices from ``GET /v2/speakers`` (the bundled default voices), sent
  as ``speaker_embedding_id``.
- The existing OmniVoice reference clips in ``announcer/voices/``. Each clip is
  registered once (``POST /v2/speakers``, session-scoped) and then used by id in
  ``speaker_mode: "embedding"``, so only the small speaker encoder is needed, not
  the 4 GB codec encoder, and later lines skip re-encoding the clip (about 1.4 s
  down to 0.2-0.45 s per line on an RTX 5080 Laptop). If registration fails the
  clip is sent inline. Local clips win when a name exists in both places.
- Best: pre-encoded reference codes for each clip (``scripts/zonos3_encode_voices.py``,
  stored in ``ZONOS3_CODES_DIR`` as ``<voice>.<fingerprint>.codes.npy``). They give
  reference-mode quality without the server ever loading its 5.6 GB encoders; a
  re-recorded clip changes the fingerprint, so stale codes are never used.

Steering: the persona speed maps onto ``delivery.tempo``. Everything else
(emotion, mood delivery, persona traits, word-level spans, loudness and a tight
start) arrives as a ready ``request`` from ``announcer/zonos3_direction.py``,
the ZONOS3 adapter of the engine-neutral speech direction. Only control ids the
running server lists in ``GET /v2/controls``, and only fields its capabilities
offer, are sent, because the server rejects unknown fields (422).
"""

import base64
import hashlib
import logging
import os
import random
import threading
import time

import config

logger = logging.getLogger(__name__)

HEALTH_CACHE_SECS = 10.0
SESSION_ID = "radio-backend"  # registered clip speakers live in this server session
PROBE_TIMEOUT_SECS = 1.5
TEMPO_CONTROL = "delivery.tempo"

# Request fields a direction may add, each sent only when the server offers it.
DIRECTION_FIELDS = ("control_spans", "condition_values", "silence_prefill_frames", "fade_out_ms", "seed")


def clip_fingerprint(clip: str) -> str:
    """Short content hash of a reference clip; changes when the clip is re-recorded."""
    h = hashlib.sha256()
    with open(clip, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def codes_path(codes_dir: str, voice_id: str, clip: str) -> str:
    """Where a clip's pre-encoded reference codes live: ``<voice>.<fingerprint>.codes.npy``."""
    return os.path.join(codes_dir, f"{voice_id}.{clip_fingerprint(clip)}.codes.npy")


def _list_reference_clips() -> dict[str, str]:
    """The OmniVoice reference clips, reused as cloning prompts."""
    try:
        from announcer.omnivoice_engine import _list_voice_mp3s
        return _list_voice_mp3s()
    except Exception as e:
        logger.debug(f"Zonos3: reference clips unavailable: {e}")
        return {}


def speed_to_tempo(speed: float) -> float:
    """Map persona speed (0.75-1.05, 0.90 neutral) onto a tempo dose in [-2, 2]."""
    return round(max(-2.0, min(2.0, (speed - 0.90) * 10.0)), 2)


def _control_ids(payload) -> set[str]:
    """Collect ``set.name`` control ids from a /v2/controls payload.

    zonos3-server returns ``{"directions": {"set.name": {...}}, "sets": {...}}``.
    List shapes (``{"controls": [{"id": "set.name"}]}``) are accepted too.
    """
    ids: set[str] = set()
    if isinstance(payload, dict) and isinstance(payload.get("directions"), dict):
        return {k for k in payload["directions"] if isinstance(k, str)}

    def visit(items, prefix=None):
        for item in items or []:
            if isinstance(item, str):
                ids.add(item if "." in item or not prefix else f"{prefix}.{item}")
            elif isinstance(item, dict):
                cid = item.get("id") or item.get("name")
                if isinstance(item.get("controls"), list):
                    visit(item["controls"], item.get("name") or prefix)
                elif isinstance(cid, str):
                    ids.add(cid if "." in cid or not prefix else f"{prefix}.{cid}")

    if isinstance(payload, list):
        visit(payload)
    elif isinstance(payload, dict):
        visit(payload.get("sets"))
        visit(payload.get("controls"))
    return ids


def _raw_control_ids(payload) -> set[str]:
    """Control ids that steer the speaker embedding (``space: raw``: accents, texture, identity...).

    The server refuses these alongside a reference prefix, so they only apply in embedding mode.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("directions"), dict):
        return set()
    sets = payload.get("sets") if isinstance(payload.get("sets"), dict) else {}
    raw = set()
    for cid, direction in payload["directions"].items():
        space = direction.get("space") if isinstance(direction, dict) else None
        if space is None:
            space = (sets.get(cid.split(".", 1)[0]) or {}).get("space")
        if space == "raw":
            raw.add(cid)
    return raw


def _speaker_name(speaker: dict) -> str:
    for key in ("name", "label", "display_name"):
        if isinstance(speaker.get(key), str) and speaker[key]:
            return speaker[key]
    path = speaker.get("path")
    if isinstance(path, str) and path:
        return os.path.splitext(os.path.basename(path))[0]
    return str(speaker.get("id"))


class Zonos3TTSEngine:
    """TTS engine backed by a local zonos3-server over HTTP."""

    ENGINE_ID = "zonos3"

    def __init__(self, base_url: str | None = None, http=None):
        self._base = (base_url or config.ZONOS3_BASE_URL).rstrip("/")
        if http is None:
            import requests
            http = requests.Session()
        self._http = http
        self._lock = threading.Lock()
        self._healthy: bool | None = None
        self._health_at = 0.0
        self._speakers: dict[str, str] | None = None  # name -> speaker id
        self._controls: set[str] | None = None
        self._raw_controls: set[str] = set()
        self._clip_ids: dict[tuple, str] = {}  # (path, size, mtime) -> registered speaker id
        self._caps: tuple[set[str], set[str]] | None = None  # (speaker modes, decoder ids)
        self._caps_raw: dict = {}
        self._codes: dict[str, list] = {}  # codes path -> pre-encoded reference codes
        self._headers = {"X-TTS-Session-ID": SESSION_ID}

    @property
    def synthesis_timeout(self) -> int:
        return config.ZONOS3_TIMEOUT

    # ---- server state ----

    def is_ready(self) -> bool:
        with self._lock:
            if self._healthy is not None and time.monotonic() - self._health_at < HEALTH_CACHE_SECS:
                return self._healthy
        try:
            r = self._http.get(f"{self._base}/v2/health", timeout=PROBE_TIMEOUT_SECS, headers=self._headers)
            healthy = r.status_code == 200
        except Exception:
            healthy = False
        with self._lock:
            if not healthy:
                # A restarted server may carry different voices or steering.
                self._speakers = None
                self._controls = None
                self._caps = None
                self._clip_ids = {}
            self._healthy = healthy
            self._health_at = time.monotonic()
        return healthy

    def _server_speakers(self) -> dict[str, str]:
        if self._speakers is not None:
            return self._speakers
        if not self.is_ready():
            return {}
        speakers: dict[str, str] = {}
        try:
            r = self._http.get(f"{self._base}/v2/speakers", timeout=PROBE_TIMEOUT_SECS * 2, headers=self._headers)
            if r.status_code == 200:
                for sp in (r.json() or {}).get("speakers", []):
                    # Session speakers are our own registered clips; they are listed by clip name.
                    if isinstance(sp, dict) and sp.get("id") and sp.get("scope") != "session":
                        speakers.setdefault(_speaker_name(sp), sp["id"])
        except Exception as e:
            logger.warning(f"Zonos3: could not list speakers: {e}")
            return {}
        self._speakers = speakers
        return speakers

    def _server_controls(self) -> set[str]:
        if self._controls is not None:
            return self._controls
        controls: set[str] = set()
        try:
            r = self._http.get(f"{self._base}/v2/controls", timeout=PROBE_TIMEOUT_SECS * 2, headers=self._headers)
            if r.status_code == 200:
                payload = r.json()
                controls = _control_ids(payload)
                self._raw_controls = _raw_control_ids(payload)
                logger.info(f"Zonos3: steering controls available: {sorted(controls)}")
            else:
                logger.info("Zonos3: server has no steering bundle; speaking without controls")
        except Exception as e:
            logger.warning(f"Zonos3: could not list controls: {e}")
            return set()
        self._controls = controls
        return controls

    def _server_capabilities(self) -> tuple[set[str], set[str]]:
        """(speaker modes, decoder ids) the server offers; empty when unknown."""
        if self._caps is not None:
            return self._caps
        modes: set[str] = set()
        decoders: set[str] = set()
        try:
            r = self._http.get(f"{self._base}/v2/capabilities", timeout=PROBE_TIMEOUT_SECS * 2, headers=self._headers)
            if r.status_code == 200:
                caps = r.json() or {}
                self._caps_raw = caps if isinstance(caps, dict) else {}
                modes = {m for m in caps.get("speaker_modes") or [] if isinstance(m, str)}
                decoders = {d.get("id") if isinstance(d, dict) else d for d in caps.get("decoders") or []}
                logger.info(f"Zonos3: speaker modes {sorted(modes)}, decoders {sorted(d for d in decoders if d)}")
        except Exception as e:
            logger.warning(f"Zonos3: could not read capabilities: {e}")
            return set(), set()
        self._caps = (modes, decoders)
        return self._caps

    # ---- voices ----

    def get_available_voices(self) -> list[str]:
        voices = list(_list_reference_clips())
        voices += [name for name in self._server_speakers() if name not in voices]
        return voices

    def voice_downloaded(self, voice_id: str) -> bool:
        return voice_id in self.get_available_voices()

    def load_voice(self, voice_id: str) -> bool:
        return self.voice_downloaded(voice_id)

    # ---- synthesis ----

    @staticmethod
    def _clip_key(clip: str) -> tuple:
        st = os.stat(clip)
        return (os.path.abspath(clip), st.st_size, int(st.st_mtime))

    @staticmethod
    def _clip_b64(clip: str) -> str:
        with open(clip, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    def _register_clip(self, voice_id: str, clip: str) -> str | None:
        """Register a reference clip with the server once; returns its speaker id."""
        key = self._clip_key(clip)
        if key in self._clip_ids:
            return self._clip_ids[key]
        body = {
            "label": f"radio: {voice_id}",
            "speaker_audio_base64": self._clip_b64(clip),
            "speaker_audio_name": os.path.basename(clip),
        }
        try:
            r = self._http.post(f"{self._base}/v2/speakers", json=body,
                                timeout=self.synthesis_timeout, headers=self._headers)
            sid = (r.json() or {}).get("id") if r.status_code == 200 else None
        except Exception as e:
            logger.warning(f"Zonos3: could not register clip for '{voice_id}': {e}")
            return None
        if not sid:
            logger.warning(f"Zonos3: clip registration for '{voice_id}' returned {r.status_code}; sending inline")
            return None
        self._clip_ids[key] = sid
        return sid

    def _attach_clip(self, body: dict, voice_id: str, clip: str, mode: str):
        body["speaker_mode"] = mode
        sid = self._register_clip(voice_id, clip)
        if sid:
            body["speaker_embedding_id"] = sid
        else:
            body["speaker_audio_base64"] = self._clip_b64(clip)

    def _stored_codes(self, voice_id: str, clip: str) -> list | None:
        """Pre-encoded reference codes for this exact clip, or None."""
        try:
            path = codes_path(config.ZONOS3_CODES_DIR, voice_id, clip)
        except OSError:
            return None
        if path in self._codes:
            return self._codes[path]
        if not os.path.exists(path):
            return None
        try:
            import numpy as np
            codes = np.load(path).astype(int).tolist()
        except Exception as e:
            logger.warning(f"Zonos3: unreadable codes {path}: {e}")
            return None
        self._codes[path] = codes
        return codes

    def _forget_clip(self, clip: str):
        self._clip_ids.pop(self._clip_key(clip), None)

    def _generate(self, body: dict):
        return self._http.post(f"{self._base}/v2/generate", json=body,
                               timeout=self.synthesis_timeout, headers=self._headers)

    def _direction_fields(self, request: dict, available: set[str]) -> dict:
        """The direction fields this server accepts, with unknown control ids removed."""
        caps = self._caps_raw
        fields: dict = {}
        spans = []
        for span in request.get("control_spans") or []:
            kept = {k: v for k, v in (span.get("controls") or {}).items() if k in available}
            if kept:
                spans.append({"start": int(span["start"]), "end": int(span["end"]), "controls": kept})
        if spans:
            fields["control_spans"] = spans
        conditions = caps.get("condition_tokens") or {}
        if request.get("condition_values") and conditions.get("enabled"):
            names = set(conditions.get("names") or [])
            values = {k: v for k, v in request["condition_values"].items() if k in names}
            if values:
                fields["condition_values"] = values
        if "silence_prefill_frames" in request and (caps.get("silence_prefill") or {}).get("available"):
            fields["silence_prefill_frames"] = int(request["silence_prefill_frames"])
        for key in ("fade_out_ms", "seed"):
            if request.get(key) is not None:
                fields[key] = request[key]
        return fields

    def synthesize(self, text: str, voice_id: str, output_path: str,
                   speed: float | None = None, controls: dict | None = None,
                   request: dict | None = None) -> str | None:
        body: dict = {
            "text": text,
            "language": config.ZONOS3_LANGUAGE,
            "stream": False,
            "format": "wav",
        }

        modes, decoders = self._server_capabilities()
        if config.ZONOS3_DECODER and config.ZONOS3_DECODER in decoders:
            body["decoder"] = config.ZONOS3_DECODER
        if config.ZONOS3_SPEAKER_CFG:
            body["speaker_cfg_scale"] = config.ZONOS3_SPEAKER_CFG

        clip = _list_reference_clips().get(voice_id)
        used_codes = False
        if clip and os.path.exists(clip):
            codes = self._stored_codes(voice_id, clip) if config.ZONOS3_SPEAKER_MODE == "reference" else None
            if codes is not None:
                # Pre-encoded codes: reference quality with no encoder on the server.
                body["speaker_mode"] = "reference"
                body["speaker_reference_codes"] = codes
                used_codes = True
            else:
                # Reference mode encodes the clip on the server (needs its codec encoders);
                # otherwise the clip's 192-d speaker embedding is used.
                mode = config.ZONOS3_SPEAKER_MODE if config.ZONOS3_SPEAKER_MODE in modes else "embedding"
                self._attach_clip(body, voice_id, clip, mode)
        else:
            clip = None
            speaker_id = self._server_speakers().get(voice_id)
            if not speaker_id:
                logger.error(f"Zonos3: unknown voice '{voice_id}'")
                return None
            body["speaker_embedding_id"] = speaker_id

        request = request or {}
        wanted = {**(controls or {}), **(request.get("controls") or {})}
        if speed is not None:
            # Same ±0.1 variety OmniVoice applies, expressed as tempo.
            wanted[TEMPO_CONTROL] = speed_to_tempo(speed + random.uniform(-0.05, 0.05))
        available = self._server_controls() if (wanted or request.get("control_spans")) else set()
        steering = {k: v for k, v in wanted.items() if k in available}
        reference = body.get("speaker_mode") == "reference" or (
            "speaker_mode" not in body and self._caps_raw.get("speaker_mode_default") == "reference")
        if reference and self._raw_controls & steering.keys():
            dropped = sorted(self._raw_controls & steering.keys())
            logger.debug(f"Zonos3: reference voice; leaving out speaker-embedding controls {dropped}")
            steering = {k: v for k, v in steering.items() if k not in self._raw_controls}
        if steering:
            body["controls"] = steering
        if request:
            body.update(self._direction_fields(request, available))

        start = time.time()
        try:
            r = self._generate(body)
            if used_codes and r.status_code in (400, 422):
                logger.warning(f"Zonos3: stored codes for '{voice_id}' rejected ({r.status_code}); using embedding mode")
                del body["speaker_reference_codes"]
                self._attach_clip(body, voice_id, clip, "embedding")
                r = self._generate(body)
            elif clip and "speaker_embedding_id" in body and r.status_code in (400, 404):
                # The server restarted and lost its session speakers: register again once.
                logger.info(f"Zonos3: registered voice for '{voice_id}' is gone; registering again")
                self._forget_clip(clip)
                del body["speaker_embedding_id"]
                sid = self._register_clip(voice_id, clip)
                if sid:
                    body["speaker_embedding_id"] = sid
                else:
                    body["speaker_audio_base64"] = self._clip_b64(clip)
                r = self._generate(body)
        except Exception as e:
            logger.error(f"Zonos3: request failed: {e}")
            with self._lock:
                self._healthy = None
            return None
        if r.status_code != 200 or not r.content:
            detail = ""
            try:
                detail = r.json()
            except Exception:
                pass
            logger.error(f"Zonos3: generate returned {r.status_code}: {detail}")
            return None
        with open(output_path, "wb") as f:
            f.write(r.content)
        logger.info(f"Zonos3: synthesized {len(text)} chars with '{voice_id}' in {time.time() - start:.2f}s"
                    f" (controls={body.get('controls', {})}, spans={len(body.get('control_spans', []))})")
        return output_path
