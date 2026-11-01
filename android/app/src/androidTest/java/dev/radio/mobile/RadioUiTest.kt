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
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class RadioUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun previewRefreshTuneAndExplicitStartPreserveManualQueueUntilConfirmed() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val shell = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        val previous = shell.all
        val radioPrefs = context.getSharedPreferences("radio_recommendations", Context.MODE_PRIVATE)
        val previousRadio = radioPrefs.all
        context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "reset", null, null)
        context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "grant", null, null)
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val library = LibraryRepository(context, db)
        val playback = FakePlaybackController()
        try {
            radioPrefs.edit().clear().putString("mood", "neutral").putFloat("exploration", .3f).apply()
            runBlocking { library.chooseFolder(FixtureDocumentsProvider.tree()).join() }
            val songs = library.state.value.songs
            assertTrue(songs.size >= 2)
            playback.playFrom(songs, songs.first().id, "Manual folder")
            playback.seek(3456)
            shell.edit().putString("opening", "Radio").putBoolean("minimal", false).putString("appearance", "Light").commit()
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                fun render() = scenario.onActivity { it.setContent { RadioApp(library, playback) } }
                fun capture(name: String) {
                    compose.waitForIdle()
                    val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
                    val dir = java.io.File(context.getExternalFilesDir(null), "checkpoint2b-review").apply { mkdirs() }
                    java.io.File(dir, name).outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
                    bitmap.recycle()
                }
                render()
                compose.waitUntil(10000) { library.radio.state.value.loaded && !library.radio.state.value.busy && library.radio.state.value.suggestions.isNotEmpty() }
                compose.onNodeWithTag("radio-screen").assertExists()
                assertTrue(library.radio.state.value.suggestions.size <= 5)
                capture("radio-light.png")
                val baseline = playback.state.value
                val generation = library.radio.state.value.generation
                compose.onNodeWithTag("refresh-radio").performClick()
                compose.waitUntil(10000) { library.radio.state.value.generation > generation && !library.radio.state.value.busy }
                assertEquals(baseline, playback.state.value)

                compose.onNodeWithTag("tune-radio").performClick()
                compose.onNodeWithTag("radio-mood-energetic").performClick()
                compose.waitUntil(10000) { library.radio.state.value.mood == "energetic" && !library.radio.state.value.busy }
                assertEquals("energetic", radioPrefs.getString("mood", null))
                assertEquals(baseline, playback.state.value)
                compose.onNodeWithText("Done").performClick()

                compose.onNodeWithTag("start-radio").performClick()
                compose.onNodeWithText("Start a new Radio queue?").assertExists()
                compose.onNodeWithText("Cancel").performClick()
                assertEquals(0, playback.startRadioCount)
                assertEquals(baseline, playback.state.value)
                compose.onNodeWithTag("start-radio").performClick()
                compose.onNodeWithTag("confirm-start-radio").performClick()
                assertEquals(1, playback.startRadioCount)
                assertEquals(baseline.currentId, playback.state.value.currentId)
                compose.onNodeWithTag("player-repeat").assertDoesNotExist()
                compose.onNodeWithContentDescription("Shuffle").assertDoesNotExist()
                compose.onNodeWithTag("vibe-lock").assertExists().assertIsNotEnabled()
                capture("radio-now-playing-light.png")
                val radioPlayback = playback.state.value
                compose.onNodeWithTag("tune-radio-player").performClick()
                assertEquals(radioPlayback, playback.state.value)
                compose.onNodeWithTag("radio-screen").assertExists()
                compose.onNodeWithTag("nav-Settings").performClick()
                compose.onNodeWithText("Dark").performClick()
                compose.onNodeWithTag("nav-Radio").performClick()
                capture("radio-dark.png")
                compose.onNodeWithText("Now playing · Radio").performClick()
                compose.waitForIdle()
                capture("radio-now-playing-dark.png")
                playback.stopRadio()
                compose.waitForIdle()
                compose.onNodeWithTag("player-repeat").assertExists()
                compose.onNodeWithContentDescription("Shuffle").assertExists()
                capture("standard-now-playing-dark.png")
            }
        } finally {
            runBlocking { library.close() }; db.close()
            shell.edit().clear().apply { previous.forEach { (key, value) -> when (value) {
                is String -> putString(key, value); is Boolean -> putBoolean(key, value); is Int -> putInt(key, value)
                is Long -> putLong(key, value); is Float -> putFloat(key, value)
            } } }.commit()
            radioPrefs.edit().clear().apply { previousRadio.forEach { (key, value) -> when (value) {
                is String -> putString(key, value); is Boolean -> putBoolean(key, value); is Int -> putInt(key, value)
                is Long -> putLong(key, value); is Float -> putFloat(key, value)
            } } }.commit()
        }
    }
}


