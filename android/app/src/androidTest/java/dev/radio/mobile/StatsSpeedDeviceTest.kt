package dev.radio.mobile

import android.util.Log
import androidx.room.Room
import androidx.room.withTransaction
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.time.ZoneId

/** Checkpoint 12.2: opening Stats (query + summary) with 50 000 journal events must take under a second on the phone. */
@RunWith(AndroidJUnit4::class)
class StatsSpeedDeviceTest {
    @Test fun statsOpenUnderOneSecondWithFiftyThousandEvents() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        try {
            val now = System.currentTimeMillis()
            db.withTransaction {
                for (i in 0 until 25_000) {
                    val at = now - i * 60_000L
                    val song = "s${i % 2000}"
                    db.journal().insert(LocalListeningEvent("st$i", song, "a$i", "song_started", at, i * 2L, "{}", 0, null, null, "local_only"))
                    db.journal().insert(LocalListeningEvent("en$i", song, "a$i", if (i % 4 == 0) "song_skipped" else "song_completed", at + 1, i * 2L + 1, "{}", 120_000, 0.8, "natural_end", "local_only"))
                    if (i % 2 == 0) db.journal().playContext(LocalPlayContext("a$i", song, "radio", null, "Similar", "auto_advance", at))
                }
            }
            val songs = (0 until 2000).associate { "s$it" to LocalSong("s$it", "r", "f", "content://x/$it", "Artist ${it % 150} - Song $it.mp3", "Music", 1, 1, 1, 200_000) }
            db.journal().statsAttempts() // warm up
            val start = System.nanoTime()
            val rows = db.journal().statsAttempts()
            val summary = StatsCalculator.summarize(rows, songs, StatsPeriod.Month, now, ZoneId.systemDefault())
            val ms = (System.nanoTime() - start) / 1_000_000
            Log.i("StatsSpeed", "50k events: query + summary $ms ms")
            assertEquals(25_000, rows.size)
            assertTrue(summary.totalPlays == 25_000)
            assertTrue("Stats took $ms ms", ms < 1000)
        } finally { db.close() }
    }
}
