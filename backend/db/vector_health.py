"""Read-only identity checks for derived embedding collections."""
from collections import Counter


def check_vector_health(database, client_factory, config, logger):
    try:
        with database.get_connection_cm() as conn:
            rows = conn.execute('SELECT id, ingest_phase FROM songs').fetchall()
        known = {str(row['id']) for row in rows}
        expected = {str(row['id']) for row in rows
                    if row['ingest_phase'] in ('embedded', 'audio_embedded', 'clustered')}
        expected_audio = {str(row['id']) for row in rows
                          if row['ingest_phase'] in ('audio_embedded', 'clustered')}
        client = client_factory()

        def inspect(name, required):
            try:
                collection = client.get_collection(name)
                # Metadata and vectors are unnecessary for an identity check.
                ids = collection.get(include=[])['ids']
                counts = Counter(ids)
                found = set(ids)
                duplicates = {key: count for key, count in counts.items() if count > 1}
                missing, stale = sorted(required - found), sorted(found - known)
                return dict(count=len(ids), duplicates=duplicates, missing_ids=missing,
                            stale_ids=stale, healthy=not (duplicates or missing or stale))
            except Exception as exc:
                logger.warning('Vector health check failed for %s: %s', name, exc)
                return dict(count=0, duplicates={}, missing_ids=sorted(required), stale_ids=[],
                            healthy=False, error=str(exc))

        text = inspect('songs', expected)
        audio = inspect(config.MUQ_AUDIO_COLLECTION, expected_audio) if config.MUQ_ENABLED else None
        # Keep both historical response shapes while exposing identity diagnostics.
        return dict(songs_collection=text['count'], sqlite_clustered=sum(
                    row['ingest_phase'] == 'clustered' for row in rows),
                    audio_collection=audio['count'] if audio else 0,
                    duplicates=text['duplicates'], audio_duplicates=audio['duplicates'] if audio else {},
                    total_entries=text['count'], duplicate_count=len(text['duplicates']),
                    missing_ids=text['missing_ids'], stale_ids=text['stale_ids'],
                    text=text, audio=audio,
                    healthy=text['healthy'] and (audio is None or audio['healthy']))
    except Exception as exc:
        logger.warning('Vector health check failed: %s', exc)
        return {'error': str(exc), 'healthy': False}
