"""ZONOS3-Mini engine: HTTP contract with a local zonos3-server, without a server or model."""
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from announcer import tts_engine, voice_manager, zonos3_engine


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


class FakeHttp:
    """Records requests; routes GET/POST by path."""

    def __init__(self, speakers=None, controls=None, wav=b"RIFFfake", down=False, generate_status=200,
                 register_status=200, capabilities=None):
        self.capabilities = capabilities
        self.calls = []
        self.headers = []
        self.register_status = register_status
        self.registered = 0
        self.speakers = speakers if speakers is not None else []
        self.controls = controls
        self.wav = wav
        self.down = down
        self.generate_status = generate_status

    def _check(self):
        if self.down:
            raise ConnectionError("refused")

    def get(self, url, timeout=None, headers=None):
        self.calls.append(("GET", url, None, timeout))
        self._check()
        if url.endswith("/v2/health"):
            return FakeResponse(payload={"status": "ok"})
        if url.endswith("/v2/speakers"):
            return FakeResponse(payload={"speakers": self.speakers})
        if url.endswith("/v2/controls"):
            if self.controls is None:
                return FakeResponse(status=404, payload={"detail": "no steer bundle"})
            return FakeResponse(payload=self.controls)
        if url.endswith("/v2/capabilities") and self.capabilities is not None:
            return FakeResponse(payload=self.capabilities)
        return FakeResponse(status=404)

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append(("POST", url, dict(json or {}), timeout))
        self.headers.append(headers or {})
        self._check()
        if url.endswith("/v2/speakers"):
            if self.register_status != 200:
                return FakeResponse(status=self.register_status, payload={"detail": "nope"})
            self.registered += 1
            return FakeResponse(payload={"id": f"spk_{self.registered}", "speaker_modes": ["embedding"]})
        status = self.generate_status
        if isinstance(status, list):
            status = status.pop(0) if len(status) > 1 else status[0]
        return FakeResponse(status=status, content=self.wav, payload={"detail": "unknown speaker"})

    def register_bodies(self):
        return [body for method, url, body, _ in self.calls if method == "POST" and url.endswith("/v2/speakers")]

    def generate_bodies(self):
        return [body for method, url, body, _ in self.calls if method == "POST" and url.endswith("/v2/generate")]


@pytest.fixture
def clips(tmp_path, monkeypatch):
    clip = tmp_path / "The Trusted Morning Host.mp3"
    clip.write_bytes(b"ID3clip")
    monkeypatch.setattr(zonos3_engine, "_list_reference_clips", lambda: {"The Trusted Morning Host": str(clip)})
    return clip


def make_engine(http, base_url="http://127.0.0.1:1919"):
    return zonos3_engine.Zonos3TTSEngine(base_url=base_url, http=http)


def test_engine_is_registered():
    assert tts_engine.AVAILABLE_ENGINES["zonos3"] is zonos3_engine.Zonos3TTSEngine


def test_server_voice_uses_speaker_id_and_writes_buffered_wav(tmp_path, clips):
    http = FakeHttp(speakers=[{"id": "default_abc123", "name": "AmericanFemale"}])
    eng = make_engine(http)
    out = tmp_path / "out.wav"

    assert eng.synthesize("Hello there.", "AmericanFemale", str(out)) == str(out)
    assert out.read_bytes() == b"RIFFfake"
    [body] = http.generate_bodies()
    assert body["text"] == "Hello there."
    assert body["stream"] is False and body["format"] == "wav"
    assert body["speaker_embedding_id"] == "default_abc123"
    assert "speaker_audio_base64" not in body


def test_reference_clip_is_registered_once_and_reused(tmp_path, clips):
    http = FakeHttp()
    eng = make_engine(http)

    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    assert eng.synthesize("Again!", "The Trusted Morning Host", str(tmp_path / "p.wav"))
    [reg] = http.register_bodies()
    assert base64.b64decode(reg["speaker_audio_base64"]) == b"ID3clip"
    assert reg["speaker_audio_name"] == "The Trusted Morning Host.mp3"
    first, second = http.generate_bodies()
    for body in (first, second):
        assert body["speaker_embedding_id"] == "spk_1"
        assert body["speaker_mode"] == "embedding"  # cloned clips have no reference codes
        assert "speaker_audio_base64" not in body
    # Registered speakers are session-scoped on the server.
    assert all(h.get("X-TTS-Session-ID") == zonos3_engine.SESSION_ID for h in http.headers)


