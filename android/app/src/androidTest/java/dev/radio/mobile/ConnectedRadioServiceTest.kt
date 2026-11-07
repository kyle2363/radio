package dev.radio.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
import dev.radio.mobile.recommendation.ConnectedPolicy
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Service-owned refill with Connected Radio on, against a scripted desktop. */
class ConnectedRadioServiceTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var library: LibraryRepository
    private lateinit var playback: ServicePlaybackController
    private lateinit var songs: List<LocalSong>
    private lateinit var link: ScriptedConnectedLink
    private var scenario: ActivityScenario<MainActivity>? = null

    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun await(message: String, condition: () -> Boolean) {
        val until = android.os.SystemClock.elapsedRealtime() + 15000
        while (android.os.SystemClock.elapsedRealtime() < until) {
            var ready = false
            main { ready = condition() }
            if (ready) return
            Thread.sleep(40)
        }
        fail("$message: ${playback.state.value}")
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
            val source = DocumentsContract.buildDocumentUriUsingTree(root, sourceId).buildUpon().appendQueryParameter("radio_fixture", index.toString()).build().toString()
            LocalSong("radio-local-$index", root.toString(), "root", source, "Radio $index.mp3", "Music", 100L - index, 1, 1, 60000)
        }
        runBlocking {
            db.library().saveRoot(LibraryRoot(uri = root.toString(), folderId = "root", name = "Music", scannedAt = 1))
            db.library().saveSongs(songs)
        }
        link = ScriptedConnectedLink(ScriptedConnectedLink.serverIds(100).reversed())
        library = LibraryRepository(context, db) { link }
        main { RadioPlaybackService.repositoryFactory = { library } }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        await("Library didn't load") { !library.state.value.loading }
        main { playback = ServicePlaybackController(context); playback.playFrom(songs.take(3), songs[0].id, "Manual queue") }
        await("Manual queue didn't play") { playback.state.value.isPlaying }
        await("Radio preview and journal didn't become ready") {
            library.journal.state.value.feedbackLoaded && library.journal.state.value.pendingWrites == 0 &&
                library.radio.state.value.loaded && !library.radio.state.value.busy && library.radio.state.value.suggestions.size >= 5
        }
        ScriptedConnectedLink.publishMappings(songs)
    }

    @After fun cleanup() {
        RadioPlaybackService.beforeRadioRefillCommit = {}
        link.gate?.complete(Unit)
        main { playback.pause(); playback.close() }
        scenario?.close(); scenario = null
        context.stopService(Intent(context, RadioPlaybackService::class.java))
        instrumentation.waitForIdleSync()
        main { RadioPlaybackService.repositoryFactory = LibraryRepository::get }
        runBlocking { library.close() }
        db.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    private fun startRadioAndWaitForRequest() {
        library.radio.setConnectedEnabled(true)
        main { playback.startRadio() }
        await("Radio didn't install its queue") { playback.state.value.radioActive && playback.state.value.playOrder.size >= 5 && playback.state.value.isPlaying }
        await("Song start didn't ask the desktop") { link.count("/recommendations") >= 1 }
    }
    private fun advanceToLowWatermark() {
        repeat(2) {
            val before = playback.state.value.currentId
            main { playback.next() }
            await("Radio didn't advance") { playback.state.value.currentId != before }
        }
        val state = playback.state.value
        assertEquals("Two upcoming entries remain", 2, state.playOrder.size - state.playOrder.indexOf(state.currentId) - 1)
    }

    @Test fun refillWaitsForThePendingSetThenAppendsSimilarPicksFirst() {
        link.gate = CompletableDeferred()
        startRadioAndWaitForRequest()
        advanceToLowWatermark()
        val waiting = playback.state.value.playOrder
        await("A request for the current song should be pending") { library.radio.connected.status.value.pendingAnchor == playback.state.value.currentId }
        Thread.sleep(900)
        assertEquals("Refill waits while a set is pending and music remains", waiting, playback.state.value.playOrder)

        link.gate!!.complete(Unit)
        await("Set arrival didn't trigger the refill") { playback.state.value.playOrder.size > waiting.size }
        val after = playback.state.value
        val set = library.radio.connected.set.value!!
        val suffix = after.playOrder.drop(waiting.size)
        assertEquals(waiting, after.playOrder.take(waiting.size))
        assertTrue("Never beyond the 5-entry bound", after.playOrder.size - after.playOrder.indexOf(after.currentId) - 1 <= 5)
        assertEquals("Similar picks come first, in server order", set.continuation.map { it.song.id }.take(2), suffix.take(2))
        assertTrue(library.radio.connectedState.value.serverAiIds.containsAll(suffix.take(2)))
        assertTrue("Option slots are not auto-queued by the connected path", suffix.take(2).none { id -> set.options.any { it.song.id == id } })
        val options = ConnectedPolicy.visibleOptions(library.radio.connectedState.value, after.currentId, after.radioActive, after.playOrder.toSet(), library.radio::isDisliked) { true }
        assertEquals("The card has the three non-similar options", 3, options.size)
        assertEquals("Current song never changes", waiting[waiting.indexOf(after.currentId)], after.currentId)
    }

    @Test fun failedRequestEndsTheWaitAndRefillsLikeOffline() {
        link.gate = CompletableDeferred()
        startRadioAndWaitForRequest()
        advanceToLowWatermark()
        val waiting = playback.state.value.playOrder
        Thread.sleep(500)
        assertEquals(waiting, playback.state.value.playOrder)
        link.gate!!.completeExceptionally(ConnectionFailure("Cannot reach the server."))
        await("A failed request didn't release the refill") { playback.state.value.playOrder.size > waiting.size }
        assertTrue(library.radio.connectedState.value.serverAiIds.isEmpty())
        assertNotNull(library.radio.connected.status.value.lastFailure)
        assertNull(library.radio.connected.set.value)
    }

    @Test fun lastSongRefillsOfflineImmediatelyAndLateSetOnlyShowsOptions() {
        link.gate = CompletableDeferred()
        library.radio.setConnectedEnabled(true)
        // One song left in the queue: waiting would run the music dry.
        main { playback.startRadio() }
        await("Radio didn't install its queue") { playback.state.value.radioActive && playback.state.value.playOrder.size >= 5 }
        main { playback.state.value.playOrder.drop(1).forEach { playback.removeFromQueue(it) } }
        await("Radio refilled the emptied queue without waiting") { playback.state.value.playOrder.size > 1 }
        assertTrue(library.radio.connectedState.value.serverAiIds.isEmpty())
        link.gate!!.complete(Unit)
        await("Late set arrived") { library.radio.connected.set.value != null }
        // The late set never appends: the queue was already refilled.
        val order = playback.state.value.playOrder
        Thread.sleep(600)
        assertEquals(order, playback.state.value.playOrder)
        assertTrue(library.radio.connectedState.value.serverAiIds.isEmpty())
    }

    @Test fun manualQueuesNeverAskTheDesktop() {
        library.radio.setConnectedEnabled(true)
        main { playback.next() }
        await("Manual queue didn't advance") { playback.state.value.currentId == songs[1].id }
        Thread.sleep(600)
        assertEquals(0, link.posts.size)
        assertFalse(playback.state.value.radioActive)
    }

    @Test fun connectedOffBehavesLikeCheckpoint2B() {
        main { playback.startRadio() }
        await("Radio didn't install its queue") { playback.state.value.radioActive && playback.state.value.playOrder.size >= 5 }
        val start = playback.state.value.playOrder.size
        advanceToLowWatermark()
        await("Offline refill didn't run") { playback.state.value.playOrder.size > start }
        assertEquals(0, link.posts.size)
        assertTrue(library.radio.connectedState.value.serverAiIds.isEmpty())
    }

    @Test fun playNextOptionStaysRightAfterTheCurrentSongThroughALaterRefill() {
        startRadioAndWaitForRequest()
        await("Set for the current song didn't arrive") { library.radio.connected.set.value != null }
        // Pause so no refill runs, leave one upcoming entry, then choose an option: [current, option, kept].
        main { playback.pause() }
        await("Radio didn't pause") { !playback.state.value.playWhenReady }
        val kept = playback.state.value.playOrder.let { it[it.indexOf(playback.state.value.currentId) + 1] }
        main { playback.state.value.playOrder.drop(playback.state.value.playOrder.indexOf(playback.state.value.currentId) + 2).forEach { playback.removeFromQueue(it) } }
        val chosen = library.radio.connected.set.value!!.options.first().song
        main { playback.addToQueue(listOf(chosen), true) }
        val before = playback.state.value
        assertEquals(listOf(before.currentId, chosen.id, kept), before.playOrder.takeLast(3))
        // Resuming leaves two upcoming entries, so the service refills with the option still upcoming.
        main { playback.toggle() }
        await("Refill didn't run with the option still upcoming") { playback.state.value.playOrder.size > before.playOrder.size }
        val after = playback.state.value
        val current = after.playOrder.indexOf(after.currentId)
        assertEquals(before.currentId, after.currentId)
        assertEquals("Chosen option stays right after the current song", chosen.id, after.playOrder[current + 1])
        assertEquals("The earlier upcoming entry keeps its place behind it", kept, after.playOrder[current + 2])
        assertTrue("Refill appended behind them", after.playOrder.size - current - 1 > 2)
    }
}
