"""Checkpoint 13A: connected talk service and gateway routes (fake LLM, TTS and encoder)."""
import hashlib
import json
import os
import shutil
import threading
import wave
from types import SimpleNamespace

import pytest

import config
from announcer import announcements, talk_segments
from db import database
from mobile_recommendations import Rejected
import mobile_talk
from mobile_talk import MobileTalk, resolve_settings

CONTEXT = "context-aaaaaaaaaaaaaaaa"
DEVICE = "device-1"


def write_wav(path, seconds=1.0, rate=16000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * int(rate * seconds))
    return str(path)


class FakeRecommendations:
    def __init__(self):
        self._lock = threading.RLock()
        self._contexts = {CONTEXT: SimpleNamespace(device_id=DEVICE)}

    def _owned(self, device_id, context_id):
        context = self._contexts.get(context_id)
        if context is None or context.device_id != device_id:
            raise Rejected(404, "context_not_found")
        return context


class Engines:
    """Fake chat + TTS behind the desktop generators."""

    def __init__(self, tmp_path):
        self.tmp, self.prompts, self.count = tmp_path, [], 0

    def chat(self):
        engines = self

        class Provider:
            def chat(self, model=None, messages=None, **kwargs):
                prompt = messages[-1]["content"]
                engines.prompts.append(prompt)
                if '"caller_line_1"' in prompt:
                    lines = {key: f"{key} text" for key in
                             ["dj_line_1", "caller_line_1", "dj_line_2", "caller_line_2", "dj_line_3",
                              "caller_line_3", "dj_line_4", "caller_line_4"]}
                    lines.update(dj_closer="closing words", mood_shift="energetic", energy_direction="up")
                    return SimpleNamespace(content=json.dumps(lines))
                return SimpleNamespace(content="Hello there, night owls.")
        return Provider()

    def tts(self, text, *args, **kwargs):
        self.count += 1
        return write_wav(self.tmp / f"tts-{self.count}.wav", seconds=0.5)


@pytest.fixture
def engines(tmp_path, monkeypatch):
    fake = Engines(tmp_path)
    monkeypatch.setattr(config, "TTS_ENABLED", True)
    for module in (talk_segments, announcements):
        monkeypatch.setattr(module, "llm_chat", fake.chat)
        monkeypatch.setattr(module, "tts_say", fake.tts)
    database.set_setting("radio_intro_frequency", "1000")
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET artist = 'Artist ' || id WHERE id <= 20")
        conn.commit()
    return fake


def copy_render(source, target):
    shutil.copyfile(source, target)


class Deferred:
    def __init__(self):
        self.pending = []

    def __call__(self, fn):
        self.pending.append(fn)

    def drain(self):
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def talk(tmp_path, engines):
    return MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path / "cache"), run=lambda fn: fn(),
                      render=copy_render, available=lambda: (True, None),
                      gap_log=str(tmp_path / "logs" / "gaps.jsonl"))


SEQ = {"n": 0}


def turn(service, seq, *, after="1", nxt="2", device=DEVICE, **extra):
    body = {"protocol_version": 1, "turn_id": f"turn-{seq}", "turn_seq": seq,
            "after_song": {"song_id": after}, "next_song": {"song_id": nxt}, "mood": "neutral",
            "local_time": {"iso": "2026-11-19T23:15:00", "weekday": "thursday"}}
    body.update(extra)
    return service.start_turn(device, CONTEXT, body)


def callers_only(frequency=2):
    return {"frequency": frequency, "callers": True, "monologues": False, "guests": False}


def guests_only(songs=2):
    return {"frequency": 2, "callers": False, "monologues": False, "guests": True, "guest_songs": songs}


def outcome(talk, job_id, value, **extra):
    return talk.outcome(DEVICE, job_id, {"protocol_version": 1, "outcome": value, **extra})


def counter(talk):
    return talk._shows[CONTEXT].talk["_song_counter"]


# -- settings ---------------------------------------------------------------

