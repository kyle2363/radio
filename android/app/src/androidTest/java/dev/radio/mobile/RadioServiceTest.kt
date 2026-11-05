package dev.radio.mobile

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.room.Room
import androidx.room.withTransaction
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
import dev.radio.mobile.portable.PortableSyncChanges
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.CompletableDeferred
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

class RadioServiceTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var library: LibraryRepository
    private lateinit var playback: ServicePlaybackController
    private lateinit var songs: List<LocalSong>
    private var scenario: ActivityScenario<MainActivity>? = null

    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun await(message: String, condition: () -> Boolean) {
        val until = android.os.SystemClock.elapsedRealtime() + 12000
        while (android.os.SystemClock.elapsedRealtime() < until) {
            var ready = false
            main { ready = condition() }
            if (ready) return
            Thread.sleep(40)
        }
        fail("$message: ${playback.state.value}")
    }
    private fun starts() = runBlocking { db.library().playbackStarts().size }
    private fun reservedSelections(): List<String> =
        org.json.JSONArray(context.getSharedPreferences("radio_recommendations", Context.MODE_PRIVATE)
            .getString("recentSelections", "[]")).let { rows ->
            (0 until rows.length()).map(rows::getString)
        }

    @Before fun setup() {
        val root = FixtureDocumentsProvider.tree()
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "background", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        context.getSharedPreferences("radio_recommendations", Context.MODE_PRIVATE).edit().clear().commit()
        context.getSharedPreferences(PlaybackPrefs.FILE, Context.MODE_PRIVATE).edit().clear().commit()
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        songs = (0..99).map { index ->
            val sourceId = if (index % 2 == 0) "background1" else "background2"
            val source = DocumentsContract.buildDocumentUriUsingTree(root, sourceId).buildUpon()
                .appendQueryParameter("radio_fixture", index.toString()).build().toString()
            LocalSong("radio-local-$index", root.toString(), "root", source, "Radio $index.mp3", "Music", 100L - index, 1, 1, 60000)
        }
        runBlocking {
            db.library().saveRoot(LibraryRoot(uri = root.toString(), folderId = "root", name = "Music", scannedAt = 1))
            db.library().saveSongs(songs)
        }
        library = LibraryRepository(context, db)
        main { RadioPlaybackService.repositoryFactory = { library } }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        await("Library didn't load") { !library.state.value.loading }
        main { playback = ServicePlaybackController(context); playback.playFrom(songs.take(3), songs[0].id, "Manual queue") }
        await("Manual queue didn't play") { playback.state.value.isPlaying && starts() >= 1 }
        await("Radio preview and journal didn't become ready") {
            library.journal.state.value.feedbackLoaded && library.journal.state.value.pendingWrites == 0 &&
                library.radio.state.value.loaded && !library.radio.state.value.busy && library.radio.state.value.suggestions.size >= 5
        }
    }

    @After fun cleanup() {
        RadioPlaybackService.beforeRadioStartCommit = {}
        RadioPlaybackService.beforeRadioRefillCommit = {}
        main { playback.pause(); playback.close() }
        scenario?.close(); scenario = null
        context.stopService(Intent(context, RadioPlaybackService::class.java))
        instrumentation.waitForIdleSync()
        main { RadioPlaybackService.repositoryFactory = LibraryRepository::get }
        runBlocking { library.close() }
        db.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    private fun pausedUnstartedQueue() {
        main { playback.pause();playback.state.value.queue.map{it.id}.forEach(playback::removeFromQueue);playback.addToQueue(songs.take(3)) }
        await("Paused unstarted queue wasn't installed") {playback.state.value.currentId!=null && !playback.state.value.playWhenReady && library.journal.state.value.pendingWrites==0}
    }
    @Test fun sharedProfileApplyFencesPendingStartAndPreservesPausedTransportAndTimer() = runBlocking {
        pausedUnstartedQueue()
        await("Paused timeline was not ready for seeking") { playback.state.value.seekable && playback.state.value.durationMs != null }
        main { playback.seek(5000); playback.sleepAtTrackEnd(); library.radio.setMood("focus") }
        await("Paused position and timer were not ready") {
            playback.state.value.positionMs in 4900..5200 && playback.state.value.sleepTimer.endOfTrack &&
                library.journal.state.value.pendingWrites == 0 && !library.radio.state.value.busy
        }
        val before = playback.state.value
        val startsBefore = starts()
        val entered = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        RadioPlaybackService.beforeRadioStartCommit = { entered.complete(Unit); release.await() }
        main { playback.startRadio() }
        await("Start batch did not reach its deferred commit") { entered.isCompleted }
        PortableSyncChanges.applying {
            db.withTransaction { db.journal().cursor(PortableSyncCursor("synthetic-sync", "phone", 1, System.currentTimeMillis())) }
            release.complete(Unit)
            kotlinx.coroutines.delay(350)
            assertEquals(before.playOrder, playback.state.value.playOrder)
            assertFalse(playback.state.value.radioActive)
            assertFalse(playback.state.value.playWhenReady)
        }
        await("Shared profile refresh did not settle") { !library.radio.state.value.busy }
        val after = playback.state.value
        assertEquals(before.currentId, after.currentId)
        assertEquals(before.positionMs, after.positionMs)
        assertEquals(before.playOrder, after.playOrder)
        assertEquals(before.queueRevision, after.queueRevision)
        assertEquals(before.sleepTimer, after.sleepTimer)
        assertEquals("focus", library.radio.state.value.mood)
        assertEquals(startsBefore, starts())
    }
    @Test fun delayedStartCannotOverrideSystemStop() {
        pausedUnstartedQueue()
        val entered=CompletableDeferred<Unit>();val release=CompletableDeferred<Unit>()
        RadioPlaybackService.beforeRadioStartCommit={entered.complete(Unit);release.await()}
        main{playback.startRadio()};await("Start batch wasn't deferred"){entered.isCompleted}
        lateinit var future:com.google.common.util.concurrent.ListenableFuture<MediaController>
        main{future=MediaController.Builder(context,SessionToken(context,ComponentName(context,RadioPlaybackService::class.java))).buildAsync()}
        val media=future.get(10,TimeUnit.SECONDS)
        try{
            val revision=playback.state.value.queueRevision
            main{media.stop()};await("System Stop didn't fence pending Start"){playback.state.value.queueRevision>revision}
            val before=playback.state.value
            release.complete(Unit);Thread.sleep(500)
            assertEquals(before.playOrder,playback.state.value.playOrder);assertFalse(playback.state.value.radioActive);assertFalse(playback.state.value.playWhenReady)
        }finally{release.complete(Unit);main{media.release()}}
    }
    @Test fun delayedStartCannotOverrideSleepExpiryWithoutAnAudibleAttempt() {
        pausedUnstartedQueue()
        val entered=CompletableDeferred<Unit>();val release=CompletableDeferred<Unit>()
        RadioPlaybackService.beforeRadioStartCommit={entered.complete(Unit);release.await()}
        main{playback.setSleepTimer(1500,false);playback.startRadio()}
        await("Start batch wasn't deferred"){entered.isCompleted}
        val revision=playback.state.value.queueRevision
        await("Sleep deadline didn't fence pending Start"){!playback.state.value.sleepTimer.active && playback.state.value.queueRevision>revision}
        val before=playback.state.value
        release.complete(Unit);Thread.sleep(500)
        assertEquals(before.playOrder,playback.state.value.playOrder);assertFalse(playback.state.value.radioActive);assertFalse(playback.state.value.playWhenReady)
    }

    @Test fun radioStartUsesServicePlayerAndRefillsWithoutChangingManualStateOnRefresh() {
        val manualStarts = starts()
        val manualQueue = playback.state.value
        val seed = library.state.value.songs.last().id
        main { playback.startRadio(seed) }
        await("Radio start didn't replace the manual queue with a playing Radio queue") {
            playback.state.value.radioActive && playback.state.value.isPlaying && playback.state.value.source == "Radio" &&
                playback.state.value.queue.size >= 5
        }
        await("Starting Radio didn't create one actual local playback start") { starts() == manualStarts + 1 }
        val radioState = playback.state.value
        assertNotEquals(manualQueue.playOrder, radioState.playOrder)
        assertEquals("Radio", radioState.source)
        assertEquals(seed, radioState.currentId)
        assertEquals(seed, radioState.playOrder.first())

        main { playback.pause(); playback.seek(5000) }
        await("Radio didn't pause at the requested position") {
            !playback.state.value.isPlaying && !playback.state.value.buffering && playback.state.value.positionMs in 4900..5200
        }
        val beforeRefresh = playback.state.value
        var generation = library.radio.state.value.generation
        main { library.radio.refresh() }
        await("Radio preview refresh didn't complete") { library.radio.state.value.generation > generation && !library.radio.state.value.busy }
        assertEquals(beforeRefresh, playback.state.value)
        generation = library.radio.state.value.generation
        main { library.radio.setMood("energetic") }
        await("Radio tune didn't update the future preview") {
            library.radio.state.value.mood == "energetic" && library.radio.state.value.generation > generation && !library.radio.state.value.busy
        }
        assertEquals(beforeRefresh, playback.state.value)

        val initialQueueSize = playback.state.value.playOrder.size
        main { playback.toggle(); playback.next() }
        await("Radio didn't advance to the next service-owned item") { playback.state.value.currentId != beforeRefresh.currentId && playback.state.value.isPlaying }
        main { playback.next() }
        await("Low-watermark service refill didn't append future Radio items") {
            playback.state.value.radioActive && playback.state.value.playOrder.size > initialQueueSize
        }
        assertTrue("Service refill remains bounded", playback.state.value.queue.size <= 16)

        main { playback.stopRadio() }
        await("Stop Radio didn't disable automatic refill") { !playback.state.value.radioActive }
        val stoppedOrder = playback.state.value.playOrder
        main { playback.next() }
        Thread.sleep(700)
        assertEquals(stoppedOrder, playback.state.value.playOrder)
    }

    @Test fun acceptedRefillsKeepManualItemsAndReserveOnlyQueueSelections() {
        main { playback.startRadio() }
        await("Radio didn't install its initial recommendation sequence") {
            playback.state.value.radioActive && playback.state.value.playOrder.size >= 5
        }
        val manualSong = songs.last { it.id !in playback.state.value.playOrder }
        val manualId = manualSong.id
        main { playback.addToQueue(listOf(manualSong)) }
        await("Manual item wasn't added to the active Radio queue") { manualId in playback.state.value.playOrder }

        val accepted = reservedSelections().toMutableSet()
        assertEquals("Initial reservation should match the Radio queue", 5, accepted.size)
        assertEquals(accepted, playback.state.value.playOrder.take(5).toSet())

        repeat(3) { refill ->
            val entered = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            RadioPlaybackService.beforeRadioRefillCommit = { entered.complete(Unit); release.await() }
            var advances = 0
            while (!entered.isCompleted && advances < 8) {
                val state = playback.state.value
                val current = state.playOrder.indexOf(state.currentId)
                if (current >= 0 && state.playOrder.size - current - 1 <= 2) {
                    await("Refill $refill didn't reach its commit barrier") { entered.isCompleted }
                    break
                }
                val oldId = state.currentId
                main { playback.next() }
                await("Radio didn't advance while approaching refill $refill") {
                    playback.state.value.currentId != oldId
                }
                advances++
            }
            assertTrue("Refill $refill reached its low-watermark within the navigation bound", entered.isCompleted)
            val acceptedBefore = accepted.toSet()
            release.complete(Unit)
            await("Refill $refill didn't reserve accepted additions") {
                reservedSelections().any { it !in acceptedBefore }
            }
            val after = playback.state.value
            assertTrue("Manual queue item must survive recommendation refill", manualId in after.playOrder)
            val newlyPresent = reservedSelections().filter { it !in acceptedBefore }
            assertTrue("Refill $refill appended recommendation IDs", newlyPresent.isNotEmpty())
            assertEquals("Refill IDs should be appended in one ordered suffix", newlyPresent,
                after.playOrder.takeLast(newlyPresent.size))
            newlyPresent.forEach { id ->
                assertFalse("Recommendation repeated across accepted batches: $id", id in accepted)
                accepted += id
            }
            assertEquals("Reservations should equal accepted Radio selections", accepted, reservedSelections().toSet())
        }
    }

    @Test fun pauseDuringRefillComputationFencesTheBatchAndItsReservations() {
        main { playback.startRadio() }
        await("Radio didn't install its initial recommendation sequence") {
            playback.state.value.radioActive && playback.state.value.playOrder.size >= 5
        }
        val acceptedBefore = reservedSelections().toSet()
        val entered = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        RadioPlaybackService.beforeRadioRefillCommit = { entered.complete(Unit); release.await() }
        var advances = 0
        while (!entered.isCompleted && advances < 8) {
            val state = playback.state.value
            val current = state.playOrder.indexOf(state.currentId)
            if (current >= 0 && state.playOrder.size - current - 1 <= 2) {
                await("Refill didn't reach its pre-commit suspension point") { entered.isCompleted }
                break
            }
            val oldId = state.currentId
            main { playback.next() }
            await("Radio didn't advance while approaching a deferred refill") {
                playback.state.value.currentId != oldId
            }
            advances++
        }
        assertTrue("Refill reached its low-watermark within the navigation bound", entered.isCompleted)
        val orderBeforePause = playback.state.value.playOrder
        main { playback.pause() }
        await("Pause didn't stop Radio playback") { !playback.state.value.playWhenReady }
        release.complete(Unit)
        // Let the service resume beyond the barrier and reject its stale queue snapshot.
        Thread.sleep(350)
        assertEquals("Rejected refill must leave the queue alone", orderBeforePause, playback.state.value.playOrder)
        assertEquals("Rejected refill must not reserve recommendations", acceptedBefore, reservedSelections().toSet())
        assertFalse(playback.state.value.playWhenReady)
    }
}
