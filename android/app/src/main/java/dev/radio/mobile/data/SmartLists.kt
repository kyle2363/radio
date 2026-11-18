package dev.radio.mobile.data

/** One journal row, reduced to what the automatic lists need. */
data class SmartListRow(val localId: String, val kind: String, val completion: Double?, val occurredAt: Long)

/** Playlists the app keeps up to date from listening history and likes. They are read-only. */
enum class SmartList(val id: String, val title: String, val description: String) {
    Top("smart:top", "Top 100", "Your most played songs"),
    Recent("smart:recent", "Recently played", "The last 100 songs you played"),
    Liked("smart:liked", "Liked songs", "Everything you've liked, newest first"),
    Gems("smart:gems", "Forgotten gems", "Favourites you haven't played in two months"),
    Heard("smart:heard", "Recently heard", "What was that song? Your last 50 plays, with times");

    companion object { fun of(id: String?): SmartList? = entries.firstOrNull { it.id == id } }
}

object SmartLists {
    const val LIMIT = 100
    const val HEARD_LIMIT = 50
    private val terminal = setOf("song_skipped", "attempt_stopped")

    /** A listen is a finished song, or one played at least halfway before moving on. Early skips don't count. */
    fun isListen(row: SmartListRow) = row.kind == "song_completed" || row.kind in terminal && (row.completion ?: 0.0) >= 0.5

    /** Most listens first; ties go to the song listened to most recently. */
    fun top(rows: List<SmartListRow>, available: Set<String>, limit: Int = LIMIT): List<String> {
        val counts = HashMap<String, Int>(); val latest = HashMap<String, Long>()
        for (row in rows) if (row.localId in available && isListen(row)) {
            counts.merge(row.localId, 1, Int::plus)
            latest.merge(row.localId, row.occurredAt, ::maxOf)
        }
        return counts.keys.sortedWith(compareByDescending<String> { counts.getValue(it) }.thenByDescending { latest.getValue(it) }).take(limit)
    }

    /** Each song once, by the last time it started playing. */
    fun recent(rows: List<SmartListRow>, available: Set<String>, limit: Int = LIMIT): List<String> {
        val latest = HashMap<String, Long>()
        for (row in rows) if (row.kind == "song_started" && row.localId in available) latest.merge(row.localId, row.occurredAt, ::maxOf)
        return latest.entries.sortedByDescending { it.value }.take(limit).map { it.key }
    }

    /** Every actual start (repeats kept), newest first, with its time: the "what was that song?" list. */
    fun heard(rows: List<SmartListRow>, available: Set<String>, limit: Int = HEARD_LIMIT): List<Pair<String, Long>> =
        rows.asSequence().filter { it.kind == "song_started" && it.localId in available }
            .sortedByDescending { it.occurredAt }.take(limit).map { it.localId to it.occurredAt }.toList()

    /** Current likes, most recently liked first; likes with no dated event (older or synced) follow by name order of id. */
    fun liked(rows: List<SmartListRow>, favorites: Set<String>, available: Set<String>): List<String> {
        val latest = HashMap<String, Long>()
        for (row in rows) if (row.kind == "favorite_changed" && row.localId in favorites) latest.merge(row.localId, row.occurredAt, ::maxOf)
        return favorites.filter { it in available }.sortedWith(compareByDescending<String> { latest[it] ?: Long.MIN_VALUE }.thenBy { it })
    }

    /**
     * Songs with strong past signals (liked, or 3+ full listens at a 70%+ full-listen rate) that haven't started in [days]
     * days. Only songs that were played at least once can be forgotten. Longest-forgotten first.
     */
    fun gems(rows: List<SmartListRow>, favorites: Set<String>, available: Set<String>, now: Long, days: Int = 60, limit: Int = LIMIT): List<String> {
        val cutoff = now - days * 86_400_000L
        val last = HashMap<String, Long>(); val full = HashMap<String, Int>(); val finished = HashMap<String, Int>()
        for (row in rows) {
            if (row.localId !in available) continue
            when (row.kind) {
                "song_started" -> last.merge(row.localId, row.occurredAt, ::maxOf)
                "song_completed" -> { full.merge(row.localId, 1, Int::plus); finished.merge(row.localId, 1, Int::plus) }
                in terminal -> { finished.merge(row.localId, 1, Int::plus); if ((row.completion ?: 0.0) >= 0.9) full.merge(row.localId, 1, Int::plus) }
            }
        }
        return last.filter { (id, at) ->
            at < cutoff && (id in favorites || (full[id] ?: 0) >= 3 && (full[id] ?: 0).toDouble() / (finished[id] ?: 1) >= 0.7)
        }.entries.sortedBy { it.value }.take(limit).map { it.key }
    }

    fun build(rows: List<SmartListRow>, favorites: Set<String>, available: Set<String>, now: Long = System.currentTimeMillis()): Map<SmartList, List<String>> = mapOf(
        SmartList.Top to top(rows, available), SmartList.Recent to recent(rows, available), SmartList.Liked to liked(rows, favorites, available),
        SmartList.Gems to gems(rows, favorites, available, now), SmartList.Heard to heard(rows, available).map { it.first })
}
