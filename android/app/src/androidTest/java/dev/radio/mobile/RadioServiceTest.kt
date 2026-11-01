package dev.radio.mobile

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
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

    @Before fun setup() {
        val root = FixtureDocumentsProvider.tree()
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "background", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        context.getSharedPreferences("radio_recommendations", Context.MODE_PRIVATE).edit().clear().commit()
        context.getSharedPreferences(PlaybackPrefs.FILE, Context.MODE_PRIVATE).edit().clear().commit()
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        songs = (0..6).map { index ->
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
        main { playback.startRadio() }
        await("Radio start didn't replace the manual queue with a playing Radio queue") {
            playback.state.value.radioActive && playback.state.value.isPlaying && playback.state.value.source == "Radio" &&
                playback.state.value.queue.size >= 5
        }
        await("Starting Radio didn't create one actual local playback start") { starts() == manualStarts + 1 }
        val radioState = playback.state.value
        assertNotEquals(manualQueue.playOrder, radioState.playOrder)
        assertEquals("Radio", radioState.source)

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
}
