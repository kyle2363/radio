package dev.radio.mobile.recommendation

import kotlin.math.abs
import kotlin.math.roundToInt

/** The three named exploration settings on the Radio tab. Any other slider value shows the nearest name plus its %. */
enum class ExplorationPreset(val label: String, val value: Double) {
    Familiar("Familiar", .10), Balanced("Balanced", .30), Adventurous("Adventurous", .70);

    companion object {
        fun nearest(value: Double): ExplorationPreset {
            if (!value.isFinite()) return Balanced
            return when {
                value < .20 -> Familiar
                value <= .50 -> Balanced
                else -> Adventurous
            }
        }
        fun summary(value: Double): String {
            val bounded = if (value.isFinite()) value.coerceIn(0.0, 1.0) else Balanced.value
            return "${nearest(bounded).label} · ${(bounded * 100).roundToInt()}%"
        }
        /** The preset the value sits on exactly (within half a percent), or null for a custom slider value. */
        fun exact(value: Double): ExplorationPreset? = entries.firstOrNull { abs(it.value - value) < .005 }
    }
}

/** Why a song is in the Radio queue, shown on Coming up, the full queue and Now Playing. */
enum class StationOrigin(val label: String) {
    Start("Station start"),
    Similar("Similar sound"),
    Taste("Your taste"),
    Mood("Fits the mood"),
    Explore("Something new"),
    Wildcard("Wildcard"),
    Context("Fits now"),
    Vibe("Vibe match"),
    Desktop("Desktop"),
    User("Added by you");

    companion object {
        fun parse(name: String?): StationOrigin? = entries.firstOrNull { it.name == name }
        fun of(pick: RadioPick): StationOrigin {
            val reason = pick.reason
            return when {
                reason.startsWith(ConnectedPolicy.SERVER_AI_PREFIX) -> Desktop
                reason == "Chosen starting song" -> Start
                reason == "Matches locked vibe" -> Vibe
                pick.slot == "explore" -> Explore
                pick.slot == "wildcard" -> Wildcard
                pick.slot == "context" -> Context
                reason.startsWith("Similar to") -> Similar
                reason.contains("taste", ignoreCase = true) || reason.contains("starting songs", ignoreCase = true) || reason == "Liked song" -> Taste
                else -> Mood
            }
        }
    }
}

/**
 * What survives about a running station beyond the Media3 timeline: the starting song and why each queued song is
 * there. Stored beside the playback bookmark; restored only when the restored queue is exactly the one it describes.
 */
data class StationRecord(
    val seedLocalId: String?,
    val seedTitle: String?,
    val origins: Map<String, StationOrigin>,
    val playOrder: List<String>,
    val currentId: String?,
) {
    fun pruned(order: List<String>): StationRecord { val keep = order.toSet(); return copy(origins = origins.filterKeys { it in keep }) }
    fun matches(order: List<String>, current: String?): Boolean = current != null && current == currentId && order == playOrder

    fun encode(): String = ConnectedJson.write(linkedMapOf<String, Any?>(
        "seed" to seedLocalId, "seedTitle" to seedTitle,
        "origins" to origins.entries.take(MAX_ORIGINS).associate { it.key to it.value.name },
        "order" to playOrder, "current" to currentId,
    ))

    companion object {
        const val MAX_ORIGINS = 100
        fun decode(text: String?): StationRecord? = runCatching {
            val m = ConnectedJson.parse(text!!) as Map<*, *>
            val origins = (m["origins"] as? Map<*, *>).orEmpty().entries.mapNotNull { (k, v) ->
                val id = k as? String ?: return@mapNotNull null
                StationOrigin.parse(v as? String)?.let { id to it }
            }.take(MAX_ORIGINS).toMap()
            StationRecord(m["seed"] as? String, m["seedTitle"] as? String, origins,
                (m["order"] as? List<*>).orEmpty().filterIsInstance<String>(), m["current"] as? String)
        }.getOrNull()
    }
}
