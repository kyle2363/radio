package dev.radio.mobile

import dev.radio.mobile.data.SmartList
import dev.radio.mobile.data.SmartListRow
import dev.radio.mobile.data.SmartLists
import org.junit.Assert.assertEquals
import org.junit.Test

class SmartListsTest {
    private fun completed(id: String, at: Long) = SmartListRow(id, "song_completed", 1.0, at)
    private fun skipped(id: String, at: Long, completion: Double) = SmartListRow(id, "song_skipped", completion, at)
    private fun stopped(id: String, at: Long, completion: Double?) = SmartListRow(id, "attempt_stopped", completion, at)
    private fun started(id: String, at: Long) = SmartListRow(id, "song_started", null, at)
    private fun favorite(id: String, at: Long) = SmartListRow(id, "favorite_changed", null, at)

    @Test fun topCountsFinishedAndHalfPlayedListensButNotEarlySkips() {
        val rows = listOf(
            completed("a", 1), completed("a", 2), completed("a", 2),
            skipped("b", 3, 0.6), stopped("b", 4, 0.9), stopped("b", 5, null),
            skipped("c", 6, 0.1), skipped("c", 7, 0.2), skipped("c", 8, 0.3),
            completed("d", 9),
        )
        assertEquals(listOf("a", "b", "d"), SmartLists.top(rows, setOf("a", "b", "c", "d")))
    }

    @Test fun topBreaksTiesByMostRecentListen() {
        val rows = listOf(completed("old", 10), completed("new", 20), completed("mid", 15))
        assertEquals(listOf("new", "mid", "old"), SmartLists.top(rows, setOf("old", "new", "mid")))
    }

    @Test fun topSkipsSongsNoLongerInTheLibraryAndStopsAtTheLimit() {
        val rows = (1..150).flatMap { n -> List(n) { completed("s$n", n.toLong()) } } + List(500) { completed("gone", 1) }
        val available = (1..150).map { "s$it" }.toSet()
        val top = SmartLists.top(rows, available)
        assertEquals(100, top.size)
        assertEquals("s150", top.first())
        assertEquals("s51", top.last())
    }

    @Test fun recentOrdersByLatestStartOncePerSong() {
        val rows = listOf(started("a", 1), started("b", 2), started("a", 3), started("gone", 4))
        assertEquals(listOf("a", "b"), SmartLists.recent(rows, setOf("a", "b")))
    }

    @Test fun likedFollowsCurrentFavouritesNewestFirst() {
        val rows = listOf(favorite("a", 1), favorite("b", 2), favorite("unliked", 3))
        // "c" was liked before history was kept (or by sync): it still counts, after the dated ones.
        assertEquals(listOf("b", "a", "c"), SmartLists.liked(rows, favorites = setOf("a", "b", "c"), available = setOf("a", "b", "c", "unliked")))
    }

    @Test fun listsAreFoundBySelectionId() {
        assertEquals(SmartList.Top, SmartList.of("smart:top"))
        assertEquals(null, SmartList.of("playlist-1"))
    }

    // Checkpoint 11: "what was that song?" — every actual start, newest first, repeats kept, at most 50.
    @Test fun heardListsEveryStartNewestFirst() {
        val rows = (1..60).map { started(if (it % 2 == 0) "a" else "b", it.toLong()) } + completed("a", 100) + started("gone", 99)
        val heard = SmartLists.heard(rows, setOf("a", "b"))
        assertEquals(50, heard.size)
        assertEquals("a" to 60L, heard.first())
        assertEquals("b" to 59L, heard[1])
        assertEquals(listOf("a", "b"), SmartLists.build(rows, emptySet(), setOf("a", "b"))[SmartList.Heard]!!.take(2))
    }
}
