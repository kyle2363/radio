package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.LocalPlaybackController
import dev.radio.mobile.playback.snapshot
import dev.radio.mobile.recommendation.StationOrigin
import dev.radio.mobile.recommendation.StationRecord
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Checkpoint 4.2: station origins, New mix, End and restoration on the real Media3 engine. */
class RadioStationTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var controller: LocalPlaybackController
    private val starts = mutableListOf<String>()
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String, uriId: String = "long1") = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
        DocumentsContract.buildDocumentUriUsingTree(FixtureDocumentsProvider.tree(), uriId).toString(),
        "$id.mp3", "", 1, 1, 1, 8000)
    private fun await(message: String, condition: () -> Boolean) {
        val end = android.os.SystemClock.elapsedRealtime() + 10000
        while (android.os.SystemClock.elapsedRealtime() < end) {
            var ready = false; main { ready = condition() }
            if (ready) return
            Thread.sleep(25)
        }
        fail(message + ": " + controller.state.value)
    }
    @Before fun setup() {
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "audio", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        main { controller = LocalPlaybackController(context, onAudibleStart = { track, _, _ -> starts += track.id }) }
    }
    @After fun cleanup() {
        main { controller.close() }; scenario.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }
    private fun startStation() {
        val items = listOf(song("seed"), song("r1", "long2"), song("r2"))
        main { controller.beginRadio(items, mapOf("seed" to StationOrigin.Start, "r1" to StationOrigin.Similar, "r2" to StationOrigin.Taste), "seed", "Seed Song") }
        await("Radio did not start") { controller.state.value.isPlaying && controller.state.value.currentId == "seed" && starts.size == 1 }
    }

    @Test fun stationCarriesOriginsSeedAndMarksSongsTheListenerAdds() {
        startStation()
        main {
            val state = controller.state.value
            assertTrue(state.radioActive)
            assertEquals("Seed Song", state.stationSeedTitle)
            assertEquals(StationOrigin.Similar, state.origins["r1"])
            controller.addToQueue(listOf(song("mine", "long2")), playNext = true)
            val after = controller.state.value
            assertEquals(StationOrigin.User, after.origins["mine"])
            assertEquals(listOf("seed", "mine", "r1", "r2"), after.playOrder)
            assertTrue(after.radioActive)
        }
    }

    @Test fun newMixReplacesOnlyRadioSongsAndNeverTouchesTheCurrentSong() {
        startStation()
        main { controller.addToQueue(listOf(song("mine", "long2")), playNext = true) }
        Thread.sleep(400)
        var before = controller.state.value
        main { before = controller.state.value }
        main {
            assertTrue(controller.replaceRadioUpcoming(before, listOf(song("n1"), song("n2", "long2")), mapOf("n1" to StationOrigin.Explore, "n2" to StationOrigin.Desktop)))
            val after = controller.state.value
            assertEquals("seed", after.currentId)
            assertTrue(after.isPlaying)
            assertEquals(listOf("seed", "mine", "n1", "n2"), after.playOrder)
            assertEquals(StationOrigin.User, after.origins["mine"])
            assertEquals(StationOrigin.Desktop, after.origins["n2"])
            assertNull(after.origins["r1"])
            assertTrue(after.positionMs >= before.positionMs)
        }
        assertEquals("New mix must not manufacture a start", 1, starts.size)
    }

    @Test fun newMixRejectsAStaleQueue() {
        startStation()
        var stale = controller.state.value
        main { stale = controller.state.value; controller.removeFromQueue("r2") }
        main {
            val before = controller.state.value
            assertFalse(controller.replaceRadioUpcoming(stale, listOf(song("n1")), emptyMap()))
            assertEquals(before.playOrder, controller.state.value.playOrder)
        }
    }

    @Test fun endFinishQueuedKeepsPlayingButStopsRadio() {
        startStation()
        main {
            controller.endRadio(stopNow = false)
            val state = controller.state.value
            assertFalse(state.radioActive)
            assertTrue(state.playWhenReady)
            assertEquals(listOf("seed", "r1", "r2"), state.playOrder)
        }
    }

    @Test fun endStopNowPausesAndKeepsANormalQueue() {
        startStation()
        main {
            controller.endRadio(stopNow = true)
            val state = controller.state.value
            assertFalse(state.radioActive)
            assertFalse(state.playWhenReady)
            assertEquals(listOf("seed", "r1", "r2"), state.playOrder)
            assertEquals("Custom queue", state.source)
            assertTrue(state.origins.isEmpty())
        }
    }

    @Test fun matchingStationRestoresOnAirPausedAndMismatchStaysNormal() {
        startStation()
        main {
            controller.pause()
            val saved = controller.state.value.snapshot()!!
            val station = controller.station()!!
            val catalog = controller.state.value.queue
            controller.close(); controller = LocalPlaybackController(context)
            controller.restorePaused(saved, catalog, false)
            controller.restoreStation(station)
            val restored = controller.state.value
            assertTrue(restored.radioActive)
            assertFalse(restored.playWhenReady)
            assertEquals("Seed Song", restored.stationSeedTitle)
            assertEquals(StationOrigin.Similar, restored.origins["r1"])

            controller.close(); controller = LocalPlaybackController(context)
            controller.restorePaused(saved, catalog, false)
            controller.restoreStation(StationRecord(null, null, emptyMap(), listOf("other"), "seed"))
            assertFalse(controller.state.value.radioActive)
        }
    }
}
