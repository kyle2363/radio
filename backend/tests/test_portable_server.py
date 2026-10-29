import json
from unittest.mock import Mock

import pytest

from db import database
from portable.catalog import read_catalog
from portable_server import ServerCatalog
from portable_sync import PortableProfileStore


def source(tmp_path):
    store = PortableProfileStore()
    client = Mock()
    text, audio = Mock(), Mock()
    text.metadata, audio.metadata = {"hnsw:space": "cosine"}, {"hnsw:space": "l2"}
    text.count.return_value, audio.count.return_value = 2, 1
    text.get.return_value = {"ids": ["1", "2"], "embeddings": [[1., 0.], [0., 1.]], "metadatas": [{}, {}]}
    audio.get.return_value = {"ids": ["1"], "embeddings": [[0., 0., 1.]], "metadatas": [{}]}
    client.get_collection.side_effect = [text, audio] * 20
    return ServerCatalog(store, root=tmp_path / "catalog", client_factory=lambda: client), client


def test_existing_vectors_only_actual_metric_and_unknown_provenance(tmp_path):
    service, client = source(tmp_path)
    catalog = service.snapshot()
    assert catalog.descriptors[("1", "text")]["model"] == "legacy_unknown:songs"
    assert catalog.descriptors[("1", "audio")]["metric"] == "l2"
    assert catalog.descriptors[("1", "text")]["dimension"] == 2
    assert catalog.descriptors[("1", "audio")]["dimension"] == 3
    assert "filepath" not in json.dumps(catalog.manifest)
    assert "file_hash" not in json.dumps(catalog.manifest)
    client.get_or_create_collection.assert_not_called()
    assert service.snapshot().manifest == catalog.manifest


def test_metadata_revision_delta_and_unchanged_vector_storage(tmp_path):
    service, _ = source(tmp_path)
    first = service.snapshot()
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET title='Changed title' WHERE id=1")
        conn.commit()
    second = service.snapshot()
    assert second.manifest["revision"] == first.manifest["revision"] + 1
    assert second.manifest["included_blobs"] == first.manifest["included_blobs"]
    delta = service.manifest(second.manifest["revision"], first.manifest["revision"])
    assert delta["included_blobs"] == []
    delta_path = service.root / f"delta-{first.manifest['revision']}-{second.manifest['revision']}"
    restored = read_catalog(delta_path, base=first)
    assert restored.songs["1"]["title"] == "Changed title"
    for desc in first.descriptors.values():
        a = service.root / str(first.manifest["revision"]) / desc["blob"]
        b = service.root / str(second.manifest["revision"]) / desc["blob"]
        assert a.samefile(b)


def test_missing_collection_allowed_but_storage_error_aborts_snapshot(tmp_path):
    store = PortableProfileStore()
    client = Mock()
    service = ServerCatalog(store, root=tmp_path / "catalog", client_factory=lambda: client)
    client.get_collection.side_effect = KeyError("absent")
    assert not service.snapshot().vectors
    client.get_collection.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="Cannot inspect"):
        service.snapshot()


def test_revision_and_blob_paths_cannot_escape_catalog_root(tmp_path):
    service, _ = source(tmp_path)
    with pytest.raises(ValueError):
        service.load("../radio.db")
    with pytest.raises(ValueError):
        service.blob("../radio.db")
