"""Isolated API contract checks for the legacy ingest routes."""
import ast
from pathlib import Path
from types import SimpleNamespace


MAIN_SOURCE = Path(__file__).resolve().parents[1] / "main.py"


class _Decorators:
    def get(self, _path):
        return lambda function: function

    def post(self, _path):
        return lambda function: function


class _Activity:
    def track(self, function):
        return function


def _load_routes(*route_names, extra_assignments=(), namespace=None):
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
    wanted = set(route_names)
    assignment_names = set(extra_assignments)
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)
        elif isinstance(node, ast.Assign):
            assigned = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if assigned & assignment_names:
                nodes.append(node)
    loaded = {
        "app": _Decorators(),
        "_catalog_ingest_activity": _Activity(),
        **(namespace or {}),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MAIN_SOURCE), "exec"), loaded)
    return loaded


def test_analyze_phase_admits_raw_songs_and_runs_analyzer():
    raw_songs = [{"id": 23}]
    calls = []
    phase_functions = {
        "understand": "understand",
        "raw": "raw",
        "analyze": "analyze",
        "normalize": "normalize",
        "describe": "describe",
        "embed": "embed",
        "audio_embed": "embed_audio",
        "cluster": "cluster",
        "lyrics_timing": "lyrics_timing",
    }
    fake_methods = {
        f"run_phase_{method}": (lambda phase=phase: calls.append(phase))
        for phase, method in phase_functions.items()
    }
    fake_pipeline = SimpleNamespace(
        get_ingest_progress=lambda: {"running": False, "phase": None},
        start_job=lambda function: (function() or True),
        **fake_methods,
    )
    fake_db = SimpleNamespace(
        get_by_ingest_phase=lambda phase: raw_songs if phase == "raw" else [],
    )
    loaded = _load_routes(
        "ingest_run_phase",
        extra_assignments=("PHASE_PREREQUISITES", "PHASE_FUNCTIONS"),
        namespace={
            "pipeline": fake_pipeline,
            "database": fake_db,
            "config": SimpleNamespace(MUSIC_DIR="unused"),
            "logger": SimpleNamespace(info=lambda *args: None, error=lambda *args: None),
        },
    )

    result = loaded["ingest_run_phase"]("analyze")

    assert result == {"status": "started", "phase": "analyze", "ready": 1}
    assert calls == ["analyze"]


def test_retry_failures_preserves_valid_phase_and_resets_legacy_failures():
    state = {
        1: {"id": 1, "ingest_phase": "raw", "ingest_error": "read failed"},
        2: {"id": 2, "ingest_phase": "analyzed", "ingest_error": "tag failed"},
        3: {"id": 3, "ingest_phase": "described", "ingest_error": "embed failed"},
        4: {"id": 4, "ingest_phase": "embedded", "ingest_error": "cluster failed"},
        5: {"id": 5, "ingest_phase": "clustered_failed", "ingest_error": "legacy failure"},
    }

    class Connection:
        def execute(self, sql, params):
            assert sql == "UPDATE songs SET ingest_error = '' WHERE id = ?"
            state[params[0]]["ingest_error"] = ""

        def commit(self):
            pass

        def close(self):
            pass

    fake_db = SimpleNamespace(
        get_ingest_failures=lambda: list(state.values()),
        get_connection=lambda: Connection(),
        set_ingest_phase=lambda song_id, phase: state[song_id].update(ingest_phase=phase),
    )
    identity = lambda function: function
    fake_pipeline = SimpleNamespace(exclusive_job=identity)
    loaded = _load_routes(
        "ingest_retry_failures",
        namespace={"pipeline": fake_pipeline, "database": fake_db},
    )

    result = loaded["ingest_retry_failures"]()

    assert result == {"status": "retried", "count": 5}
    assert [state[song_id]["ingest_phase"] for song_id in range(1, 5)] == ["raw", "analyzed", "described", "embedded"]
    assert state[5]["ingest_phase"] == "raw"
    assert all(not song["ingest_error"] for song in state.values())
