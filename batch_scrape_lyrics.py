"""
batch_scrape_lyrics.py
======================
Scrapes lyrics from Genius.com for files with "lyrics": "TODO" in docs/understand_new/.
Constructs direct Genius URLs instead of searching (Genius search is JS-rendered).
On failure, leaves lyrics as "TODO" for manual fill.

Usage:
    python batch_scrape_lyrics.py [--resume] [--dry-run] [--delay 2]
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
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
DEFAULT_DELAY = 2.0


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


def make_genius_slug(text):
    """Convert artist/title to Genius URL slug format."""
    # Lowercase
    text = text.lower()
    # Remove special characters except hyphens and spaces
    text = re.sub(r'[^\w\s-]', '', text)
    # Replace spaces with hyphens
    text = re.sub(r'\s+', '-', text)
    # Remove multiple hyphens
    text = re.sub(r'-+', '-', text)
    # Strip leading/trailing hyphens
    text = text.strip('-')
    return text


def build_genius_urls(artist, title):
    """Build multiple Genius URL candidates from artist and title."""
    artist_slug = make_genius_slug(artist)
    title_slug = make_genius_slug(title)

    if not artist_slug or not title_slug:
        return []

    # Primary URL: Artist-title-lyrics
    urls = [f"https://genius.com/{artist_slug}-{title_slug}-lyrics"]

    # Try without "the" prefix
    if artist_slug.startswith('the-'):
        urls.append(f"https://genius.com/{artist_slug[4:]}-{title_slug}-lyrics")

    # Try title only (some songs are listed under title only)
    urls.append(f"https://genius.com/{title_slug}-lyrics")

    # Try artist-title without "lyrics" suffix (some pages)
    urls.append(f"https://genius.com/{artist_slug}-{title_slug}")

    return urls


def scrape_lyrics(url):
    """Scrape lyrics from a Genius song page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'lxml')

        # Genius lyrics containers
        containers = soup.select('div[data-lyrics-container="true"]')
        if not containers:
            containers = soup.select('div[class*="Lyrics__Container"]')
        if not containers:
            containers = soup.select('div.lyrics')

        if not containers:
            return None

        lyrics_parts = []
        for container in containers:
            for br in container.find_all('br'):
                br.replace_with('\n')
            text = container.get_text(separator='\n')
            if text.strip():
                lyrics_parts.append(text.strip())

        if not lyrics_parts:
            return None

        lyrics = '\n\n'.join(lyrics_parts)
        lyrics = re.sub(r'\n{3,}', '\n\n', lyrics)
        lyrics = lyrics.strip()
        lyrics = re.sub(r'\[.*?\]\(.*?\)', '', lyrics)

        if len(lyrics) < 20:
            return None

        return lyrics
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

    delay = DEFAULT_DELAY
    if '--delay' in sys.argv:
        idx = sys.argv.index('--delay')
        if idx + 1 < len(sys.argv):
            try:
                delay = float(sys.argv[idx + 1])
            except ValueError:
                pass

    json_files = sorted(UNDERSTAND_DIR.glob('*.json'))
    json_files = [f for f in json_files if f.name != 'batch_manifest.json']

    print(f"Found {len(json_files)} JSON file(s) in {UNDERSTAND_DIR}")
    print(f"Resume: {resume}, Dry run: {dry_run}, Delay: {delay}s")
    print()

    if not json_files:
        print("No files to process.")
        return

    total = len(json_files)
    found = 0
    skipped = 0
    failed = 0
    not_found = 0

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
                not_found += 1
                continue

            print(f"  Looking up: {artist} - {title}")
            urls = build_genius_urls(artist, title)

            lyrics = None
            for url in urls:
                lyrics = scrape_lyrics(url)
                if lyrics:
                    print(f"    Found: {url[:70]}...")
                    break
                time.sleep(0.5)  # Small delay between URL attempts

            if lyrics:
                lang = detect_language(lyrics)
                if dry_run:
                    print(f"    Would save lyrics ({len(lyrics)} chars, lang={lang})")
                else:
                    entry['lyrics'] = lyrics
                    if lang:
                        entry['vocal_language'] = lang
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"    Saved lyrics ({len(lyrics)} chars, lang={lang})")
                found += 1
            else:
                print(f"    Not found on Genius, leaving as TODO")
                not_found += 1

        except Exception as e:
            print(f"  ERROR: {e} (leaving as TODO)")
            not_found += 1

        print()
        if i < total:
            time.sleep(delay)

    print("=" * 60)
    print(f"Done! Total: {total} | Found: {found} | Skipped: {skipped} | Not Found: {not_found} | Failed: {failed}")


if __name__ == '__main__':
    main()
