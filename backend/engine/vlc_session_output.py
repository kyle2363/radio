"""Translate prepared public plans into private, observed local VLC playback."""
import threading


class VlcSessionOutput:
    def __init__(self, player, resolve_media, on_event):
        self.player, self.resolve_media, self.on_event = player, resolve_media, on_event
        self._lock = threading.RLock()
        self._instance_id = self._session_id = self._item_id = None
        self._started_instances = set()
        self._seek_ms = 0
        self._pending = None
        self._pending_duration = 0
        self._closed = False
        # Session storage is the sole owner of completion persistence.
        player._record_direct_completion = False
        self._unsubscribe = player.on_observation(self._observe)

    def start(self, snapshot):
        instance_id = snapshot["instance_id"]
        with self._lock:
            if self._closed:
                return False
            if instance_id in self._started_instances:
                return instance_id == self._instance_id
            items = [{"filepath": self.resolve_media(item), "public_item_id": item["item_id"],
                      "song_id": item.get("song_id"), "gain_db": item.get("gain_db", 0),
                      "is_tts": item["kind"] == "speech"}
                     for item in snapshot["items"][snapshot.get("active_index", 0):]]
            self._instance_id = instance_id
            self._session_id = snapshot["id"]
            self._item_id = None
            self._seek_ms = snapshot.get("position_ms", 0) or 0
            self._started_instances.add(instance_id)
            self._pending = None
            if snapshot.get("desired_status") == "paused" or snapshot.get("status") == "paused":
                # A paused handoff is primed without touching native playback.
                # Calling play then pause can emit audible frames and starts.
                self._pending = (items, snapshot.get("history_session_id"), instance_id)
                active = snapshot["items"][snapshot.get("active_index", 0)] if items else {}
                self._pending_duration = active.get("duration_ms", 0)
                self._item_id = items[0]["public_item_id"] if items else None
                return True
        # Do not hold the adapter lock across native start: an observation may
        # arrive before play_chain returns, including from another thread.
        accepted = self.player.play_chain(items, session_id=snapshot.get("history_session_id"),
                                          instance_id=instance_id)
        if not accepted:
            with self._lock:
                if self._instance_id == instance_id:
                    self._instance_id = None
                self._started_instances.discard(instance_id)
        return accepted

    def resume(self):
        with self._lock:
            if self._closed:
                return False
            pending, self._pending = self._pending, None
        if pending is None:
            return self.player.resume()
        items, history_session, instance_id = pending
        accepted = self.player.play_chain(items, session_id=history_session, instance_id=instance_id)
        if accepted is False:
            with self._lock:
                if self._instance_id == instance_id and not self._closed:
                    self._pending = pending
        return accepted

    def pause(self):
        with self._lock:
            if self._closed:
                return False
            if self._pending is not None:
                return True
        return self.player.pause()

    def seek(self, position_ms):
        with self._lock:
            if self._closed:
                return False
            if self._pending is not None:
                self._seek_ms = position_ms
                return True
        return self.player.seek(position_ms)

    def get_state(self):
        with self._lock:
            if self._pending is not None:
                return "paused"
        return self.player.get_state()

    def get_time(self):
        with self._lock:
            if self._pending is not None:
                return self._seek_ms
        return self.player.get_time()

    def get_current_song_id(self):
        with self._lock:
            if self._pending is not None:
                items = self._pending[0]
                return items[0].get("song_id") if items else None
        return self.player.get_current_song_id()

    def get_length(self):
        with self._lock:
            if self._pending is not None:
                return self._pending_duration
        return self.player.get_length()

    def get_position(self):
        with self._lock:
            if self._pending is not None:
                return self._seek_ms / self._pending_duration if self._pending_duration else 0
        return self.player.get_position()

    def is_playing(self):
        with self._lock:
            if self._pending is not None:
                return False
        return self.player.is_playing()

    def is_talk_playing(self):
        with self._lock:
            if self._pending is not None:
                return False
        return self.player.is_talk_playing()

    def _observe(self, event):
        with self._lock:
            if self._closed or event["instance_id"] != self._instance_id:
                return
            self._item_id = event["item_id"]
            payload = {**event, "session_id": self._session_id,
                       "event_id": f"vlc:{event['instance_id']}:{event['generation']}:{event['observation_seq']}"}
            seek = self._seek_ms if event["kind"] == "started" else 0
            if seek:
                self._seek_ms = 0
                payload["position_ms"] = seek
        if seek:
            self.player.seek(seek)
        self.on_event(payload)

    def quiesce(self, instance_id, command_id):
        with self._lock:
            if instance_id != self._instance_id:
                return False
            if self._pending is not None:
                return {"confirmed": True, "instance_id": instance_id, "command_id": command_id,
                        "position_ms": self._seek_ms, "item_id": self._item_id}
        self.player.pause()
        confirm = getattr(self.player, "wait_for_pause", None)
        confirmed = confirm(instance_id, timeout=1.0) if confirm else self.player.get_state() == "paused"
        with self._lock:
            if instance_id != self._instance_id:
                return False
            return {"confirmed": bool(confirmed), "instance_id": instance_id,
                    "command_id": command_id, "position_ms": self.player.get_time(),
                    "item_id": self._item_id}

    def stop(self):
        with self._lock:
            if self._closed:
                return
            self._instance_id = None
            pending, self._pending = self._pending, None
        if pending is not None:
            return
        self.player.stop()

    def close(self):
        """Dispose this subscription without changing shared native playback."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._instance_id = self._session_id = self._item_id = None
            self._pending = None
            self._started_instances.clear()
            unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()

    dispose = close

    def __getattr__(self, name):
        return getattr(self.player, name)
