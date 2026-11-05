"""Edge cases for archetype metrics derived from durable play events."""

from db import database


def test_metrics_ignore_undone_rows_and_use_terminal_outcomes_for_completion():
    database.record_play_event(1, None, "playing")
    database.record_play_event(2, None, "playing")
    database.record_play_event(1, None, "completed")
    database.record_play_event(2, None, "skip_early")
    database.record_play_event(3, None, "hearted")
    database.record_play_event(4, None, "skip_late")
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE play_events SET undone=1 WHERE signal='skip_late'")
        conn.commit()

    metrics = database.get_archetype_metrics()

    assert metrics["total_plays"] == 2
    assert metrics["signal_counts"].get("skip_late", 0) == 0
    assert metrics["completion_rate"] == 0.5


def test_historic_terminal_rows_are_play_count_fallback_without_playing_starts():
    database.record_play_event(1, None, "completed")
    database.record_play_event(2, None, "skip_late")
    database.record_play_event(3, None, "hearted")

    metrics = database.get_archetype_metrics()

    assert metrics["total_plays"] == 2
    assert metrics["completion_rate"] == 0.5