def test_failed_registration_sends_the_clip_inline(tmp_path, clips):
    http = FakeHttp(register_status=500)
    eng = make_engine(http)

    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert base64.b64decode(body["speaker_audio_base64"]) == b"ID3clip"
    assert body["speaker_mode"] == "embedding"
    assert "speaker_embedding_id" not in body


def test_stale_registration_after_server_restart_is_redone_once(tmp_path, clips):
    http = FakeHttp(generate_status=[200, 400, 200])
    eng = make_engine(http)
    out = tmp_path / "o.wav"

    assert eng.synthesize("One", "The Trusted Morning Host", str(out))
    assert eng.synthesize("Two", "The Trusted Morning Host", str(out))
    assert http.registered == 2
    assert [b["speaker_embedding_id"] for b in http.generate_bodies()] == ["spk_1", "spk_1", "spk_2"]


def test_only_controls_the_server_lists_are_sent(tmp_path, clips):
    # Shape of a live zonos3-server GET /v2/controls (sets and directions keyed by name).
    controls = {
        "bundle": "studio_mect60k_x10",
        "sets": {"delivery": {"space": "attnres_source"}, "expressive": {"space": "attnres_source"}},
        "directions": {
            "delivery.tempo": {"set": "delivery", "space": "attnres_source", "kind": "axis"},
            "expressive.expressive": {"set": "expressive", "space": "attnres_source", "kind": "axis"},
        },
        "ui": {"controls": []},
    }
    http = FakeHttp(controls=controls)
    eng = make_engine(http)

    eng.synthesize("Fast.", "The Trusted Morning Host", str(tmp_path / "a.wav"), speed=1.05,
                   controls={"expressive.expressive": 4, "mood.happy": 3})
    [body] = http.generate_bodies()
    assert body["controls"]["expressive.expressive"] == 4
    assert "mood.happy" not in body["controls"]
    assert 0 < body["controls"]["delivery.tempo"] <= 2


def test_flat_control_ids_are_understood(tmp_path, clips):
    http = FakeHttp(controls={"controls": [{"id": "delivery.tempo"}]})
    eng = make_engine(http)
    eng.synthesize("Slow.", "The Trusted Morning Host", str(tmp_path / "a.wav"), speed=0.75)
    [body] = http.generate_bodies()
    assert -2 <= body["controls"]["delivery.tempo"] < 0


def test_without_steer_bundle_no_controls_are_sent(tmp_path, clips):
    http = FakeHttp(controls=None)
    eng = make_engine(http)
    eng.synthesize("Plain.", "The Trusted Morning Host", str(tmp_path / "a.wav"), speed=1.0, controls={"delivery.lively": 2})
    [body] = http.generate_bodies()
    assert "controls" not in body


def test_unknown_voice_and_server_errors_return_none(tmp_path, clips):
    eng = make_engine(FakeHttp())
    assert eng.synthesize("Hi", "Nobody", str(tmp_path / "a.wav")) is None

    failing = make_engine(FakeHttp(generate_status=422))
    out = tmp_path / "b.wav"
    assert failing.synthesize("Hi", "The Trusted Morning Host", str(out)) is None
    assert not out.exists()


def test_down_server_is_not_ready_and_lists_only_local_clips(clips):
    http = FakeHttp(down=True)
    eng = make_engine(http)
    assert eng.is_ready() is False
    assert eng.get_available_voices() == ["The Trusted Morning Host"]
    # Health is cached briefly so status polling does not hammer a dead port.
    eng.is_ready()
    assert sum(1 for c in http.calls if c[1].endswith("/v2/health")) == 1


def test_running_server_is_ready_and_lists_server_voices(clips):
    eng = make_engine(FakeHttp(speakers=[{"id": "default_1", "path": "en/AmericanMale.mp3"}, {"id": "default_2"}]))
    assert eng.is_ready() is True
    assert eng.get_available_voices() == ["The Trusted Morning Host", "AmericanMale", "default_2"]


