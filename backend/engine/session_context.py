"""Private per-listener namespaces for legacy announcer module state.

The announcer modules keep session counters and locks in module globals.  A
SessionNamespace gives each listener a shallow private globals mapping and
rebinds module-owned functions to that mapping, while leaving imported shared
services (TTS, models, and configuration) shared.
"""

import random
import threading
from collections import deque
from importlib import import_module
from types import FunctionType


class SessionNamespace:
    """Expose a module's functions and globals through a private namespace."""

    def __init__(self, module, state=None):
        namespace = dict(vars(module))
        namespace.update(state or {})

        for name, value in tuple(vars(module).items()):
            if isinstance(value, FunctionType) and value.__module__ == module.__name__:
                rebound = FunctionType(
                    value.__code__,
                    namespace,
                    value.__name__,
                    value.__defaults__,
                    value.__closure__,
                )
                rebound.__kwdefaults__ = value.__kwdefaults__
                rebound.__annotations__ = value.__annotations__
                rebound.__doc__ = value.__doc__
                rebound.__module__ = value.__module__
                rebound.__qualname__ = value.__qualname__
                rebound.__dict__.update(value.__dict__)
                namespace[name] = rebound

        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_globals", namespace)

    def __getattr__(self, name):
        try:
            return self._globals[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        if name in {"_module", "_globals"}:
            object.__setattr__(self, name, value)
        else:
            self._globals[name] = value


def new_talk_context():
    """Create fresh mutable talk-segment state without loading models."""
    module = import_module("announcer.talk_segments")
    return SessionNamespace(
        module,
        {
            "_talk_lock": threading.RLock(),
            "_song_counter": 0,
            "_current_guest_takeover": None,
            "_recent_callers": [],
            "_recent_guests": [],
            "_recent_segment_types": [],
            "_recent_monologue_openings": deque(maxlen=4),
            "_recent_monologue_types": deque(maxlen=3),
            "_last_segment_mood": None,
            "random": random.Random(),
        },
    )


def new_announcement_context():
    """Create fresh mutable announcement scheduling state."""
    module = import_module("announcer.announcements")
    return SessionNamespace(
        module,
        {
            "_announce_lock": threading.Lock(),
            "_last_announcement_time": 0,
            "_radio_intro_count": 0,
            "_songs_since_last_skip": 0,
            "random": random.Random(),
        },
    )
