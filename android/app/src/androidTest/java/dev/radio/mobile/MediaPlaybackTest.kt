package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.LocalPlaybackController
import org.junit.*
import org.junit.Assert.*

class MediaPlaybackTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var controller: LocalPlaybackController
    private val starts = mutableListOf<Pair<String, String>>()
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String, duration: Long = 8000) = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
        DocumentsContract.buildDocumentUriUsingTree(FixtureDocumentsProvider.tree(), id).toString(), "$id.mp3", "", 1, 1, 1, duration)
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
        main { controller = LocalPlaybackController(context) { track, attempt, _ -> starts += track.id to attempt } }
    }
    @After fun cleanup() {
        main { controller.close() }; scenario.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }
    @Test fun realMp3PlayPauseSeekAndNextUseActualPlayerEvents() {
        val queue = listOf(song("long1"), song("long2"))
        main { controller.playFrom(queue, "long1", "Selected folder") }
        await("First MP3 didn't start") { controller.state.value.isPlaying && controller.state.value.positionMs > 100 }
        main { assertEquals(1, starts.size); controller.pause() }
        await("Didn't pause") { !controller.state.value.isPlaying && !controller.state.value.playWhenReady }
        main { controller.seek(3000) }
        await("Seek didn't update position") { controller.state.value.positionMs in 2950..3100 }
        Thread.sleep(200)
        main { assertTrue(controller.state.value.positionMs < 3100); assertEquals(1, starts.size); controller.toggle() }
        await("Didn't resume") { controller.state.value.isPlaying }
        main { assertEquals(1, starts.size); controller.next() }
        await("Next didn't start") { controller.state.value.currentId == "long2" && controller.state.value.isPlaying }
        main {
            assertEquals(2, starts.size)
            assertEquals(listOf("long1", "long2"), controller.state.value.queue.map { it.id })
            assertFalse(controller.state.value.canNext)
            controller.previous()
        }
        await("Previous didn't return to first") { controller.state.value.currentId == "long1" && controller.state.value.isPlaying }
        main {
            controller.shuffle(true)
            assertEquals(setOf("long1", "long2"), controller.state.value.playOrder.toSet())
            controller.reconcileLibrary(queue, accessLost = true)
            assertFalse(controller.state.value.isPlaying)
            assertNotNull(controller.state.value.error)
        }
    }
    @Test fun automaticAdvanceQueueEndAndReplayHaveDistinctActualStarts() {
        main { controller.playFrom(listOf(song("short1", 600), song("long2")), "short1", "Songs") }
        await("Short MP3 never started") { starts.any { it.first == "short1" } }
        await("Queue didn't automatically advance") { controller.state.value.currentId == "long2" && controller.state.value.isPlaying }
        main { assertEquals(2, starts.size); controller.seek(controller.state.value.durationMs!! - 100) }
        await("Queue didn't end") { controller.state.value.ended && !controller.state.value.isPlaying }
        main { assertEquals("long2", controller.state.value.currentId); assertEquals(2, starts.size); controller.toggle() }
        await("Replay didn't start") { controller.state.value.isPlaying && starts.size == 3 }
        main { assertEquals(3, starts.map { it.second }.distinct().size) }
    }
    @Test fun shuffleReactivationChangesOrderWithoutRestartingOrSeeking() {
        // Single/two-track queues have only one order with the current song pinned first.
        for (count in listOf(1, 2, 3, 12)) {
            val queue = List(count) { song("long1").copy(id = "shuffle$it") }
            val selected = queue[count / 2].id
            main { controller.playFrom(queue, selected, "Shuffle test") }
            await("Shuffle fixture didn't start") { controller.state.value.isPlaying }
            main { controller.pause(); controller.seek(3000) }
            await("Shuffle fixture didn't pause and seek") { !controller.state.value.isPlaying && controller.state.value.positionMs in 2950..3100 }
            var lastOrder: List<String>? = null
            var audibleStarts = 0
            main { audibleStarts = starts.size }
            repeat(6) {
                main { controller.shuffle(true) }
                await("Shuffle mode wasn't enabled") { controller.state.value.shuffled }
                main {
                    val state = controller.state.value
                    assertEquals(queue.map { it.id }.toSet(), state.playOrder.toSet())
                    assertEquals(count, state.playOrder.size)
                    if (count > 2) lastOrder?.let { assertNotEquals("Reactivating shuffle must change the upcoming order", it, state.playOrder) }
                    lastOrder = state.playOrder
                    assertEquals(selected, state.currentId)
                    assertEquals("Current song must start the new shuffle list", selected, state.playOrder.first())
                    assertTrue(state.positionMs in 2950..3100)
                    assertFalse(state.playWhenReady)
                    assertEquals(audibleStarts, starts.size)
                    controller.shuffle(false)
                    assertEquals(queue.map { it.id }, controller.state.value.playOrder)
                }
            }
            main { controller.shuffle(true); controller.jumpTo(controller.state.value.playOrder.first()) }
            await("Shuffled first song didn't start") { controller.state.value.isPlaying }
            main {
                controller.pause()
                val expectedOrder = controller.state.value.playOrder
                for (id in expectedOrder.drop(1)) {
                    controller.next()
                    assertEquals("Next must follow the displayed shuffle order", id, controller.state.value.currentId)
                }
                assertFalse(controller.state.value.canNext)
            }
        }
    }
    @Test fun decoderFailureDoesNotRecordAPlayAndNextCanRecover() {
        main { controller.playFrom(listOf(song("broken"), song("long1")), "broken", "Folder") }
        await("Invalid MP3 did not report an error") { controller.state.value.error != null }
        main { assertFalse(controller.state.value.isPlaying); assertTrue(starts.isEmpty()); controller.next() }
        await("Next failed to recover") { controller.state.value.currentId == "long1" && controller.state.value.isPlaying }
        main { assertEquals(listOf("long1"), starts.map { it.first }); assertNull(controller.state.value.error) }
    }
    @Test fun queueEditsPreserveAudioPositionAndManualShuffleOrder() {
        val songs = List(5) { song("long1").copy(id = "edit$it") }
        main { controller.playFrom(songs.take(4), "edit1", "Folder") }
        await("Editing fixture didn't play") { controller.state.value.isPlaying }
        main {
            controller.pause(); controller.seek(3000)
            controller.moveInQueue("edit3", 0)
            assertEquals(listOf("edit3", "edit0", "edit1", "edit2"), controller.state.value.playOrder)
            controller.addToQueue(listOf(songs[4], songs[0]), true)
            assertEquals(listOf("edit3", "edit1", "edit4", "edit0", "edit2"), controller.state.value.playOrder)
            assertEquals("edit1", controller.state.value.currentId)
            assertEquals(3000L, controller.state.value.positionMs)
            assertEquals(1, starts.size)
            controller.removeFromQueue("edit3")
            controller.shuffle(true)
            controller.moveInQueue("edit0", controller.state.value.playOrder.lastIndex)
            assertEquals("edit0", controller.state.value.playOrder.last())
            controller.addToQueue(listOf(songs[2]), true)
            assertEquals(listOf("edit1", "edit2"), controller.state.value.playOrder.take(2))
            assertEquals(4, controller.state.value.queue.size)
            assertEquals(3000L, controller.state.value.positionMs)
            controller.next()
            assertEquals("edit2", controller.state.value.currentId)
            assertFalse(controller.state.value.playWhenReady)
            assertEquals(1, starts.size)
            controller.toggle()
        }
        await("Edited queue didn't resume") { controller.state.value.currentId == "edit2" && controller.state.value.isPlaying && starts.size == 2 }
        var next = ""
        main { next = controller.state.value.playOrder[controller.state.value.playOrder.indexOf("edit2") + 1]; controller.removeFromQueue("edit2") }
        await("Removing current item didn't continue to the correct next track") { controller.state.value.currentId == next && controller.state.value.isPlaying && starts.size == 3 }
        main {
            controller.pause(); controller.clearUpcoming()
            assertFalse(controller.state.value.canNext)
            assertEquals(controller.state.value.currentId, controller.state.value.playOrder.last())
            controller.state.value.playOrder.toList().forEach(controller::removeFromQueue)
            assertTrue(controller.state.value.queue.isEmpty())
            assertNull(controller.state.value.currentId)
            assertFalse(controller.state.value.playWhenReady)
        }
    }
    @Test fun sleepTimerCanBeReplacedCancelledAndStopAtTrackEndWithoutStartingNext() {
        main { controller.playFrom(listOf(song("long1"), song("long2")), "long1", "Timer") }
        await("Timer fixture didn't play") { controller.state.value.isPlaying }
        main {
            controller.setSleepTimer(5000, false)
            controller.setSleepTimer(1000, true)
            assertTrue(controller.state.value.sleepTimer.active)
            assertTrue(controller.sessionPlayer.volume < 0.2f)
            controller.cancelSleepTimer()
            assertEquals(1f, controller.sessionPlayer.volume)
            assertTrue(controller.state.value.playWhenReady)
            controller.setSleepTimer(500, false)
        }
        await("Countdown didn't pause audio") { !controller.state.value.playWhenReady && !controller.state.value.sleepTimer.active }
        main { assertEquals(1, starts.size); assertEquals(1f, controller.sessionPlayer.volume) }
        var previousStarts = 0
        main {
            previousStarts = starts.size
            controller.playFrom(listOf(song("short1", 600), song("long2")), "short1", "End of song")
            controller.sleepAtTrackEnd()
        }
        await("Short song didn't start") { starts.size == previousStarts + 1 }
        await("End-of-song timer didn't stop") { !controller.state.value.playWhenReady && !controller.state.value.sleepTimer.active }
        main {
            assertEquals("short1", controller.state.value.currentId)
            assertEquals(previousStarts + 1, starts.size)
        }
    }
}
