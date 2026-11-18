"""Reference feel-scoring rules shared with Android (checkpoint 9A).

A feel's evidence is append-only: bucket ratings (value 0-1, optional weight)
and pairwise comparisons (winner/loser or tie). Each item fades with age
(half weight after ``HALF_LIFE_DAYS``). Scores come from a weighted
Bradley-Terry model with a Gaussian prior: every song has a latent strength,
ratings compare it with a fixed anchor at 0 and comparisons compare two songs.
The fit is a fixed number of Jacobi diagonal-Newton sweeps in sorted song
order, so Python and Kotlin produce identical numbers (``feel_golden.json``).
"""
import math

HALF_LIFE_DAYS = 182.5
DAY_SECONDS = 86_400.0
PRIOR = 0.1
SWEEPS = 100
MAX_STEP = 1.0
CONFIDENCE_PSEUDO = 2.0
NEIGHBOURS = 5
NEIGHBOUR_CONFIDENCE = 0.5
ENERGY_EPSILON = 0.02
PLAYED_BOOST = 1.25


def recency_weight(age_days):
    return 1.0 if age_days <= 0 else 0.5 ** (age_days / HALF_LIFE_DAYS)


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def _age(now, at):
    return (now - at) / DAY_SECONDS


def score_feel(ratings, comparisons, now):
    """Return {song: {"score", "confidence", "member"}} for every song with evidence."""
    rated = [(r["song"], float(r["value"]), float(r.get("weight", 1.0)) * recency_weight(_age(now, r["at"])))
             for r in ratings]
    pairs = [(c["winner"], c["loser"], 0.5 if c.get("tie") else 1.0, recency_weight(_age(now, c["at"])))
             for c in comparisons]
    songs = sorted({s for s, _, _ in rated} | {w for w, _, _, _ in pairs} | {l for _, l, _, _ in pairs})
    index = {song: i for i, song in enumerate(songs)}
    evidence = [0.0] * len(songs)
    for song, _, weight in rated:
        evidence[index[song]] += weight
    for winner, loser, _, weight in pairs:
        evidence[index[winner]] += weight
        evidence[index[loser]] += weight

    strength = [0.0] * len(songs)
    for _ in range(SWEEPS):
        grad = [-PRIOR * s for s in strength]
        hess = [PRIOR] * len(songs)
        for song, value, weight in rated:
            i = index[song]
            p = _sigmoid(strength[i])
            grad[i] += weight * (value - p)
            hess[i] += weight * p * (1.0 - p)
        for winner, loser, outcome, weight in pairs:
            i, j = index[winner], index[loser]
            p = _sigmoid(strength[i] - strength[j])
            grad[i] += weight * (outcome - p)
            grad[j] -= weight * (outcome - p)
            curvature = weight * p * (1.0 - p)
            hess[i] += curvature
            hess[j] += curvature
        strength = [s + max(-MAX_STEP, min(MAX_STEP, g / h)) for s, g, h in zip(strength, grad, hess)]

    states = {}
    for song, i in index.items():
        score = _sigmoid(strength[i])
        states[song] = {"score": score, "confidence": evidence[i] / (evidence[i] + CONFIDENCE_PSEUDO),
                        "member": evidence[i] > 0 and score > 0.5}
    return states


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def neighbour_scores(states, vectors):
    """Low-confidence scores for unrated songs from their nearest rated neighbours. Never members."""
    rated = sorted(song for song in states if song in vectors)
    borrowed = {}
    for song in sorted(vectors):
        if song in states:
            continue
        near = sorted(((_cosine(vectors[song], vectors[r]), r) for r in rated), key=lambda t: (-t[0], t[1]))
        near = [(sim, r) for sim, r in near[:NEIGHBOURS] if sim > 0]
        if not near:
            continue
        total = sum(sim for sim, _ in near)
        score = sum(sim * states[r]["score"] for sim, r in near) / total
        mean_conf = sum(states[r]["confidence"] for _, r in near) / len(near)
        borrowed[song] = {"score": score, "confidence": near[0][0] * mean_conf * NEIGHBOUR_CONFIDENCE,
                          "member": False}
    return borrowed


def uncertain_pairs(states, played, count):
    """Neighbouring songs in the ranking whose order is least certain, played songs favoured."""
    ranked = sorted(states, key=lambda s: (states[s]["score"], s))
    candidates = []
    for a, b in zip(ranked, ranked[1:]):
        gap = abs(states[a]["score"] - states[b]["score"])
        doubt = 1.0 - (states[a]["confidence"] + states[b]["confidence"]) / 2.0
        boost = PLAYED_BOOST if a in played and b in played else 1.0
        candidates.append(((1.0 - gap) * doubt * boost, a, b))
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [(a, b) for _, a, b in candidates[:count]]


def settled_percent(states):
    """How settled a feel's ranking is: mean confidence of its songs, 0-100."""
    if not states:
        return 0.0
    return 100.0 * sum(s["confidence"] for s in states.values()) / len(states)


def energy_changed(previous, current):
    return previous is None or abs(current - previous) >= ENERGY_EPSILON


def legacy_energy_rating(user_energy, count, updated_at):
    """Desktop ``user_energy`` running mean becomes one rating weighted by its count."""
    if user_energy is None or not count:
        return None
    return {"value": float(user_energy), "weight": float(count), "at": float(updated_at or 0.0)}
