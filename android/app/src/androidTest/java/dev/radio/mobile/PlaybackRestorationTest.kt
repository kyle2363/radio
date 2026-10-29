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
import org.json.JSONArray
import org.json.JSONObject
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TestName
import org.junit.runners.MethodSorters
import java.io.File

/** Ordered halves can also be run separately with adb force-stop between them. */
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class PlaybackRestorationTest {
    @get:Rule val testName = TestName()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val directory get() = File(context.cacheDir, ".test-runtime/playback-restoration").apply { mkdirs() }
    private lateinit var db: LibraryDatabase
    private lateinit var library: LibraryRepository
    private var activity: ActivityScenario<MainActivity>? = null
    private lateinit var playback: ServicePlaybackController
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun await(message: String, condition: () -> Boolean) {
        val until = android.os.SystemClock.elapsedRealtime() + 10000
        while (android.os.SystemClock.elapsedRealtime() < until) {
            var ready = false; main { ready = condition() }
            if (ready) return
            Thread.sleep(30)
        }
        fail(message + ": " + playback.state.value)
    }
    private fun starts() = runBlocking { db.library().playbackStarts().size }
    @Before fun setup() {
        val root = FixtureDocumentsProvider.tree()
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "background", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val file = File(directory, "cold-start.db")
        val seed = testName.methodName.startsWith("aSave")
        if (seed) context.deleteDatabase(file.absolutePath)
        else assertTrue("Run the save half first", file.exists())
        db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath)
            .addMigrations(LIBRARY_MIGRATION_1_2, LIBRARY_MIGRATION_2_3, LIBRARY_MIGRATION_3_4).build()
        if (seed) runBlocking {
            db.library().saveRoot(LibraryRoot(uri = root.toString(), folderId = "root", name = "Music", scannedAt = 1))
            db.library().saveSongs((1..3).map { i -> LocalSong("background$i", root.toString(), "root",
                DocumentsContract.buildDocumentUriUsingTree(root, "background$i").toString(),
                "Background $i.mp3", "Music", i.toLong(), 1, 1, 60000) })
        }
        library = LibraryRepository(context, db)
        main { RadioPlaybackService.repositoryFactory = { library } }
        context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE).edit().putString("backdrop", "Static").commit()
        activity = ActivityScenario.launch(MainActivity::class.java)
        main { playback = ServicePlaybackController(context) }
        await("Catalog didn't load") { !library.state.value.loading }
    }
    @After fun cleanup() {
        main { playback.pause(); playback.close() }
        activity?.close(); activity = null
        context.stopService(Intent(context, RadioPlaybackService::class.java))
        instrumentation.waitForIdleSync()
        runBlocking { library.playbackSnapshots.load() } // Drain final service checkpoints to disk.
        main { RadioPlaybackService.repositoryFactory = LibraryRepository::get }
        runBlocking { library.close() }
        db.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }
    @Test fun aSaveShuffledQueueAfterAdvancingAndSeeking() {
        val songs = library.state.value.songs.sortedBy { it.id }
        main { playback.playFrom(songs, songs.first().id, "Restoration folder") }
        await("First song didn't play") { playback.state.value.isPlaying && starts() == 1 }
        var selected = ""
        main { playback.shuffle(true); selected = playback.state.value.playOrder[1]; playback.jumpTo(selected) }
        await("Second song didn't play") { playback.state.value.currentId == selected && playback.state.value.isPlaying && starts() == 2 }
        main { playback.setSleepTimer(600000, false); playback.pause(); playback.seek(12345) }
        await("Pause/seek wasn't applied") { !playback.state.value.playWhenReady && playback.state.value.positionMs == 12345L }
        val saved = runBlocking { library.playbackSnapshots.load() }!!
        assertEquals(12345L, saved.positionMs)
        assertEquals(selected, saved.currentId)
        assertNotEquals(saved.currentId, saved.playOrder.first())
        val expected = JSONObject().put("queue", JSONArray(saved.queueIds)).put("order", JSONArray(saved.playOrder))
            .put("current", saved.currentId).put("positionMs", saved.positionMs).put("source", saved.source)
        File(directory, "expected.json").writeText(expected.toString())
    }
    @Test fun bFreshServiceRestoresExactQueuePausedAndOnlyExplicitPlayRecordsStart() {
        val json = JSONObject(File(directory, "expected.json").readText())
        fun ids(key: String) = json.getJSONArray(key).let { array -> List(array.length()) { array.getString(it) } }
        val current = json.getString("current")
        await("Saved session didn't restore") { playback.state.value.currentId == current }
        val restored = playback.state.value
        assertEquals(ids("queue"), restored.queue.map { it.id })
        assertEquals(ids("order"), restored.playOrder)
        assertEquals(json.getString("source"), restored.source)
        assertEquals(json.getLong("positionMs"), restored.positionMs)
        assertTrue(restored.shuffled)
        assertTrue(restored.seekable)
        assertFalse(restored.isPlaying)
        assertFalse(restored.playWhenReady)
        assertFalse(restored.buffering)
        assertFalse("A restored session must not revive the old sleep timer", restored.sleepTimer.active)
        Thread.sleep(250)
        assertEquals("Restoration must not record a play", 2, starts())
        main { playback.seek(8000); playback.toggle() }
        await("Explicit Play didn't resume at the restored seek position") { playback.state.value.isPlaying && playback.state.value.positionMs >= 8000 && starts() == 3 }
        val next = ids("order")[ids("order").indexOf(current) + 1]
        main { playback.next() }
        await("Next didn't follow the restored shuffle order") { playback.state.value.currentId == next && playback.state.value.isPlaying && starts() == 4 }
    }
}
