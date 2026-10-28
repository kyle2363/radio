"""Session output contracts: requests are never evidence of actual playback."""
from types import SimpleNamespace

from engine.vlc_session_output import VlcSessionOutput


class FakePlayer:
    def __init__(self):
        self.calls = []
        self.callback = None
        self.subscriptions = []
        self.state = "playing"
        self.seeks = []

    def on_observation(self, callback):
        self.callback = callback
        self.subscriptions.append(callback)
        return lambda: self.subscriptions.remove(callback)

    def play_chain(self, items, **kwargs):
        self.calls.append((items, kwargs))
        return True

    def pause(self):
        self.state = "paused"

    def stop(self):
        self.state = "stopped"

    def get_state(self):
        return self.state

    def get_time(self):
        return 432

    def seek(self, position_ms):
        self.seeks.append(position_ms)


def plan(instance="instance-1"):
    return {"id": "session-1", "instance_id": instance,
            "items": [{"item_id": "speech-1", "kind": "speech"},
                      {"item_id": "song-1", "kind": "song", "song_id": 1}]}


def test_start_idempotent_without_fabricating_observations():
    player, events = FakePlayer(), []
    output = VlcSessionOutput(player, lambda item: "private/" + item["item_id"], events.append)
    assert output.start(plan())
    assert output.start(plan())
    assert len(player.calls) == 1
    assert events == []
    items, kwargs = player.calls[0]
    assert items[0]["is_tts"] and items[0]["public_item_id"] == "speech-1"
    assert kwargs["instance_id"] == "instance-1"
    assert player._record_direct_completion is False


def test_actual_observation_before_start_returns_has_identity_and_stable_event_id():
    player, events = FakePlayer(), []
    def play(items, **kwargs):
        player.callback({"instance_id": kwargs["instance_id"], "item_id": "speech-1",
                         "kind": "started", "generation": 7, "observation_seq": 1,
                         "position_ms": 0})
        return True
    player.play_chain = play
    output = VlcSessionOutput(player, lambda item: "generated.wav", events.append)
    assert output.start(plan())
    assert len(events) == 1
    assert events[0]["event_id"]
    assert events[0]["item_id"] == "speech-1"
    assert events[0]["session_id"] == "session-1"


def test_resumed_started_observation_reports_applied_seek_position():
    player, events = FakePlayer(), []
    resumed = {**plan(), "position_ms": 2750}
    output = VlcSessionOutput(player, lambda item: "generated.wav", events.append)
    assert output.start(resumed)
    player.callback({"instance_id": "instance-1", "item_id": "speech-1", "kind": "started",
                     "generation": 7, "observation_seq": 1, "position_ms": 0})
    assert player.seeks == [2750]
    assert events[0]["position_ms"] == 2750


def test_quiesce_correlates_confirmation_and_rejects_stale_instance():
    player = FakePlayer()
    output = VlcSessionOutput(player, lambda item: "generated.wav", lambda event: None)
    output.start(plan())
    assert output.quiesce("old", "command") is False
    ack = output.quiesce("instance-1", "command")
    assert ack == {"confirmed": True, "instance_id": "instance-1", "command_id": "command",
                   "position_ms": 432, "item_id": None}


def test_retired_observations_cannot_escape_adapter():
    player, events = FakePlayer(), []
    output = VlcSessionOutput(player, lambda item: "generated.wav", events.append)
    output.start(plan())
    output.stop()
    player.callback({"instance_id": "instance-1", "item_id": "song-1", "kind": "started",
                     "generation": 1, "observation_seq": 1, "position_ms": 0})
    assert events == []


def test_close_unsubscribes_once_and_does_not_stop_other_session():
    player = FakePlayer()
    first = VlcSessionOutput(player, lambda item: "generated.wav", lambda event: None)
    assert first.start(plan())
    second = VlcSessionOutput(player, lambda item: "generated.wav", lambda event: None)
    assert second.start(plan("instance-2"))
    first.close()
    first.close()
    assert len(player.subscriptions) == 1
    assert player.state == "playing"
    assert first.start(plan("instance-3")) is False
    first.stop()
    assert player.state == "playing"
    second.dispose()
    assert player.subscriptions == []


def test_closed_adapter_drops_already_captured_callback():
    player, events = FakePlayer(), []
    output = VlcSessionOutput(player, lambda item: "generated.wav", events.append)
    output.start(plan())
    callback = player.callback
    output.close()
    callback({"instance_id": "instance-1", "item_id": "song-1", "kind": "started",
              "generation": 1, "observation_seq": 1, "position_ms": 0})
    assert events == []


def test_paused_plan_is_primed_without_native_play_and_resumes_same_grant():
    player, events = FakePlayer(), []
    output = VlcSessionOutput(player, lambda item: 'private/' + item['item_id'], events.append)
    paused = {**plan(), 'status': 'paused', 'desired_status': 'paused', 'position_ms': 2750}
    paused['items'][0]['duration_ms'] = 6000
    assert output.start(paused)
    assert player.calls == [] and events == []
    assert output.get_state() == 'paused' and output.get_time() == 2750
    assert output.quiesce('instance-1', 'move')['position_ms'] == 2750
    output.seek(3000)
    assert player.seeks == [] and output.get_time() == 3000
    assert output.get_length() == 6000 and output.get_position() == 0.5
    assert not output.is_playing() and not output.is_talk_playing()
    assert output.resume()
    assert len(player.calls) == 1 and player.calls[0][1]['instance_id'] == 'instance-1'
    player.callback({'instance_id': 'instance-1', 'item_id': 'speech-1', 'kind': 'started',
                     'generation': 3, 'observation_seq': 1, 'position_ms': 0})
    assert player.seeks == [3000] and events[0]['position_ms'] == 3000
