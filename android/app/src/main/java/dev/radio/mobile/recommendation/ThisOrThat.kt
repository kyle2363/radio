package dev.radio.mobile.recommendation

import dev.radio.mobile.portable.FeelRules
import kotlin.random.Random

/**
 * Checkpoint 9C this-or-that: one round of song pairs for a feel. Mostly the least certain neighbouring
 * pairs (FeelRules.uncertainPairs, played songs favoured), about [randomShare] random pairs; no pair twice
 * in a round and no song more than [MAX_PER_SONG] times. Deterministic for a seeded [Random].
 */
object ThisOrThat {
    const val MAX_PER_SONG = 3
    /** Non-members below this confidence still take part in a tag feel's rounds. */
    const val LOW_CONFIDENCE = 0.5

    fun round(states: Map<String, FeelRules.FeelState>, played: Set<String>, available: Set<String>, random: Random,
        size: Int = 10, randomShare: Double = 0.2, scale: Boolean = false): List<Pair<String, String>> {
        val pool = states.filter { (id, s) -> id in available && (scale || s.member || s.confidence < LOW_CONFIDENCE) }
        if (pool.size < 2 || size <= 0) return emptyList()
        val ids = pool.keys.sorted()
        val seen = mutableSetOf<Set<String>>()
        val uses = mutableMapOf<String, Int>()
        val out = mutableListOf<Pair<String, String>>()
        fun add(a: String, b: String): Boolean {
            if (a == b || !seen.add(setOf(a, b))) return false
            if ((uses[a] ?: 0) >= MAX_PER_SONG || (uses[b] ?: 0) >= MAX_PER_SONG) { seen.remove(setOf(a, b)); return false }
            uses[a] = (uses[a] ?: 0) + 1; uses[b] = (uses[b] ?: 0) + 1
            out += if (random.nextBoolean()) a to b else b to a
            return true
        }
        val randomCount = (size * randomShare.coerceIn(0.0, 1.0)).toInt()
        for ((a, b) in FeelRules.uncertainPairs(pool, played, ids.size)) { if (out.size >= size - randomCount) break; add(a, b) }
        var tries = 0
        while (out.size < size && tries++ < size * 20) add(ids[random.nextInt(ids.size)], ids[random.nextInt(ids.size)])
        return out
    }
}
