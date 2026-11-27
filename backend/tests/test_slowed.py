"""Checkpoint 14B: Slowed Radio voice on the desktop (fake LLM, TTS and encoder)."""
import hashlib
import json
import threading
from datetime import date

import pytest

import slowed
from announcer.personas import PERSONAS, get_guest_personas
from mobile_recommendations import Rejected
from mobile_talk import MobileTalk
from slowed import SlowedBank, SlowedLineWorker, check_line
from tests.test_mobile_talk import (CONTEXT, DEVICE, FakeRecommendations, copy_render, engines, write_wav,  # noqa: F401
                                    turn)


# -- guests -------------------------------------------------------------------

def test_effects_guests_and_voices():
    assert PERSONAS["guest_chill"].voice_id_omnivoice == "The Midnight Confidant"
    undertow = PERSONAS["guest_undertow"]
    assert undertow.name == "The Undertow" and undertow.voice_id_omnivoice == "deep radio voice"
    assert undertow.speed_base == 0.82 and undertow.slot_bias == {"energy_range": (0.0, 0.5)}
    # Slowed sets only: never picked for a normal takeover.
    assert undertow not in get_guest_personas() and PERSONAS["guest_chill"] in get_guest_personas()
    assert set(slowed.GUESTS) == {"guest_chill", "guest_undertow"}


def test_presets_match_the_phone_and_the_seed_row():
    from db import database
    assert {k: v["rate"] for k, v in slowed.PRESETS.items()} == {"really_slow": 0.75, "slowed": 0.85, "lofi": 0.90}
    seeded = json.loads(database.get_setting("slowed_presets"))
    for name, preset in slowed.PRESETS.items():
        for key in ("rate", "room", "damping", "wet", "weight"):
            assert seeded[name][key] == preset[key]


# -- line checks --------------------------------------------------------------

@pytest.mark.parametrize("text,kind,reason", [
    ("Let it all slow down now, sink beneath the surface with me and let the current carry every note somewhere deep and soft",
     "opener_auto", None),
    ("Still slowed, still drifting under the waves", "mid", None),
    ("That's the end of my set, the music is back to normal speed now, handing back to the station", "closer", None),
    ("Short", "opener_auto", "length"),
    ("Let it slow down tonight, sink beneath the surface with me and let the current carry every note somewhere deep and soft",
     "opener_auto", "time"),
    ("Let the station breathe, sink with me and let the current carry every note somewhere deep and soft and warm", "closer", "cue"),
    ("This beautiful little moment belongs to you now, just breathe with me and let every note carry you somewhere new",
     "opener_auto", "cue"),
    ("Let it slow down, sink beneath (whispers) the surface with me and let the current carry every note somewhere soft",
     "opener_auto", "markup"),
])
def test_check_line_rules(text, kind, reason):
    assert check_line(text, kind) == reason


def test_check_line_rejects_library_names_and_repeats():
    line = "Let it slow down, sink beneath gravity falls with me and let the current carry every note somewhere deep and soft"
    assert check_line(line, "opener_auto", names={"gravity falls"}) == "name"
    assert check_line(line, "opener_auto", names={"gravity falls"}, allow_names=True) is None
    again = "Let it slow down, sink beneath the waves with me and let the tide carry every note somewhere deep and soft"
    assert check_line(again, "opener_auto", existing=[line]) == "repeat"


# -- bank -----------------------------------------------------------------------

@pytest.fixture
def bank(tmp_path):
    return SlowedBank(directory=str(tmp_path / "lines"), render=copy_render, duration=lambda path: 4200,
                      today=lambda: date(2026, 11, 27))


def add(bank, tmp_path, guest="guest_undertow", kind="opener_auto", preset="slowed", n=1):
    ids = []
    for i in range(n):
        ids.append(bank.add(guest, kind, preset, f"line {kind} {i}", write_wav(tmp_path / f"{kind}-{i}-{len(ids)}.wav")))
    return ids


