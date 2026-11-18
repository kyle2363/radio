package dev.radio.mobile.recommendation

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.portable.FeelRules
import kotlin.math.roundToInt
import kotlin.random.Random

/**
 * Inputs for one feel sprint deck (checkpoint 9B, plan "Tagging many songs"). All maps are keyed by local song id.
 * [states] holds this feel's directly evidenced songs (rated, tagged or compared); those never return to a deck.
 * [vectors], [tags] and [energy] are optional: songs without them simply score lower on that part.
 */
data class SprintInput(
    val songs: List<LocalSong>,
    val states: Map<String, FeelRules.FeelState> = emptyMap(),
    val vectors: Map<String, FloatArray> = emptyMap(),
    /** Lower-case emotional/genre tags, used only by the cold-start hint. */
    val tags: Map<String, Set<String>> = emptyMap(),
    /** 0–1 energy (audio or your own Energy feel), used only by the cold-start hint. */
    val energy: Map<String, Double> = emptyMap(),
    /** Songs played together (one list per listening session). */
    val sessions: List<List<String>> = emptyList(),
    val hintTags: Set<String> = emptySet(),
    val hintEnergy: ClosedFloatingPointRange<Double>? = null,
    /** Limit the deck to these songs (prune a bulk add). */
    val only: Set<String>? = null,
    val size: Int = FeelSprint.DECK_SIZE,
    val randomShare: Double = FeelSprint.RANDOM_SHARE,
)

/** Orders candidates for a sprint. Hints only order cards; nothing here ever tags a song. */
object FeelSprint {
    const val DECK_SIZE = 20
    const val RANDOM_SHARE = 0.2
    const val SESSION_GAP_MS = 30 * 60_000L
    private const val SIMILARITY = 0.45
    private const val CO_PLAY = 0.30
    private const val HINT = 0.25

    fun deck(input: SprintInput, random: Random): List<LocalSong> {
        val candidates = input.songs.filter { it.available && it.id !in input.states && (input.only == null || it.id in input.only) }
            .distinctBy { it.id }.shuffled(random) // equal scores fall in random, not alphabetical, order
        if (candidates.isEmpty() || input.size <= 0) return emptyList()
        val near = FeelRules.neighbourScores(input.states, input.vectors)
        val members = input.states.filterValues { it.member }.keys
        val coPlays = HashMap<String, Int>()
        for (session in input.sessions) if (session.any { it in members }) session.toSet().forEach { coPlays.merge(it, 1, Int::plus) }
        fun hint(id: String): Double {
            val parts = mutableListOf<Double>()
            if (input.hintTags.isNotEmpty()) {
                val overlap = input.tags[id].orEmpty().count { it in input.hintTags }
                parts += (overlap.toDouble() / minOf(2, input.hintTags.size)).coerceAtMost(1.0)
            }
            input.hintEnergy?.let { range -> parts += if (input.energy[id]?.let { it in range } == true) 1.0 else 0.0 }
            return if (parts.isEmpty()) 0.0 else parts.average()
        }
        fun score(id: String): Double {
            val coPlay = (coPlays[id] ?: 0).toDouble()
            return SIMILARITY * (near[id]?.score ?: 0.0) + CO_PLAY * coPlay / (coPlay + 1.0) + HINT * hint(id)
        }
        val size = minOf(input.size, candidates.size)
        val randomCount = (size * input.randomShare.coerceIn(0.0, 1.0)).roundToInt()
        val scores = candidates.associate { it.id to score(it.id) }
        val ranked = candidates.sortedByDescending { scores.getValue(it.id) }.take(size - randomCount)
        val chosen = ranked.map { it.id }.toSet()
        val extra = candidates.filter { it.id !in chosen }.shuffled(random).take(randomCount)
        // Spread the random cards through the deck (every fifth card) so a short sprint still sees some.
        val result = ArrayList<LocalSong>(size)
        val r = ranked.iterator(); val e = extra.iterator()
        while (r.hasNext() || e.hasNext()) {
            if (e.hasNext() && (result.size % 5 == 4 || !r.hasNext())) result += e.next() else result += r.next()
        }
        return result
    }

    /** Groups actual starts (song id, time) into listening sessions split on gaps longer than [gapMs]. */
    fun sessions(starts: List<Pair<String, Long>>, gapMs: Long = SESSION_GAP_MS): List<List<String>> {
        val groups = mutableListOf<MutableList<String>>()
        var last: Long? = null
        for ((id, at) in starts.sortedBy { it.second }) {
            if (last == null || at - last > gapMs) groups += mutableListOf<String>()
            groups.last() += id
            last = at
        }
        return groups
    }
}
