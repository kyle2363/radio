import sys
import threading
import types
from collections import deque
from types import FunctionType

from engine.session_context import (
    SessionNamespace,
    new_announcement_context,
    new_talk_context,
)


def _fake_inner_template(*, delta=1) -> int:
    """Increment module counter under its lock."""
    global counter
    with lock:
        counter += delta
    return counter


def _fake_outer_template():
    return inner()


def _build_function(module, function, name):
    cloned = FunctionType(
        function.__code__,
        module.__dict__,
        name,
        function.__defaults__,
        function.__closure__,
    )
    cloned.__module__ = module.__name__
    cloned.__kwdefaults__ = function.__kwdefaults__
    cloned.__annotations__ = function.__annotations__
    cloned.__doc__ = function.__doc__
    return cloned


def test_namespace_rebinds_nested_functions_and_isolates_state():
    module = types.ModuleType("fake_announcer")
    module.counter = 99
    module.inner = _build_function(module, _fake_inner_template, "inner")
    module.outer = _build_function(module, _fake_outer_template, "outer")
    initial_lock = threading.RLock()

    left = SessionNamespace(module, {"counter": 0, "lock": initial_lock})
    right = SessionNamespace(module, {"counter": 0, "lock": threading.RLock()})

    assert left.outer.__globals__ is left._globals
    assert left.inner.__globals__ is left._globals
    assert "lock" in left._globals
    assert left.outer.__globals__["inner"] is left.inner
    assert left.inner.__globals__["lock"] is initial_lock
    assert left.inner.__kwdefaults__ == {"delta": 1}
    assert left.inner.__annotations__ == {"return": int}
    assert left.outer() == 1
    assert left.outer() == 2
    assert right.outer() == 1
    assert module.counter == 99
    assert left.inner.__doc__ == "Increment module counter under its lock."
    assert left._globals["inner"] is left.inner
    assert module.__name__ not in sys.modules


def test_announcement_and_talk_factories_create_fresh_state():
    talk_a = new_talk_context()
    talk_b = new_talk_context()
    announcement_a = new_announcement_context()
    announcement_b = new_announcement_context()

    assert talk_a._song_counter == 0
    assert talk_b._song_counter == 0
    talk_a._song_counter += 1
    assert talk_b._song_counter == 0
    assert talk_a._recent_callers is not talk_b._recent_callers
    assert talk_a._recent_monologue_openings is not talk_b._recent_monologue_openings
    assert isinstance(talk_a._recent_monologue_openings, deque)
    assert talk_a.random is not talk_b.random

    announcement_a._radio_intro_count += 1
    assert announcement_b._radio_intro_count == 0
    assert announcement_a._announce_lock is not announcement_b._announce_lock
    assert announcement_a.random is not announcement_b.random