def test_settings_use_phone_override_else_fresh_desktop_value():
    database.set_setting("talk_frequency", "6")
    database.set_setting("talk_caller_enabled", "false")
    resolved = resolve_settings({})
    assert resolved["talk_frequency"] == 6 and resolved["talk_caller_enabled"] == "false"
    database.set_setting("talk_frequency", "3")
    assert resolve_settings({})["talk_frequency"] == 3
    resolved = resolve_settings({"frequency": 9, "callers": True, "guest_songs": 5})
    assert resolved["talk_frequency"] == 9
    assert resolved["talk_caller_enabled"] == "true"
    assert resolved["talk_guest_songs_per_set"] == 5
    assert resolved["talk_monologue_enabled"] == "true"


def test_desktop_talk_switch_does_not_gate_the_phone(talk):
    database.set_setting("talk_segments_enabled", "false")
    turn(talk, 1, talk=callers_only())
    assert turn(talk, 2, talk=callers_only())["state"] == "pending"


# -- capability -------------------------------------------------------------

def test_capability_shape_and_unavailable_reason(tmp_path):
    service = MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path), available=lambda: (False, "tts_disabled"))
    cap = service.capability()
    assert cap == {"available": False, "reason": "tts_disabled",
                   "segment_types": ["caller", "dj_monologue", "guest_takeover"],
                   "announcements": True, "max_clip_seconds": 180}


def test_unavailable_turn_returns_none_but_still_counts_the_song(tmp_path, engines):
    service = MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path), run=lambda fn: fn(),
                         render=copy_render, available=lambda: (False, "tts_disabled"))
    assert turn(service, 1) == {"state": "none", "reason": "tts_disabled"}
    assert counter(service) == 1


# -- turns, commit and rollback ----------------------------------------------

def test_counter_below_frequency_gives_no_clip(talk):
    reply = turn(talk, 1, talk=callers_only(3))
    assert talk.job(DEVICE, reply["job_id"])["state"] == "none"
    assert counter(talk) == 1


def test_caller_clip_ready_with_metadata_and_shift(talk):
    turn(talk, 1, talk=callers_only())
    reply = turn(talk, 2, talk=callers_only())
    job = talk.job(DEVICE, reply["job_id"])
    assert job["state"] == "ready" and job["kind"] == "talk" and job["segment_type"] == "caller"
    assert job["title"].endswith("on the line")
    assert job["speakers"][0]["id"] == "dj" and job["speakers"][1]["id"].startswith("caller_")
    assert job["transcript"][0] == {"speaker": "dj", "text": "dj_line_1 text"}
    assert job["transcript"][1]["speaker"].startswith("caller_")
    assert job["transcript"][-1] == {"speaker": "dj", "text": "closing words"}
    assert job["shift"] == {"mood": "energetic", "feel": None, "restore_at_end": False}
    assert job["guest_set"] is None
    assert job["duration_ms"] > 0
    data = talk.resource(DEVICE, job["resource"]["id"])
    assert hashlib.sha256(data).hexdigest() == job["resource"]["sha256"]
    assert len(data) == job["resource"]["byte_length"]
    assert job["resource"]["mime"] == "audio/mp4"


def test_played_commits_the_show_state(talk):
    turn(talk, 1, talk=callers_only())
    job = turn(talk, 2, talk=callers_only())["job_id"]
    assert counter(talk) == 2            # provisional: not reset until the clip is reported
    assert outcome(talk, job, "played")["committed"] is True
    assert counter(talk) == 0
    assert talk._shows[CONTEXT].talk["_recent_segment_types"] == ["caller"]
    assert talk.job(DEVICE, turn(talk, 3, talk=callers_only())["job_id"])["state"] == "none"


def test_skipped_after_start_commits_like_played(talk):
    turn(talk, 1, talk=callers_only())
    job = turn(talk, 2, talk=callers_only())["job_id"]
    assert outcome(talk, job, "skipped", heard_s=4.5)["committed"] is True
    assert counter(talk) == 0


@pytest.mark.parametrize("value", ["missed", "discarded"])
def test_missed_or_discarded_rolls_back(talk, value):
    turn(talk, 1, talk=callers_only())
    job = turn(talk, 2, talk=callers_only())
    rid = talk.job(DEVICE, job["job_id"])["resource"]["id"]
    assert outcome(talk, job["job_id"], value)["committed"] is False
    assert counter(talk) == 2
    assert talk._shows[CONTEXT].talk["_recent_segment_types"] == []
    with pytest.raises(Rejected):
        talk.resource(DEVICE, rid)
    # The next song start still triggers talk because nothing was committed.
    assert talk.job(DEVICE, turn(talk, 3, talk=callers_only())["job_id"])["state"] == "ready"


