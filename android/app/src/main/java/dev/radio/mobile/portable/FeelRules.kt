package dev.radio.mobile.portable

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * Mirror of backend/portable/feel.py (checkpoint 9A); parity is pinned by
 * backend/tests/fixtures/feel_golden.json. Keep constants, iteration order and
 * arithmetic identical to the Python reference.
 */
object FeelRules {
    const val HALF_LIFE_DAYS = 182.5
    private const val DAY_SECONDS = 86_400.0
    private const val PRIOR = 0.1
    private const val SWEEPS = 100
    private const val MAX_STEP = 1.0
    private const val CONFIDENCE_PSEUDO = 2.0
    private const val NEIGHBOURS = 5
    private const val NEIGHBOUR_CONFIDENCE = 0.5
    const val ENERGY_EPSILON = 0.02
    private const val PLAYED_BOOST = 1.25

    data class Rating(val song: String, val value: Double, val at: Double, val weight: Double = 1.0)
    data class Comparison(val winner: String, val loser: String, val at: Double, val tie: Boolean = false)
    data class FeelState(val score: Double, val confidence: Double, val member: Boolean)

    fun recencyWeight(ageDays: Double): Double = if (ageDays <= 0) 1.0 else 0.5.pow(ageDays / HALF_LIFE_DAYS)

    private fun sigmoid(x: Double) = 1.0 / (1.0 + exp(-x))

    fun scoreFeel(ratings: List<Rating>, comparisons: List<Comparison>, now: Double): Map<String, FeelState> {
        val rated = ratings.map { Triple(it.song, it.value, it.weight * recencyWeight((now - it.at) / DAY_SECONDS)) }
        val songs = (ratings.map { it.song } + comparisons.flatMap { listOf(it.winner, it.loser) }).toSortedSet().toList()
        val index = songs.withIndex().associate { it.value to it.index }
        val evidence = DoubleArray(songs.size)
        for ((song, _, weight) in rated) evidence[index.getValue(song)] += weight
        val pairs = comparisons.map { c ->
            val weight = recencyWeight((now - c.at) / DAY_SECONDS)
            evidence[index.getValue(c.winner)] += weight
            evidence[index.getValue(c.loser)] += weight
            PairTerm(index.getValue(c.winner), index.getValue(c.loser), if (c.tie) 0.5 else 1.0, weight)
        }

        var strength = DoubleArray(songs.size)
        repeat(SWEEPS) {
            val grad = DoubleArray(songs.size) { -PRIOR * strength[it] }
            val hess = DoubleArray(songs.size) { PRIOR }
            for ((song, value, weight) in rated) {
                val i = index.getValue(song)
                val p = sigmoid(strength[i])
                grad[i] += weight * (value - p)
                hess[i] += weight * p * (1.0 - p)
            }
            for (t in pairs) {
                val p = sigmoid(strength[t.i] - strength[t.j])
                grad[t.i] += t.weight * (t.outcome - p)
                grad[t.j] -= t.weight * (t.outcome - p)
                val curvature = t.weight * p * (1.0 - p)
                hess[t.i] += curvature
                hess[t.j] += curvature
            }
            strength = DoubleArray(songs.size) { strength[it] + (grad[it] / hess[it]).coerceIn(-MAX_STEP, MAX_STEP) }
        }
        return songs.withIndex().associate { (i, song) ->
            val score = sigmoid(strength[i])
            song to FeelState(score, evidence[i] / (evidence[i] + CONFIDENCE_PSEUDO), evidence[i] > 0 && score > 0.5)
        }
    }

    private class PairTerm(val i: Int, val j: Int, val outcome: Double, val weight: Double)

    private fun cosine(a: FloatArray, b: FloatArray): Double {
        var dot = 0.0; var na = 0.0; var nb = 0.0
        for (k in 0 until minOf(a.size, b.size)) { dot += a[k] * b[k]; na += a[k] * a[k]; nb += b[k] * b[k] }
        val norm = sqrt(na) * sqrt(nb)
        return if (norm == 0.0) 0.0 else dot / norm
    }

    /** Low-confidence scores for unrated songs from their nearest rated neighbours. Never members. */
    fun neighbourScores(states: Map<String, FeelState>, vectors: Map<String, FloatArray>): Map<String, FeelState> {
        val rated = states.keys.filter { it in vectors }.sorted()
        val borrowed = linkedMapOf<String, FeelState>()
        for (song in vectors.keys.sorted()) {
            if (song in states) continue
            val near = rated.map { cosine(vectors.getValue(song), vectors.getValue(it)) to it }
                .sortedWith(compareBy({ -it.first }, { it.second })).take(NEIGHBOURS).filter { it.first > 0 }
            if (near.isEmpty()) continue
            val total = near.sumOf { it.first }
            val score = near.sumOf { it.first * states.getValue(it.second).score } / total
            val meanConfidence = near.sumOf { states.getValue(it.second).confidence } / near.size
            borrowed[song] = FeelState(score, near[0].first * meanConfidence * NEIGHBOUR_CONFIDENCE, false)
        }
        return borrowed
    }

    /** Neighbouring songs in the ranking whose order is least certain, played songs favoured. */
    fun uncertainPairs(states: Map<String, FeelState>, played: Set<String>, count: Int): List<Pair<String, String>> {
        val ranked = states.keys.sortedWith(compareBy({ states.getValue(it).score }, { it }))
        return ranked.zipWithNext().map { (a, b) ->
            val sa = states.getValue(a); val sb = states.getValue(b)
            val boost = if (a in played && b in played) PLAYED_BOOST else 1.0
            Triple((1.0 - abs(sa.score - sb.score)) * (1.0 - (sa.confidence + sb.confidence) / 2.0) * boost, a, b)
        }.sortedWith(compareBy({ -it.first }, { it.second }, { it.third })).take(count).map { it.second to it.third }
    }

    fun settledPercent(states: Map<String, FeelState>): Double =
        if (states.isEmpty()) 0.0 else 100.0 * states.values.sumOf { it.confidence } / states.size

    fun energyChanged(previous: Double?, current: Double): Boolean =
        previous == null || abs(current - previous) >= ENERGY_EPSILON
}
