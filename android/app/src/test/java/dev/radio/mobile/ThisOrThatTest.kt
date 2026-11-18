package dev.radio.mobile

import dev.radio.mobile.portable.FeelRules.FeelState
import dev.radio.mobile.recommendation.ThisOrThat
import org.junit.Assert.*
import org.junit.Test
import kotlin.random.Random

class ThisOrThatTest {
    private fun states(n: Int, member: Boolean = true, confidence: Double = 0.2) =
        (0 until n).associate { "s$it" to FeelState(it / n.toDouble(), confidence, member) }
    private fun key(p: Pair<String, String>) = setOf(p.first, p.second)

    @Test fun needsAtLeastTwoSongs() {
        assertTrue(ThisOrThat.round(states(1), emptySet(), setOf("s0"), Random(1)).isEmpty())
        assertTrue(ThisOrThat.round(emptyMap(), emptySet(), emptySet(), Random(1)).isEmpty())
        assertEquals(1, ThisOrThat.round(states(2), emptySet(), setOf("s0", "s1"), Random(1)).size)
    }

    @Test fun tenDistinctPairsOfAvailableSongs() {
        val s = states(30)
        val available = s.keys - setOf("s3", "s4")
        val round = ThisOrThat.round(s, emptySet(), available, Random(7))
        assertEquals(10, round.size)
        assertEquals(10, round.map(::key).toSet().size)
        round.forEach { (a, b) -> assertNotEquals(a, b); assertTrue(a in available && b in available) }
    }

    @Test fun noSongTooOften() {
        val round = ThisOrThat.round(states(30), emptySet(), states(30).keys, Random(3))
        val counts = round.flatMap { listOf(it.first, it.second) }.groupingBy { it }.eachCount()
        assertTrue(counts.values.max() <= ThisOrThat.MAX_PER_SONG)
    }

    @Test fun deterministicForSeed() {
        val s = states(40)
        assertEquals(ThisOrThat.round(s, setOf("s1"), s.keys, Random(9)), ThisOrThat.round(s, setOf("s1"), s.keys, Random(9)))
    }

    @Test fun mostlyUncertainPairs() {
        val s = states(40)
        val uncertain = dev.radio.mobile.portable.FeelRules.uncertainPairs(s, emptySet(), 40).map(::key).toSet()
        val round = ThisOrThat.round(s, emptySet(), s.keys, Random(5))
        assertTrue(round.count { key(it) in uncertain } >= 8)
        val allRandom = ThisOrThat.round(s, emptySet(), s.keys, Random(5), randomShare = 1.0)
        assertEquals(10, allRandom.size)
    }

    @Test fun tagFeelsSkipConfidentNonMembers() {
        val s = mapOf("a" to FeelState(0.9, 0.2, true), "b" to FeelState(0.8, 0.2, true),
            "no" to FeelState(0.1, 0.9, false), "maybe" to FeelState(0.4, 0.1, false))
        val songs = ThisOrThat.round(s, emptySet(), s.keys, Random(2), size = 10).flatMap { listOf(it.first, it.second) }.toSet()
        assertFalse("no" in songs)
        assertTrue(songs.isNotEmpty())
        // Energy (scale) uses every rated song.
        val energy = ThisOrThat.round(mapOf("x" to FeelState(0.1, 0.9, false), "y" to FeelState(0.9, 0.9, false)), emptySet(), setOf("x", "y"), Random(1), scale = true)
        assertEquals(1, energy.size)
    }
}
