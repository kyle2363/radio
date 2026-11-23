"""Caller phone line: an occasional light phone band and occasional generated ambience."""

import random
from collections import Counter

import numpy as np
import pytest
import soundfile as sf

from announcer import phone_line

RATE = 24000


def _speech_like(path, seconds=3.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(RATE * seconds)) / RATE
    voice = 0.3 * np.sin(2 * np.pi * 160 * t) + 0.1 * np.sin(2 * np.pi * 1200 * t)
    voice += 0.2 * np.sin(2 * np.pi * 40 * t)          # rumble the phone band removes
    voice += 0.05 * rng.standard_normal(len(t))
    voice *= (np.sin(2 * np.pi * 3 * t) > -0.3)        # syllable-ish gaps
    sf.write(path, voice.astype(np.float32), RATE)
    return path


def _band_energy(data, low, high):
    spec = np.abs(np.fft.rfft(data))
    freqs = np.fft.rfftfreq(len(data), 1 / RATE)
    return float(np.sum(spec[(freqs >= low) & (freqs < high)] ** 2))


def test_only_kitchen_street_and_rain_remain():
    assert set(phone_line.SCENES) == {"kitchen", "street", "rain"}


def test_scenes_are_random_per_call_and_none_is_most_likely():
    rng = random.Random(0)
    picks = [phone_line.CallScene.pick(rng=rng) for _ in range(4000)]
    kinds = Counter(p.kind for p in picks)
    assert kinds.most_common(1)[0][0] is None
    assert set(kinds) == {None, "kitchen", "street", "rain"}
    phones = Counter(p.phone for p in picks)
    assert phones[False] > phones[True] > 0


def test_settings_switch_each_part_off():
    rng = random.Random(1)
    picks = [phone_line.CallScene.pick(rng=rng, phone=False, ambience=False) for _ in range(200)]
    assert all(p.kind is None and not p.phone for p in picks)


def test_light_phone_band_cuts_rumble_and_keeps_the_voice(tmp_path):
    src = _speech_like(str(tmp_path / "line.wav"))
    out = phone_line.process(src, phone_line.CallScene(kind=None, phone=True), out_dir=str(tmp_path))
    assert out != src
    before, _ = sf.read(src)
    after, rate = sf.read(out)
    assert rate == RATE and len(after) == len(before)
    assert _band_energy(after, 30, 50) < 0.5 * _band_energy(before, 30, 50)
    assert _band_energy(after, 140, 180) > 0.7 * _band_energy(before, 140, 180)
    assert _band_energy(after, 1100, 1300) > 0.9 * _band_energy(before, 1100, 1300)
    assert np.max(np.abs(after)) <= 0.9


def test_a_plain_call_returns_the_line_untouched(tmp_path):
    src = _speech_like(str(tmp_path / "line.wav"))
    assert phone_line.process(src, phone_line.CallScene(kind=None, phone=False), out_dir=str(tmp_path)) == src


def test_source_file_is_never_modified(tmp_path):
    src = _speech_like(str(tmp_path / "line.wav"))
    original = sf.read(src)[0].copy()
    phone_line.process(src, phone_line.CallScene(kind="kitchen", phone=True), out_dir=str(tmp_path))
    assert np.array_equal(sf.read(src)[0], original)


@pytest.mark.parametrize("kind", sorted(phone_line.SCENES))
def test_ambience_sits_well_under_the_voice(tmp_path, kind):
    src = _speech_like(str(tmp_path / "line.wav"))
    dry = sf.read(src)[0]
    wet = sf.read(phone_line.process(src, phone_line.CallScene(kind=kind, phone=False, rng=random.Random(3)),
                                     out_dir=str(tmp_path)))[0]
    bed = wet - dry
    ratio_db = 20 * np.log10(np.sqrt(np.mean(bed ** 2)) / np.sqrt(np.mean(dry ** 2)))
    assert -40 < ratio_db < -16


def test_kitchen_and_rain_are_quieter_than_street():
    assert phone_line.SCENE_LEVEL_DB["kitchen"] < phone_line.SCENE_LEVEL_DB["street"]
    assert phone_line.SCENE_LEVEL_DB["rain"] < phone_line.SCENE_LEVEL_DB["street"]


@pytest.mark.parametrize("kind", sorted(phone_line.SCENES))
def test_every_scene_renders_finite_audio(kind):
    bed = phone_line.render_scene(kind, RATE, int(RATE * 4), np.random.default_rng(5),
                                  phone_line.scene_params(kind, random.Random(5)))
    assert bed.shape == (RATE * 4,) and np.all(np.isfinite(bed))
    assert 0 < np.max(np.abs(bed)) <= 1.0


def test_calls_never_repeat_the_same_ambience(tmp_path):
    src = _speech_like(str(tmp_path / "line.wav"))
    dry = sf.read(src)[0]
    beds = [sf.read(phone_line.process(src, phone_line.CallScene(kind="street", phone=False), out_dir=str(tmp_path)))[0] - dry
            for _ in range(3)]
    for i in range(3):
        for j in range(i + 1, 3):
            assert abs(np.corrcoef(beds[i], beds[j])[0, 1]) < 0.5


def test_lines_of_one_call_share_a_scene_but_not_the_audio(tmp_path):
    src = _speech_like(str(tmp_path / "line.wav"))
    scene = phone_line.CallScene(kind="rain", phone=True, rng=random.Random(4))
    first = phone_line.process(src, scene, out_dir=str(tmp_path))
    second = phone_line.process(src, scene, out_dir=str(tmp_path))
    assert first != second and not np.array_equal(sf.read(first)[0], sf.read(second)[0])


def test_missing_audio_returns_the_original_path(tmp_path):
    scene = phone_line.CallScene(kind="street", phone=True)
    assert phone_line.process(str(tmp_path / "missing.wav"), scene, out_dir=str(tmp_path)) == str(tmp_path / "missing.wav")
