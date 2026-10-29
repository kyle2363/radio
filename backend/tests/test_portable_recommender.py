"""Pure offline golden, learning, availability, controls and desktop comparison."""
from copy import deepcopy
import json
from pathlib import Path
import time

import numpy as np
import pytest

from portable.catalog import Catalog, read_catalog, write_catalog
from portable.recommender import OfflineRecommender, allocate_portable_slots

FIXTURE = Path(__file__).parent / "fixtures" / "portable_golden.json"


def golden(tmp_path, settings=None, state=None):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    write_catalog(tmp_path / "catalog", data["songs"], data["embeddings"],
                  catalog_id=data["catalog_id"], revision=1)
    engine = OfflineRecommender(read_catalog(tmp_path / "catalog"), data["local_tracks"],
                                settings={**data["settings"], **(settings or {})},
                                state=state, seed=data["seed"])
    for event in data["history"]:
        engine.feedback(event)
    return data, engine


def test_golden_scores_selection_queue_and_replay(tmp_path):
    data, engine = golden(tmp_path)
    ranked = engine.rank(now=data["now"])
    expected = data["expected"]
    assert [r["song_id"] for r in ranked] == expected["rank_ids"]
    assert [r["score"] for r in ranked] == pytest.approx(expected["scores"], abs=1e-7)
    picks = engine.recommend(now=data["now"])
    assert [p["song_id"] for p in picks] == expected["selection_ids"]
    assert [p["slot"] for p in picks] == expected["slots"]
    engine.refresh_upcoming(now=data["now"])
    engine.enqueue("4")
    before = engine.export_state()
    assert engine.replay() == expected["replay_current"]
    assert engine.state["upcoming"] == expected["upcoming_after_replay"]
    assert engine.state == before
    assert engine.advance() == "4"
    assert engine.state["upcoming"] == before["upcoming"]
    # Queue preparation and choice never increment listening events.
    assert engine.state["events"] == before["events"]


def test_uses_both_spaces_and_feedback_learns_immediately(tmp_path):
    data, engine = golden(tmp_path)
    engine.settings["audio_blend"] = 0
    text = engine.rank(now=2000)
    engine.settings["audio_blend"] = 1
    audio = engine.rank(now=2000)
    assert text[0]["song_id"] == "2"
    assert audio[0]["song_id"] == "3"
    before = {r["song_id"]:r["score"] for r in audio}
    event = {"event_id":"heart4","type":"hearted","song_id":"4","timestamp":2000}
    assert engine.feedback(event)
    assert not engine.feedback(event)
    after = {r["song_id"]:r["score"] for r in engine.rank(now=2001)}
    assert after["4"] > before["4"]
    assert after["2"] != before["2"]  # A changed taste vector, not just a favorite boost.
    restored = OfflineRecommender(engine.catalog, data["local_tracks"], state=engine.export_state(), seed=data["seed"])
    assert restored.rank(now=2001) == engine.rank(now=2001)
    assert not restored.feedback(event)


def test_only_local_playable_and_phone_only_fallback(tmp_path):
    data, engine = golden(tmp_path)
    local = [{"local_id":"phone-only","title":"Secret local song","energy":.7},
             {"local_id":"missing","song_id":"1","playable":False}]
    offline = OfflineRecommender(engine.catalog, local)
    assert [p["song_id"] for p in offline.recommend(now=1)] == ["local:phone-only"]
    assert offline.rank(now=1)[0]["vector_fallback"]
    assert offline.search("secret")[0]["local_id"] == "phone-only"
    offline.feedback({"type":"started","song_id":"local:phone-only","timestamp":1})
    assert len(offline.recommend(now=2)) == 1  # No stuck queue on tiny libraries.
    offline.feedback({"type":"disliked","song_id":"local:phone-only","timestamp":3})
    assert offline.recommend(now=4) == []  # Never override an explicit blacklist.


@pytest.mark.parametrize("setting,expected", [
    ({"genres":["ambient"]}, {"1","3","5"}),
    ({"folders":["Morning"]}, {"1","3"}),
    ({"collections":["calm"]}, {"1","3"}),
    ({"favorites_only":True}, set()),
])
def test_filters_are_hard_constraints(tmp_path, setting, expected):
    _, engine = golden(tmp_path, setting)
    assert {p["song_id"] for p in engine.recommend(count=20, now=2000)} == expected


def test_mood_energy_feedback_skips_favorites_and_vibe(tmp_path):
    _, engine = golden(tmp_path, {"mood":"energetic"})
    assert engine.state["mood"] == "energetic"
    engine.tracks["6"]["energy"] = .9
    engine.tracks["6"]["user_energy"] = None
    assert engine._energy("6") == .9
    engine.feedback({"type":"energy","song_id":"6","value":.1,"timestamp":2001})
    assert engine._energy("6") == pytest.approx(.66)
    engine.feedback({"type":"hearted","song_id":"6","timestamp":2002})
    engine.feedback({"type":"preference","value":{"favorites_only":True},"timestamp":2003})
    assert {r["song_id"] for r in engine.rank(now=2004)} == {"6"}
    engine.feedback({"type":"unhearted","song_id":"6","timestamp":2005})
    assert engine.rank(now=2006) == []
    engine.feedback({"type":"preference","value":{"favorites_only":False,"vibe_lock":"4"},"timestamp":2007})
    picks = engine.recommend(now=2008)
    assert [p["slot"] for p in picks] == ["exploit"] * 4 + ["explore"]
    for offset in range(3):
        engine.feedback({"type":"skipped","song_id":"4","reason":"too_intense","timestamp":2010+offset})
    assert engine.state["mood"] == "chill"
    assert next(r for r in engine.rank(now=2013) if r["song_id"] == "4")["skip_penalty"] > .5
    engine.feedback({"type":"completed","song_id":"3","timestamp":2014})
    assert engine.state["skip_streak"] == 0


