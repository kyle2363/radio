"""
batch_fill_genius.py
====================
Searches Genius.com for lyrics and fills in JSON files.
No API key needed - uses web scraping.

Usage:
    python batch_fill_genius.py [--resume] [--dry-run] [--limit N]
"""

import json
import os
import re
import sys
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

UNDERSTAND_DIR = Path(__file__).parent / "docs" / "understand_new"
REQUEST_DELAY = 1.5


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
        r'\s*\[NCS\s+Release\]',
        r'\s*\[NCS10\s+Release\]',
        r'\s*\[NCS\s+Fanmade\]',
        r'\s*\[NCS\s+Release\s+Music\s+Video\]',
        r'\s*\[NCN\s+Release\]',
        r'\s*\[NCS\s+EP\s+Release\]',
        r'\s*\[NCS\s+Uplifting\s+Release\]',
        r'\s*\[Copyright\s+Free\]',
        r'\s*\[Free\s+Download\]',
        r'\s*\(edit\)',
        r'\s*\(Audio\)',
        r'\s*\(cut\)',
        r'\s*\(2\)',
        r'\s*\(Lyric\s+Video\)',
        r'\s*\(Audio\s+Edit\)',
        r'\s*\[COPYRIGHTED\]',
        r'\s*\[Privated\s+NCS\s+Release\]',
        r'\s*\[Deleted\s+NCS\s+Release\]',
        r'\s*\[Deleted\s+NCS\s+SoundCloud\s+Promotion\]',
        r'\s*\[Unreleased\]',
        r'\s*\[Official\s+Audio\]',
        r'\s*\[Lyrics\]',
        r'\s*\(Lyric\s+Video\)',
        r'\s*\(Official\s+Music\s+Video\)',
    ]
    for s in suffixes:
        name = re.sub(s, '', name, flags=re.IGNORECASE)
    name = re.sub(r'^\s*\[.*?\]\s*', '', name)
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'[^\x00-\x7F]+', '', name)
    for sep in [' \u2013 ', ' \u2212 ', ' - ']:
        if sep in name:
            idx = name.rfind(sep)
            artist = name[:idx].strip()
            title = name[idx+len(sep):].strip()
            if artist and title:
                return artist, title
    return "", name


def search_genius(artist, title):
    """Search Genius for a song and return lyrics."""
    try:
        query = f"{artist} {title}"
        search_url = f"https://genius.com/api/search/song?q={requests.utils.quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"    Genius search failed: HTTP {resp.status_code}")
            return None
        data = resp.json()
        songs = data.get("response", {}).get("sections", [])
        if not songs:
            return None
        hits = songs[0].get("hits", [])
        if not hits:
            return None
        song_url = hits[0]["result"].get("url")
        if not song_url:
            return None
        print(f"    Found on Genius: {song_url}")
        lyrics = scrape_lyrics(song_url, headers)
        return lyrics
    except Exception as e:
        print(f"    Genius search error: {e}")
        return None


LANG_PATTERN = re.compile(
    r'^(português|español|türkçe|français|deutsch|italiano|'
    r'svenska|magyar|polski|nederlands|čeština|slovenščina|'
    r'bahasa indonesia|tiếng việt|srpski|العربية)\s*'
    r'(\([^)]*\))?\s*$',
    re.IGNORECASE
)

SECTION_RE = re.compile(
    r'^\[(?:Verse|Chorus|Intro|Bridge|Outro|Pre[ -]Chorus|Drop|Instrumental|Post[ -]Chorus|Refrain)[^\]]*\]',
    re.MULTILINE | re.IGNORECASE
)


def has_non_ascii_mostly(s):
    """Check if a line has non-ASCII characters (language names in non-Latin scripts)."""
    if not s:
        return False
    stripped = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    non_ascii = sum(1 for c in stripped if ord(c) > 127)
    return non_ascii > 0 and len(stripped) < 40


def is_bad_line(s):
    if not s:
        return True
    if re.match(r'^\d+\s*Contributors?\s*$', s, re.IGNORECASE):
        return True
    if re.match(r'^Translations?\s*$', s, re.IGNORECASE):
        return True
    if re.match(r'^Embed\s*$', s, re.IGNORECASE):
        return True
    if re.match(r'^Read\s+More\s*$', s, re.IGNORECASE):
        return True
    if LANG_PATTERN.match(s.strip()):
        return True
    if has_non_ascii_mostly(s.strip()):
        return True
    if s.strip().lower() in ('english', 'česky', 'polski', 'magyar', 'svenska'):
        return True
    return False


