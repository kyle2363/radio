package dev.radio.mobile

import android.content.Context
import android.net.Uri
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.ui.RadioApp
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test

class ListeningFeedbackUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun feedbackPersistsAcrossRecreationWithoutChangingPlayback() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        val previous = prefs.all
        context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "reset", null, null)
        context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "grant", null, null)
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val library = LibraryRepository(context, db)
        val playback = FakePlaybackController()
        try {
            runBlocking { library.chooseFolder(FixtureDocumentsProvider.tree()).join() }
            val song = library.state.value.songs.first()
            playback.playFrom(library.state.value.songs, song.id, "Feedback fixture")
            playback.seek(12000)
            val before = playback.state.value
            prefs.edit().putString("opening", "Now playing").putBoolean("minimal", false).commit()
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                fun render() { scenario.onActivity { it.setContent { RadioApp(library, playback) } } }
                fun menu(tag: String) { compose.onNodeWithTag("player-options").performClick(); compose.onNodeWithTag(tag).performClick() }
                render()
                compose.waitUntil(5000) { library.journal.state.value.feedbackLoaded && library.journal.state.value.pendingWrites == 0 }
                compose.onNodeWithTag("favorite-song").performScrollTo().performClick()
                compose.waitUntil(5000) { library.journal.state.value.feedback[song.id]?.favorite == true }
                compose.onNodeWithTag("favorite-song").assertIsSelected().assertTextContains("Liked")
                compose.waitUntil(5000) { library.journal.state.value.pendingWrites == 0 }
                menu("dislike-song")
                compose.waitUntil(5000) { library.journal.state.value.feedback[song.id]?.disliked == true }
                compose.onNodeWithTag("favorite-song").assertIsNotSelected().assertTextContains("Like")
                compose.onNodeWithTag("player-options").performClick()
                compose.onNodeWithText("Remove dislike").assertExists()
                compose.onNodeWithText("Remove dislike").performClick()
                compose.waitUntil(5000) { library.journal.state.value.feedback[song.id]?.disliked == false }
                menu("dislike-song")
                compose.waitUntil(5000) { library.journal.state.value.feedback[song.id]?.disliked == true }
                scenario.recreate(); render()
                compose.onNodeWithTag("favorite-song").assertIsNotSelected()
                assertEquals(before, playback.state.value)
                compose.waitForIdle()
                val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
                val directory = java.io.File(context.getExternalFilesDir(null), "checkpoint2b-review").apply { mkdirs() }
                java.io.File(directory, "standard-now-playing.png").outputStream().use {
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
                }
                bitmap.recycle()
                menu("replay-song")
                assertEquals(1, playback.replayCount)
                compose.onNodeWithTag("player-options").performClick()
                compose.onNodeWithText("Skip reason").performClick()
                compose.onNodeWithText("Too slow").performClick()
                assertEquals("too_slow", playback.lastSkipReason)
            }
        } finally {
            runBlocking { library.close() }; db.close()
            prefs.edit().clear().apply { previous.forEach { (key, value) -> when (value) {
                is String -> putString(key, value); is Boolean -> putBoolean(key, value); is Int -> putInt(key, value)
                is Long -> putLong(key, value); is Float -> putFloat(key, value)
            } } }.commit()
        }
    }
}
