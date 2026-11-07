package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class ConnectedPolicyTest {
    private fun song(id: String) = LocalSong(id, "tree", "folder", "content://fixture/$id", "$id.mp3", "Folder", 1, 1, 1, 1000, true)
    private fun pick(id: String, slot: String) = ConnectedPick(song(id), slot, "because $id")
    private val fence = ConnectedFence("cur", "neutral", .3, null, 1, 0, 1, "d")
    private fun set(continuation: List<ConnectedPick> = emptyList(), options: List<ConnectedPick> = emptyList(), vibe: Boolean = false, anchor: String = "cur") =
        ConnectedSet(anchor, "req", "res", "llm", vibe, fence.copy(anchorLocalId = anchor), continuation, options, 0, 0)
    private fun ui(set: ConnectedSet?, enabled: Boolean = true, pending: String? = null) =
        ConnectedUi(ConnectedStatus(enabled = enabled, pendingAnchor = pending), set)
    private fun options(ui: ConnectedUi, current: String? = "cur", radio: Boolean = true, queued: Set<String> = emptySet(), disliked: Set<String> = emptySet(), available: Set<String>? = null) =
        ConnectedPolicy.visibleOptions(ui, current, radio, queued, { it in disliked }, { available == null || it in available }).map { it.song.id }

    private val three = listOf(pick("a", "explore"), pick("b", "wildcard"), pick("c", "context"))

    @Test fun cardShowsAllNonExploitOptionsOnlyForTheCurrentSong() {
        assertEquals(listOf("a", "b", "c"), options(ui(set(options = three))))
    }

    @Test fun cardIsHiddenInEveryUnavailableState() {
        val good = set(options = three)
        assertTrue("off", options(ui(good, enabled = false)).isEmpty())
        assertTrue("no set", options(ui(null)).isEmpty())
        assertTrue("pending", options(ui(good, pending = "cur")).isEmpty())
        assertTrue("another song is current", options(ui(good), current = "next").isEmpty())
        assertTrue("nothing playing", options(ui(good), current = null).isEmpty())
        assertTrue("radio ended or manual queue", options(ui(good), radio = false).isEmpty())
        assertTrue("all filtered", options(ui(good), queued = setOf("a", "b"), disliked = setOf("c")).isEmpty())
        assertTrue("all unavailable", options(ui(good), available = emptySet()).isEmpty())
        assertTrue("empty set", options(ui(set())).isEmpty())
    }

    @Test fun queuedDislikedAndUnavailableOptionsAreFilteredIndividually() {
        assertEquals(listOf("b"), options(ui(set(options = three)), queued = setOf("a"), disliked = setOf("c")))
        assertEquals(listOf("a", "c"), options(ui(set(options = three)), available = setOf("a", "c")))
    }

    @Test fun refillTakesAsManySimilarPicksAsSurviveCappedAtFour() {
        val picks = (1..6).map { pick("e$it", "exploit") }
        assertEquals(listOf("e1", "e2", "e3", "e4"), ConnectedPolicy.refillPicks(set(picks), emptySet(), false, 5, { false }, { true }).map { it.song.id })
        assertEquals(listOf("e1", "e2", "e3"), ConnectedPolicy.refillPicks(set(picks.take(3)), emptySet(), false, 5, { false }, { true }).map { it.song.id })
        assertEquals(listOf("e1", "e2", "e3", "e4"), ConnectedPolicy.refillPicks(set(picks), emptySet(), true, 5, { false }, { true }).map { it.song.id })
    }

    @Test fun refillNeverExceedsTheRoomLeftInTheFiveEntryBound() {
        val picks = (1..6).map { pick("e$it", "exploit") }
        val vibe = set(picks, vibe = true)
        // upcoming 2 -> room 3; upcoming 1 -> room 4; upcoming 0 -> room 5.
        assertEquals(3, ConnectedPolicy.refillPicks(vibe, emptySet(), true, 3, { false }, { true }).size)
        assertEquals(1, ConnectedPolicy.refillPicks(set(picks), emptySet(), false, 1, { false }, { true }).size)
        assertEquals(0, ConnectedPolicy.refillPicks(set(picks), emptySet(), false, 0, { false }, { true }).size)
        assertEquals(0, ConnectedPolicy.refillPicks(set(picks), emptySet(), false, -3, { false }, { true }).size)
        assertEquals(4, ConnectedPolicy.exploitLimit(false, ConnectedPolicy.REFILL_BOUND - 0))
    }

    @Test fun refillSkipsQueuedDislikedAndUnavailableAndNeverUsesOptionSlots() {
        val picks = listOf(pick("q", "exploit"), pick("d", "exploit"), pick("u", "exploit"), pick("ok1", "exploit"), pick("ok2", "exploit"), pick("ok3", "exploit"))
        val s = set(picks, options = three)
        val chosen = ConnectedPolicy.refillPicks(s, setOf("q"), false, 5, { it == "d" }, { it != "u" }).map { it.song.id }
        assertEquals(listOf("ok1", "ok2", "ok3"), chosen)
        assertTrue(chosen.none { it in setOf("a", "b", "c") })
        assertTrue(ConnectedPolicy.refillPicks(null, emptySet(), false, 5, { false }, { true }).isEmpty())
    }

    @Test fun refillWaitsOnlyForThePendingCurrentSongWhileMusicRemains() {
        assertTrue(ConnectedPolicy.shouldWait("cur", "cur", 1))
        assertTrue(ConnectedPolicy.shouldWait("cur", "cur", 2))
        assertFalse("queue would run dry", ConnectedPolicy.shouldWait("cur", "cur", 0))
        assertFalse("answer for an older song", ConnectedPolicy.shouldWait("old", "cur", 2))
        assertFalse("nothing pending (off, failed, done)", ConnectedPolicy.shouldWait(null, "cur", 2))
    }

    @Test fun serverAiPrefixIsStable() {
        assertEquals("Server AI", ConnectedPolicy.SERVER_AI_PREFIX)
    }
}
