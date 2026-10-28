"""Stable recommendation-side view of a session's replaceable audio output."""
import threading


class SessionPlaybackPort:
    def __init__(self, *, request_prepared=None, request_song=None):
        self._lock = threading.RLock()
        self._mutation_lock = threading.RLock()
        self._output = None
        self._generation = 0
        self._instance_id = None
        self._request_prepared = request_prepared
        self._request_song = request_song
        self._actions = {}

    def configure_actions(self, **actions):
        if set(actions) - {"stop", "pause", "resume", "seek", "volume", "eq", "normalization"}:
            raise ValueError("Unsupported session command")
        with self._lock:
            self._actions = dict(actions)

    def _action(self, name, *args):
        with self._lock:
            callback = self._actions.get(name)
        if callback is None:
            raise RuntimeError("Playback controls must use the coordinator")
        return callback(*args)

    def stop(self):
        return self._action("stop")

    def pause(self):
        return self._action("pause")

    def resume(self):
        return self._action("resume")

    def seek(self, position_ms):
        return self._action("seek", position_ms)

    def set_volume(self, volume):
        return self._action("volume", volume)

    def set_eq_preset(self, preset):
        return self._action("eq", preset)

    def set_normalization_enabled(self, enabled):
        return self._action("normalization", enabled)

    def get_eq_preset(self):
        return self._read("get_eq_preset", "flat")

    def configure_requests(self, *, prepared=None, song=None):
        with self._lock:
            self._request_prepared = prepared
            self._request_song = song

    def request_prepared_plan(self, plan):
        with self._lock:
            request = self._request_prepared
        if request is None:
            raise RuntimeError("Prepared playback must be started through the coordinator")
        return request(plan)

    def bind_output(self, output, *, instance_id=None):
        with self._mutation_lock, self._lock:
            self._generation += 1
            self._output = output
            self._instance_id = instance_id
            return self._generation

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def instance_id(self):
        with self._lock:
            return self._instance_id

    def dispatch(self, expected_generation, method, *args, **kwargs):
        if method not in ("start", "stop", "pause", "resume", "seek", "set_volume", "set_eq_preset", "set_normalization_enabled"):
            raise ValueError("Unsupported output command")
        with self._mutation_lock:
            with self._lock:
                if expected_generation != self._generation or self._output is None:
                    return False
                output = self._output
            return getattr(output, method)(*args, **kwargs) is not False

    def _read(self, name, default):
        with self._lock:
            output = self._output
        if output is None:
            return default
        return getattr(output, name)()

    def get_current_song_id(self):
        return self._read("get_current_song_id", None)

    def get_time(self):
        return self._read("get_time", 0)

    def get_length(self):
        return self._read("get_length", 0)

    def get_position(self):
        return self._read("get_position", 0)

    def get_state(self):
        return self._read("get_state", "stopped")

    def get_volume(self):
        return self._read("get_volume", 70)

    def is_playing(self):
        return self._read("is_playing", False)

    def is_talk_playing(self):
        return self._read("is_talk_playing", False)

    @property
    def _explicitly_stopped(self):
        with self._lock:
            return bool(getattr(self._output, "_explicitly_stopped", False))

    @_explicitly_stopped.setter
    def _explicitly_stopped(self, value):
        with self._lock:
            if self._output is not None:
                self._output._explicitly_stopped = bool(value)

    def play_song(self, *args, **kwargs):
        with self._lock:
            request = self._request_song
        if request is not None:
            return request(*args, **kwargs)
        raise RuntimeError("Prepared playback must be started through the coordinator")

    def play_chain(self, *args, **kwargs):
        raise RuntimeError("Prepared playback must be started through the coordinator")
