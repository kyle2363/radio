"""Clean lyrics in understand_new JSON files — remove contributor/title/read more noise."""
import json
import re
import os
import glob

DIR = r"C:\Users\kyleh\Desktop\data\projects\radio\docs\understand_new"

# Files that have lyrics (not TODO)
LYRICS_FILES = [
    "Adib sin - Fated (feat. Hikaru Station).json",
    "Au5 - Divine (Official Audio).json",
    "Au5 - For You (Official Audio).json",
    "Ben Walter - Blossom.json",
    "CORSAK - Reverse (feat. 马吟吟).json",
    "Echos - Fiction.json",
    "Echos - Haunt.json",
    "Exede - Fancy Things (Lyrics).json",
    "HateBerry - Flying High (Feat. Ethan Klein).json",
    "HateBerry - No One.json",
    "i'm geist - ender.json",
    "i'm geist - i hate myself so much (lyrics).json",
    "i'm geist - imysm (lyrics).json",
    "Joon - a little more honest.json",
    "Joon - changed (feat. starfall).json",
    "Joon - falling for you.json",
    "Joon - nervousness.json",
    "Joon - pictures again.json",
    "Joon - with ease.json",
    "Joon - with ease (Official Lyric Video).json",
    "Joon - with or without (you).json",
    "Kazukii - Heart (ft. Hikaru Station).json",
    "Kill Paris - Made of Time (feat. Trove & Emily Vaughn).json",
    "Miles Away - Rewind Please (Lyrics).json",
    "Miro - Ashes.json",
    "Miro - Overwrite (ft. Q'AILA).json",
    "Miro - The Garden Of Memories (ft. Noctilucent).json",
    "MostlyEverything - On The Rocks (Lyrics).json",
    "NEFFEX - Free Me 💀 [Copyright Free] No.134.json",
    "NEFFEX - Things Are Gonna Get Better 👊  [Copyright Free] No.14.json",
    "raph - ificouldbelikeu (official audio).json",
    "Rin - tiptoe.json",
    "Satellite Empire - Sacrifice (Miro Remix).json",
    "sinxi - you're poison in my veins, my disease (ft. heylog) (lyrics).json",
    "Univz - The End Of Eternity.json",
    "Virginia To Vegas - heaven tonight (Lyric Video).json",
]

# Section tag pattern: [Verse 1], [Chorus], [Bridge], [Intro], [Outro], [Drop], etc.
SECTION_RE = re.compile(r'^\s*\[.+\]')

def clean_lyrics(raw):
    """Remove non-lyrics noise from raw scraped lyrics."""
    if not raw or raw.strip() == "TODO":
        return raw

    text = raw.strip()

    # Remove "X Contributors" lines at the start
    text = re.sub(r'^\s*\d+\s*Contributors\s*\n', '', text)

    # Remove "Song Title Lyrics" line
    text = re.sub(r'^\s*\n*', '', text)  # strip leading blank lines first

    # Try to find where actual lyrics start (first section tag like [Verse], [Chorus], etc.)
    # But first check if there's a "Read More" or intro quote before section tags
    # Look for the first line that starts with [ and has content like Verse/Chorus/Intro/Outro/Bridge/Post-Chorus/Drop/Interlude/etc.
    lines = text.split('\n')

    # Find first real section tag line
    first_section_idx = None
    for i, line in enumerate(lines):
        if SECTION_RE.match(line):
            first_section_idx = i
            break

    if first_section_idx is not None:
        # Everything before the first section tag is noise
        cleaned = '\n'.join(lines[first_section_idx:])
    else:
        # No section tags found — try to clean known patterns:
        # Remove "Read More" and everything before it
        cleaned = re.sub(r'.*?(?=\n\s*\n|\n[^\n])', '', text, flags=re.DOTALL)
        # Remove intro quotes like "..." at the start
        cleaned = re.sub(r'^\s*"[^"]*"\s*\n', '', cleaned)
        cleaned = re.sub(r'^\s*“[^”]*”\s*\n', '', cleaned)

    # Clean up: remove lines that are just whitespace or "Read More"
    cleaned_lines = []
    for line in cleaned.split('\n'):
        stripped = line.strip()
        if stripped in ('', 'Read More', 'Read More\n'):
            continue
        cleaned_lines.append(line)

    result = '\n'.join(cleaned_lines).strip()

    # Remove duplicate blank lines (keep max one blank line between sections)
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result


def main():
    cleaned_count = 0
    skipped_count = 0
    error_count = 0

    for fname in LYRICS_FILES:
        fpath = os.path.join(DIR, fname)
        if not os.path.exists(fpath):
            print(f"SKIP (missing): {fname}")
            skipped_count += 1
            continue

        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list) or len(data) == 0:
                print(f"SKIP (bad format): {fname}")
                skipped_count += 1
                continue

            entry = data[0]
            if 'lyrics' not in entry:
                print(f"SKIP (no lyrics key): {fname}")
                skipped_count += 1
                continue

            raw = entry.get('lyrics', '')
            if not raw or raw.strip() == "TODO":
                print(f"SKIP (no lyrics): {fname}")
                skipped_count += 1
                continue

            original_len = len(raw)
            cleaned = clean_lyrics(raw)
            cleaned_len = len(cleaned)

            if cleaned == raw:
                print(f"UNCHANGED: {fname} ({original_len} chars)")
                continue

            entry['lyrics'] = cleaned
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            print(f"CLEANED: {fname} ({original_len} -> {cleaned_len} chars)")
            cleaned_count += 1

        except Exception as e:
            print(f"ERROR: {fname} — {e}")
            error_count += 1

    print(f"\nDone! Cleaned: {cleaned_count}, Skipped: {skipped_count}, Errors: {error_count}")


if __name__ == '__main__':
    main()