def test_unreported_clip_is_rolled_back_by_the_next_turn(talk):
    turn(talk, 1, talk=callers_only())
    first = turn(talk, 2, talk=callers_only())["job_id"]
    second = turn(talk, 3, talk=callers_only())["job_id"]
    assert talk.job(DEVICE, first)["state"] == "cancelled"
    assert talk.job(DEVICE, second)["state"] == "ready"
    with pytest.raises(Rejected) as late:
        outcome(talk, first, "played")
    assert late.value.status == 409


def test_outcome_is_idempotent_and_conflicts_on_change(talk):
    turn(talk, 1, talk=callers_only())
    job = turn(talk, 2, talk=callers_only())["job_id"]
    outcome(talk, job, "played")
    assert outcome(talk, job, "played")["outcome"] == "played"
    with pytest.raises(Rejected) as conflict:
        outcome(talk, job, "missed")
    assert conflict.value.status == 409


def test_duplicate_turn_is_idempotent_and_stale_turn_rejected(talk):
    first = turn(talk, 5)
    assert turn(talk, 5) == first
    assert counter(talk) == 1
    with pytest.raises(Rejected) as stale:
        turn(talk, 4)
    assert stale.value.status == 409 and stale.value.detail == {"reason": "stale_turn"}


def test_superseded_pending_job_is_cancelled_and_never_published(tmp_path, engines):
    runner = Deferred()
    service = MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path / "c"), run=runner, render=copy_render,
                         available=lambda: (True, None))
    turn(service, 1, talk=callers_only())
    first = turn(service, 2, talk=callers_only())["job_id"]
    second = turn(service, 3, talk=callers_only())["job_id"]
    runner.drain()
    assert service.job(DEVICE, first)["state"] == "cancelled"
    assert service.job(DEVICE, second)["state"] == "ready"
    assert len(service._resources) == 1


def test_counter_advances_only_from_reported_starts(talk):
    for seq in range(1, 4):
        turn(talk, seq, talk=callers_only(10))
    assert counter(talk) == 3


# -- guest takeover -----------------------------------------------------------

def test_guest_set_lifecycle(talk):
    turn(talk, 1, talk=guests_only())
    intro = talk.job(DEVICE, turn(talk, 2, talk=guests_only())["job_id"])
    assert intro["segment_type"] == "guest_takeover_intro"
    assert intro["guest_set"]["songs"] == 2 and intro["guest_set"]["persona"]
    assert intro["title"].endswith("takes over")
    outcome(talk, intro["job_id"], "played")
    assert talk._shows[CONTEXT].talk["_current_guest_takeover"]["songs_remaining"] == 2
    middle = talk.job(DEVICE, turn(talk, 3, talk=guests_only())["job_id"])
    assert middle["state"] == "none"
    outro = talk.job(DEVICE, turn(talk, 4, talk=guests_only())["job_id"])
    assert outro["segment_type"] == "guest_takeover_outro" and outro["guest_set"] == {"ends": True}
    outcome(talk, outro["job_id"], "played")
    assert talk._shows[CONTEXT].talk["_current_guest_takeover"] is None


def test_missed_guest_intro_does_not_start_a_takeover(talk):
    turn(talk, 1, talk=guests_only())
    intro = turn(talk, 2, talk=guests_only())["job_id"]
    outcome(talk, intro, "missed")
    assert talk._shows[CONTEXT].talk["_current_guest_takeover"] is None


def test_missed_guest_outro_still_ends_the_set(talk):
    turn(talk, 1, talk=guests_only())
    outcome(talk, turn(talk, 2, talk=guests_only())["job_id"], "played")
    turn(talk, 3, talk=guests_only())
    outro = turn(talk, 4, talk=guests_only())["job_id"]
    outcome(talk, outro, "missed")
    show = talk._shows[CONTEXT].talk
    assert show["_current_guest_takeover"] is None
    assert show["_recent_guests"]


