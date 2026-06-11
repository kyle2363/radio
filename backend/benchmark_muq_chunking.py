"""MuQ-MuLan Chunking Benchmark (Corrected)

Key insight: MuQ-MuLan already internally chunks audio into 10-second clips
and averages them. This benchmark tests whether we can beat that default
by intercepting per-clip latents and using smarter aggregation.

Methods:
  A — Model Default: full waveform → model's internal 10s chunking + uniform avg
  B — Uniform 10s: manual 10s splits → embed each clip → uniform avg (validates match with A)
  C — Energy Weighted: 10s clips → embed each → weighted avg by RMS energy
  D — Top-K Selective: 10s clips → embed each → keep top-K by energy → uniform avg

Evaluates retrieval quality using artist labels as similarity proxy.
Prints metrics + human-readable similar songs for manual assessment.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import faiss
import numpy as np

# Ensure we're in backend/ so config imports work
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if os.getcwd() != BACKEND_DIR:
    os.chdir(BACKEND_DIR)

import config
from llm import muq_engine

# ─── Config ─────────────────────────────────────────────────────────────
NUM_SONGS = 125
CLIP_SECONDS = 10  # matches MuQ-MuLan's internal clip_secs
TOP_K = 10
SEED = 42
TOP_K_SELECTIVE = 0.7  # keep top 70% of clips for Method D


# ─── Helpers ────────────────────────────────────────────────────────────

def parse_artist_title(filepath: str) -> tuple[str, str]:
    name = Path(filepath).stem
    name = re.sub(r'\s*\(Official\s*(Video|Audio|Lyric\s*Video|Music\s*Video|Visualizer)\s*\)', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\[Official\s*(Video|Audio|Lyric\s*Video|Music\s*Video|Visualizer)\s*\]', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(Lyrics?\)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(AMV\)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(Visualizer\)\s*$', '', name, flags=re.IGNORECASE)
    name = name.strip()
    if ' - ' in name:
        artist, title = name.split(' - ', 1)
        return artist.strip(), title.strip()
    return 'Unknown', name


def collect_mp3s(music_dir: str, n: int, seed: int) -> list[str]:
    mp3s = []
    for root, _dirs, files in os.walk(music_dir):
        for f in files:
            if f.lower().endswith('.mp3'):
                mp3s.append(os.path.join(root, f))
    random.seed(seed)
    random.shuffle(mp3s)
    return mp3s[:n]


def split_into_clips(wav: np.ndarray, sr: int, clip_sec: int) -> list[np.ndarray]:
    """Split audio into clips matching MuQ-MuLan's _get_all_clips behavior.

    - Non-overlapping clips of clip_sec seconds
    - Final clip padded with wrap-around (same as model default)
    """
    clip_samples = sr * clip_sec
    clips = []
    accum = 0
    while accum + clip_samples <= len(wav):
        clips.append(wav[accum:accum + clip_samples])
        accum += clip_samples
    if accum < len(wav):
        remainder = wav[accum:]
        pad_len = clip_samples - len(remainder)
        pad = wav[:pad_len]
        clips.append(np.concatenate([remainder, pad]))
    return clips


def embed_clip(model, device, clip: np.ndarray) -> np.ndarray:
    """Embed a single 10s clip via MuQ-MuLan (1 clip = no internal chunking)."""
    import torch
    tensor = torch.tensor(clip, dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        emb = model(wavs=tensor)
    return emb.cpu().numpy().flatten()


def normalize_l2(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


def rms_energy(clip: np.ndarray) -> float:
    return float(np.sqrt(np.mean(clip.astype(np.float64) ** 2)))


# ─── Embedding Methods ─────────────────────────────────────────────────

def embed_model_default(model, device, filepath: str) -> np.ndarray:
    """Method A: Pass full waveform — model does its own 10s chunking + uniform avg."""
    emb = muq_engine.embed_audio(filepath)
    return normalize_l2(np.array(emb, dtype=np.float32))


def embed_uniform_10s(model, device, filepath: str) -> np.ndarray:
    """Method B: Manual 10s splits → embed each → uniform avg (should match A)."""
    import librosa
    wav, _ = librosa.load(filepath, sr=config.MUQ_SAMPLE_RATE, mono=True)
    clips = split_into_clips(wav, config.MUQ_SAMPLE_RATE, CLIP_SECONDS)
    clip_embs = np.array([embed_clip(model, device, c) for c in clips])
    avg = clip_embs.mean(axis=0)
    return normalize_l2(avg.astype(np.float32))


def embed_energy_weighted(model, device, filepath: str) -> np.ndarray:
    """Method C: 10s clips → embed each → weighted avg by RMS energy."""
    import librosa
    wav, _ = librosa.load(filepath, sr=config.MUQ_SAMPLE_RATE, mono=True)
    clips = split_into_clips(wav, config.MUQ_SAMPLE_RATE, CLIP_SECONDS)
    clip_embs = []
    weights = []
    for c in clips:
        clip_embs.append(embed_clip(model, device, c))
        weights.append(rms_energy(c) + 1e-8)
    weights = np.array(weights)
    weights = weights / weights.sum()
    clip_embs = np.array(clip_embs)
    weighted = np.average(clip_embs, axis=0, weights=weights)
    return normalize_l2(weighted.astype(np.float32))


def embed_topk_selective(model, device, filepath: str) -> np.ndarray:
    """Method D: 10s clips → embed each → keep top-K by energy → uniform avg."""
    import librosa
    wav, _ = librosa.load(filepath, sr=config.MUQ_SAMPLE_RATE, mono=True)
    clips = split_into_clips(wav, config.MUQ_SAMPLE_RATE, CLIP_SECONDS)
    energies = [(i, rms_energy(c)) for i, c in enumerate(clips)]
    energies.sort(key=lambda x: x[1], reverse=True)
    k = max(1, int(len(clips) * TOP_K_SELECTIVE))
    top_indices = sorted([e[0] for e in energies[:k]])  # keep original order
    clip_embs = np.array([embed_clip(model, device, clips[i]) for i in top_indices])
    avg = clip_embs.mean(axis=0)
    return normalize_l2(avg.astype(np.float32))


# ─── Evaluation ─────────────────────────────────────────────────────────

def build_index(embeddings: np.ndarray) -> faiss.IndexFlatIP:
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    return index


def compute_metrics(
    artists: list[str],
    index: faiss.IndexFlatIP,
    all_embs: np.ndarray,
    top_k: int,
) -> dict:
    D, I = index.search(all_embs, top_k + 1)

    precisions = []
    hit_rates = []
    song_aps = []

    for i in range(len(artists)):
        query_artist = artists[i]
        neighbor_indices = I[i][1:top_k + 1]
        neighbor_artists = [artists[j] for j in neighbor_indices]

        hits = sum(1 for a in neighbor_artists if a == query_artist)
        precisions.append(hits / top_k)

        hit = 1.0 if hits > 0 else 0.0
        hit_rates.append(hit)

        relevant = [1.0 if a == query_artist else 0.0 for a in neighbor_artists]
        n_relevant = sum(relevant)
        if n_relevant > 0:
            ap = sum(
                sum(relevant[:r + 1]) / (r + 1)
                for r in range(len(relevant)) if relevant[r] == 1.0
            ) / n_relevant
        else:
            ap = 0.0
        song_aps.append(ap)

    return {
        'precision_at_k': np.mean(precisions),
        'artist_hit_rate': np.mean(hit_rates),
        'mAP': np.mean(song_aps),
    }


def print_similar_songs(
    query_artist: str, query_title: str,
    retrieved_indices: np.ndarray, retrieved_scores: np.ndarray,
    artists: list[str], titles: list[str],
    method_name: str, k: int = 5,
):
    print(f"  [{method_name}] \"{query_artist} - {query_title}\"")
    for rank, (idx, score) in enumerate(zip(retrieved_indices[:k], retrieved_scores[:k]), 1):
        a, t = artists[idx], titles[idx]
        marker = "[same artist]" if a == query_artist else ""
        print(f"    {rank}. {a} - {t}  (sim: {score:.3f})  {marker}")
    print()


# ─── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print(f"MuQ-MuLan Chunking Benchmark ({NUM_SONGS} songs)")
    print(f"Clip: {CLIP_SECONDS}s | Top-K: {TOP_K} | Selective: {TOP_K_SELECTIVE}")
    print(f"Seed: {SEED}")
    print("=" * 65)
    print()

    if not muq_engine.is_available():
        print("ERROR: MuQ-MuLan is not available. Install torch + muq.")
        sys.exit(1)

    print("Loading MuQ-MuLan model...")
    t0 = time.time()
    _ = muq_engine.get_embedding_dimension()
    from llm.muq_engine import _load_model, _get_device
    model = _load_model()
    device = _get_device()
    print(f"  Model loaded in {time.time() - t0:.1f}s on {device}")
    print()

    print(f"Sampling {NUM_SONGS} random songs from {config.MUSIC_DIR}...")
    mp3s = collect_mp3s(config.MUSIC_DIR, NUM_SONGS, SEED)
    print(f"  Got {len(mp3s)} songs")
    print()

    artists = []
    titles = []
    for path in mp3s:
        a, t = parse_artist_title(path)
        artists.append(a)
        titles.append(t)

    # ─── Generate embeddings ────────────────────────────────────────
    methods = {
        'A': ('Model Default (10s internal)', embed_model_default),
        'B': ('Uniform 10s (manual)', embed_uniform_10s),
        'C': ('Energy Weighted 10s', embed_energy_weighted),
        'D': ('Top-K Selective 10s', embed_topk_selective),
    }

    all_embeddings = {}
    timings = {}

    for key, (name, embed_fn) in methods.items():
        print(f"--- Method {key}: {name} ---")
        embs = []
        t_start = time.time()
        for i, path in enumerate(mp3s):
            emb = embed_fn(model, device, path)
            embs.append(emb)
            if (i + 1) % 25 == 0 or (i + 1) == len(mp3s):
                elapsed = time.time() - t_start
                rate = (i + 1) / elapsed
                eta = (len(mp3s) - i - 1) / rate if rate > 0 else 0
                print(f"  [{i+1}/{len(mp3s)}] {elapsed:.1f}s elapsed, ~{eta:.0f}s remaining")
        arr = np.array(embs, dtype=np.float32)
        all_embeddings[key] = arr
        timings[key] = time.time() - t_start
        print(f"  Done in {timings[key]:.1f}s")
        print()

    # ─── Build indices & evaluate ───────────────────────────────────
    print("=" * 65)
    print("Evaluation Results")
    print("=" * 65)
    print()

    results = {}
    for key, (name, _) in methods.items():
        emb = all_embeddings[key]
        index = build_index(emb)
        metrics = compute_metrics(artists, index, emb, TOP_K)
        results[key] = metrics
        results[key]['index'] = index

    print(f"{'Method':<30} {'P@10':>8} {'ArtistHit@10':>14} {'mAP':>8} {'Time':>8}")
    print("-" * 70)
    for key, (name, _) in methods.items():
        r = results[key]
        t = timings[key]
        print(f"  {key}. {name:<26} {r['precision_at_k']:>7.4f} {r['artist_hit_rate']:>14.4f} {r['mAP']:>8.4f} {t:>7.1f}s")
    print()

    # Check if B matches A (validation)
    a_mAP = results['A']['mAP']
    b_mAP = results['B']['mAP']
    diff_pct = abs(a_mAP - b_mAP) / max(a_mAP, 1e-8) * 100
    if diff_pct < 2.0:
        print(f"  [VALIDATED] Method B matches A within {diff_pct:.1f}% — manual chunking matches model default")
    else:
        print(f"  [WARNING] Method B differs from A by {diff_pct:.1f}% — investigate")
    print()

    best_key = max(results, key=lambda k: results[k]['mAP'])
    best_name = methods[best_key][0]
    print(f"Winner: Method {best_key} ({best_name}) by mAP")
    print()

    # ─── Qualitative: top-5 similar for every 12th song ─────────────
    print("=" * 65)
    print("Qualitative: Top-5 Similar Songs (every 12th song)")
    print("=" * 65)
    print()

    for key, (name, _) in methods.items():
        index = results[key]['index']
        D, I = index.search(all_embeddings[key], TOP_K + 1)

        print(f"--- Method {key}: {name} ---")
        for i in range(0, len(mp3s), 12):
            neighbor_mask = I[i] != i
            neighbor_indices = I[i][neighbor_mask][:5]
            neighbor_scores = D[i][neighbor_mask][:5]
            print_similar_songs(
                artists[i], titles[i],
                neighbor_indices, neighbor_scores,
                artists, titles,
                f"{key}: {name}", k=5
            )
        print()

    # ─── Save results ───────────────────────────────────────────────
    output = {
        'config': {
            'num_songs': NUM_SONGS,
            'clip_seconds': CLIP_SECONDS,
            'top_k': TOP_K,
            'seed': SEED,
            'sample_rate': config.MUQ_SAMPLE_RATE,
            'selective_keep': TOP_K_SELECTIVE,
        },
        'timings': {methods[k][0]: timings[k] for k in methods},
        'metrics': {
            methods[k][0]: {
                'precision_at_10': float(results[k]['precision_at_k']),
                'artist_hit_rate_at_10': float(results[k]['artist_hit_rate']),
                'mAP': float(results[k]['mAP']),
            } for k in methods
        },
        'songs': [
            {'artist': artists[i], 'title': titles[i], 'path': mp3s[i]}
            for i in range(len(mp3s))
        ],
    }
    out_path = os.path.join(BACKEND_DIR, '..', 'benchmark_results.json')
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to {out_path}")


if __name__ == '__main__':
    main()
