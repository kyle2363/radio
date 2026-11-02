package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.LocalPlaybackController
import dev.radio.mobile.playback.snapshot
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class RadioQueueTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var controller: LocalPlaybackController
    private val starts = mutableListOf<String>()
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String, uriId: String = id) = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
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

    @Test fun manualQueueRejectsRadioAppendAndRemainsUnchanged() {
        val manual = listOf(song("long1"), song("long2"))
        main { controller.playFrom(manual, "long1", "Manual playlist") }
        await("Manual queue did not start") { controller.state.value.isPlaying }
        val before = controller.state.value
        main {
            assertFalse(controller.appendRadio(before, listOf(song("radio-1", "long2"))))
            val after = controller.state.value
            assertEquals(before.currentId, after.currentId)
            assertEquals(before.playOrder, after.playOrder)
            assertEquals(before.positionMs, after.positionMs)
            assertEquals(before.isPlaying, after.isPlaying)
            assertFalse(after.radioActive)
        }
    }

    @Test fun startingRadioDisablesExistingShuffleAndKeepsRecommendationOrder() {
        main { controller.playFrom(listOf(song("manual", "long1")), "manual", "Manual") }
        await("Manual queue did not start") { controller.state.value.isPlaying && starts.size == 1 }
        main { controller.shuffle(true) }
        await("Manual shuffle did not activate") { controller.state.value.shuffled }

        val recommendations = listOf(song("radio-a", "long1"), song("radio-b", "long2"), song("radio-c", "long1"))
        main { controller.beginRadio(recommendations) }
        await("Radio did not start its first recommendation") {
            controller.state.value.isPlaying && controller.state.value.currentId == "radio-a" && starts.size == 2
        }
        main {
            val started = controller.state.value
            assertFalse("Radio must start with shuffle disabled", started.shuffled)
            assertEquals(listOf("radio-a", "radio-b", "radio-c"), started.playOrder)

            assertTrue(controller.appendRadio(started, listOf(song("radio-d", "long2"))))
            val refilled = controller.state.value
            assertFalse("Radio refill must remain unshuffled", refilled.shuffled)
            assertEquals(listOf("radio-a", "radio-b", "radio-c", "radio-d"), refilled.playOrder)
            assertEquals("Refill must not manufacture an audible start", 2, starts.size)
        }
    }

    @Test fun appendPreservesActivePlaybackAndRejectsStaleSnapshotsAfterManualEdits() {
        main { controller.beginRadio(listOf(song("long1"), song("long2"))) }
        await("Radio queue did not start") { controller.state.value.isPlaying }
        main { controller.pause(); controller.seek(3000) }
        await("Radio queue did not pause at requested position") { !controller.state.value.isPlaying && controller.state.value.positionMs in 2950..3100 }
        var snapshot = controller.state.value
        main {
            assertTrue(controller.appendRadio(snapshot, listOf(song("radio-1", "long2"))))
            var after = controller.state.value
            assertEquals(snapshot.currentId, after.currentId)
            assertEquals(snapshot.positionMs, after.positionMs)
            assertEquals(snapshot.isPlaying, after.isPlaying)
            assertEquals(snapshot.playOrder + "radio-1", after.playOrder)
            assertTrue(after.radioActive)

            snapshot = after
            controller.moveInQueue("long2", 0)
            assertFalse(controller.appendRadio(snapshot, listOf(song("radio-2", "long2"))))
            snapshot = controller.state.value
            controller.clearUpcoming()
            assertFalse(controller.appendRadio(snapshot, listOf(song("radio-3", "long2"))))
            assertFalse(controller.state.value.radioActive)
        }
    }

    @Test fun radioRetiresPlayedPrefixWithinBoundAndCanReintroduceEarlierSong() {
        main { controller.beginRadio(listOf(song("long1"))) }
        await("Radio queue did not start") { controller.state.value.isPlaying }
        main {
            controller.pause()
            assertTrue(controller.appendRadio(controller.state.value, (1..5).map { song("radio-$it", "long2") }))
            repeat(15) { index ->
                controller.next()
                val state = controller.state.value
                assertTrue("queue grew beyond bounded timeline at step $index", state.queue.size <= 16)
                if (index < 14) {
                    val nextId = "later-${index + 1}"
                    assertTrue(controller.appendRadio(state, listOf(song(nextId, "long2"))))
                }
            }
            val state = controller.state.value
            assertTrue(state.radioActive)
            val priorId = "long1"
            assertTrue(priorId !in state.playOrder.drop(state.playOrder.indexOf(state.currentId) + 1))
            assertTrue("A played item can be recommended again after it leaves upcoming", controller.appendRadio(state, listOf(song(priorId))))
            assertTrue(controller.state.value.queue.size <= 16)
        }
    }
    @Test fun shuffledAppendRetainsCurrentPositionAndCommittedRemainingOrder() {
        val items=listOf(song("long1"))+(1..4).map{song("shuffle-$it","long2")}
        main{controller.beginRadio(items)}
        await("Radio didn't begin"){controller.state.value.isPlaying}
        main{controller.pause();controller.shuffle(true);repeat(3){controller.next()}}
        await("Shuffled item didn't become seekable"){!controller.state.value.buffering && controller.state.value.seekable}
        main{controller.seek(2300)}
        await("Shuffled Radio didn't settle"){!controller.state.value.isPlaying && !controller.state.value.buffering && controller.state.value.positionMs in 2250..2400}
        main{
            val before=controller.state.value;val remaining=before.playOrder.drop(before.playOrder.indexOf(before.currentId)+1)
            assertTrue(controller.appendRadio(before,listOf(song("long1"),song("shuffle-new","long2"))))
            val after=controller.state.value
            assertEquals(before.currentId,after.currentId);assertEquals(before.positionMs,after.positionMs);assertEquals(before.isPlaying,after.isPlaying)
            assertEquals(remaining,after.playOrder.drop(after.playOrder.indexOf(after.currentId)+1).take(remaining.size))
            assertTrue(after.shuffled);assertEquals(after.playOrder.size,after.playOrder.distinct().size)
        }
    }
    @Test fun radioBookmarkRestoresPausedWithoutAutomaticOwnership() {
        val items=listOf(song("long1"),song("long2"))
        main{controller.beginRadio(items)};await("Radio didn't begin"){controller.state.value.isPlaying}
        main{
            controller.pause();controller.seek(2000)
            val saved=controller.state.value.snapshot()!!
            controller.close();controller=LocalPlaybackController(context)
            controller.restorePaused(saved,items,false)
            val restored=controller.state.value
            assertFalse(restored.radioActive);assertFalse(restored.isPlaying);assertFalse(restored.playWhenReady)
            assertEquals(saved.currentId,restored.currentId);assertEquals(saved.positionMs,restored.positionMs)
        }
    }
}


