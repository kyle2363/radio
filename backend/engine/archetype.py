"""Listener archetype detection.

Computed from behavioral metrics to understand listening patterns.
Archetypes: explorer, comfort_listener, deep_listener, mood_listener
"""

from db import database

ARCHETYPE_RULES = {
    "explorer": {
        "description": "Loves discovering new music",
        "criteria": {
            "wildcard_acceptance": 0.3,  # 30%+ wildcard songs liked
            "cluster_diversity": 0.5,  # plays from 50%+ of clusters
        },
    },
    "comfort_listener": {
        "description": "Prefers familiar favorites",
        "criteria": {
            "replay_rate": 0.2,  # 20%+ replays
            "avg_completion": 0.8,  # high completion rate
        },
    },
    "deep_listener": {
        "description": "Listens to songs all the way through",
        "criteria": {
            "completion_rate": 0.7,  # 70%+ completion
            "skip_early_rate": 0.1,  # rarely skips early
        },
    },
    "mood_listener": {
        "description": "Chooses music based on mood",
        "criteria": {
            "cluster_consistency": 0.4,  # stays in similar clusters
            "energy_consistency": 0.5,  # similar energy range
        },
    },
}


def detect_archetype():
    """Detect the listener's archetype from behavioral data.

    Returns archetype name and metrics dict.
    """
    metrics = database.get_archetype_metrics()

    if metrics["total_plays"] < 5:
        return "mood_listener", metrics  # Default for new users

    scorecard = {}

    # Explorer: high wildcard acceptance, high cluster diversity
    total_signals = max(1, metrics["total_plays"])
    hearted = metrics.get("signal_counts", {}).get("hearted", 0)
    completed = metrics.get("signal_counts", {}).get("completed", 0)
    skip_early = metrics.get("signal_counts", {}).get("skip_early", 0)

    # Wildcard acceptance rate (hearted/replayed songs that were wildcards)
    wildcard_acceptance = hearted / max(1, hearted + skip_early)

    # Cluster diversity (unique clusters / total clusters)
    total_clusters = 18  # N_CLUSTERS
    unique_clusters = min(metrics["unique_songs"], total_clusters)
    cluster_diversity = unique_clusters / total_clusters

    # Replay rate
    replay_rate = metrics.get("signal_counts", {}).get("replayed", 0) / max(1, total_signals)

    # Completion rate
    completion_rate = metrics["completion_rate"]

    # Skip early rate
    skip_early_rate = skip_early / max(1, total_signals)

    scorecard["explorer"] = (wildcard_acceptance * 0.5 + cluster_diversity * 0.5)
    scorecard["comfort_listener"] = (replay_rate * 0.5 + completion_rate * 0.5)
    scorecard["deep_listener"] = (completion_rate * 0.6 + (1 - skip_early_rate) * 0.4)
    scorecard["mood_listener"] = (cluster_diversity * 0.3 + (1 - skip_early_rate) * 0.7)

    # Find highest scoring archetype
    best_archetype = max(scorecard, key=scorecard.get)

    # Apply minimum thresholds
    if scorecard[best_archetype] < 0.3:
        best_archetype = "mood_listener"

    return best_archetype, {
        **metrics,
        "scorecard": scorecard,
        "wildcard_acceptance": wildcard_acceptance,
        "cluster_diversity": cluster_diversity,
        "replay_rate": replay_rate,
        "completion_rate": completion_rate,
        "skip_early_rate": skip_early_rate,
    }