def test_bank_manifest_is_revisioned_and_metadata_only(bank, tmp_path):
    empty = bank.manifest(0)
    assert empty["revision"] == 0 and empty["lines"] == [] and empty["retired"] == []
    first = add(bank, tmp_path, n=2)
    manifest = bank.manifest(0)
    assert manifest["revision"] == 2 and [line["id"] for line in manifest["lines"]] == first
    line = manifest["lines"][0]
    assert set(line) == {"id", "guest", "kind", "preset", "sha256", "byte_length", "duration_ms", "mime", "created"}
    assert "text" not in line and line["mime"] == "audio/mp4" and line["duration_ms"] == 4200
    data = bank.read(line["id"])
    assert hashlib.sha256(data).hexdigest() == line["sha256"] and len(data) == line["byte_length"]
    second = add(bank, tmp_path, kind="mid", preset=None)
    delta = bank.manifest(2)
    assert [l["id"] for l in delta["lines"]] == second and delta["revision"] == 3 and not delta["full"]
    # A revision from the future (another desktop) means a full list.
    assert bank.manifest(99)["full"] is True


def test_bank_rejects_unknown_or_mismatched_lines(bank, tmp_path):
    wav = write_wav(tmp_path / "x.wav")
    with pytest.raises(ValueError):
        bank.add("guest_nerd", "opener_auto", "slowed", "x", wav)
    with pytest.raises(ValueError):
        bank.add("guest_chill", "mid", "slowed", "x", wav)
    with pytest.raises(ValueError):
        bank.add("guest_chill", "closer", None, "x", wav)
    assert bank.read("../../etc/passwd") is None and bank.read("a" * 20) is None


def test_most_played_lines_retire_once_a_day(bank, tmp_path):
    ids = add(bank, tmp_path, n=10)
    retired = bank.record_plays({ids[0]: 5, ids[1]: 3, ids[2]: 1})
    assert retired == [ids[0], ids[1]]                           # 20% of 10
    manifest = bank.manifest(10)
    assert sorted(manifest["retired"]) == sorted(retired) and manifest["revision"] == 11
    assert bank.read(ids[0]) is None
    assert bank.record_plays({ids[3]: 9}) == []                   # already retired today


def test_gaps_follow_the_plan_targets(bank):
    wanted = slowed.targets()
    per_guest = sum(n for (g, _, _), n in wanted.items() if g == "guest_undertow")
    assert per_guest == 112
    assert bank.gaps()[0][1] == "opener_auto"


# -- worker -------------------------------------------------------------------

def good_lines(kind, count=4):
    words = {"opener_auto": "slow down and sink beneath the surface with me, letting the current carry each note somewhere deep",
             "closer": "the set is done, the music is back to normal speed and I am handing back to the station"}
    starts = ["Breathe out", "Easy now", "Close your eyes", "Lean back", "Stay here", "Hush now"]
    return json.dumps({"lines": [f"{starts[i]}, {words[kind]} variation {chr(97 + i)}" for i in range(count)]})


def test_worker_fills_a_gap_while_idle_and_never_while_busy(bank, tmp_path):
    spoken = []
    chats = []

    def chat(prompt):
        chats.append(prompt)
        return good_lines("opener_auto")

    def speak(text, guest):
        spoken.append((guest, text))
        return write_wav(tmp_path / f"s{len(spoken)}.wav")
    busy = {"on": True}
    worker = SlowedLineWorker(bank, idle=lambda: not busy["on"], chat=chat, speak=speak, names=set())
    assert worker.fill_once() is False and not chats
    busy["on"] = False
    assert worker.fill_once() is True
    guest, kind, preset, _ = bank.gaps()[0]
    assert spoken and all(g == "guest_chill" for g, _ in spoken)
    assert "Never name a song" in chats[0] and "slowed set" in chats[0]
    assert bank.stats()["lines"] == len(spoken)


def test_worker_skips_while_a_talk_turn_holds_the_gate(bank, tmp_path):
    gate = threading.Lock()
    calls = []
    worker = SlowedLineWorker(bank, idle=lambda: True, chat=lambda p: calls.append(p) or good_lines("opener_auto"),
                              speak=lambda t, g: write_wav(tmp_path / "w.wav"), names=set(), gate=gate)
    with gate:
        worker.fill_once()
    assert calls == [] and bank.stats()["lines"] == 0


