package dev.radio.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.activity.compose.setContent
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.lifecycle.Lifecycle
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.LocalPlaybackController
import dev.radio.mobile.ui.RadioApp
import kotlinx.coroutines.runBlocking
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.*

class PlaybackUiTest {
    @get:Rule val compose = createEmptyComposeRule()
    @Test fun folderStartsRealAudioAndQueueIgnoresLaterSortChanges() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        prefs.edit().putString("opening", "Library").putString("sort", "ModifiedNewest").putString("backdrop", "Static").commit()
        val root = FixtureDocumentsProvider.tree()
        val uri = root.toString()
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "audio", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        fun song(id: String, title: String, folder: String, modified: Long) = LocalSong(id, uri, folder,
            DocumentsContract.buildDocumentUriUsingTree(root, id).toString(), "$title.mp3", folder, modified, 1, 1, 8000)
        runBlocking {
            db.library().saveRoot(LibraryRoot(uri = uri, folderId = "root", name = "Music", scannedAt = 1))
            db.library().saveFolders(listOf(LocalFolder("one", uri, "root", "First folder", "one", 2), LocalFolder("two", uri, "root", "Other folder", "two", 1)))
            db.library().saveSongs(listOf(song("long1", "First", "one", 2), song("long2", "Second", "one", 1), song("short1", "Other song", "two", 3)))
        }
        val repository = LibraryRepository(context, db)
        lateinit var playback: LocalPlaybackController
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity -> playback = LocalPlaybackController(context); activity.setContent { RadioApp(repository, playback) } }
            compose.waitUntil(5000) { !repository.state.value.loading }
            compose.onNodeWithText("First folder").performClick()
            compose.onNodeWithText("First").performClick()
            compose.waitUntil(10000) { playback.state.value.isPlaying }
            assertEquals(listOf("long1", "long2"), playback.state.value.queue.map { it.id })
            compose.onNodeWithContentDescription("Pause").assertIsDisplayed().performClick()
            compose.waitUntil { !playback.state.value.isPlaying }
            compose.onNodeWithTag("player-seek").performSemanticsAction(SemanticsActions.SetProgress) { it(3000f / 1000f) }
            compose.waitUntil { playback.state.value.positionMs >= 2950 }
            compose.onNodeWithText("0:03").assertIsDisplayed()
            compose.onNodeWithContentDescription("Queue").performClick()
            compose.onNodeWithTag("playback-queue").assertIsDisplayed()
            compose.onNodeWithText("First folder").assertIsDisplayed()
            compose.onNodeWithTag("player-transport").assertIsDisplayed()
            compose.onNodeWithText("0:03").assertIsDisplayed()
            compose.onNodeWithText("Other song").assertDoesNotExist()
            compose.onNodeWithText("Second").performClick()
            compose.waitUntil(10000) { playback.state.value.currentId == "long2" && playback.state.value.isPlaying }
            compose.onNodeWithText("2 / 2").assertIsDisplayed()
            compose.onNodeWithContentDescription("Pause").assertIsDisplayed()
            compose.onNodeWithContentDescription("Show artwork").performClick()
            compose.waitForIdle()
            val image = instrumentation.uiAutomation.takeScreenshot()
            val review = java.io.File(context.getExternalFilesDir(null), "playback-review").apply { mkdirs() }
            java.io.File(review, "actual-mp3-player.png").outputStream().use { image.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
            image.recycle()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithText("Date modified · newest").performClick()
            compose.onNodeWithText("Title · A–Z").performClick()
            assertEquals(listOf("long1", "long2"), playback.state.value.queue.map { it.id })
            compose.onNodeWithContentDescription("Search library").performClick()
            compose.onNodeWithTag("search-input").performTextInput("First")
            compose.onNode(hasText("First") and !hasSetTextAction()).performClick()
            compose.waitUntil(10000) { playback.state.value.currentId == "long1" && playback.state.value.isPlaying }
            assertEquals("Search results", playback.state.value.source)
            assertEquals(listOf("long1"), playback.state.value.queue.map { it.id })
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithTag("search-input").assertTextContains("First")
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithText("First folder").assertIsDisplayed()
            scenario.moveToState(Lifecycle.State.CREATED)
            assertTrue(playback.state.value.isPlaying)
            scenario.moveToState(Lifecycle.State.RESUMED)
            assertTrue(playback.state.value.playWhenReady)
        } finally {
            scenario.onActivity { playback.close() }
            scenario.close(); runBlocking { repository.close() }; db.close()
            runCatching { context.contentResolver.releasePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        }
    }
}