def clean_lyrics(text):
    """Strip Genius metadata, descriptions, and translation junk from lyrics text."""
    if not text or not text.strip():
        return text

    lines = text.split('\n')

    # Phase 1: Strip unambiguous junk lines
    filtered = []
    for line in lines:
        stripped = line.strip()
        if is_bad_line(stripped):
            continue
        filtered.append(line)

    text = '\n'.join(filtered).strip()
    if not text:
        return text

    # Phase 2: Strip standalone "Title Lyrics" header lines
    lines = text.split('\n')
    if len(lines) > 1:
        first = lines[0].strip()
        if re.match(r'^[\w\s\/\&\-\.\,\'\"\(\)\[\]\+]+Lyrics?\s*$', first, re.IGNORECASE):
            lines = lines[1:]
            text = '\n'.join(lines).strip()

    if not text:
        return text

    # Phase 3: Strip description paragraphs before first section marker
    match = SECTION_RE.search(text)
    if match and match.start() > 0:
        before = text[:match.start()].strip()
        if before:
            has_quoted = bool(re.search(r'["\u201c\u201d].+?["\u201d\u201c]', before))
            has_artist_ref = bool(re.search(
                r'(this\s+song)|(this\s+next\s+single)|'
                r'(the\s+\w+\'s\s+(?:hit|single|song|track|debut|EP|album))|'
                r'(Arguably|Widely\s+regarded|According\s+to|'
                r'Inspired\s+by|describes\s+how|reflects\s+on|'
                r'recalls|recollects|narrates|talks?\s+about|'
                r'sings?\s+about|the\s+(?:track|song)\s+(?:is|was|features|finds|sees)|'
                r'this\s+(?:ballad|anthem|track))',
                before, re.IGNORECASE))
            has_ellipsis = '...' in before or '\u2026' in before

            if has_quoted or has_artist_ref or has_ellipsis:
                text = text[match.start():]

    return text.strip()


def scrape_lyrics(url, headers):
    """Scrape lyrics from a Genius page."""
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, 'html.parser')
        containers = soup.find_all("div", {"data-lyrics-container": "true"})
        if not containers:
            return None

        parts = []
        for c in containers:
            t = c.get_text("\n").strip()
            if t:
                parts.append(t)
        raw = "\n".join(parts)

        lines = raw.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if is_bad_line(stripped):
                continue
            cleaned.append(stripped)

        lyrics = "\n".join(cleaned)
        lyrics = re.sub(r'\n{3,}', '\n\n', lyrics)
        lyrics = clean_lyrics(lyrics)

        if len(lyrics) > 10:
            return lyrics
        return None
    except Exception as e:
        print(f"    Scrape error: {e}")
        return None


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
            if entry.get('lyrics', 'TODO') not in ('TODO', '', 'lyrics not found'):
                print(f"  Skipping (already has lyrics)")
                skipped += 1
                continue

            if not resume:
                print(f"  Skipping (use --resume to process)")
                skipped += 1
                continue

            artist, title = clean_filename(filepath.name)
            if not artist or not title:
                print(f"  Could not parse artist/title")
                continue

            print(f"  Searching Genius: {artist} - {title}")
            lyrics = search_genius(artist, title)

            if lyrics:
                if dry_run:
                    print(f"    Would save ({len(lyrics)} chars)")
                else:
                    entry['lyrics'] = lyrics
                    lang = detect_language(lyrics)
                    if lang:
                        entry['vocal_language'] = lang
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"    Saved lyrics ({len(lyrics)} chars, lang={lang})")
                found += 1
            else:
                print(f"    Not found on Genius")

        except Exception as e:
            print(f"  ERROR: {e}")

        print()
        if i < total:
            time.sleep(REQUEST_DELAY)

    print("=" * 60)
    print(f"Done! Total: {total} | Found: {found} | Skipped: {skipped} | Failed: {failed}")


if __name__ == '__main__':
    main()
