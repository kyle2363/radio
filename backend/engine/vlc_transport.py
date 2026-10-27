"""Native VLC handles with immutable origin and explicit in-flight ownership."""
import logging
import threading

logger = logging.getLogger(__name__)


class _MediaView:
    def __init__(self, owner):
        self._owner = owner

    def __getattr__(self, method):
        return lambda *args: self._owner._run(self._owner.media_player, method, args)


class VLCTransport:
    def __init__(self, instance, events, generation, handlers, enqueue):
        self._retire_lock = threading.Lock()
        self._retired = False
        self._released = False
        self._inflight = 0
        self._starting = 0
        self._stop_after_start = False
        self.list_player = self.media_player = self.media_list = None
        self._registrations = []
        self._callbacks = []
        self.media_view = _MediaView(self)
        try:
            self.list_player = instance.media_list_player_new()
            self.media_player = instance.media_player_new()
            self.media_list = instance.media_list_new([])
            self.list_player.set_media_player(self.media_player)
            self.list_player.set_media_list(self.media_list)
            list_events = self.list_player.event_manager()
            mp_events = self.media_player.event_manager()
            for manager, event, handler in (
                (list_events, events.MediaListPlayerNextItemSet, handlers["next"]),
                (list_events, events.MediaPlayerEndReached, handlers["ended"]),
                (list_events, events.MediaListPlayerStopped, handlers["stopped"]),
                (mp_events, events.MediaPlayerEndReached, handlers["media_end"]),
            ):
                def callback(_event, target=handler):
                    enqueue(target, generation)
                self._callbacks.append(callback)
                result = manager.event_attach(event, callback)
                if isinstance(result, int) and result != 0:
                    raise RuntimeError(f"VLC callback registration failed: {event}")
                self._registrations.append((manager, event))
        except Exception:
            self.retire()
            raise

    def _run(self, target, method, args=(), *, starting=False):
        with self._retire_lock:
            if self._retired:
                return None
            self._inflight += 1
            self._starting += int(starting)
        try:
            return getattr(target, method)(*args)
        finally:
            with self._retire_lock:
                self._inflight -= 1
                self._starting -= int(starting)
                if self._retired and self._inflight == 0:
                    if self._stop_after_start:
                        self.list_player.stop()
                    self._release()

    def play(self):
        return self._run(self.list_player, "play", starting=True)

    def set_pause(self, value):
        return self._run(self.list_player, "set_pause", (value,))

    def _release(self):
        if self._released:
            return
        self._released = True
        for resource in (self.list_player, self.media_player, self.media_list):
            if resource is not None:
                try:
                    resource.release()
                except Exception:
                    logger.exception("VLC native resource release failed")
        self._callbacks.clear()

    def retire(self):
        """Stop now; release after outstanding native calls have returned."""
        with self._retire_lock:
            if self._retired:
                return
            self._retired = True
            self._stop_after_start = self._starting > 0
            for manager, event in self._registrations:
                try:
                    manager.event_detach(event)
                except Exception:
                    logger.exception("VLC callback detach failed")
            try:
                if self.list_player is not None:
                    self.list_player.stop()
            finally:
                if self._inflight == 0:
                    self._release()
