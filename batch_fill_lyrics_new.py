"""
batch_fill_lyrics_new.py
=======================
Fills 'lyrics' field in docs/understand_new/ JSONs via lyrics.ovh API.
On failure, leaves lyrics as "TODO" for manual fill.

Usage:
    python batch_fill_lyrics_new.py [--resume] [--dry-run]
"""

import json
import os
import re
import sys
import time
import requests
from pathlib import Path

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

UNDERSTAND_DIR = Path(__file__).parent / "docs" / "understand_new"
REQUEST_DELAY = 1.0


def clean_filename(fname):
    name = fname.replace(".json", "")

    suffixes = [
        r'\s*\(Official\s+Lyric\s+Video\)',
        r'\s*\(Official\s+Audio\)',
        r'\s*\(Official\s+Video\)',
        r'\s*\(Lyrics\)',
        r'\s*\(Audio\)',
        r'\s*\(Visualizer\)',
        r'\s*\(Lyric\s+Video\)',
        r'\s*\(Music\s+Video\)',
        r'\s*\(CC\)',
        r'\s*\[Official\s+Lyric\s+Video\]',
        r'\s*\[Official\s+Audio\]',
        r'\s*\[Official\s+Video\]',
        r'\s*\[Lyrics\]',
        r'\s*\[Audio\]',
        r'\s*\[Visualizer\]',
        r'\s*\[Lyric\s+Video\]',
        r'\s*\[Music\s+Video\]',
        r'\s*\[CC\]',
        r'\s*\(Lyrics\s*\u2044\s*AMV\)',
        r'\s*\(Lyric\s+Video\)\s*\u2044\s*AMV',
        r'\s*\(Lyrics\s*\u2044\s*AMV\)',
        r'\s*\(Visualizer\s*\u2044\s*Lyric\s+Video\)',
        r'\s*\(Lyrics\s*x\s*AMV\)',
        r'\s*\[Copyright\s+Free\].*',
        r'\s*Slowed\s+To\s+Perfection',
        r'\s*\(SadBois\s+Edit\)',
        r'\s*\(AMV\s+Edit\)',
        r'\s*\(AMV\)',
        r'\s*\(With\s+or\s+Without\s+You\)',
        r'\s*\(Official\s+Music\s+Video\)',
        r'\s*\(Official\s+Performance\s+MV\)',
        r'\s*\(w\/\s*.*?\)',
        r'\s*\(ft\..*?\)',
        r'\s*\(feat\..*?\)',
        r"\s*\(w\u2009\u2009/\s+.*?\)",
    ]

    for s in suffixes:
        name = re.sub(s, '', name, flags=re.IGNORECASE)

    name = re.sub(r'^\s*\[.*?\]\s*', '', name)
    name = re.sub(r'[^\x00-\x7F]+', '', name)

    for sep in [' \u2013 ', ' \u2212 ', ' - ']:
        if sep in name:
            idx = name.rfind(sep)
            artist = name[:idx].strip()
            title = name[idx+len(sep):].strip()
            if artist and title:
                return artist, title

    return "", name


def normalize_for_api(artist, title):
    artist = re.sub(r'[^\w\s\-\.\'&]', '', artist).strip()
    title = re.sub(r'[^\w\s\-\.\'&]', '', title).strip()
    artist = artist.lower().replace(' ', '+')
    title = title.lower().replace(' ', '+')
    return artist, title


def fetch_lyrics_lyrics_ovh(artist, title):
    try:
        api_artist, api_title = normalize_for_api(artist, title)
        url = f"https://api.lyrics.ovh/v1/{api_artist}/{api_title}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            lyrics = data.get('lyrics', '').strip()
            if lyrics and len(lyrics) > 10:
                lang = detect_language(lyrics)
                return lyrics, lang
        return None, None
    except Exception:
        return None, None


def detect_language(lyrics):
    if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', lyrics):
        return 'ko'
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', lyrics):
        return 'ja'
    if re.search(r'[\u4e00-\u9fff]', lyrics):
        return 'zh'
    spanish_words = ['que', 'la', 'los', 'las', 'el', 'una', 'un', 'del', 'y', 'es', 'son', 'con', 'por', 'para']
    if sum(1 for w in spanish_words if w in lyrics.lower().split()) >= 3:
        return 'es'
    french_words = ['le', 'la', 'les', 'de', 'un', 'une', 'des', 'est', 'sont', 'avec', 'pour', 'dans']
    if sum(1 for w in french_words if w in lyrics.lower().split()) >= 3:
        return 'fr'
    german_words = ['der', 'die', 'das', 'und', 'ist', 'ein', 'eine', 'mit', 'auf', 'den']
    if sum(1 for w in german_words if w in lyrics.lower().split()) >= 3:
        return 'de'
    return 'en'


def main():
    resume = '--resume' in sys.argv
    dry_run = '--dry-run' in sys.argv
    limit = None
    if '--limit' in sys.argv:
        idx = sys.argv.index('--limit')
        if idx + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[idx + 1])
            except ValueError:
                pass

    json_files = sorted(UNDERSTAND_DIR.glob('*.json'))
    json_files = [f for f in json_files if f.name != 'batch_manifest.json']
    if limit:
        json_files = json_files[:limit]

    print(f"Found {len(json_files)} JSON file(s) in {UNDERSTAND_DIR}")
    print(f"Resume: {resume}, Dry run: {dry_run}")
    print()

    if not json_files:
        print("No files to process.")
        return

    total = len(json_files)
    found = 0
    skipped = 0
    failed = 0

    for i, filepath in enumerate(json_files, 1):
        safe_name = filepath.name.encode('utf-8', errors='replace').decode('utf-8')
        print(f"[{i}/{total}] {safe_name}")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not data or not isinstance(data, list):
                print(f"  Invalid format, skipping")
                failed += 1
                continue

            entry = data[0]
            if entry.get('lyrics', 'TODO') != 'TODO':
                print(f"  Skipping (already has lyrics)")
                skipped += 1
                continue

            if not resume:
                print(f"  Skipping (use --resume to process)")
                skipped += 1
                continue

            artist, title = clean_filename(filepath.name)
            if not artist or not title:
                print(f"  Could not parse artist/title, leaving as TODO")
                continue

            print(f"  Searching: {artist} - {title}")
            lyrics, lang = fetch_lyrics_lyrics_ovh(artist, title)

            if lyrics:
                if dry_run:
                    print(f"    Would save ({len(lyrics)} chars, lang={lang})")
                else:
                    entry['lyrics'] = lyrics
                    if lang:
                        entry['vocal_language'] = lang
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"    Saved lyrics ({len(lyrics)} chars, lang={lang})")
                found += 1
            else:
                print(f"    Not found, leaving as TODO")

        except Exception as e:
            print(f"  ERROR: {e} (leaving as TODO)")

        print()
        if i < total:
            time.sleep(REQUEST_DELAY)

    print("=" * 60)
    print(f"Done! Total: {total} | Found: {found} | Skipped: {skipped} | Failed: {failed}")


if __name__ == '__main__':
    main()
