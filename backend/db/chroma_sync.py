"""Refresh derived cluster metadata without rebuilding embedding vectors."""
import config
from db import database
from db.chroma import get_chroma_client


def sync_cluster_metadata(assignments=None, client=None):
    """Update existing records only, then verify. Missing embeddings stay absent.

    Safe to retry after partial failure: SQLite owns the assignments and reads
    never depend on this metadata refresh completing successfully.
    """
    if assignments is None:
        conn = database.get_connection()
        try:
            assignments = dict(conn.execute('SELECT id, cluster_id FROM songs').fetchall())
        finally:
            conn.close()
    expected = {str(sid): int(cid) if cid is not None else -1 for sid, cid in assignments.items()}
    if not expected:
        return {}
    client = client if client is not None else get_chroma_client()
    names = {item if isinstance(item, str) else item.name for item in client.list_collections()}
    result = {}
    for name in dict.fromkeys(['songs', config.MUQ_AUDIO_COLLECTION]):
        if name not in names:
            continue
        collection = client.get_collection(name=name)
        checked = updated = 0
        ids = list(expected)
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            found = collection.get(ids=batch, include=['metadatas'])
            changed = [sid for sid, meta in zip(found['ids'], found['metadatas'])
                       if (meta or {}).get('cluster_id') != expected[sid]]
            if changed:
                collection.update(ids=changed, metadatas=[{'cluster_id': expected[sid]} for sid in changed])
                updated += len(changed)
            verified = collection.get(ids=found['ids'], include=['metadatas']) if found['ids'] else found
            if (set(verified['ids']) != set(found['ids']) or
                    any((meta or {}).get('cluster_id') != expected[sid]
                        for sid, meta in zip(verified['ids'], verified['metadatas']))):
                raise RuntimeError(f'Cluster metadata verification failed for {name}; refresh can be retried')
            checked += len(found['ids'])
        result[name] = {'checked': checked, 'updated': updated, 'missing': len(expected) - checked}
    return result
