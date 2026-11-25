package dev.radio.mobile.music

/**
 * Library search (checkpoint 11). Every query word must match some word of the song's text: as a plain substring,
 * or, for words of 4+ letters, within one edit (two for 8+), counting a swap of neighbouring letters as one edit.
 * Songs whose words all match plainly come first; otherwise the input order (the library sort) is kept.
 */
object SongSearch {
    const val RECENT_LIMIT = 8

    fun <T> filter(items: List<T>, query: String, text: (T) -> List<String>): List<T> = index(items, text).filter(query)

    /**
     * The library's searchable text, lowercased and split into words once. Build it when the library changes, then
     * filter it per keystroke (off the main thread) instead of re-reading every song each time a letter is typed.
     */
    fun <T> index(items: List<T>, text: (T) -> List<String>): Index<T> = Index(items.map { item ->
        val haystack = text(item).joinToString(" ").lowercase()
        Entry(item, haystack, tokens(haystack))
    })

    class Entry<T> internal constructor(val item: T, val haystack: String, val words: List<String>)

    class Index<T> internal constructor(private val entries: List<Entry<T>>) {
        fun filter(query: String): List<T> {
            val words = tokens(query)
            if (words.isEmpty()) return emptyList()
            val exact = ArrayList<T>()
            val fuzzy = ArrayList<T>()
            for (entry in entries) {
                var allPlain = true
                var all = true
                for (word in words) {
                    if (entry.haystack.contains(word)) continue
                    allPlain = false
                    val allowed = when { word.length >= 8 -> 2; word.length >= 4 -> 1; else -> 0 }
                    if (allowed == 0 || entry.words.none { close(word, it, allowed) }) { all = false; break }
                }
                if (all) (if (allPlain) exact else fuzzy).add(entry.item)
            }
            return exact + fuzzy
        }
    }

    internal fun close(word: String, candidate: String, allowed: Int): Boolean {
        if (kotlin.math.abs(candidate.length - word.length) <= allowed && distance(word, candidate, allowed) <= allowed) return true
        // A typo in a word the user hasn't finished typing: compare against the candidate's same-length prefix.
        return candidate.length > word.length && distance(word, candidate.take(word.length), allowed) <= allowed
    }

    private val wordBreak = Regex("[^\\p{L}\\p{N}]+")
    internal fun tokens(text: String) = text.lowercase().split(wordBreak).filter { it.isNotEmpty() }

    /** Optimal-string-alignment distance, stopping early above [limit] (returns limit + 1). */
    fun distance(a: String, b: String, limit: Int): Int {
        if (kotlin.math.abs(a.length - b.length) > limit) return limit + 1
        var prev2 = IntArray(b.length + 1)
        var prev = IntArray(b.length + 1) { it }
        var cur = IntArray(b.length + 1)
        for (i in 1..a.length) {
            cur[0] = i
            var rowMin = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                var v = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
                if (i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1]) v = minOf(v, prev2[j - 2] + 1)
                cur[j] = v
                rowMin = minOf(rowMin, v)
            }
            if (rowMin > limit) return limit + 1
            val t = prev2; prev2 = prev; prev = cur; cur = t
        }
        return minOf(prev[b.length], limit + 1)
    }

    /** Newest first, case-insensitive duplicates removed, at most [RECENT_LIMIT]. */
    fun remember(recent: List<String>, query: String): List<String> {
        val q = query.trim()
        if (q.isEmpty()) return recent
        return (listOf(q) + recent.filterNot { it.equals(q, ignoreCase = true) }).take(RECENT_LIMIT)
    }
}
