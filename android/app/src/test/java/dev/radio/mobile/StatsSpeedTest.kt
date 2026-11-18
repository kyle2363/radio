package dev.radio.mobile

import dev.radio.mobile.data.*
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneOffset
import kotlin.random.Random

/** Checkpoint 12.2: the calculation half of opening Stats with 50 000 journal events (25 000 plays). */
class StatsSpeedTest {
    @Test fun summarizesFiftyThousandEventsQuickly() {
        val random = Random(8)
        val now = 1_795_000_000_000L
        val songs = (0 until 2000).associate { "s$it" to LocalSong("s$it", "r", "f", "content://x/$it", "Artist ${it % 150} - Song $it.mp3", "Music/${it % 40}", 1, 1, 1, 200_000) }
        val kinds = listOf("radio", "playlist", "folder", "search", "all_songs")
        val rows = (0 until 25_000).map { i ->
            val skipped = random.nextInt(4) == 0
            StatsAttempt("a$i", "s${random.nextInt(2000)}", now - random.nextLong(3L * 365 * 86_400_000), if (skipped) "song_skipped" else "song_completed",
                random.nextLong(5_000, 240_000), random.nextDouble(), if (skipped) "explicit_navigation" else "natural_end",
                kinds[i % kinds.size], "id${i % 30}", "Similar", if (i % 3 == 0) "tap" else "auto_advance")
        }.sortedBy { it.startedAt }
        StatsCalculator.summarize(rows, songs, StatsPeriod.All, now, ZoneOffset.UTC) // warm up the JIT
        val best = (1..3).minOf {
            val start = System.nanoTime()
            StatsPeriod.entries.forEach { StatsCalculator.summarize(rows, songs, it, now, ZoneOffset.UTC) }
            (System.nanoTime() - start) / 1_000_000 / StatsPeriod.entries.size
        }
        println("Stats summarize, 25k plays: ${best} ms per period")
        assertTrue("summarize took $best ms", best < 500)
    }
}
