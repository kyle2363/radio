package dev.radio.mobile.recommendation

/** What the UI and Radio screen need to know about Connected Radio, published by [RadioRepository.connectedState]. */
data class ConnectedUi(
    val status: ConnectedStatus = ConnectedStatus(),
    val set: ConnectedSet? = null,
    /** Local IDs queued from connected "similar" picks, for the queue's Server AI marker. */
    val serverAiIds: Set<String> = emptySet(),
)

/** Pure rules for what connected data may be shown or consumed. No Android, storage or network. */
object ConnectedPolicy {
    const val SERVER_AI_PREFIX = "Server AI"
    const val MAX_NORMAL_EXPLOIT = 4 // The server decides the mix (3 similar at default exploration); the phone only caps and bounds it.
    const val MAX_VIBE_EXPLOIT = 4
    const val REFILL_BOUND = 5

    /**
     * Options card contents. Empty (card hidden) when Connected Radio is off, Radio isn't playing, a request is still
     * pending, the set expired (another song is current) or every option was filtered out.
     */
    fun visibleOptions(ui: ConnectedUi, currentId: String?, radioActive: Boolean, queued: Set<String>,
        isDisliked: (String) -> Boolean, isAvailable: (String) -> Boolean): List<ConnectedPick> {
        val set = ui.set ?: return emptyList()
        if (!ui.status.enabled || !radioActive || currentId == null || ui.status.pendingAnchor != null || set.anchorLocalId != currentId) return emptyList()
        return set.options.filter { it.song.id !in queued && it.song.id != currentId && !isDisliked(it.song.id) && isAvailable(it.song.id) }
    }

    /** How many connected similar picks one refill may take: at most 4 (as many as survive filtering), never beyond the room left. */
    fun exploitLimit(vibeLocked: Boolean, room: Int) = minOf(room.coerceAtLeast(0), if (vibeLocked) MAX_VIBE_EXPLOIT else MAX_NORMAL_EXPLOIT)

    /** Connected similar picks, in server order, that may still be appended. */
    fun refillPicks(set: ConnectedSet?, exclude: Set<String>, vibeLocked: Boolean, room: Int,
        isDisliked: (String) -> Boolean, isAvailable: (String) -> Boolean): List<ConnectedPick> {
        if (set == null) return emptyList()
        return set.continuation.filter { it.song.id !in exclude && !isDisliked(it.song.id) && isAvailable(it.song.id) }.take(exploitLimit(vibeLocked, room))
    }

    /**
     * Refill waits for a pending connected answer only while it can: the answer must be for the current song and
     * at least one upcoming entry must remain to keep music going. The source's own 75 s deadline bounds the wait.
     * Desktop first (checkpoint 4.2) also waits with nothing queued; the caller bounds that wait with [desktopFirstWaitMs].
     */
    fun shouldWait(pendingAnchor: String?, currentId: String?, upcoming: Int, desktopFirst: Boolean = false) =
        pendingAnchor != null && pendingAnchor == currentId && (upcoming >= 1 || desktopFirst)

    /** With nothing queued, wait for the desktop until 15 s before the current song ends, then let local Radio fill in. */
    fun desktopFirstWaitMs(durationMs: Long?, positionMs: Long): Long = ((durationMs ?: 0L) - positionMs - DESKTOP_FIRST_MARGIN_MS).coerceAtLeast(0L)
    const val DESKTOP_FIRST_MARGIN_MS = 15_000L

    /** One line under the options card: how the set was ranked and which tuning the desktop actually applied. */
    fun summary(set: ConnectedSet): String {
        val parts = mutableListOf(when (set.rankedBy) { "llm" -> "LLM"; "fallback" -> "Fallback ranking"; "deterministic" -> "Deterministic"; else -> set.rankedBy },
            "${set.continuation.size} similar", "${set.options.size} " + if (set.options.size == 1) "option" else "options")
        set.applied?.let { a ->
            a.variety?.takeIf { it > 0 }?.let { parts += "variety ${Math.round(it * 100)}%" }
            if (a.energy == "rising" || a.energy == "falling") parts += a.energy
            when (a.pockets) { "on" -> parts += "pockets on"; "inactive" -> parts += "pockets need more listening" }
        }
        return parts.joinToString(" · ")
    }
}