def test_superseded_guest_outro_still_ends_the_set(tmp_path, engines):
    runner = Deferred()
    service = MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path / "c"), run=runner, render=copy_render,
                         available=lambda: (True, None))
    turn(service, 1, talk=guests_only())
    intro = turn(service, 2, talk=guests_only())["job_id"]
    runner.drain()
    outcome(service, intro, "played")
    turn(service, 3, talk=guests_only())
    turn(service, 4, talk=guests_only())          # outro due
    turn(service, 5, talk=guests_only())          # skipped before it was ready
    runner.drain()
    assert service._shows[CONTEXT].talk["_current_guest_takeover"] is None


# -- announcements -------------------------------------------------------------

def test_announcement_when_no_talk_is_due(talk):
    database.set_setting("radio_intro_frequency", "1")
    job = talk.job(DEVICE, turn(talk, 1, talk=callers_only(10))["job_id"])
    assert job["kind"] == "announcement" and job["segment_type"] == "announcement"
    assert job["trigger"] == "radio_intro"
    assert job["transcript"] == [{"speaker": "dj", "text": "Hello there, night owls."}]
    outcome(talk, job["job_id"], "played")
    assert talk._shows[CONTEXT].announce["_last_announcement_time"] > 0
    # Within the desktop interval no second announcement is made.
    assert talk.job(DEVICE, turn(talk, 2, talk=callers_only(10))["job_id"])["state"] == "none"


def test_skip_streak_announcement_takes_priority(talk):
    job = talk.job(DEVICE, turn(talk, 1, talk=callers_only(10), skip_streak=3)["job_id"])
    assert job["trigger"] == "skip_streak"


def test_talk_segment_suppresses_the_announcement(talk):
    database.set_setting("radio_intro_frequency", "1")
    turn(talk, 1, talk=callers_only())
    job = talk.job(DEVICE, turn(talk, 2, talk=callers_only())["job_id"])
    assert job["kind"] == "talk"


# -- context and prompts ---------------------------------------------------------

def test_prompts_receive_listener_context_and_phone_time(talk, engines):
    turn(talk, 1, talk=callers_only())
    turn(talk, 2, talk=callers_only(), feel={"id": "f1", "name": "Rainy commute"},
         milestones=[{"id": "m1", "text": "1,000 songs heard"}],
         next_song_history={"play_count": 50, "days_since": 1},
         session_feedback=[{"song": {"song_id": "3"}, "liked": True}])
    prompt = engines.prompts[-1]
    assert "Listener context" in prompt
    assert "Rainy commute" in prompt and "1,000 songs heard" in prompt
    assert "favourites" in prompt and "liked 'Fixture 3'" in prompt
    assert "Thursday" in prompt


def test_filename_songs_and_metadata_gap_log(talk, tmp_path):
    turn(talk, 1, talk=callers_only(), after="25")
    turn(talk, 2, talk=callers_only(), next_song={"title": "", "source": "filename"})
    turn(talk, 3, talk=callers_only(), after="25")
    lines = [json.loads(line) for line in open(tmp_path / "logs" / "gaps.jsonl", encoding="utf-8")]
    keys = {(entry["song"], entry["missing"]) for entry in lines}
    assert ("catalog:25", "artist") in keys and ("filename:", "title") in keys
    assert len(lines) == len(keys)


def test_song_reference_validation(talk):
    with pytest.raises(Rejected) as unknown:
        turn(talk, 1, after="99999")
    assert unknown.value.status == 400
    with pytest.raises(Rejected) as mixed:
        turn(talk, 1, next_song={"song_id": "2", "title": "x", "source": "catalog"})
    assert mixed.value.status == 422


def test_foreign_device_and_unknown_context(talk):
    with pytest.raises(Rejected) as foreign:
        turn(talk, 1, device="device-2")
    assert foreign.value.status == 404
    job = turn(talk, 1)["job_id"]
    with pytest.raises(Rejected):
        talk.job("device-2", job)


def test_resources_expire(tmp_path, engines):
    now = {"t": 1_000_000.0}
    service = MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path / "c"), run=lambda fn: fn(),
                         render=copy_render, available=lambda: (True, None), wall=lambda: now["t"])
    turn(service, 1, talk=callers_only())
    job = service.job(DEVICE, turn(service, 2, talk=callers_only())["job_id"])
    rid = job["resource"]["id"]
    assert job["resource"]["expires_at"] == 1_000_000 + 7200
    path = service._resources[rid].path
    now["t"] += 7200
    with pytest.raises(Rejected):
        service.resource(DEVICE, rid)
    assert not os.path.exists(path)


