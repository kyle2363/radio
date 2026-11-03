from engine.session_context import new_talk_context


def test_caller_openings_rotate_and_remain_session_local():
    first, second = new_talk_context(), new_talk_context()
    previous = []
    for _ in range(50):
        selected = first._pick_caller_opening()
        assert selected not in previous[-3:]
        previous.append(selected)
    assert not second._recent_caller_openings
    first.reset_talk_counters()
    assert not first._recent_caller_openings


def test_monologue_duration_and_topic_rotation():
    context = new_talk_context()
    assert '30-75 second' in context.MONOLOGUE_PROMPT_TEMPLATE
    assert '45-90' not in context.MONOLOGUE_PROMPT_TEMPLATE
    previous = []
    for _ in range(30):
        selected, _ = context._pick_monologue_type()
        assert selected not in previous[-3:]
        previous.append(selected)
    assert 'everyday_observation' in context.MONOLOGUE_TYPES
    assert 'listener_question' in context.MONOLOGUE_TYPES
