package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.AudioSettings
import dev.radio.mobile.playback.LocalPlaybackController
import org.junit.*
import org.junit.Assert.*

/** Crossfade and speed on the real player with real MP3s: the journal still sees one finished song and one new start. */
class CrossfadePlaybackTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var controller: LocalPlaybackController
    private val starts = mutableListOf<Pair<String, Long>>()
    private val outcomes = mutableListOf<Triple<String, String, String>>()
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String, duration: Long = 8000) = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
        DocumentsContract.buildDocumentUriUsingTree(FixtureDocumentsProvider.tree(), id).toString(), "$id.mp3", "", 1, 1, 1, duration)
    private fun await(message: String, timeoutMs: Long = 12000, condition: () -> Boolean) {
        val end = android.os.SystemClock.elapsedRealtime() + timeoutMs
        while (android.os.SystemClock.elapsedRealtime() < end) {
            var ready = false; main { ready = condition() }
            if (ready) return
            Thread.sleep(20)
        }
        fail(message + ": " + controller.state.value)
    }
    @Before fun setup() {
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "audio", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        main { controller = LocalPlaybackController(context, onAttemptFinished = { song, _, kind, _, _, reason -> outcomes += Triple(song, kind, reason) }) { track, _, _ ->
            starts += track.id to android.os.SystemClock.elapsedRealtime() } }
    }
    @After fun cleanup() {
        main { controller.close() }; scenario.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    @Test fun crossfadeMovesOnBeforeTheEndAndRecordsAFinishedSong() {
        main { controller.applyAudio(AudioSettings(crossfadeSeconds = 2)); controller.playFrom(listOf(song("long1"), song("long2")), "long1", "Songs") }
        await("First song didn't start") { starts.any { it.first == "long1" } }
        await("Crossfade didn't move to the next song") { controller.state.value.currentId == "long2" && starts.any { it.first == "long2" } }
        main {
            val first = starts.first { it.first == "long1" }.second
            val second = starts.first { it.first == "long2" }.second
            val duration = controller.state.value.queue.first { it.id == "long1" }.durationMs!!
            // The next song begins about two seconds before the first would have ended, not after it.
            assertTrue("Moved on after ${second - first} ms of a $duration ms song", second - first in (duration - 3500)..(duration - 1000))
            assertEquals(listOf(Triple("long1", "song_completed", "natural_end")), outcomes)
            assertTrue(controller.state.value.isPlaying)
        }
    }

    @Test fun pausingDuringACrossfadeStopsItCleanly() {
        main { controller.applyAudio(AudioSettings(crossfadeSeconds = 2)); controller.playFrom(listOf(song("long1"), song("long2")), "long1", "Songs") }
        await("Crossfade didn't move to the next song") { controller.state.value.currentId == "long2" && controller.state.value.isPlaying }
        main { controller.pause() }
        await("Didn't pause") { !controller.state.value.isPlaying }
        main { controller.toggle() }
        await("Didn't resume the next song") { controller.state.value.isPlaying && controller.state.value.currentId == "long2" }
        main { assertEquals(listOf("long1", "long2"), starts.map { it.first }) }
    }

    @Test fun skippingIsInstantEvenWithCrossfadeOn() {
        main { controller.applyAudio(AudioSettings(crossfadeSeconds = 4)); controller.playFrom(listOf(song("long1"), song("long2")), "long1", "Songs") }
        await("First song didn't start") { controller.state.value.isPlaying && controller.state.value.positionMs > 300 }
        var pressed = 0L
        main { pressed = android.os.SystemClock.elapsedRealtime(); controller.next() }
        await("Next didn't start") { controller.state.value.currentId == "long2" && controller.state.value.isPlaying }
        main {
            // A manual skip is logged as navigation (these test songs are short, so "late"), never as a crossfaded finish.
            assertEquals("late_navigation", outcomes.single().third)
            assertTrue("Next took ${starts.last().second - pressed} ms", starts.last().second - pressed < 1500)
        }
    }

    @Test fun fasterSpeedFinishesSooner() {
        main { controller.applyAudio(AudioSettings(speed = 2f)); controller.playFrom(listOf(song("long1"), song("long2")), "long1", "Songs") }
        await("First song didn't start") { starts.any { it.first == "long1" } }
        await("Didn't reach the next song") { starts.any { it.first == "long2" } }
        main {
            val elapsed = starts[1].second - starts[0].second
            assertTrue("An 8 s song took $elapsed ms at 2x", elapsed < 6000)
            assertEquals("song_completed", outcomes.first().second)
        }
    }
}
