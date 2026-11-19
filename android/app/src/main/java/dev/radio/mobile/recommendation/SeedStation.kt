package dev.radio.mobile.recommendation

import kotlin.math.sqrt

/**
 * What a "Start radio from this" station was started from: one song or a playlist. Saved so the station keeps its
 * direction after the app or service restarts. [localIds] are local song ids, in the source's order.
 */
data class SeedSpec(val kind: Kind, val label: String, val localIds: List<String>) {
    enum class Kind { Song, Playlist }

    /** Unit and record separators: song ids and playlist names never contain them. */
    fun encode(): String = kind.name + FIELD + label + FIELD + localIds.joinToString(RECORD)

    companion object {
        private const val FIELD = "\u001F"
        private const val RECORD = "\u001E"
        fun decode(value: String?): SeedSpec? {
            val parts = value?.split(FIELD) ?: return null
            if (parts.size != 3) return null
            val kind = Kind.entries.firstOrNull { it.name == parts[0] } ?: return null
            val ids = parts[2].split(RECORD).filter { it.isNotEmpty() }
            return if (ids.isEmpty()) null else SeedSpec(kind, parts[1], ids)
        }
    }
}

/**
 * Song and playlist stations: a score added to the ordinary Radio ranking for songs that sound like the seed songs,
 * from the centroid of their vectors (similarity squared, so the station stays near its start). Positive similarity only, so unrelated songs are left to the mood ranking and
 * opposite ones are never pushed. Recency, dislikes, Vibe lock and refill bounds stay in [OfflineRecommender].
 */
object SeedStation {
    /** Strong enough to outweigh the rolling "similar to the last song" pull, weaker than a feel member. */
    const val WEIGHT = 1.5

    private data class Group(val space: String, val model: String, val version: String, val dimension: Int)

    private fun unit(v: FloatArray): FloatArray? {
        val norm = sqrt(v.fold(0.0) { a, b -> a + b.toDouble() * b })
        return if (norm.isFinite() && norm > 1e-12) FloatArray(v.size) { (v[it] / norm).toFloat() } else null
    }

    /**
     * Scores keyed by catalog song id. [includeSeeds] keeps the seed songs themselves in reach (a playlist station
     * plays some of its own songs); a song station already played its seed first.
     */
    fun scores(seeds: Set<String>, vectors: List<RadioVector>, audioBlend: Double, includeSeeds: Boolean): Map<String, Double> {
        if (seeds.isEmpty()) return emptyMap()
        val blend = audioBlend.coerceIn(0.0, 1.0)
        val bySpace = HashMap<String, MutableMap<String, Double>>() // song -> space -> similarity
        val groups = vectors.filter { it.space in setOf("text", "audio") && it.values.isNotEmpty() && it.values.all(Float::isFinite) }
            .groupBy { Group(it.space, it.model, it.version, it.values.size) }
        for ((group, rows) in groups) {
            val units = rows.mapNotNull { row -> unit(row.values)?.let { row.songId to it } }.toMap()
            val seedVectors = seeds.mapNotNull(units::get)
            if (seedVectors.isEmpty()) continue
            val centroid = unit(FloatArray(group.dimension) { i -> seedVectors.sumOf { it[i].toDouble() }.toFloat() }) ?: continue
            for ((id, v) in units) {
                val similarity = v.indices.fold(0.0) { a, i -> a + v[i] * centroid[i] }
                // Several models of one space: keep the best.
                val spaces = bySpace.getOrPut(id) { HashMap() }
                spaces[group.space] = maxOf(similarity, spaces[group.space] ?: -1.0)
            }
        }
        val result = HashMap<String, Double>()
        for ((id, spaces) in bySpace) {
            if (!includeSeeds && id in seeds) continue
            val text = spaces["text"]; val audio = spaces["audio"]
            val similarity = if (text != null && audio != null) text * (1 - blend) + audio * blend else text ?: audio ?: continue
            // Squared: songs right next to the seed beat songs halfway between it and whatever played last.
            if (similarity > 1e-6) result[id] = WEIGHT * similarity * similarity
        }
        return result
    }
}
