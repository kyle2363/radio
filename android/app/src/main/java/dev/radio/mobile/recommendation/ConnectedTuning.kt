package dev.radio.mobile.recommendation

/**
 * Phone-only tuning for Connected Radio (checkpoint 4.1). Every field is optional; null means "use the desktop
 * default", so [ConnectedTuning] with no values behaves exactly like checkpoint 4. Stored on this phone only and sent
 * with the connected context; it never changes desktop settings or offline Radio.
 */
data class ConnectedTuning(
    /** 0 (steady) to 1 (varied): random noise on the desktop's retrieval query. */
    val variety: Double? = null,
    /** Picks per song, 5-8. Options shown = picks - similar. */
    val picks: Int? = null,
    /** Similar (exploit) picks per song, 2-4. Null lets the server decide. */
    val similar: Int? = null,
    /** auto, rising, steady or falling. */
    val energy: String? = null,
    /** on or off; null follows the desktop's LLM reranking setting. */
    val reranking: String? = null,
    /** Taste pockets (constellations). */
    val pockets: Boolean? = null,
) {
    val isDefault: Boolean get() = normalized() == ConnectedTuning()

    fun normalized() = ConnectedTuning(
        variety?.takeIf { it.isFinite() }?.coerceIn(0.0, 1.0),
        picks?.coerceIn(MIN_PICKS, MAX_PICKS),
        similar?.coerceIn(MIN_SIMILAR, MAX_SIMILAR),
        energy?.takeIf { it in ENERGY },
        reranking?.takeIf { it == "on" || it == "off" },
        pockets,
    )

    /** The context's `tuning` object, or null when nothing is overridden. */
    fun toWire(): Map<String, Any?>? {
        val n = normalized()
        val out = linkedMapOf<String, Any?>()
        n.variety?.let { out["variety"] = it }
        n.picks?.let { out["picks"] = it }
        n.similar?.let { out["similar"] = it }
        n.energy?.let { out["energy"] = it }
        n.reranking?.let { out["reranking"] = it }
        n.pockets?.let { out["pockets"] = it }
        return out.ifEmpty { null }
    }

    fun encode(): String = ConnectedJson.write(toWire() ?: emptyMap<String, Any?>())

    companion object {
        const val MIN_PICKS = 5
        const val MAX_PICKS = 8
        const val MIN_SIMILAR = 2
        const val MAX_SIMILAR = 4
        val ENERGY = listOf("auto", "rising", "steady", "falling")

        fun decode(text: String?): ConnectedTuning = runCatching {
            val m = ConnectedJson.parse(text!!) as Map<*, *>
            ConnectedTuning((m["variety"] as? Number)?.toDouble(), (m["picks"] as? Number)?.toInt(), (m["similar"] as? Number)?.toInt(),
                m["energy"] as? String, m["reranking"] as? String, m["pockets"] as? Boolean).normalized()
        }.getOrDefault(ConnectedTuning())
    }
}

/** An unsynced phone play the desktop's recency window should know about. */
data class ConnectedPlayed(val localId: String, val ageS: Long)

/** What the desktop reports it actually used for a set (`applied` in the reply). */
data class ConnectedApplied(val variety: Double?, val picks: Int, val similar: Int, val energy: String, val reranking: String, val pockets: String) {
    companion object {
        fun parse(raw: Any?): ConnectedApplied? {
            val m = raw as? Map<*, *> ?: return null
            return ConnectedApplied(
                (m["variety"] as? Number)?.toDouble()?.takeIf { it.isFinite() },
                (m["picks"] as? Number)?.toInt() ?: return null,
                (m["similar"] as? Number)?.toInt() ?: return null,
                m["energy"] as? String ?: return null,
                m["reranking"] as? String ?: return null,
                m["pockets"] as? String ?: return null,
            )
        }
    }
}
