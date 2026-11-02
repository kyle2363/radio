"""Conservative repair for unfinished Windows path duplicates. Dry run by default.

Run with the radio backend stopped. Refuse copies with history, user data,
references, or processing beyond audio analysis. Preserve original song IDs.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
import json
import ntpath
from pathlib import Path
import sqlite3


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def plan_repair(conn):
    conn.row_factory = sqlite3.Row
    groups = defaultdict(list)
    for row in conn.execute('SELECT * FROM songs ORDER BY id'):
        if ntpath.splitdrive(row['filepath'])[0]:
            groups[ntpath.normcase(ntpath.normpath(row['filepath']))].append(dict(row))
    plan = []
    protected = ('play_count', 'skip_count', 'complete_count', 'replay_count',
                 'last_played', 'user_tags', 'user_notes', 'user_energy_count',
                 'user_energy_updated_at', 'art_file', 'ai_description', 'lyrics',
                 'emotional_tags', 'genre_tags', 'instrument_tags')
    for group in groups.values():
        if len(group) < 2:
            continue
        original, *copies = group
        if original['ingest_phase'] != 'clustered':
            raise ValueError(f"Original {original['id']} is not fully processed")
        for copy in copies:
            if copy['ingest_phase'] not in ('raw', 'analyzed'):
                raise ValueError(f"Copy {copy['id']} has advanced processing")
            if any(copy.get(key) for key in protected) or copy.get('user_energy') is not None:
                raise ValueError(f"Copy {copy['id']} contains history or metadata")
            if copy.get('cluster_id', -1) != -1:
                raise ValueError(f"Copy {copy['id']} has a cluster assignment")
            plan.append({'remove_id': copy['id'], 'keep_id': original['id'],
                         'filepath': copy['filepath'], 'phase': copy['ingest_phase']})
    ids = {item['remove_id'] for item in plan}
    if not ids:
        return plan

    def contains_id(value, key=''):
        if isinstance(value, dict):
            return any(contains_id(v, k) for k, v in value.items())
        if isinstance(value, list):
            return any(contains_id(v, key) for v in value)
        # Position/duration integers can coincidentally equal a duplicate ID.
        return (key == 'id' or 'song' in key) and type(value) is int and value in ids

    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if table == 'songs':
            continue
        columns = list(conn.execute(f'PRAGMA table_info({quoted(table)})'))
        foreign_keys = list(conn.execute(f'PRAGMA foreign_key_list({quoted(table)})'))
        reference_columns = {row[3] for row in foreign_keys if row[2] == 'songs'}
        reference_columns.update(row[1] for row in columns if row[1].endswith('song_id'))
        for row in conn.execute(f'SELECT * FROM {quoted(table)}'):
            for column in columns:
                name = column[1]
                value = row[name]
                if name in reference_columns and value in ids:
                    raise ValueError(f'Referenced duplicate in {table}.{name}')
                if isinstance(value, str) and (name.endswith('_json') or value.startswith(('{', '['))):
                    try:
                        parsed = json.loads(value)
                    except ValueError:
                        continue
                    if contains_id(parsed):
                        raise ValueError(f'Possible duplicate reference in {table}.{name}')
                if table == 'settings' and 'song' in row['key']:
                    if str(value) in {str(song_id) for song_id in ids}:
                        raise ValueError(f"Duplicate reference in setting {row['key']}")
    return plan


def open_readonly(database):
    return sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)


def repair(database, backup_dir, apply=False):
    database = Path(database).resolve()
    with closing(open_readonly(database)) as source:
        plan = plan_repair(source)
        if not apply or not plan:
            return {'duplicates': len(plan), 'applied': False, 'plan': plan}
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stem = 'before-ingest-path-repair-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = backup_dir / (stem + '.db')
        with closing(sqlite3.connect(database)) as target:
            target.execute('PRAGMA foreign_keys=ON')
            target.execute('BEGIN IMMEDIATE')
            if plan_repair(target) != plan:
                raise ValueError('Database changed after planning; stop the backend and retry')
            with closing(sqlite3.connect(backup)) as snapshot:
                source.backup(snapshot)
                if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup integrity check failed')
            (backup_dir / (stem + '.json')).write_text(json.dumps(plan, indent=2), encoding='utf-8')
            before = target.execute('SELECT COUNT(*) FROM songs').fetchone()[0]
            target.executemany('DELETE FROM songs WHERE id=?', [(item['remove_id'],) for item in plan])
            if target.execute('SELECT COUNT(*) FROM songs').fetchone()[0] != before - len(plan):
                raise ValueError('Unexpected row count; rolling back')
            if target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Foreign key check failed; rolling back')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Integrity check failed; rolling back')
            target.commit()
    return {'duplicates': len(plan), 'applied': True, 'backup': str(backup)}


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=root / 'data' / 'radio.db')
    parser.add_argument('--backup-dir', type=Path, default=root / '.test-runtime' / 'ingest-path-repair')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = repair(args.database, args.backup_dir, args.apply)
    print(json.dumps({key: value for key, value in result.items() if key != 'plan'}, indent=2))