def test_voice_manager_falls_back_to_piper_when_zonos_server_is_down(monkeypatch, tmp_path):
    piper_calls = []

    def piper_synth(text, voice, path):
        piper_calls.append(voice)
        Path(path).write_bytes(b"wav")
        return path

    piper = SimpleNamespace(is_ready=lambda: True, synthesize=piper_synth)
    zonos = SimpleNamespace(is_ready=lambda: False, get_available_voices=lambda: [])
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("zonos3", zonos))
    monkeypatch.setattr(manager, "get_engine", lambda eid=None: piper if eid == "piper" else zonos)
    monkeypatch.setattr(voice_manager, "run_with_timeout", lambda fn, timeout: (True, fn()))
    monkeypatch.setattr(voice_manager, "_append_silence", lambda path: None)
    monkeypatch.setattr(voice_manager, "_prepend_silence", lambda path: None)

    assert manager.synthesize("Hello", None, str(tmp_path))
    assert piper_calls == ["amy"]


def test_voice_manager_uses_piper_when_zonos_returns_nothing(monkeypatch, tmp_path):
    piper_calls = []

    def piper_synth(text, voice, path):
        piper_calls.append(voice)
        Path(path).write_bytes(b"wav")
        return path

    piper = SimpleNamespace(is_ready=lambda: True, synthesize=piper_synth)
    zonos = SimpleNamespace(is_ready=lambda: True, synthesis_timeout=30,
                            synthesize=lambda *a, **k: None)
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("zonos3", zonos))
    monkeypatch.setattr(manager, "get_engine", lambda eid=None: piper if eid == "piper" else zonos)
    monkeypatch.setattr(voice_manager, "run_with_timeout", lambda fn, timeout: (True, fn()))
    monkeypatch.setattr(voice_manager, "_append_silence", lambda path: None)
    monkeypatch.setattr(voice_manager, "_prepend_silence", lambda path: None)

    assert manager.synthesize("Hello", "Somebody", str(tmp_path))
    assert piper_calls == ["amy"]


# Shape of a live zonos3-server GET /v2/capabilities with the full decoder and encoders loaded.
FULL_CAPS = {
    "decoders": [{"id": "keep2_plain"}, {"id": "full_mr"}],
    "decoder": "keep2_plain",
    "speaker_modes": ["reference", "embedding"],
}
LIGHT_CAPS = {"decoders": [{"id": "keep2_plain"}], "decoder": "keep2_plain", "speaker_modes": ["embedding"]}


def test_clip_voices_use_reference_mode_and_full_decoder_when_served(tmp_path, clips):
    http = FakeHttp(capabilities=FULL_CAPS)
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert body["speaker_mode"] == "reference"
    assert body["decoder"] == "full_mr"
    assert "speaker_cfg_scale" not in body


