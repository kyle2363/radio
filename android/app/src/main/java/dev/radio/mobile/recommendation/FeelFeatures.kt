package dev.radio.mobile.recommendation

import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Imported catalog features for feels (checkpoint 9C), keyed by local song id and only for songs matched to the
 * current catalog revision. [vectors] come from one comparable space (audio preferred, else text) so cosine
 * similarities between songs mean something. [tags] are lower-case emotional and genre tags; [energy] is audio energy.
 */
class FeelFeatures(
    val vectors: Map<String, FloatArray> = emptyMap(),
    val tags: Map<String, Set<String>> = emptyMap(),
    val energy: Map<String, Double> = emptyMap(),
) {
    companion object {
        /** Last features Radio prepared, for screens without a [RadioRepository] (the feel sprint). Empty until then. */
        val latest = MutableStateFlow(FeelFeatures())

        internal fun build(accepted: Map<String, String>, vectors: List<RadioVector>, meta: (String) -> CatalogTrackMeta?): FeelFeatures {
            val wanted = accepted.values.toSet()
            val groups = vectors.filter { it.songId in wanted && it.values.isNotEmpty() && it.values.all(Float::isFinite) }
                .groupBy { listOf(it.space, it.model, it.version, it.values.size.toString()) }
            val best = groups.entries.maxWithOrNull(compareBy<Map.Entry<List<String>, List<RadioVector>>>({ if (it.key[0] == "audio") 1 else 0 }, { it.value.size }))
            val bySong = best?.value.orEmpty().associateBy({ it.songId }, { it.values })
            val vectorsByLocal = HashMap<String, FloatArray>(); val tags = HashMap<String, Set<String>>(); val energy = HashMap<String, Double>()
            for ((local, song) in accepted) {
                bySong[song]?.let { vectorsByLocal[local] = it }
                meta(song)?.let { m ->
                    if (m.tags.isNotEmpty()) tags[local] = m.tags
                    energy[local] = m.energy
                }
            }
            return FeelFeatures(vectorsByLocal, tags, energy)
        }
    }
}
