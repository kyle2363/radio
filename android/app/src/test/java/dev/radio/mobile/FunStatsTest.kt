package dev.radio.mobile

import dev.radio.mobile.data.*
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

/** Checkpoint 8C: extra measures, Wrapped picks, milestones and the taste map projection. */
class FunStatsTest {
    private val zone: ZoneId = ZoneOffset.UTC
    private fun at(date: String, hour: Int = 12) = LocalDate.parse(date).atTime(hour, 0).toInstant(ZoneOffset.UTC).toEpochMilli()
    private var n = 0
    private fun play(id: String, start: Long, heardMs: Long = 200_000, skipped: Boolean = false, completion: Double? = if (skipped) 0.1 else 1.0) =
        StatsAttempt("a${n++}", id, start, if (skipped) "song_skipped" else "song_completed", heardMs, completion,
            if (skipped) "explicit_navigation" else "natural_end", null, null, null, null)
    private fun song(id: String, artist: String, folder: String = "Music") = LocalSong(id, "root", "f", "content://x/$id", "$artist - $id.mp3", folder, 1, 1, 1, 200_000)
    private val now = at("2026-11-18") // Wednesday; "This week" starts Monday 16 Nov.

    @Test fun discoveryRateCountsFirstEverPlaysInThePeriod() {
        val all = listOf(play("old", at("2026-10-01")), play("old", at("2026-11-16")), play("new", at("2026-11-16")), play("new", at("2026-11-17")))
        val m = FunStats.measures(all, StatsPeriod.Week, now, zone, setOf("old", "new", "never"), { "A" })
        // This week: old (repeat), new (first ever), new (repeat) → 1 of 3.
        assertEquals(1.0 / 3, m.discoveryRate!!, 1e-9)
        // Coverage is all time: 2 of the 3 available songs were ever played.
        assertEquals(2.0 / 3, m.libraryCoverage!!, 1e-9)
    }

    @Test fun varietyIsNormalisedArtistEntropy() {
        val one = listOf(play("a", at("2026-11-16")), play("b", at("2026-11-17")))
        assertEquals(0, FunStats.measures(one, StatsPeriod.Week, now, zone, setOf("a", "b"), { "Same" }).variety)
        val even = listOf(play("a", at("2026-11-16")), play("b", at("2026-11-17")))
        assertEquals(100, FunStats.measures(even, StatsPeriod.Week, now, zone, setOf("a", "b"), { it }).variety)
        assertNull(FunStats.measures(emptyList(), StatsPeriod.Week, now, zone, emptySet(), { it }).variety)
    }

    @Test fun loyaltyIsTheShareFromTheTopTwentySongs() {
        val all = (1..25).map { play("s$it", at("2026-11-16"), heardMs = if (it <= 20) 100_000 else 50_000) }
        val m = FunStats.measures(all, StatsPeriod.Week, now, zone, all.map { it.localId }.toSet(), { "A" })
        assertEquals(2_000_000.0 / 2_250_000, m.loyalty!!, 1e-9)
    }

    @Test fun comebacksAreSongsBackAfterNinetyDaysAway() {
        val all = listOf(play("back", at("2026-07-01")), play("back", at("2026-11-17")),
            play("recent", at("2026-10-01")), play("recent", at("2026-11-17")),
            play("fresh", at("2026-11-17")))
        assertEquals(listOf("back"), FunStats.measures(all, StatsPeriod.Week, now, zone, emptySet(), { "A" }).comebacks)
    }

    @Test fun wrappedPicksTheSongYouCouldntSkipAndTheBiggestGrower() {
        val songs = mapOf("loved" to song("loved", "Alpha"), "skippy" to song("skippy", "Beta"), "grower" to song("grower", "Gamma"))
        val last = listOf(play("grower", at("2026-11-10"), skipped = true), play("grower", at("2026-11-11"), skipped = true), play("grower", at("2026-11-12")))
        val week = List(4) { play("loved", at("2026-11-16", 8 + it)) } +
            List(5) { play("skippy", at("2026-11-17", 8 + it), skipped = it == 0) } +
            List(3) { play("grower", at("2026-11-17", 14 + it)) }
        val w = FunStats.wrapped(last + week, songs, StatsPeriod.Week, now, zone, { it.artist })
        assertEquals("loved", w.couldntSkip?.key)
        assertEquals("grower", w.biggestGrower?.first)
        assertEquals(1.0 - 1.0 / 3, w.biggestGrower!!.second, 1e-9)
        assertEquals("skippy", w.topSong?.key)
        assertEquals(listOf("skippy", "loved"), w.discoveries)
        assertEquals(12, w.plays)
        assertEquals(2, w.longestStreak)
        // Card visuals: the top songs list, the listening clock and the streak flame.
        assertEquals(listOf("skippy", "loved", "grower"), w.topSongs.map { it.key })
        assertTrue(w.topSongs.size <= FunStats.WRAPPED_TOP)
        assertEquals(24, w.hours.size)
        assertTrue("plays at 8:00 count in the 8 o'clock bar", w.hours[8] > 0)
        assertEquals(0L, w.hours[3])
    }

    @Test fun wrappedTopFeelUsesListeningTime() {
        val week = listOf(play("a", at("2026-11-16"), 300_000), play("b", at("2026-11-16"), 100_000), play("c", at("2026-11-17"), 100_000))
        val feels = mapOf("a" to "calm", "b" to "hype", "c" to "hype")
        val w = FunStats.wrapped(week, emptyMap(), StatsPeriod.Week, now, zone, { it.artist }, feelOf = feels::get)
        assertEquals("calm", w.topFeel?.key)
    }

    @Test fun milestonesTrackProgressAndCompletion() {
        val library = listOf(song("x", "A", "Music/Folk"), song("y", "A", "Music/Folk"), song("z", "A", "Music/Folk"), song("w", "A", "Music/Pop"))
        val all = List(100) { play("x", at("2026-11-17") - it * 60_000L) } + play("y", at("2026-11-16")) + play("z", at("2026-11-16"))
        val m = FunStats.milestones(all, library, now, zone).associateBy { it.milestone }
        assertTrue(m.getValue(Milestone.Song100).achieved)
        assertTrue(m.getValue(Milestone.FolderComplete).achieved)
        assertFalse(m.getValue(Milestone.Plays1000).achieved)
        assertEquals(102.0 / 1000, m.getValue(Milestone.Plays1000).progress, 1e-9)
        assertFalse(m.getValue(Milestone.Streak30).achieved)
    }

    @Test fun tasteMapProjectsOntoTheMainDirectionsWithinTheUnitSquare() {
        // Points along one direction in 6-D with a little noise on another: order along the line is kept on one axis.
        val vectors = (0 until 10).associate { i -> "s$i" to FloatArray(6) { d -> when (d) { 0 -> i.toFloat(); 1 -> i * 2f; 3 -> if (i % 2 == 0) .1f else -.1f; else -> 0f } } }
        val points = TasteMap.project(vectors)
        assertEquals(10, points.size)
        points.values.forEach { (x, y) -> assertTrue(x in 0f..1f && y in 0f..1f) }
        val xs = (0 until 10).map { points.getValue("s$it").first }
        assertTrue(xs == xs.sorted() || xs == xs.sortedDescending())
        assertTrue(TasteMap.project(emptyMap()).isEmpty())
        assertEquals(setOf("only"), TasteMap.project(mapOf("only" to floatArrayOf(1f, 2f))).keys)
    }
}
