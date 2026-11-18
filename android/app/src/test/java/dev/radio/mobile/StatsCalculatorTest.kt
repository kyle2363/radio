package dev.radio.mobile

import dev.radio.mobile.data.*
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

class StatsCalculatorTest {
    private val zone: ZoneId = ZoneOffset.UTC
    private fun at(date: String, hour: Int, minute: Int = 0) = LocalDate.parse(date).atTime(hour, minute).toInstant(ZoneOffset.UTC).toEpochMilli()
    private var n = 0
    private fun play(id: String, start: Long, heardMs: Long, end: String? = "song_completed", completion: Double? = 1.0, reason: String? = "natural_end",
        source: String? = null, sourceId: String? = null, origin: String? = null, trigger: String? = null) =
        StatsAttempt("a${n++}", id, start, end, heardMs, completion, reason, source, sourceId, origin, trigger)
    private fun song(id: String, artist: String, folder: String) = LocalSong(id, "root", "f", "content://x/$id", "$artist - $id.mp3", folder, 1, 1, 1, 200_000)

    // Golden journal: Wed 11 – Mon 16 Nov 2026, "today" is Tue 17 Nov.
    private val rows = listOf(
        play("s1", at("2026-11-11", 8), 200_000, source = "playlist", sourceId = "pl1", trigger = "tap"),
        play("s2", at("2026-11-11", 8, 4), 30_000, "song_skipped", 0.15, "explicit_navigation", source = "playlist", sourceId = "pl1", trigger = "next"),
        play("s1", at("2026-11-12", 22), 190_000, "attempt_stopped", 0.95, "late_navigation", source = "radio", origin = "Similar", trigger = "auto_advance"),
        play("s3", at("2026-11-12", 23), 10_000, "song_skipped", 0.05, "explicit_navigation", source = "radio", origin = "Explore", trigger = "media_button"),
        play("s1", at("2026-11-15", 23), 200_000),           // Sunday, before 8A (no context)
        play("s2", at("2026-11-16", 7), 20_000, null, null, null), // still unfinished
    )
    private val songs = mapOf("s1" to song("s1", "Alpha", "Music/EDM"), "s2" to song("s2", "Beta", "Music/Pop"), "s3" to song("s3", "Alpha", "Music/EDM"))
    private val now = at("2026-11-17", 12)

    @Test fun overviewFollowsTheDefinitions() {
        val o = StatsCalculator.overview(rows)
        assertEquals(6, o.plays)
        assertEquals(3, o.distinctSongs)
        assertEquals(650_000L, o.listeningMs)
        // 5 finished; full = natural ends (2) + late navigation (1); skips = 2.
        assertEquals(3.0 / 5, o.fullListenRate!!, 1e-9)
        assertEquals(2.0 / 5, o.skipRate!!, 1e-9)
    }

    @Test fun streaksNeedThirtySecondsAndToleratesNoPlayYetToday() {
        val s = StatsCalculator.summarize(rows, songs, StatsPeriod.All, now, zone)
        // Days with ≥30 s: 11, 12, 15, 16 (the 16th only has 20 s → not counted). So 11–12 = 2, 15 alone = 1.
        assertEquals(2, s.longestStreak)
        assertEquals(0, s.currentStreak) // 16th doesn't count and today has nothing.
        val (current, longest) = StatsCalculator.streaks(setOf(LocalDate.parse("2026-11-15"), LocalDate.parse("2026-11-16")), LocalDate.parse("2026-11-17"))
        assertEquals(2, current); assertEquals(2, longest)
    }

    @Test fun sessionsSplitOnThirtyMinutesOfSilence() {
        val sessions = StatsCalculator.sessions(rows)
        assertEquals(listOf(2, 1, 1, 1, 1), sessions.map { it.plays }) // 22:03 → 23:00 is a 57-minute gap
    }


    @Test fun weekStartsOnMonday() {
        val week = StatsCalculator.summarize(rows, songs, StatsPeriod.Week, now, zone)
        assertEquals(1, week.overview.plays)
        assertEquals(5, week.previous!!.plays)
        val month = StatsCalculator.summarize(rows, songs, StatsPeriod.Month, now, zone)
        assertEquals(6, month.overview.plays)
        assertEquals(365, month.calendar.size)
        assertEquals(LocalDate.parse("2026-11-17"), month.calendar.last().first)
        assertEquals(230_000L / 60_000, month.calendar.first { it.first == LocalDate.parse("2026-11-11") }.second)
    }

    @Test fun topListsSourcesAndRadioReport() {
        val s = StatsCalculator.summarize(rows, songs, StatsPeriod.All, now, zone)
        assertEquals("s1", s.topSongs.first().key); assertEquals(3, s.topSongs.first().plays)
        assertEquals(listOf("Alpha", "Beta"), s.topArtists.map { it.key })
        assertEquals("Music/EDM", s.topFolders.first().key)
        assertEquals(listOf(Ranked("pl1", 2, 230_000)), s.topPlaylists)
        assertEquals(setOf("playlist", "radio"), s.sources.map { it.kind }.toSet())
        assertEquals(3, s.chosenPlays) // tap, next, media button
        assertEquals(1, s.autoPlays)
        assertEquals(at("2026-11-11", 8), s.contextSince)
        val similar = s.radio.first { it.origin == "Similar" }
        assertEquals(1, similar.full); assertEquals(0, similar.skips)
        assertEquals(1, s.radio.first { it.origin == "Explore" }.skips)
    }

    @Test fun clockLabels() {
        val night = LongArray(24).also { it[23] = 100; it[0] = 100; it[14] = 50 }
        assertEquals("Night owl", StatsCalculator.clockLabel(night, LongArray(24)))
        val early = LongArray(24).also { it[6] = 100; it[13] = 60 }
        assertEquals("Early bird", StatsCalculator.clockLabel(early, LongArray(24)))
        assertNull(StatsCalculator.clockLabel(LongArray(24), LongArray(24)))
    }

    @Test fun songDetailSkipMapAndWeeks() {
        val s = StatsCalculator.song(rows, "s2", 200_000, now, zone)
        assertEquals(2, s.plays)
        assertEquals(1, s.skipMap[1]) // skipped at 15%
        assertEquals(1, s.weekly[11]) // Mon 16th is this week
        assertEquals(1, s.weekly[10])
        assertEquals(0.0, s.fullListenRate!!, 1e-9)
    }

    @Test fun forgottenGems() {
        val day = 86_400_000L
        fun r(id: String, kind: String, at: Long, completion: Double? = null) = SmartListRow(id, kind, completion, at)
        val old = now - 90 * day
        val rows = listOf(
            // loved: 3 full listens 90 days ago
            r("loved", "song_started", old), r("loved", "song_completed", old), r("loved", "song_started", old + 1), r("loved", "song_completed", old + 1),
            r("loved", "song_started", old + 2), r("loved", "song_skipped", old + 2, 0.95),
            // liked but recent → not forgotten
            r("liked-recent", "song_started", now - day),
            // liked long ago
            r("liked-old", "song_started", old - day),
            // played often but skipped a lot
            r("meh", "song_started", old), r("meh", "song_skipped", old, 0.1), r("meh", "song_started", old), r("meh", "song_completed", old),
        )
        val ids = setOf("loved", "liked-recent", "liked-old", "meh", "never-played")
        assertEquals(listOf("liked-old", "loved"), SmartLists.gems(rows, setOf("liked-recent", "liked-old", "never-played"), ids, now))
    }
}
