"""
batch_fill_lyrics.py
====================
Fills in the 'lyrics' field for all JSON files in docs/understand/
using web search (lyrics.ovh API + Genius fallback).

Usage:
    python batch_fill_lyrics.py [--resume] [--dry-run]

Options:
    --resume    Skip files that already have lyrics filled in
    --dry-run   Show what would be done without making changes
"""

import json
import os
import re
import sys
import time
import requests
from pathlib import Path

# Force UTF-8 stdout for Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ---------- config ----------
UNDERSTAND_DIR = Path(__file__).parent / "docs" / "understand"
REQUEST_DELAY = 1.0          # seconds between API calls
MAX_RETRIES = 2

# ---------- helpers ----------

def clean_filename(fname):
    """Extract artist and title from filename."""
    name = fname.replace(".json", "")
    
    # Remove common video/audio suffixes from title
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
    
    # Remove leading bracketed text like [Korean], [Korean translation], etc.
    name = re.sub(r'^\s*\[.*?\]\s*', '', name)
    
    # Remove unicode bracket wrappers like [symbol]Chillout[symbol]
    name = re.sub(r'[^\x00-\x7F]+', '', name)
    
    # Split on last occurrence of " - " or " – " (dash separator)
    for sep in [' \u2013 ', ' \u2212 ', ' - ']:
        if sep in name:
            idx = name.rfind(sep)
            artist = name[:idx].strip()
            title = name[idx+len(sep):].strip()
            if artist and title:
                return artist, title
    
    # Fallback: if no separator found, return empty
    return "", name


def normalize_for_api(artist, title):
    """Normalize artist/title for lyrics API lookup."""
    artist = re.sub(r'[^\w\s\-\.\'&]', '', artist).strip()
    title = re.sub(r'[^\w\s\-\.\'&]', '', title).strip()
    artist = artist.lower().replace(' ', '+')
    title = title.lower().replace(' ', '+')
    return artist, title


def fetch_lyrics_lyrics_ovh(artist, title):
    """Try lyrics.ovh API: https://lyrics.ovh"""
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
        elif resp.status_code == 404:
            return None, None
        else:
            return None, None
    except Exception as e:
        print(f"    lyrics.ovh error: {e}")
    return None, None


def detect_language(lyrics):
    """Heuristic language detection from lyrics text."""
    if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', lyrics):
        return 'ko'
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', lyrics):
        return 'ja'
    if re.search(r'[\u4e00-\u9fff]', lyrics):
        return 'zh'
    spanish_words = ['que', 'la', 'los', 'las', 'el', 'una', 'un', 'del', 'y', 'es', 'son', 'con', 'por', 'para']
    spanish_count = sum(1 for w in spanish_words if w in lyrics.lower().split())
    if spanish_count >= 3:
        return 'es'
    french_words = ['le', 'la', 'les', 'de', 'un', 'une', 'des', 'est', 'sont', 'avec', 'pour', 'dans']
    french_count = sum(1 for w in french_words if w in lyrics.lower().split())
    if french_count >= 3:
        return 'fr'
    german_words = ['der', 'die', 'das', 'und', 'ist', 'ein', 'eine', 'mit', 'auf', 'den']
    german_count = sum(1 for w in german_words if w in lyrics.lower().split())
    if german_count >= 3:
        return 'de'
    return 'en'


def process_file(filepath, dry_run=False):
    """Process a single JSON file. Returns (success, lyrics_status, new_lang)."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return False, f"read error: {e}", None
    
    if not data or not isinstance(data, list):
        return False, "invalid format", None
    
    entry = data[0]
    
    current_lyrics = entry.get('lyrics', 'TODO')
    if current_lyrics and current_lyrics != 'TODO':
        return False, f"already has lyrics ({len(current_lyrics)} chars)", None
    
    filename = filepath.name
    artist, title = clean_filename(filename)
    
    if not artist or not title:
        return False, f"could not parse artist/title from filename", None
    
    print(f"  Searching: {artist} - {title}")
    
    # Try lyrics.ovh
    lyrics, lang = fetch_lyrics_lyrics_ovh(artist, title)
    
    if lyrics:
        if dry_run:
            print(f"    Would save lyrics ({len(lyrics)} chars, lang={lang})")
            return True, f"would save ({len(lyrics)} chars)", lang
        entry['lyrics'] = lyrics
        if lang:
            entry['vocal_language'] = lang
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"    Saved lyrics ({len(lyrics)} chars, lang={lang})")
        return True, "saved", lang
    
    # Not found
    if dry_run:
        print(f"    Would mark as 'lyrics not found'")
        return True, "would mark not found", None
    
    entry['lyrics'] = 'lyrics not found'
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"    Marked as 'lyrics not found'")
    return True, "not found", None


def main():
    resume = '--resume' in sys.argv
    dry_run = '--dry-run' in sys.argv
    
    # Collect JSON files
    json_files = sorted(UNDERSTAND_DIR.glob('*.json'))
    # Exclude manifest
    json_files = [f for f in json_files if f.name != 'batch_manifest.json']
    
    print(f"Found {len(json_files)} JSON file(s) in {UNDERSTAND_DIR}")
    print(f"Resume: {resume}, Dry run: {dry_run}")
    print()
    
    if not json_files:
        print("No files to process.")
        return
    
    total = len(json_files)
    success = 0
    skipped = 0
    failed = 0
    lyrics_found = 0
    lyrics_not_found = 0
    
    for i, filepath in enumerate(json_files, 1):
        try:
            safe_name = filepath.name.encode('utf-8', errors='replace').decode('utf-8')
        except Exception:
            safe_name = str(filepath.name)
        print(f"[{i}/{total}] {safe_name}")
        
        try:
            if resume:
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if data and data[0].get('lyrics', 'TODO') != 'TODO':
                        print(f"  Skipping (already has lyrics)")
                        skipped += 1
                        continue
                except Exception as e:
                    print(f"  Error checking: {e}")
            
            ok, status, lang = process_file(filepath, dry_run=dry_run)
            
            if ok:
                success += 1
                if 'not found' in status.lower():
                    lyrics_not_found += 1
                elif 'saved' in status.lower() or 'would save' in status.lower():
                    lyrics_found += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1
        
        print()
        
        # Rate limiting (skip on last file)
        if i < total:
            time.sleep(REQUEST_DELAY)
    
    print("=" * 60)
    print(f"Done! Total: {total} | Success: {success} | Skipped: {skipped} | Failed: {failed}")
    print(f"  Lyrics found: {lyrics_found}")
    print(f"  Lyrics not found: {lyrics_not_found}")


if __name__ == '__main__':
    main()
