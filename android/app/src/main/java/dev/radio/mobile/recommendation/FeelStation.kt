package dev.radio.mobile.recommendation

import dev.radio.mobile.portable.FeelRules

/**
 * Feel stations (checkpoint 9C, plan "Feel stations in Radio"). Turns one feel's states into a score added to the
 * ordinary Radio ranking, in tiers: your members by rank, then neighbours by similarity × confidence, then nothing
 * (the mood ranking decides the rest). Recency, dislikes, Vibe lock and refill bounds stay in [OfflineRecommender].
 */
object FeelStation {
    /** Members sit above every neighbour and every ordinary score (which stays roughly within -1..1). */
    const val MEMBER = 2.0
    /** Rated songs that don't fit the feel. */
    const val NOT_IN_FEEL = -0.5
    private const val NEIGHBOUR_WEIGHT = 2.0

    enum class Tier { Member, Neighbour, Out }
    fun tier(score: Double): Tier = when { score >= MEMBER -> Tier.Member; score > 0 -> Tier.Neighbour; else -> Tier.Out }

    /** Scores keyed like [states] and [vectors] (local song ids). Unscored songs are left to the mood ranking. */
    fun scores(states: Map<String, FeelRules.FeelState>, vectors: Map<String, FloatArray>): Map<String, Double> {
        if (states.isEmpty()) return emptyMap()
        val result = HashMap<String, Double>()
        for ((id, s) in states) result[id] = if (s.member) MEMBER + s.score else NOT_IN_FEEL
        for ((id, s) in FeelRules.neighbourScores(states, vectors)) {
            // Only neighbours that lean into the feel; leaning-out neighbours fall back to the mood ranking.
            if (s.score > .5) result[id] = ((s.score - .5) * 2 * s.confidence * NEIGHBOUR_WEIGHT).coerceIn(1e-6, MEMBER - 1e-6)
        }
        return result
    }
}