def test_server_voices_also_get_the_full_decoder(tmp_path, clips):
    http = FakeHttp(capabilities=FULL_CAPS, speakers=[{"id": "default_1", "label": "Savannah en American F 1"}])
    eng = make_engine(http)
    assert eng.synthesize("Hi.", "Savannah en American F 1", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert body["decoder"] == "full_mr"
    assert "speaker_mode" not in body  # bundled voices ship reference codes; checkpoint default


def test_light_server_falls_back_to_embedding_and_default_decoder(tmp_path, clips):
    http = FakeHttp(capabilities=LIGHT_CAPS)
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert body["speaker_mode"] == "embedding"
    assert "decoder" not in body


def test_configured_embedding_mode_and_speaker_guidance(tmp_path, clips, monkeypatch):
    monkeypatch.setattr(zonos3_engine.config, "ZONOS3_SPEAKER_MODE", "embedding")
    monkeypatch.setattr(zonos3_engine.config, "ZONOS3_SPEAKER_CFG", 1.5)
    http = FakeHttp(capabilities=FULL_CAPS)
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert body["speaker_mode"] == "embedding"
    assert body["speaker_cfg_scale"] == 1.5


@pytest.fixture
def stored_codes(tmp_path, clips, monkeypatch):
    import numpy as np
    codes_dir = tmp_path / "codes"
    codes_dir.mkdir()
    monkeypatch.setattr(zonos3_engine.config, "ZONOS3_CODES_DIR", str(codes_dir))
    codes = np.arange(30, dtype=np.int32).reshape(3, 10)
    np.save(zonos3_engine.codes_path(str(codes_dir), "The Trusted Morning Host", str(clips)), codes)
    return codes


def test_stored_reference_codes_are_sent_without_registering(tmp_path, stored_codes):
    http = FakeHttp(capabilities=FULL_CAPS)
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert body["speaker_reference_codes"] == stored_codes.tolist()
    assert body["speaker_mode"] == "reference"
    assert body["decoder"] == "full_mr"
    assert "speaker_embedding_id" not in body and "speaker_audio_base64" not in body
    assert http.register_bodies() == []


def test_stored_codes_work_even_when_server_lacks_encoders(tmp_path, stored_codes):
    # Pre-encoded codes need no encoder, so a light server still gets reference mode.
    http = FakeHttp(capabilities=LIGHT_CAPS)
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert body["speaker_mode"] == "reference" and "speaker_reference_codes" in body


def test_codes_for_an_older_version_of_the_clip_are_ignored(tmp_path, stored_codes, clips):
    clips.write_bytes(b"ID3clip-re-recorded")
    http = FakeHttp(capabilities=FULL_CAPS)
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    [body] = http.generate_bodies()
    assert "speaker_reference_codes" not in body
    assert len(http.register_bodies()) == 1


def test_rejected_codes_fall_back_to_embedding_once(tmp_path, stored_codes):
    http = FakeHttp(capabilities=FULL_CAPS, generate_status=[400, 200])
    eng = make_engine(http)
    assert eng.synthesize("Morning!", "The Trusted Morning Host", str(tmp_path / "o.wav"))
    first, second = http.generate_bodies()
    assert "speaker_reference_codes" in first
    assert second["speaker_mode"] == "embedding" and second["speaker_embedding_id"] == "spk_1"


# ---- speech direction request fields ----

LIVE_CAPS = {
    **FULL_CAPS,
    "condition_tokens": {"enabled": True, "names": ["loudness", "fmax", "speaking_rate"]},
    "silence_prefill": {"available": True, "default_frames": 3},
}
STEER = {"directions": {"emotions.happy": {}, "delivery.loud": {}, "delivery.tempo": {}}}


def test_direction_request_is_sent_with_unknown_ids_removed(tmp_path, clips):
    http = FakeHttp(capabilities=LIVE_CAPS, controls=STEER)
    eng = make_engine(http)
    request = {
        "controls": {"emotions.happy": 2, "emotions.made_up": 3},
        "control_spans": [{"start": 0, "end": 3, "controls": {"delivery.loud": 1.5}},
                          {"start": 4, "end": 8, "controls": {"style.gone": 1}}],
        "condition_values": {"loudness": -16.0, "brightness": 3},
        "silence_prefill_frames": 0,
        "speaker_hack": True,
    }
    assert eng.synthesize("Wow now.", "The Trusted Morning Host", str(tmp_path / "o.wav"), speed=0.9, request=request)
    [body] = http.generate_bodies()
    assert body["controls"]["emotions.happy"] == 2 and "emotions.made_up" not in body["controls"]
    assert body["control_spans"] == [{"start": 0, "end": 3, "controls": {"delivery.loud": 1.5}}]
    assert body["condition_values"] == {"loudness": -16.0}
    assert body["silence_prefill_frames"] == 0
    assert "speaker_hack" not in body


def test_direction_fields_the_server_lacks_are_dropped(tmp_path, clips):
    http = FakeHttp(capabilities=FULL_CAPS, controls=None)
    eng = make_engine(http)
    request = {"controls": {"emotions.happy": 2},
               "control_spans": [{"start": 0, "end": 3, "controls": {"delivery.loud": 1.5}}],
               "condition_values": {"loudness": -16.0}, "silence_prefill_frames": 0}
    assert eng.synthesize("Wow now.", "The Trusted Morning Host", str(tmp_path / "o.wav"), request=request)
    [body] = http.generate_bodies()
    for field in ("controls", "control_spans", "condition_values", "silence_prefill_frames"):
        assert field not in body


RELEASE = {"onset_seconds": 4.0, "ramp_seconds": 1.0, "hold": 0.35, "exempt": ["delivery.tempo"]}
EASING = {"steer_hold": 0.25, "steer_onset_seconds": 1.5, "steer_ramp_seconds": 1.0}


def test_steer_easing_is_sent_when_the_server_releases_steering(tmp_path, clips):
    http = FakeHttp(capabilities={**FULL_CAPS, "steer_release": RELEASE}, controls=STEER)
    eng = make_engine(http)
    request = {"controls": {"emotions.happy": 2}, **EASING}
    assert eng.synthesize("Wow now.", "The Trusted Morning Host", str(tmp_path / "o.wav"), request=request)
    [body] = http.generate_bodies()
    for key, value in EASING.items():
        assert body[key] == value


def test_steer_easing_is_left_out_without_server_release(tmp_path, clips):
    # A hold below 1 on a server without steer_release is a 400, so it must not be sent.
    http = FakeHttp(capabilities={**FULL_CAPS, "steer_release": None}, controls=STEER)
    eng = make_engine(http)
    request = {"controls": {"emotions.happy": 2}, **EASING}
    assert eng.synthesize("Wow now.", "The Trusted Morning Host", str(tmp_path / "o.wav"), request=request)
    [body] = http.generate_bodies()
    assert not any(key in body for key in EASING)


RAW_STEER = {
    "sets": {"accents_en": {"space": "raw"}, "texture": {"space": "raw"}, "emotions": {"space": "attnres_source"}},
    "directions": {"accents_en.british": {"kind": "named"}, "texture.deep": {"kind": "named"},
                   "emotions.happy": {"kind": "named"}},
}


def test_reference_mode_drops_speaker_embedding_controls(tmp_path, stored_codes):
    # Raw (speaker-embedding) steering is refused with a reference prefix, so it is left out.
    http = FakeHttp(capabilities=FULL_CAPS, controls=RAW_STEER)
    eng = make_engine(http)
    request = {"controls": {"accents_en.british": 1.2, "texture.deep": 1, "emotions.happy": 2}}
    assert eng.synthesize("Cheers.", "The Trusted Morning Host", str(tmp_path / "o.wav"), request=request)
    [body] = http.generate_bodies()
    assert body["speaker_mode"] == "reference"
    assert body["controls"] == {"emotions.happy": 2}


def test_embedding_mode_keeps_speaker_embedding_controls(tmp_path, clips, monkeypatch):
    monkeypatch.setattr(zonos3_engine.config, "ZONOS3_SPEAKER_MODE", "embedding")
    http = FakeHttp(capabilities=FULL_CAPS, controls=RAW_STEER)
    eng = make_engine(http)
    request = {"controls": {"accents_en.british": 1.2, "emotions.happy": 2}}
    assert eng.synthesize("Cheers.", "The Trusted Morning Host", str(tmp_path / "o.wav"), request=request)
    [body] = http.generate_bodies()
    assert body["speaker_mode"] == "embedding"
    assert body["controls"] == {"accents_en.british": 1.2, "emotions.happy": 2}


PITCH_STEER = {"sets": {"delivery": {"space": "attnres_source"}},
               "directions": {"delivery.pitch": {"set": "delivery", "space": "attnres_source", "kind": "axis"},
                              "delivery.tempo": {"set": "delivery", "space": "attnres_source", "kind": "axis"}}}


def test_a_voice_adjustment_is_added_to_every_line_in_that_voice(tmp_path, clips, monkeypatch):
    monkeypatch.setattr(zonos3_engine, "VOICE_ADJUSTMENTS", {"The Trusted Morning Host": {"delivery.pitch": -0.35}})
    http = FakeHttp(controls=PITCH_STEER)
    eng = make_engine(http)
    eng.synthesize("Hi.", "The Trusted Morning Host", str(tmp_path / "a.wav"), request={"controls": {"delivery.pitch": -0.1}})
    [body] = http.generate_bodies()
    assert body["controls"]["delivery.pitch"] == pytest.approx(-0.45)


def test_other_voices_get_no_adjustment(tmp_path, clips, monkeypatch):
    monkeypatch.setattr(zonos3_engine, "VOICE_ADJUSTMENTS", {"Somebody Else": {"delivery.pitch": -0.35}})
    http = FakeHttp(controls=PITCH_STEER)
    make_engine(http).synthesize("Hi.", "The Trusted Morning Host", str(tmp_path / "a.wav"))
    [body] = http.generate_bodies()
    assert "delivery.pitch" not in body.get("controls", {})


def test_blondie_is_pitched_a_little_lower():
    assert -0.6 < zonos3_engine.VOICE_ADJUSTMENTS["Blondie - Radio Host"]["delivery.pitch"] < 0
