"""Portable format acceptance, corruption and incremental revision contracts."""
import json
from pathlib import Path

import numpy as np
import pytest

from portable.catalog import CatalogError, canonical_json, read_catalog, sha256, write_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "portable_golden.json"


def fixture_catalog(tmp_path, dtype="float32"):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    path = tmp_path / dtype
    write_catalog(path, data["songs"], data["embeddings"], catalog_id=data["catalog_id"], revision=1, dtype=dtype)
    return data, read_catalog(path)


def resign(path, change):
    manifest = json.loads((path / "manifest.json").read_text())
    change(manifest)
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = sha256(canonical_json(manifest))
    (path / "manifest.json").write_bytes(canonical_json(manifest))


def test_roundtrip_both_spaces_mixed_models_and_missing_vectors(tmp_path):
    songs = [{"id":str(i), "filepath":"PRIVATE", "token":"SECRET", "title":None} for i in range(4)]
    embeddings = [{"song_id":"0","space":"text","model":"old","model_version":"unknown","vector":[1,0]},
                  {"song_id":"1","space":"text","model":"new","model_version":"2","vector":[0,1,0]},
                  {"song_id":"2","space":"audio","model":"muq","model_version":"1","vector":[1,1]}]
    manifest = write_catalog(tmp_path / "package", songs, embeddings, catalog_id="test", revision=1)
    catalog = read_catalog(tmp_path / "package")
    assert set(catalog.songs) == {"0","1","2","3"}
    assert len(catalog.vectors) == 3
    assert catalog.descriptors[("1","text")]["dimension"] == 3
    assert "PRIVATE" not in canonical_json(manifest).decode()
    assert "SECRET" not in canonical_json(manifest).decode()
    assert all(vector.dtype == np.float32 for vector in catalog.vectors.values())
    assert not catalog.vectors[("0","text")].flags.writeable


def test_deterministic_atomic_immutable_export(tmp_path):
    data, catalog = fixture_catalog(tmp_path)
    manifest = write_catalog(tmp_path / "another", reversed(data["songs"]), reversed(data["embeddings"]),
                             catalog_id=data["catalog_id"], revision=1)
    assert manifest == catalog.manifest
    with pytest.raises(CatalogError, match="already exists"):
        write_catalog(tmp_path / "another", [], [], catalog_id="x", revision=1)
    assert not list(tmp_path.glob(".catalog-*"))


def test_incremental_reuse_removal_and_new_model(tmp_path):
    data, base = fixture_catalog(tmp_path)
    songs = [s for s in data["songs"] if s["id"] != "5"]
    embeddings = [e for e in data["embeddings"] if e["song_id"] != "5"]
    embeddings[0] = {**embeddings[0], "model_version":"2", "vector":[0,0,1]}
    path = tmp_path / "delta"
    manifest = write_catalog(path, songs, embeddings, catalog_id=data["catalog_id"], revision=2, previous=base)
    assert len(manifest["included_blobs"]) == 1
    assert manifest["removed_song_ids"] == ["5"]
    assert len(list((path / "vectors").glob("*.bin"))) == 1
    with pytest.raises(CatalogError, match="base"):
        read_catalog(path)
    updated = read_catalog(path, base=base)
    assert set(updated.songs) == set(base.songs) - {"5"}
    assert updated.descriptors[("1","text")]["model_version"] == "2"
    np.testing.assert_array_equal(updated.vectors[("2","audio")], base.vectors[("2","audio")])
    # Atomic failure retains old in-memory data and immutable package.
    assert base.descriptors[("1","text")]["model_version"] == "1"


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(version=99),
    lambda m: m["embeddings"][0].update(blob="../../secret"),
    lambda m: m["embeddings"][0].update(dimension=9999999),
    lambda m: m["embeddings"][0].update(metric="madeup"),
    lambda m: m["embeddings"].append(m["embeddings"][0].copy()),
    lambda m: m["songs"].append(m["songs"][0].copy()),
])
def test_import_rejects_invalid_signed_data(tmp_path, mutation):
    _, catalog = fixture_catalog(tmp_path)
    resign(catalog.root, mutation)
    with pytest.raises(CatalogError):
        read_catalog(catalog.root)


def test_corruption_and_missing_blob(tmp_path):
    _, catalog = fixture_catalog(tmp_path)
    blob = catalog.root / next(iter(catalog.descriptors.values()))["blob"]
    original = blob.read_bytes()
    blob.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(CatalogError, match="checksum"):
        read_catalog(catalog.root)
    blob.unlink()
    with pytest.raises(CatalogError):
        read_catalog(catalog.root)


@pytest.mark.parametrize("vector", [[0,0],[float("nan"),1],[float("inf"),1],[[1,0]]])
def test_invalid_vectors_do_not_publish(tmp_path, vector):
    entry = {"song_id":"1","space":"text","model":"test","model_version":"1","vector":vector}
    with pytest.raises(CatalogError):
        write_catalog(tmp_path / "bad", [{"id":"1"}], [entry], catalog_id="test", revision=1)
    assert not (tmp_path / "bad").exists()


def test_float16_similarity_and_rank_quality(tmp_path):
    # 2300 x 128 seeded synthetic corpus: precision experiment, not a music-quality claim.
    rng = np.random.default_rng(448)
    vectors = rng.normal(size=(2300,128)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    restored = vectors.astype(np.float16).astype(np.float32)
    queries = vectors[:20]
    original = queries @ vectors.T
    approximate = queries @ restored.T
    assert np.max(np.abs(original - approximate)) < 0.0003
    overlap = [len(set(np.argsort(a)[-20:]) & set(np.argsort(b)[-20:])) / 20
               for a,b in zip(original, approximate)]
    assert np.mean(overlap) >= .99
    print("FLOAT16_BENCHMARK", json.dumps({"songs":2300,"dimension":128,"queries":20,
          "max_cosine_error":float(np.max(np.abs(original-approximate))),
          "mean_top20_overlap":float(np.mean(overlap)),"seed":448}))
    data, single = fixture_catalog(tmp_path, "float16")
    assert all(desc["dtype"] == "float16" for desc in single.descriptors.values())