def test_forget_device_drops_everything(talk):
    turn(talk, 1, talk=callers_only())
    turn(talk, 2, talk=callers_only())
    talk.forget_device(DEVICE)
    assert not talk._shows and not talk._jobs and not talk._resources


def test_no_desktop_history_writes(talk):
    def counts():
        with database.get_connection_cm() as conn:
            return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("play_events", "queue_history")]
    before = counts()
    turn(talk, 1, talk=callers_only())
    outcome(talk, turn(talk, 2, talk=callers_only())["job_id"], "played")
    assert counts() == before


# -- gateway ------------------------------------------------------------------

def test_gateway_talk_routes_end_to_end(tmp_path, engines, monkeypatch):
    from mobile_gateway import create_mobile_gateway
    from mobile_recommendations import MobileRecommendations
    from tests.test_mobile_pairing import request, setup
    store, identity, pairing = setup(tmp_path)
    recommendations = MobileRecommendations(store, deadline_seconds=5)
    service = MobileTalk(recommendations, cache_dir=str(tmp_path / "cache"), run=lambda fn: fn(),
                         render=copy_render, available=lambda: (True, None))
    app = create_mobile_gateway(store, identity, pairing, recommendations=recommendations, talk=service)
    app.state.admission.enable(1)
    device = store.issue_device("Phone", ["catalog:read", "recommend:read"])
    headers = {"Authorization": "Bearer " + device["token"], "Content-Type": "application/json"}
    prefix = "/api/portable/v1"

    def call(method, path, body=None, extra=None):
        content = None if body is None else json.dumps(body).encode()
        return request(app, method, prefix + path, {**headers, **(extra or {})}, content)

    caps = call("GET", "/capabilities").json()
    assert caps["connected_talk"]["available"] is True
    assert caps["native_speech_generation"] is False
    context = call("POST", "/contexts", {"protocol_version": 1, "mood": "neutral", "exploration": .3,
                                         "vibe_lock_song_id": None, "recent": []}).json()["context_id"]
    base = {"protocol_version": 1, "after_song": {"song_id": "1"}, "next_song": {"song_id": "2"}, "mood": "neutral",
            "talk": callers_only()}
    first = call("POST", f"/contexts/{context}/talk", {**base, "turn_id": "t1", "turn_seq": 1})
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    reply = call("POST", f"/contexts/{context}/talk", {**base, "turn_id": "t2", "turn_seq": 2}).json()
    job = call("GET", f"/talk/jobs/{reply['job_id']}").json()
    assert job["state"] == "ready"
    rid = job["resource"]["id"]
    clip = call("GET", f"/talk/resources/{rid}")
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/mp4"
    assert hashlib.sha256(clip.content).hexdigest() == job["resource"]["sha256"]
    head = call("HEAD", f"/talk/resources/{rid}")
    assert head.status_code == 200 and head.headers["content-length"] == str(job["resource"]["byte_length"])
    assert call("GET", f"/talk/resources/{rid}", extra={"Range": "bytes=0-10"}).status_code == 400
    done = call("POST", f"/talk/jobs/{reply['job_id']}/outcome", {"protocol_version": 1, "outcome": "played"})
    assert done.json()["committed"] is True
    stale = call("POST", f"/contexts/{context}/talk", {**base, "turn_id": "t0", "turn_seq": 1})
    assert stale.status_code == 409
    assert call("GET", "/talk/jobs/" + "x" * 32).status_code == 404

    sync_only = store.issue_device("Other", ["catalog:read"])
    denied = request(app, "GET", prefix + f"/talk/jobs/{reply['job_id']}",
                     {"Authorization": "Bearer " + sync_only["token"]}, None)
    assert denied.status_code == 403
    other = store.issue_device("Second", ["recommend:read"])
    foreign = request(app, "GET", prefix + f"/talk/jobs/{reply['job_id']}",
                      {"Authorization": "Bearer " + other["token"]}, None)
    assert foreign.status_code == 404

