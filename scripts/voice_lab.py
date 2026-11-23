"""Design, audition and install radio voices with the local zonos3-server.

Three jobs:

``designs``  Render every voice option in ``VOICE_DESIGNS`` (several takes each) to
             ``.test-runtime/voice_lab/<timestamp>/`` with an ``index.html`` to listen.
             A *stock* option is a ZONOS3 bundled voice in reference mode; a *designed*
             option steers a bundled voice's speaker embedding (age, gender, accent,
             texture...) in embedding mode. Either way the result becomes an ordinary
             reference clip once installed, so the radio keeps speaking in reference mode.
``ed``       Extend the short Ed Holderness clip: render neutral presenter lines from
             Ed's reference codes, check each with Whisper, and build a candidate
             10-15 s clip (the real 3.9 s first) for you to approve.
``install``  Copy a chosen take (or the Ed candidate) into ``backend/announcer/voices/``
             as ``<name>.mp3``, add its transcript and description, and encode its
             reference codes. Only this command writes outside ``.test-runtime``.

Examples (from the repo root, with the zonos3-server running):
  backend\\venv\\Scripts\\python -B scripts/voice_lab.py designs
  backend\\venv\\Scripts\\python -B scripts/voice_lab.py designs --only "The Highland Storyteller" --takes 4
  backend\\venv\\Scripts\\python -B scripts/voice_lab.py ed
  backend\\venv\\Scripts\\python -B scripts/voice_lab.py install --voice "The Highland Storyteller" --take <path.wav>
  backend\\venv\\Scripts\\python -B scripts/voice_lab.py install --voice "Ed Holderness" --take <ed_candidate.wav> --replace
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import requests
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES_DIR = os.path.join(ROOT, "backend", "announcer", "voices")
TRANSCRIPTS = os.path.join(VOICES_DIR, "transcripts.json")
DESCRIPTIONS = os.path.join(VOICES_DIR, "radio voice prompt.txt")
LAB_DIR = os.path.join(ROOT, ".test-runtime", "voice_lab")
CODES_DIR = os.path.join(ROOT, "data", "zonos3_voices")
BASE_URL = os.environ.get("ZONOS3_BASE_URL", "http://127.0.0.1:1919").rstrip("/")
HEADERS = {"X-TTS-Session-ID": "voice-lab"}
TARGET_LUFS = -18.0
SAMPLE_GAP_SECS = 0.35


@dataclass
class Option:
    """One way to make a voice: a bundled base voice, plus steering when designed."""
    label: str
    base: str                      # bundled voice name prefix, e.g. "Rupert"
    controls: dict = field(default_factory=dict)  # raw doses => embedding mode
    blend: str | None = None       # a second bundled voice to blend with (server slerp)
    blend_t: float = 0.5           # 0 = all base, 1 = all blend

    @property
    def designed(self) -> bool:
        return bool(self.controls) or bool(self.blend)


@dataclass
class VoiceDesign:
    name: str          # becomes the clip name: "<name>.mp3"
    use: str           # what it is for
    description: str   # written to "radio voice prompt.txt"
    script: str        # ~12 s, in character; becomes the transcript
    options: list[Option]


# An "Installed:" option is the recipe behind the clip in backend/announcer/voices (a blend of two
# bundled voices, so the clip is not a near-copy of a stock voice: speaker similarity under 0.6).
# Doses stay inside the studio's "safe" ranges (GET /v2/controls -> ui.controls). The non-English
# accents (accents_l1.*) switch the voice into that language above about 1.0, so they stay at 0.7-0.8.
VOICE_DESIGNS: list[VoiceDesign] = [
    # --- replacements for personas that share a voice ---
    VoiceDesign(
        "The Festival Raver", "caller_pulse (DJ Pulse)",
        "A young male EDM devotee in his late 20s with a bright, quick, slightly breathless voice and a neutral American accent. He talks fast, like he just stepped out of a warehouse set, and his excitement builds toward the end of every sentence. Clean, close studio recording.",
        "Okay so I just got back from a four day festival and my ears are still ringing. Best set was at sunrise, one-thirty-two BPM, rolling bassline, and then the whole field just lost it when the drop finally came in.",
        [Option("Jett, stock", "Jett"),
         Option("Wyatt, younger and livelier", "Wyatt", {"identity.age": -1.2, "pitch.pitch_range": 1.5, "texture.shrill": 1}),
         Option("Jett, younger and brighter", "Jett", {"identity.age": -0.8, "texture.shrill": 1.2})]),
    VoiceDesign(
        "The Hype Machine", "guest_hype (DJ Blaze)",
        "A loud, confident male club MC in his early 30s with a punchy, slightly raspy voice and an American accent. He projects like he is talking over a crowd, rides every word with energy, and never lets a sentence sit flat. Studio recording with a little grit.",
        "Let me hear you one more time. We are not slowing down tonight, not even a little bit. This next record goes straight to the floor, so turn it up, open a window, and let the whole block know what time it is.",
        [Option("Jett, raspy and deeper", "Jett", {"texture.raspy": 1.5, "texture.deep": 1}),
         Option("Thaddeus, younger and varied", "Thaddeus", {"identity.age": -1.2, "pitch.pitch_range": 1.5}),
         Option("Wyatt, raspy and varied", "Wyatt", {"texture.raspy": 2, "pitch.pitch_range": 1.2})]),
    VoiceDesign(
        "The Moonlit Whisper", "guest_chill (DJ Moonlight)",
        "A calm young woman in her early 30s with a soft, airy, close-mic voice and a neutral American accent. She speaks slowly and gently, leaving room between phrases, like someone keeping you company at two in the morning. Intimate, warm studio recording.",
        "Hey, it's just us now. The city's gone quiet, the kettle's on, and I've got a handful of slow songs I've been saving for exactly this hour. Get comfortable, there's no rush tonight.",
        [Option("Willow, stock", "Willow"),
         Option("Savannah, soft and low", "Savannah", {"texture.soft": 3, "pitch.pitch": -0.8, "pitch.pitch_range": -0.8}),
         Option("Imogen, soft", "Imogen", {"texture.soft": 2.5})]),
    VoiceDesign(
        "The Milanese Selector", "guest_international (DJ Etoile)",
        "A sophisticated woman in her 40s with a smooth, warm voice and a light Italian accent in her English. She speaks unhurriedly and with quiet amusement, choosing each word as if it were a record. Warm, rich studio recording.",
        "Bonsoir. Tonight I bring you records from three cities, Lagos, Lisbon, and a little club in Montreal where I first heard this next one. Close your eyes and let the rhythm decide where you are.",
        [Option("Imogen, French accent", "Imogen", {"accents_l1.fra": 0.8, "identity.age": 0.8}),
         Option("Savannah, French accent, lower", "Savannah", {"accents_l1.fra": 0.7, "pitch.pitch": -0.8}),
         Option("Ginevra, stock (Italian voice reading English)", "Ginevra")]),
    VoiceDesign(
        "The Old Hollywood Narrator", "guest_classic (The Director)",
        "A distinguished man in his 60s with a deep, rounded voice and a cultured transatlantic delivery. He speaks with theatrical timing, slowing for emphasis and savouring every phrase, like he is narrating the trailer of a classic film. Rich studio recording.",
        "Picture it. A rain-soaked street, a neon sign humming over a little jazz club, and a band that nobody remembers anymore. Tonight we bring them back, one scene at a time. Roll the first reel.",
        [Option("Installed: Cillian x Thaddeus, older (seed 1)", "Cillian", {'identity.age': 2, 'texture.authoritative': 1.5}, blend="Thaddeus", blend_t=0.5),
         Option("Rupert, older and deeper", "Rupert", {"identity.age": 1.5, "texture.deep": 2}),
         Option("Thaddeus, older", "Thaddeus", {"identity.age": 1.5, "texture.authoritative": 1.5}),
         Option("Wyatt, older and deeper", "Wyatt", {"identity.age": 1.8, "texture.deep": 2, "pitch.pitch_range": 0.8})]),
    VoiceDesign(
        "The Midnight Confidant", "guest_late_night (DJ Midnight)",
        "A man in his 40s with a deep, velvety late-night radio voice and a neutral American accent. He speaks quietly and slowly, close to the microphone, like a confidant keeping you company in the small hours. Warm, intimate studio recording.",
        "It's a little past three, and if you're still awake, you're in good company. I've slowed tonight's records right down and let them drift. Turn the lights low, and just let this one breathe for a while.",
        [Option("Installed: Gaspard x Thiago, deep and soft (seed 1)", "Gaspard", {'texture.deep': 2.5, 'texture.soft': 1.5, 'accents_en.american': 1.2}, blend="Thiago", blend_t=0.5),
         Option("Thaddeus, late-night (deep, clean room)", "Thaddeus", {"texture.deep": 2, "room.clean": 2.5}),
         Option("Wyatt, deep and husky", "Wyatt", {"texture.deep": 2.5, "texture.husky": 1.5, "pitch.pitch_range": -0.8}),
         Option("Rupert, deep and soft", "Rupert", {"texture.deep": 2, "texture.soft": 1.5})]),
    # --- regional callers ---
    VoiceDesign(
        "The Highland Storyteller", "new caller: older Scottish storyteller",
        "An elderly Scottish man in his 70s with a deep, weathered voice and a broad Highland accent. He tells long, winding stories at an unhurried pace with a dry twinkle of humour. Warm, close recording.",
        "Aye, now this one takes me back. Nineteen seventy-four, a wee dance hall up past Inverness, rain coming in sideways, and a fiddler who played this tune so fast the floorboards gave up on us.",
        [Option("Installed: Cillian x Jett, elderly Scottish, raspy (seed 1)", "Cillian", {'identity.age': 2, 'accents_en.scottish': 1.5, 'texture.raspy': 1.5}, blend="Jett", blend_t=0.4),
         Option("Rupert, elderly Scottish", "Rupert", {"identity.age": 1.5, "accents_en.scottish": 1.5, "texture.deep": 2, "pitch.pitch_range": -1}),
         Option("Cillian, older Scottish", "Cillian", {"identity.age": 1.8, "accents_en.scottish": 1.5, "texture.deep": 1.5}),
         Option("Wyatt, elderly Scottish, raspy", "Wyatt", {"identity.age": 1.8, "accents_en.scottish": 1.5, "texture.raspy": 1.5})]),
    VoiceDesign(
        "The Galway Sweetheart", "new caller: Irish woman",
        "A friendly Irish woman in her 30s with a soft, lilting voice and a gentle west-of-Ireland accent. She speaks warmly and a little quickly, laughing easily and teasing the host like an old friend. Clean, close recording.",
        "Ah, how are you keeping? Listen, I've been driving round Galway all evening with this station on, and I had to ring in, because that last song had me singing at the traffic lights like an eejit.",
        [Option("Installed: Savannah x Ginevra, Irish, younger (seed 1)", "Savannah", {'accents_en.irish': 1.5, 'identity.age': -0.6}, blend="Ginevra", blend_t=0.45),
         Option("Imogen, Irish and soft", "Imogen", {"accents_en.irish": 1.5, "texture.soft": 2}),
         Option("Savannah, Irish", "Savannah", {"accents_en.irish": 1.5, "texture.soft": 1.5}),
         Option("Savannah, Irish, younger", "Savannah", {"accents_en.irish": 1.5, "identity.age": -0.8})]),
    VoiceDesign(
        "The Sydney Backpacker", "new caller: young Australian",
        "A cheerful young Australian man in his early 20s with a bright, easygoing voice and a broad Aussie accent. He talks fast, ends sentences on an upward lilt, and sounds like he is calling from a hostel common room. Clean recording.",
        "G'day, mate, I'm calling all the way from a hostel in Lisbon, and someone's got your station on in the kitchen. Absolute banger, that last one. Whole room stopped making toast just to listen.",
        [Option("Installed: Jett x Thiago, young Australian (seed 1)", "Jett", {'identity.age': -1.5, 'accents_en.australian': 1.5}, blend="Thiago", blend_t=0.45),
         Option("Jett, young Australian", "Jett", {"identity.age": -1.5, "accents_en.australian": 1.5, "pitch.pitch_range": 1}),
         Option("Wyatt, young Australian", "Wyatt", {"identity.age": -1.5, "accents_en.australian": 1.5, "pitch.pitch_range": 1}),
         Option("Savannah, young Australian (female)", "Savannah", {"identity.age": -1, "accents_en.australian": 1.5})]),
    VoiceDesign(
        "The Night Shift Nurse", "new caller: Indian English night-shift worker",
        "A warm, tired-but-cheerful woman in her 30s with a clear voice and a light Indian English accent. She speaks kindly and precisely, a little quietly, like she is on her break in a hospital corridor at four in the morning. Clean recording.",
        "Hello, hi, I'm on my break on the night shift, so I have exactly ten minutes. This station keeps me going till sunrise, honestly. Could you play something gentle for the whole ward? We've had a long one.",
        [Option("Installed: Sravya x Imogen, Indian English (seed 2)", "Sravya", {'accents_en.indian': 1.2}, blend="Imogen", blend_t=0.5),
         Option("Ananya, stock (Hindi voice reading English)", "Ananya"),
         Option("Savannah, Indian English accent", "Savannah", {"accents_en.indian": 1.5, "texture.soft": 1}),
         Option("Imogen, Indian English accent", "Imogen", {"accents_en.indian": 1.5})]),
    # --- character callers ---
    VoiceDesign(
        "The Long-Haul Trucker", "new caller: gravelly old trucker",
        "A gravelly American man in his 60s with a deep, rough, road-worn voice. He speaks slowly and plainly, a little tired, like a long-haul driver calling in from a truck stop at two in the morning. Warm, close recording.",
        "Evening. I'm parked up outside of Amarillo, coffee's terrible, and I've got about four hundred miles left before sunrise. Y'all have kept me company since Oklahoma, so I figured I owed you a call.",
        [Option("Wyatt, gravelly old man", "Wyatt", {"identity.gender": -1, "identity.age": 2, "texture.raspy": 2.5, "texture.deep": 2}),
         Option("Thaddeus, gravelly and older", "Thaddeus", {"identity.age": 1.8, "texture.raspy": 2.5, "texture.deep": 1.5}),
         Option("Wyatt, husky, less rasp", "Wyatt", {"identity.age": 1.5, "texture.husky": 2.5, "texture.deep": 2})]),
    VoiceDesign(
        "The Session Musician", "new caller: retired session musician",
        "A retired American session bassist in his late 60s with a deep, mellow, slightly husky voice. He speaks slowly and fondly, with the easy authority of someone who played on hundreds of records nobody credits. Warm studio recording.",
        "You know what I love about this track? Listen to the bass under the second verse. That's somebody who wasn't trying to be heard, just holding the whole room together. I spent thirty years doing exactly that.",
        [Option("Installed: Rodrigo x Thaddeus, older and husky (seed 2)", "Rodrigo", {'identity.age': 2, 'texture.husky': 2, 'accents_en.american': 1}, blend="Thaddeus", blend_t=0.35),
         Option("Thaddeus, older and husky", "Thaddeus", {"identity.age": 2, "texture.husky": 2}),
         Option("Wyatt, older, deep and husky", "Wyatt", {"identity.age": 2, "texture.deep": 1.5, "texture.husky": 1.5}),
         Option("Rupert, older and deep (British)", "Rupert", {"identity.age": 2, "texture.deep": 1.5})]),
    VoiceDesign(
        "The Bilingual Caller", "new caller: bilingual, Spanish accent",
        "A lively bilingual woman in her 30s with a warm, expressive voice and a Spanish accent in her English. She speaks quickly and with her whole heart, and slips the odd Spanish word into her sentences. Clean, close recording.",
        "Oye, okay, I have to tell you, my abuela was in the kitchen and she heard this song and she started dancing with the spoon. In my family, if the grandmother dances, it is officially a good song.",
        [Option("Ximena, stock (Spanish voice reading English)", "Ximena"),
         Option("Savannah, light Spanish accent", "Savannah", {"accents_l1.spa": 0.45}),
         Option("Rodrigo, stock (male, Spanish voice reading English)", "Rodrigo")]),
    # --- station roles ---
    VoiceDesign(
        "The Newsreader", "station role: news and weather breaks",
        "A crisp, authoritative British newsreader in his 40s with a clear, measured voice and impeccable diction. He reads steadily and neutrally, every word distinct, with the calm gravity of a public broadcaster. Clean, dry studio recording.",
        "The headlines at the top of the hour. Overnight storms have eased across the region, with rail services expected to return to normal by mid-morning. Temperatures will climb to fourteen degrees this afternoon.",
        [Option("Rupert, BBC newsreader", "Rupert", {"accents_en.british": 1.5, "texture.authoritative": 3, "room.clean": 2}),
         Option("Imogen, BBC newsreader (female)", "Imogen", {"accents_en.british": 1.2, "texture.authoritative": 2.5, "room.clean": 2}),
         Option("Rupert, stock", "Rupert")]),
    VoiceDesign(
        "The Traffic Reporter", "station role: traffic and travel",
        "An upbeat American woman in her 30s with a bright, quick, clear voice. She delivers traffic updates briskly and cheerfully, like she is reading from a helicopter seat with a smile. Clean studio recording.",
        "Quick look at the roads. Northbound on the ninety-five, an earlier breakdown has cleared, but you're still looking at ten extra minutes past the river. Downtown is moving nicely, and the bridge is clear in both directions.",
        [Option("Savannah, bright and brisk", "Savannah", {"texture.shrill": 1, "pitch.pitch_range": 1, "room.clean": 1.5}),
         Option("Savannah, stock", "Savannah"),
         Option("Imogen, bright (British)", "Imogen", {"texture.shrill": 1, "room.clean": 1.5})]),
    VoiceDesign(
        "The Sponsor Voice", "station role: fake-sponsor ad reads",
        "A polished American announcer in his 40s with a rich, deep, smiling voice. He delivers ad copy with big warmth and perfect timing, a classic radio commercial voice. Clean, bright studio recording.",
        "This hour of uninterrupted music is brought to you by Night Owl Coffee. Roasted slow, brewed strong, and always open late. Night Owl Coffee, because the best ideas show up after midnight.",
        [Option("Installed: Thiago x Cillian, authoritative (seed 2)", "Thiago", {'texture.authoritative': 2, 'accents_en.american': 1.2}, blend="Cillian", blend_t=0.5),
         Option("Thaddeus, stock", "Thaddeus"),
         Option("Wyatt, deep and authoritative", "Wyatt", {"texture.deep": 1.5, "texture.authoritative": 2, "room.clean": 2}),
         Option("Thaddeus, deeper", "Thaddeus", {"texture.deep": 1.5, "room.clean": 1.5})]),
    # --- surprises, for future personas ---
    VoiceDesign(
        "The Welsh Choirmaster", "surprise: a choir conductor who hears harmony in everything",
        "A cheerful Welsh man in his 50s with a rich, musical voice and a sing-song Welsh accent. He speaks with gusto, rising and falling like a hymn, and cannot help pointing out every harmony in a song. Warm recording.",
        "Now then, did you hear that, in the chorus? Three voices, and the top one sits just a whisker sharp, on purpose mind. That's the bit that gives you goosebumps. My choir would have that sorted by Tuesday.",
        [Option("Installed: Jett x Wyatt, Welsh and older (seed 2)", "Jett", {'accents_en.welsh': 1.5, 'identity.age': 1.3}, blend="Wyatt", blend_t=0.5),
         Option("Rupert, Welsh and older", "Rupert", {"accents_en.welsh": 1.5, "identity.age": 1, "pitch.pitch_range": 1.5}),
         Option("Cillian, Welsh", "Cillian", {"accents_en.welsh": 1.5, "pitch.pitch_range": 1.2}),
         Option("Wyatt, Welsh", "Wyatt", {"accents_en.welsh": 1.5, "identity.age": 1})]),
    VoiceDesign(
        "The Techno Purist", "surprise: a stern Berlin techno purist",
        "A serious German man in his 40s with a low, flat, precise voice and a German accent in his English. He speaks slowly and deadpan, judging every track by its kick drum and finding most of them lacking. Dry studio recording.",
        "I must be honest with you. The kick drum on this track is acceptable. Not good, acceptable. In Berlin we would play this at six in the morning, maybe, when everyone is too tired to complain.",
        [Option("Installed: Wolfgang x Gaspard, flat and husky (seed 1)", "Wolfgang", {'pitch.pitch_range': -1.5, 'texture.husky': 1}, blend="Gaspard", blend_t=0.45),
         Option("Wolfgang, stock (German voice reading English)", "Wolfgang"),
         Option("Rupert, German accent, monotone", "Rupert", {"accents_l1.deu": 0.8, "pitch.pitch_range": -1.5}),
         Option("Wyatt, German accent, deep", "Wyatt", {"accents_l1.deu": 0.8, "texture.deep": 1.5, "pitch.pitch_range": -1})]),
    VoiceDesign(
        "The Tarot Reader", "surprise: a mystic who reads songs like tarot cards",
        "A theatrical woman in her 50s with a low, smoky, mysterious voice and a neutral accent. She speaks slowly and knowingly, dropping into a near-whisper for the important parts, as if reading your future from the song. Intimate recording.",
        "Mm, I drew a card before I called. The Moon. Illusion, longing, things not quite as they seem. And then this song came on, and I thought, well. The universe is not even being subtle tonight.",
        [Option("Installed: Imogen x Ginevra, low and mysterious (seed 1)", "Imogen", {'texture.husky': 2, 'pitch.pitch': -1, 'identity.age': 1.3}, blend="Ginevra", blend_t=0.5),
         Option("Morgana, stock", "Morgana"),
         Option("Imogen, older, husky and low", "Imogen", {"identity.age": 1.5, "texture.husky": 2, "pitch.pitch": -1}),
         Option("Savannah, husky and low", "Savannah", {"texture.husky": 2.5, "pitch.pitch": -1, "identity.age": 1})]),
    VoiceDesign(
        "The Retired Teacher", "surprise: a kind retired schoolteacher",
        "A kind, elderly American woman in her 70s with a gentle, slightly thin voice and careful diction. She speaks warmly and a little slowly, like a favourite schoolteacher reading aloud to a class. Soft, clean recording.",
        "Good evening, dear. I taught music for thirty-one years, and every spring my fifth graders would beg me to play this one. I'd pretend to say no, and then I'd put it on anyway. Some things never change.",
        [Option("Savannah, elderly", "Savannah", {"identity.age": 2, "texture.soft": 1.5}),
         Option("Imogen, elderly (British)", "Imogen", {"identity.age": 2, "texture.soft": 1}),
         Option("Willow, older", "Willow", {"identity.age": 2})]),
]

ED_NAME = "Ed Holderness"
ED_LINES = [
    "Lovely to have your company this morning, wherever you're listening.",
    "That was a proper classic, and I'll tell you what, it still sounds brilliant.",
    "It's just gone half past seven, the kettle's on, and the roads are fairly quiet.",
    "Plenty more coming up between now and nine, so stay right where you are.",
    "Good morning, good morning. Let's ease into the day with a bit of soul.",
    "If you're up early with us, thank you, it genuinely makes the show.",
    "Right then, here's one that always gets the phones ringing.",
    "Bit of a grey start out there, but there's sunshine on the way this afternoon.",
    "You're listening to the overnight show, and I'm keeping you company until six.",
    "I've had loads of messages about that last record, so here it is again.",
    "Now, this next track was recorded in a tiny studio in Sheffield.",
    "Stay with me, we've got the news and the weather just after this.",
]


# ---- server ----

def speakers() -> dict[str, str]:
    r = requests.get(f"{BASE_URL}/v2/speakers", headers=HEADERS, timeout=10)
    r.raise_for_status()
    out = {}
    for sp in r.json().get("speakers", []):
        if sp.get("scope") == "default":
            out[(sp.get("name") or sp.get("label") or "").split(" ")[0]] = sp["id"]
    return out


def generate(text: str, body: dict, out_path: str, seed: int) -> bool:
    request = {"text": text, "language": "en_us", "format": "wav", "stream": False, "seed": seed,
               "condition_values": {"loudness": TARGET_LUFS}, **body}
    r = requests.post(f"{BASE_URL}/v2/generate", json=request, headers=HEADERS, timeout=120)
    if r.status_code != 200:
        print(f"    ! {r.status_code}: {r.text[:200]}")
        return False
    with open(out_path, "wb") as f:
        f.write(r.content)
    return True


def option_body(option: Option, ids: dict[str, str]) -> dict:
    sid = ids.get(option.base)
    if not sid:
        raise SystemExit(f"No bundled voice named {option.base!r} on the server")
    if option.blend:
        other = ids.get(option.blend)
        if not other:
            raise SystemExit(f"No bundled voice named {option.blend!r} on the server")
        return {"speaker_mode": "embedding", "speaker_blend_embedding_id_a": sid, "speaker_blend_embedding_id_b": other,
                "speaker_blend_t": option.blend_t, "controls": option.controls}
    if option.designed:
        return {"speaker_mode": "embedding", "speaker_embedding_id": sid, "controls": option.controls}
    return {"speaker_mode": "reference", "speaker_embedding_id": sid}


# ---- audio helpers ----

def read_mono(path: str) -> tuple[np.ndarray, int]:
    data, rate = sf.read(path, always_2d=True)
    return data.mean(axis=1), rate


def loudness(data: np.ndarray, rate: int) -> float:
    import pyloudnorm
    return pyloudnorm.Meter(rate).integrated_loudness(data)


def to_lufs(data: np.ndarray, rate: int, target: float) -> np.ndarray:
    now = loudness(data, rate)
    if not np.isfinite(now):
        return data
    data = data * (10 ** ((target - now) / 20))
    peak = float(np.max(np.abs(data))) or 1.0
    return data * min(1.0, 0.89 / peak)


def trim_silence(data: np.ndarray, rate: int, floor_db: float = -45.0) -> np.ndarray:
    win = max(1, int(rate * 0.02))
    level = np.sqrt(np.convolve(data ** 2, np.ones(win) / win, mode="same"))
    loud = np.nonzero(level > 10 ** (floor_db / 20) * max(1e-9, float(np.max(np.abs(data)))))[0]
    if not len(loud):
        return data
    pad = int(rate * 0.05)
    return data[max(0, loud[0] - pad): loud[-1] + pad]


def resample(data: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target:
        return data
    from scipy.signal import resample_poly
    g = np.gcd(rate, target)
    return resample_poly(data, target // g, rate // g)


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is needed on PATH")
    return exe


def decode_mp3(path: str, out_wav: str):
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", path, out_wav], check=True)


# ---- Whisper check ----

_whisper = None


def transcribe(path: str) -> str:
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        try:
            _whisper = WhisperModel("small.en", device="cuda", compute_type="float16")
        except Exception:
            _whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(path, beam_size=1)
    return " ".join(s.text.strip() for s in segments)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def word_error_rate(expected: str, heard: str) -> float:
    a, b = words(expected), words(heard)
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return d[len(b)] / max(1, len(a))


# ---- page ----

def write_index(out_dir: str, title: str, sections: list[tuple[str, str, list[tuple[str, str, str]]]]):
    """sections: (heading, intro html, [(label, relative wav, note)])."""
    parts = [f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>",
             "<style>body{font:15px system-ui;max-width:900px;margin:24px auto;padding:0 16px;background:#111;color:#ddd}"
             "h2{margin-top:36px;color:#8cf}.t{display:grid;grid-template-columns:1fr 330px;gap:10px;align-items:center;"
             "padding:6px 0;border-bottom:1px solid #333}.n{color:#999;font-size:13px}p{color:#bbb}"
             "audio{width:330px}.bad{color:#f88}</style>",
             f"<h1>{html.escape(title)}</h1>"]
    for heading, intro, takes in sections:
        parts.append(f"<h2>{html.escape(heading)}</h2>{intro}")
        for label, rel, note in takes:
            parts.append(f"<div class=t><div>{html.escape(label)}<div class=n>{note}</div>"
                         f"<div class=n>{html.escape(rel)}</div></div>"
                         f"<audio controls preload=none src=\"{html.escape(rel)}\"></audio></div>")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def new_out_dir(kind: str) -> str:
    out = os.path.join(LAB_DIR, f"{kind}_{datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(out, exist_ok=True)
    return out


# ---- commands ----

def cmd_designs(args):
    ids = speakers()
    chosen = [v for v in VOICE_DESIGNS if not args.only or v.name in args.only]
    out = new_out_dir("designs")
    sections, manifest = [], []
    for voice in chosen:
        print(f"{voice.name}")
        takes = []
        for oi, option in enumerate(voice.options, 1):
            body = option_body(option, ids)
            for seed in range(1, args.takes + 1):
                rel = f"{slug(voice.name)}/opt{oi}_{slug(option.label)}_seed{seed}.wav"
                path = os.path.join(out, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if not generate(voice.script, body, path, seed):
                    continue
                blend = f"{option.base} x {option.blend} ({option.blend_t:g}), " if option.blend else ""
                note = "designed: " + blend + ", ".join(f"{k} {v:+g}" for k, v in option.controls.items()) if option.designed else "stock voice, reference mode"
                if args.check:
                    wer = word_error_rate(voice.script, transcribe(path))
                    seconds = sf.info(path).duration
                    flag = " class=bad" if wer > 0.15 else ""
                    note += f" &middot; <span{flag}>misread {wer:.0%}</span> &middot; {seconds:.1f} s"
                takes.append((f"Option {oi}: {option.label} (seed {seed})", rel, note))
                manifest.append({"voice": voice.name, "option": oi, "label": option.label, "seed": seed, "wav": path})
                print(f"  option {oi} seed {seed}: {rel}")
        intro = (f"<p><b>For:</b> {html.escape(voice.use)}</p><p><b>Description:</b> {html.escape(voice.description)}</p>"
                 f"<p><b>Script:</b> {html.escape(voice.script)}</p>")
        sections.append((voice.name, intro, takes))
    write_index(out, "Voice designs", sections)
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nOpen {os.path.join(out, 'index.html')}")


def cmd_ed(args):
    clip = os.path.join(VOICES_DIR, f"{ED_NAME}.mp3")
    codes = sorted(p for p in os.listdir(CODES_DIR) if p.startswith(f"{ED_NAME}.") and p.endswith(".codes.npy"))
    if not codes:
        raise SystemExit("No reference codes for Ed; run scripts/zonos3_encode_voices.py first")
    ref_codes = np.load(os.path.join(CODES_DIR, codes[-1])).astype(int).tolist()
    out = new_out_dir("ed")
    original_wav = os.path.join(out, "original.wav")
    decode_mp3(clip, original_wav)
    original = transcripts().get(ED_NAME, "")

    rows = []
    for li, line in enumerate(ED_LINES, 1):
        for seed in range(1, args.takes + 1):
            rel = f"takes/line{li:02d}_seed{seed}.wav"
            path = os.path.join(out, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            body = {"speaker_mode": "reference", "speaker_reference_codes": ref_codes}
            if not generate(line, body, path, seed):
                continue
            heard = transcribe(path)
            wer = word_error_rate(line, heard)
            rows.append({"line": line, "seed": seed, "rel": rel, "path": path, "wer": wer,
                         "seconds": sf.info(path).duration, "heard": heard})
            print(f"  line {li} seed {seed}: misread {wer:.0%}")

    # Candidate: the real clip first, then the cleanest take of distinct lines up to ~14 s.
    best_per_line = {}
    for row in sorted(rows, key=lambda r: (r["wer"], abs(r["seconds"] - 4.0))):
        best_per_line.setdefault(row["line"], row)
    ranked = sorted(best_per_line.values(), key=lambda r: r["wer"])
    data, rate = read_mono(original_wav)
    data = to_lufs(trim_silence(data, rate), rate, TARGET_LUFS)
    pieces, used, total = [data], [], len(data) / rate
    for row in ranked:
        if row["wer"] > 0.1 or total > args.seconds - 2.5:
            continue
        take, take_rate = read_mono(row["path"])
        take = to_lufs(trim_silence(resample(take, take_rate, rate), rate), rate, TARGET_LUFS)
        if total + SAMPLE_GAP_SECS + len(take) / rate > args.seconds + 1:
            continue
        pieces += [np.zeros(int(rate * SAMPLE_GAP_SECS)), take]
        used.append(row)
        total += SAMPLE_GAP_SECS + len(take) / rate
    candidate = np.concatenate(pieces)
    candidate_path = os.path.join(out, "ed_candidate.wav")
    sf.write(candidate_path, candidate, rate)
    candidate_text = " ".join([original] + [r["line"] for r in used])
    with open(os.path.join(out, "ed_candidate.txt"), "w", encoding="utf-8") as f:
        f.write(candidate_text)

    used_rels = {r["rel"] for r in used}
    sections = [
        ("Candidate extended clip", f"<p>{total:.1f} s: the original clip, then {len(used)} generated takes.</p>"
         f"<p><b>Transcript:</b> {html.escape(candidate_text)}</p>",
         [("Original (3.9 s, real)", "original.wav", html.escape(original)),
          ("Candidate", "ed_candidate.wav", "install with: voice_lab.py install --voice \"Ed Holderness\" --take " + html.escape(candidate_path) + " --replace")]),
        ("All takes", "<p>Sorted by line. Takes marked * are in the candidate.</p>",
         [(("* " if r["rel"] in used_rels else "") + f"{r['line']} (seed {r['seed']})", r["rel"],
           f"<span{' class=bad' if r['wer'] > 0.1 else ''}>misread {r['wer']:.0%}</span> &middot; {r['seconds']:.1f} s &middot; heard: {html.escape(r['heard'])}")
          for r in rows]),
    ]
    write_index(out, "Ed Holderness extension", sections)
    print(f"\nCandidate {total:.1f} s from {len(used)} takes. Open {os.path.join(out, 'index.html')}")


def transcripts() -> dict:
    with open(TRANSCRIPTS, encoding="utf-8") as f:
        return json.load(f)


def cmd_install(args):
    designs = {v.name: v for v in VOICE_DESIGNS}
    voice = designs.get(args.voice)
    if voice is None and args.voice != ED_NAME and not (args.script and args.description):
        raise SystemExit(f"Unknown voice {args.voice!r}; give --script and --description for a voice of your own")
    target = os.path.join(VOICES_DIR, f"{args.voice}.mp3")
    if os.path.exists(target) and not args.replace:
        raise SystemExit(f"{target} exists; pass --replace to overwrite it")
    if os.path.exists(target):
        backup = os.path.join(VOICES_DIR, "replaced", f"{args.voice}.{datetime.now():%Y%m%d_%H%M%S}.mp3")
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copy2(target, backup)
        print(f"Kept the old clip at {backup}")

    take = args.take
    if not take.lower().endswith(".wav"):  # an MP3 or other recording of your own
        take = os.path.join(LAB_DIR, "install_src.wav")
        os.makedirs(LAB_DIR, exist_ok=True)
        decode_mp3(args.take, take)
    data, rate = read_mono(take)
    data = to_lufs(trim_silence(data, rate), rate, args.lufs)
    tmp = os.path.join(LAB_DIR, "install_tmp.wav")
    os.makedirs(LAB_DIR, exist_ok=True)
    sf.write(tmp, data, rate)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", tmp, "-ac", "1", "-b:a", "192k", target], check=True)
    os.remove(tmp)

    if args.script:
        script = args.script
    elif voice:
        script = voice.script
    else:
        script = open(os.path.splitext(args.take)[0] + ".txt", encoding="utf-8").read().strip()
    description = args.description or (voice.description if voice else None)
    texts = transcripts()
    texts[args.voice] = script
    with open(TRANSCRIPTS, "w", encoding="utf-8") as f:
        json.dump(texts, f, indent=4, ensure_ascii=False)

    if description:
        with open(DESCRIPTIONS, encoding="utf-8") as f:
            existing = f.read()
        if f"\n{args.voice}\n" not in f"\n{existing}":
            with open(DESCRIPTIONS, "a", encoding="utf-8") as f:
                f.write(f"\n{args.voice}\n{description}\n")

    print(f"Installed {target}; encoding reference codes...")
    subprocess.run([sys.executable, "-B", os.path.join(ROOT, "scripts", "zonos3_encode_voices.py")], check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("designs", help="render every voice option for auditioning")
    d.add_argument("--only", nargs="*", help="voice names to render")
    d.add_argument("--takes", type=int, default=2, help="seeds per option")
    d.add_argument("--no-check", dest="check", action="store_false", help="skip the Whisper misread check")
    e = sub.add_parser("ed", help="render Ed extension takes and a candidate clip")
    e.add_argument("--takes", type=int, default=2, help="seeds per line")
    e.add_argument("--seconds", type=float, default=14.0, help="candidate clip length")
    i = sub.add_parser("install", help="install a chosen take as a radio voice")
    i.add_argument("--voice", required=True)
    i.add_argument("--take", required=True, help="the chosen WAV, or a recording of your own (MP3 is fine)")
    i.add_argument("--script", help="what the clip says (for a voice not in VOICE_DESIGNS)")
    i.add_argument("--description", help="a description of the voice (for a voice not in VOICE_DESIGNS)")
    i.add_argument("--replace", action="store_true", help="overwrite an existing clip (the old one is kept in voices/replaced/)")
    i.add_argument("--lufs", type=float, default=TARGET_LUFS, help="clip loudness (raise it for a voice that comes out too quiet)")
    args = ap.parse_args()
    {"designs": cmd_designs, "ed": cmd_ed, "install": cmd_install}[args.cmd](args)


if __name__ == "__main__":
    main()