def test_model_identity_separates_equal_dimension_vectors(tmp_path):
    _, engine = golden(tmp_path)
    catalog = engine.catalog
    changed = deepcopy(catalog.descriptors)
    changed[("2","text")]["model"] = "unrelated-same-dimension"
    changed[("2","audio")]["model_version"] = "future"
    isolated = OfflineRecommender(Catalog(catalog.manifest,catalog.songs,catalog.vectors,changed),
                                 [{"local_id":"1","song_id":"1"},{"local_id":"2","song_id":"2"}])
    isolated.feedback({"type":"completed","song_id":"1","timestamp":0})
    candidate = next(r for r in isolated.rank(now=100) if r["song_id"] == "2")
    assert candidate["text_similarity"] is None and candidate["audio_similarity"] is None
    assert candidate["vector_fallback"]


def test_seed_is_portable_and_equal_inputs_reproduce(tmp_path):
    _, engine = golden(tmp_path)
    first = engine.recommend(now=2000)
    assert engine.recommend(now=2000) == first
    assert engine._tie("1") == "723b18f37c13ab46f1436ec784078479ddd5590dbd8c9cbb4bf1300cf6d4cc39"


def test_no_app_imports_in_runtime_package():
    import ast
    root = Path(__file__).parents[1] / "portable"
    prohibited = ("config", "engine", "db", "requests", "httpx", "vlc", "chromadb", "llm")
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] in prohibited for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] not in prohibited


def test_desktop_slot_stage_exact_parity_over_representative_cases(monkeypatch):
    from engine.slots import allocate_slots
    from engine.reranker import deterministic_assignment
    from db import database
    monkeypatch.setattr(database,"get_effective_energy",lambda sid:.5)
    rng = np.random.default_rng(913)
    for size in (1,3,5,40,100):
        for current_cluster in (None,0,4):
            for exploration in (0.,.3,.7,1.):
                for locked in (False,True):
                    candidates = [{"song_id":str(i),"score":float(rng.random()),
                                   "cluster_id":int(rng.integers(0,6)),"energy":float(rng.random())}
                                  for i in range(size)]
                    current = {"id":999,"energy":.5,"cluster_id":current_cluster}
                    desktop = allocate_slots(deepcopy(candidates),current,
                                             vibe_lock={"active":locked},exploration_level=exploration)
                    portable = allocate_portable_slots(deepcopy(candidates),current,
                                                        vibe_lock=locked,exploration_level=exploration)
                    assert portable == desktop
                    desktop_picks = deterministic_assignment(candidates,desktop)
                    assert [(p["id"],p["slot"]) for p in desktop_picks] == \
                        [(row["song_id"],slot) for slot,rows in portable.items() for row in rows]


def test_representative_2300_exact_search_benchmark(tmp_path):
    rng = np.random.default_rng(322)
    count = 2300
    songs = {str(i):{"id":str(i),"title":f"Synthetic {i}","artist":f"Artist {i%600}",
                      "energy":float(rng.random()),"valence":float(rng.random()),"cluster_id":i%28}
             for i in range(count)}
    vectors, descriptors = {}, {}
    for space, dimension in (("text",768),("audio",512)):
        matrix = rng.normal(size=(count,dimension)).astype(np.float32)
        matrix /= np.linalg.norm(matrix,axis=1,keepdims=True)
        for i,vector in enumerate(matrix):
            key = (str(i),space)
            vectors[key] = vector
            descriptors[key] = {"space":space,"model":f"synthetic-{space}","model_version":"1","dimension":dimension}
    engine = OfflineRecommender(Catalog({},songs,vectors,descriptors),
                                [{"local_id":f"local-{i}","song_id":str(i)} for i in range(count)],seed=322)
    for i in range(100):
        engine.feedback({"event_id":str(i),"type":"completed","song_id":str(i),"timestamp":i*200})
    measurements=[]
    for _ in range(4):
        start=time.perf_counter()
        result=engine.recommend(now=25000)
        measurements.append((time.perf_counter()-start)*1000)
    assert len(result) == 5
    # Mathematical exact-search quality vs independent scalar dot products in both spaces.
    queries=engine._taste()
    ranked=engine.rank(now=25000)
    for row in ranked[::100]:
        for group,query in queries.items():
            vector=vectors[(row["song_id"],group[0])]
            reference=float(sum(float(a)*float(b) for a,b in zip(vector,query)))
            assert row[f"{group[0]}_similarity"] == pytest.approx(reference,abs=1e-6)
    report={"songs":count,"dimensions":{"text":768,"audio":512},"seed":322,
            "rank_and_select_ms":measurements,"exact_scalar_checks":len(ranked[::100])*2,
            "note":"Synthetic exact-search correctness and latency; no real listening-quality claim"}
    (tmp_path/"benchmark.json").write_text(json.dumps(report,indent=2))
    print("PORTABLE_BENCHMARK",json.dumps(report))
