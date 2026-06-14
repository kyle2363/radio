"""Test clean_lyrics on various understand files."""
import json, re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
import config

sections = r'(?:Verse|Chorus|Intro|Bridge|Outro|Pre[ -]Chorus|Drop|Instrumental|Post[ -]Chorus|Refrain)'
section_pat = re.compile(rf'^\[(?:{sections})[^\]]*\]', re.MULTILINE | re.IGNORECASE)

def clean_lyrics(text):
    if not text or not text.strip():
        return text
    text = re.sub(r'^\d+\s+Contributors\s*\n(.+?)(?:Lyrics)?\s*\n', '', text, flags=re.DOTALL)
    if not text.strip():
        return text
    match = section_pat.search(text)
    if match and match.start() > 0:
        before = text[:match.start()].strip()
        if before:
            has_quoted_title = bool(re.search(r'["\u201c].+?["\u201d]', before))
            has_artist_ref = bool(re.search(
                r'(the\s+\w+\'\s+(?:hit|single|song|track|debut|EP|album))|'
                r'(This\s+(?:song|single|track|next))|'
                r'(Arguably\s+(?:the|The))|'
                r'(Widely\s+regarded)|'
                r'(According\s+to)',
                before, re.IGNORECASE
            ))
            is_long = len(before) > 150
            ends_with_ellipsis = before.rstrip().endswith('\u2026') or before.rstrip().endswith('...')
            if has_quoted_title or has_artist_ref or is_long or ends_with_ellipsis:
                text = text[match.start():]
    return text.strip()

test_files = [
    "EDEN - rock + roll.json",
    "Nightly - Twenty Something.json",
    "aldn - fall at the same time.json",
    "aldn - precious [official lyric video].json",
    "asumuh - missing u.json",
    "Forester - Present (Lyric Video).json",
    "MALO - Mimo's Theme.json",
    "OneRepublic - Didn't I (Official Music Video).json",
    "Ben Hazlewood - Paint Me Black ft. Mali Koa Hood (Unikron Remix).json",
    "Taylor Swift - Love Story.json",
    "The Eden Project - Man Down.json",
    "The Eden Project - Times Like These.json",
    "The Eden Project - Statues ft. Leah Kelly.json",
    "[Progressive House] - Hellberg - The Girl (feat. Cozi Zuehlsdorff) [Monstercat Release].json",
    "aldn - predictable (Lyrics).json",
    "aldn - sink.json",
    "aldn - tellmewhatuwant (visualizer).json",
    "Aaryan Shah - Renegade.json",
    "Adib Sin - Airwaves (Ft Azuria Sky & Leopold).json",
    "Alan Walker - Fade [NCS Release].json",
]

for fname in test_files:
    path = os.path.join(config.ACESTEP_JSON_DIR, fname)
    if not os.path.exists(path):
        print(f"{fname}: FILE NOT FOUND")
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    text = data[0].get('lyrics', '')
    original = text
    cleaned = clean_lyrics(text)
    changed = cleaned != original.strip()
    print(f"\n{fname}")
    print(f"  Changed: {changed}  ({len(original)} -> {len(cleaned)} chars)")
    if changed:
        # Show what was removed
        removed = original[:min(80, len(original))]
        print(f"  Original starts: {removed[:80]!r}")
        print(f"  Cleaned starts:  {cleaned[:80]!r}")
    else:
        print(f"  Starts: {original[:80]!r}")
