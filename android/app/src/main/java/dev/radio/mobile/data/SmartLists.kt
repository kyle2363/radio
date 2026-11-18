package dev.radio.mobile.data

/** One journal row, reduced to what the automatic lists need. */
data class SmartListRow(val localId: String, val kind: String, val completion: Double?, val occurredAt: Long)

/** Playlists the app keeps up to date from listening history and likes. They are read-only. */
enum class SmartList(val id: String, val title: String, val description: String) {
    Top("smart:top", "Top 100", "Your most played songs"),
    Recent("smart:recent", "Recently played", "The last 100 songs you played"),
    Liked("smart:liked", "Liked songs", "Everything you've liked, newest first");

    companion object { fun of(id: String?): SmartList? = entries.firstOrNull { it.id == id } }
}

object SmartLists {
    const val LIMIT = 100
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

    /** Current likes, most recently liked first; likes with no dated event (older or synced) follow by name order of id. */
    fun liked(rows: List<SmartListRow>, favorites: Set<String>, available: Set<String>): List<String> {
        val latest = HashMap<String, Long>()
        for (row in rows) if (row.kind == "favorite_changed" && row.localId in favorites) latest.merge(row.localId, row.occurredAt, ::maxOf)
        return favorites.filter { it in available }.sortedWith(compareByDescending<String> { latest[it] ?: Long.MIN_VALUE }.thenBy { it })
    }

    fun build(rows: List<SmartListRow>, favorites: Set<String>, available: Set<String>): Map<SmartList, List<String>> = mapOf(
        SmartList.Top to top(rows, available), SmartList.Recent to recent(rows, available), SmartList.Liked to liked(rows, favorites, available))
}