# -- live lines in connected turns --------------------------------------------------

@pytest.fixture
def talk(tmp_path, engines):  # noqa: F811
    lines = []

    def live(moment, guest, preset, **kwargs):
        lines.append((moment, guest, preset, kwargs))
        return {"opener": "Let me slow it all down now, sink with me beneath the surface for a while",
                "mid": "Still slowed, still drifting", "closer": "Back to normal speed now, handing back to you"}[moment]
    service = MobileTalk(FakeRecommendations(), cache_dir=str(tmp_path / "cache"), run=lambda fn: fn(),
                         render=copy_render, available=lambda: (True, None), live_line=live,
                         speak=lambda text, guest: write_wav(tmp_path / f"live-{len(lines)}.wav"),
                         bank=lambda: SlowedBank(directory=str(tmp_path / "bank"), render=copy_render, duration=lambda p: 1000))
    service.lines = lines
    return service


def test_effects_turn_returns_a_live_opener_with_slowed_metadata(talk):
    reply = turn(talk, 1, effects={"moment": "opener", "preset": "really_slow", "guest": "guest_undertow", "songs": 6,
                                   "trigger": "manual"})
    job = talk.job(DEVICE, reply["job_id"])
    assert job["state"] == "ready" and job["segment_type"] == "slowed_opener"
    assert job["slowed"] == {"moment": "opener", "preset": "really_slow", "guest": "guest_undertow", "songs": 6, "trigger": "manual"}
    assert job["speakers"] == [{"id": "guest_undertow", "name": "The Undertow"}]
    assert job["title"] == "The Undertow slows it down" and job["shift"] is None and job["guest_set"] is None
    moment, guest, preset, kwargs = talk.lines[0]
    assert (moment, guest, preset, kwargs["songs"], kwargs["trigger"]) == ("opener", "guest_undertow", "really_slow", 6, "manual")
    assert kwargs["last_song"]["song_id"] == 1 and kwargs["next_song"]["song_id"] == 2
    # Played: it counts as the guest's takeover for cooldowns.
    talk.outcome(DEVICE, reply["job_id"], {"protocol_version": 1, "outcome": "played"})
    assert talk._shows[CONTEXT].talk["_recent_guests"][-1] == "guest_undertow"
    assert talk._shows[CONTEXT].talk["_current_guest_takeover"] is None


def test_closer_gets_the_played_songs(talk):
    reply = turn(talk, 1, effects={"moment": "closer", "preset": "slowed", "guest": "guest_chill"},
                 recent_songs=[{"song_id": "3"}, {"song_id": "4"}])
    assert talk.job(DEVICE, reply["job_id"])["segment_type"] == "slowed_closer"
    played = talk.lines[0][3]["played_songs"]
    assert [song["song_id"] for song in played] == [3, 4]


def test_failed_live_line_gives_no_clip_so_the_phone_uses_the_bank(talk):
    talk._live_line = lambda *a, **k: None
    reply = turn(talk, 1, effects={"moment": "opener", "preset": "slowed", "guest": "guest_chill"})
    assert talk.job(DEVICE, reply["job_id"])["state"] == "none"


def test_slowed_active_holds_other_guest_takeovers(talk):
    guests = {"frequency": 2, "callers": False, "monologues": False, "guests": True, "guest_songs": 2}
    turn(talk, 1, talk=guests, slowed_active=True)
    reply = turn(talk, 2, talk=guests, slowed_active=True)
    job = talk.job(DEVICE, reply["job_id"])
    assert job.get("segment_type") != "guest_takeover_intro"
    assert talk._shows[CONTEXT].talk["_current_guest_takeover"] is None


@pytest.mark.parametrize("effects", [
    {"moment": "opener", "preset": "chipmunk", "guest": "guest_chill"},
    {"moment": "opener", "preset": "slowed", "guest": "guest_nerd"},
    {"moment": "encore", "preset": "slowed", "guest": "guest_chill"},
    {"moment": "opener", "preset": "slowed", "guest": "guest_chill", "songs": 11},
])
def test_effects_validation(talk, effects):
    with pytest.raises(Rejected):
        turn(talk, 1, effects=effects)


