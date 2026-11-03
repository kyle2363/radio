"""Preview or repair cached cluster IDs. Stop the backend before --apply."""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def audit(database, chroma_path):
    with closing(readonly(database)) as songs:
        assignments = dict(songs.execute('SELECT id, cluster_id FROM songs'))
    with closing(readonly(Path(chroma_path) / 'chroma.sqlite3')) as cache:
        rows = cache.execute("""SELECT c.name,e.embedding_id,m.int_value
            FROM embeddings e JOIN segments s ON s.id=e.segment_id
            JOIN collections c ON c.id=s.collection
            LEFT JOIN embedding_metadata m ON m.id=e.id AND m.key='cluster_id'""").fetchall()
    result = {}
    import config
    for name in dict.fromkeys(['songs', config.MUQ_AUDIO_COLLECTION]):
        matched = [(int(sid), cid) for space, sid, cid in rows
                   if space == name and sid.isdigit() and int(sid) in assignments]
        result[name] = dict(checked=len(matched), mismatched=sum(
            cid != (assignments[sid] if assignments[sid] is not None else -1) for sid, cid in matched))
    return assignments, result


def ensure_backend_stopped():
    try:
        connection = socket.create_connection(('127.0.0.1', 8765), timeout=3)
    except ConnectionRefusedError:
        return
    except OSError as exc:
        raise RuntimeError('Cannot verify that the backend is stopped; repair refused') from exc
    connection.close()
    raise RuntimeError('Stop the radio backend before applying the metadata repair')


def main():
    import config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(config.DB_PATH))
    parser.add_argument('--chroma-path', type=Path, default=Path(config.CHROMA_PATH))
    parser.add_argument('--backup-dir', type=Path, default=ROOT / '.test-runtime' / 'cluster-metadata-backups')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assignments, before = audit(args.database, args.chroma_path)
    print(json.dumps({'before': before}, indent=2))
    if not args.apply:
        return
    ensure_backend_stopped()
    backup = args.backup_dir / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    for src, dst in [(args.database, backup / 'radio.db'),
                     (args.chroma_path / 'chroma.sqlite3', backup / 'chroma' / 'chroma.sqlite3')]:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with closing(readonly(src)) as source, closing(sqlite3.connect(dst)) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed')
    for item in args.chroma_path.iterdir():
        if item.is_dir():
            shutil.copytree(item, backup / 'chroma' / item.name)
    config.CHROMA_PATH = str(args.chroma_path.resolve())
    from db.chroma_sync import sync_cluster_metadata
    result = sync_cluster_metadata(assignments)
    after_assignments, after = audit(args.database, args.chroma_path)
    if after_assignments != assignments or any(r['mismatched'] for r in after.values()):
        raise RuntimeError('Post-repair audit failed; backup is at ' + str(backup))
    report = dict(before=before, refresh=result, after=after, backup=str(backup))
    (backup / 'repair-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
