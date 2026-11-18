package dev.radio.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Previews are never plays and leave the main queue, position and shuffle alone (checkpoint 9). */
class PreviewPlaybackTest {
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
        fail("$message: ${playback.state.value} / ${playback.preview.previewState.value}")
    }
    private fun starts() = runBlocking { db.library().playbackStarts().size }

    @Before fun setup() {
        val root = FixtureDocumentsProvider.tree()
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "background", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        context.getSharedPreferences(PlaybackPrefs.FILE, Context.MODE_PRIVATE).edit().clear().commit()
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        songs = (0..3).map { index ->
            val source = DocumentsContract.buildDocumentUriUsingTree(root, if (index % 2 == 0) "background1" else "background2")
                .buildUpon().appendQueryParameter("preview_fixture", index.toString()).build().toString()
            LocalSong("preview-local-$index", root.toString(), "root", source, "Preview $index.mp3", "Music", 10L - index, 1, 1, 60000)
        }
        runBlocking {
            db.library().saveRoot(LibraryRoot(uri = root.toString(), folderId = "root", name = "Music", scannedAt = 1))
            db.library().saveSongs(songs)
        }
        library = LibraryRepository(context, db)
        main { RadioPlaybackService.repositoryFactory = { library } }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        await("Library didn't load") { !library.state.value.loading }
        main { playback = ServicePlaybackController(context); playback.playFrom(songs.take(2), songs[0].id, "Manual queue") }
        await("Main queue didn't play") { playback.state.value.isPlaying && starts() >= 1 && library.journal.state.value.pendingWrites == 0 }
    }

    @After fun cleanup() {
        main { playback.preview.exitPreview(); playback.pause(); playback.close() }
        scenario?.close(); scenario = null
        context.stopService(Intent(context, RadioPlaybackService::class.java))
        instrumentation.waitForIdleSync()
        main { RadioPlaybackService.repositoryFactory = LibraryRepository::get }
        runBlocking { library.close() }
        db.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    @Test fun previewPausesMainRecordsNothingAndResumesOnExit() {
        val before = playback.state.value
        val startsBefore = starts()
        val historyBefore = library.journal.state.value.history.size
        main { playback.preview.enterPreview() }
        await("Main didn't pause") { !playback.state.value.playWhenReady }
        val pausedAt = playback.state.value.positionMs
        main { playback.preview.playPreview(songs[3]) }
        await("Preview didn't play") { (playback.preview.previewState.value as? PreviewState.Playing)?.let { it.songId == songs[3].id && it.positionMs > it.clipStartMs } == true }
        val clip = playback.preview.previewState.value as PreviewState.Playing
        assertEquals(PreviewPolicy.window(60000).startMs, clip.clipStartMs)
        Thread.sleep(1200)
        main { playback.preview.stopPreview() }
        await("Preview didn't stop") { playback.preview.previewState.value == PreviewState.Idle }
        Thread.sleep(500)
        assertEquals("A preview must never record a start", startsBefore, starts())
        assertEquals(historyBefore, library.journal.state.value.history.size)
        val during = playback.state.value
        assertEquals(before.currentId, during.currentId)
        assertEquals(before.playOrder, during.playOrder)
        assertEquals(before.shuffled, during.shuffled)
        assertEquals(pausedAt, during.positionMs)
        main { playback.preview.exitPreview() }
        await("Main didn't resume") { playback.state.value.isPlaying }
        assertEquals(before.currentId, playback.state.value.currentId)
    }

    @Test fun exitNeverResumesMusicThatWasPaused() {
        main { playback.pause() }
        await("Main didn't pause") { !playback.state.value.playWhenReady }
        main { playback.preview.enterPreview(); playback.preview.playPreview(songs[2], 0.5f) }
        await("Preview didn't play") { playback.preview.previewState.value is PreviewState.Playing }
        main { playback.preview.exitPreview() }
        Thread.sleep(800)
        assertFalse(playback.state.value.playWhenReady)
    }
}