def test_live_line_checks_and_retries(engines):  # noqa: F811
    replies = iter(["(whispers) too short", "Let me slow it all down now, sink with me beneath the surface for a while"])
    text = slowed.live_line("opener", "guest_undertow", "slowed", chat=lambda prompt: next(replies), hour=23)
    assert text.startswith("Let me slow")
    assert slowed.live_line("closer", "guest_chill", "lofi", chat=lambda prompt: "no cue here at all, just words") is None


# -- gateway ---------------------------------------------------------------------

def test_gateway_slowed_routes(tmp_path, engines):  # noqa: F811
    from mobile_gateway import create_mobile_gateway
    from mobile_recommendations import MobileRecommendations
    from tests.test_mobile_pairing import request, setup
    store, identity, pairing = setup(tmp_path)
    recommendations = MobileRecommendations(store, deadline_seconds=5)
    bank = SlowedBank(directory=str(tmp_path / "bank"), render=copy_render, duration=lambda p: 1000,
                      today=lambda: date(2026, 11, 27))
    ids = [bank.add("guest_chill", "opener_auto", "slowed", f"line {i}", write_wav(tmp_path / f"b{i}.wav")) for i in range(5)]
    service = MobileTalk(recommendations, cache_dir=str(tmp_path / "cache"), run=lambda fn: fn(), render=copy_render,
                         available=lambda: (False, "tts_disabled"), bank=lambda: bank)
    app = create_mobile_gateway(store, identity, pairing, recommendations=recommendations, talk=service)
    app.state.admission.enable(1)
    device = store.issue_device("Phone", ["catalog:read", "recommend:read"])
    headers = {"Authorization": "Bearer " + device["token"], "Content-Type": "application/json"}
    prefix = "/api/portable/v1"

    def call(method, path, body=None, extra=None):
        content = None if body is None else json.dumps(body).encode()
        return request(app, method, prefix + path, {**headers, **(extra or {})}, content)

    caps = call("GET", "/capabilities").json()["connected_talk"]["slowed"]
    assert caps["guests"] == ["guest_chill", "guest_undertow"] and caps["presets"]["slowed"]["rate"] == 0.85
    listing = call("POST", "/talk/slowed-lines", {"protocol_version": 1, "since": 0})
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    lines = listing.json()["lines"]
    assert [line["id"] for line in lines] == ids
    clip = call("GET", f"/talk/slowed-lines/{ids[0]}")
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/mp4"
    assert hashlib.sha256(clip.content).hexdigest() == lines[0]["sha256"]
    assert call("HEAD", f"/talk/slowed-lines/{ids[0]}").headers["content-length"] == str(lines[0]["byte_length"])
    assert call("GET", "/talk/slowed-lines/" + "z" * 20).status_code == 404
    assert call("GET", "/talk/slowed-lines?since=0").status_code == 404
    plays = call("POST", "/talk/slowed-lines/plays", {"protocol_version": 1, "plays": {ids[0]: 4}})
    assert plays.json() == {"recorded": 1, "retired": 1}
    assert call("POST", "/talk/slowed-lines", {"protocol_version": 1, "since": 5}).json()["retired"] == [ids[0]]
    assert call("POST", "/talk/slowed-lines", {"protocol_version": 2}).status_code == 400
    assert call("POST", "/talk/slowed-lines/plays", {"protocol_version": 1, "plays": {"bad id": 1}}).status_code == 422
    no_scope = store.issue_device("Sync only", ["catalog:read"])
    denied = request(app, "POST", prefix + "/talk/slowed-lines", {"Authorization": "Bearer " + no_scope["token"],
                                                                   "Content-Type": "application/json"},
                     json.dumps({"protocol_version": 1}).encode())
    assert denied.status_code == 403
    anonymous = request(app, "GET", prefix + f"/talk/slowed-lines/{ids[1]}", {}, None)
    assert anonymous.status_code == 401
