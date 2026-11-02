package dev.radio.mobile

import android.content.Context
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.ui.RadioContent
import dev.radio.mobile.recommendation.RadioDiagnostics
import dev.radio.mobile.recommendation.RadioRepository
import dev.radio.mobile.recommendation.RadioState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import org.junit.Rule
import org.junit.Test

class RadioPersonalizationUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun coldStartPromptSearchCancelAndRecommendationDetailsAreAvailable() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefs = context.getSharedPreferences("radio_recommendations", Context.MODE_PRIVATE)
        val previous = prefs.all
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val songs = listOf(
            LocalSong("seed-one", "root", "folder", "content://fixture/one", "Amber Lights.mp3", "Music", 1, 1, 1, 10000),
            LocalSong("seed-two", "root", "folder", "content://fixture/two", "Quiet Shore.mp3", "Music", 1, 1, 1, 10000),
        )
        val libraryState = LibraryState(songs = songs, loading = false)
        val local = kotlinx.coroutines.flow.MutableStateFlow(libraryState)
        val journal = ListeningJournalRepository(context, db, scope)
        val repository = RadioRepository(context, db, local, journal, scope)
        val radio = RadioState(
            loaded = true,
            status = "Local recommendations · match songs to use imported metadata",
            diagnostics = RadioDiagnostics(
                eligibleTracks = 2,
                textVectorTracks = 1,
                audioVectorTracks = 2,
                tasteEvents = 0,
                contributingTasteEvents = 0,
                hasUsableTaste = false,
            ),
            bootstrapCandidates = songs.map { it.id }.toSet(),
        )
        try {
            fun capture(name:String) {
                compose.waitForIdle()
                // Semantics can settle before the emulator compositor draws the
                // replacement activity content or finishes a dialog animation.
                InstrumentationRegistry.getInstrumentation().waitForIdleSync()
                Thread.sleep(350)
                val bitmap=InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
                val directory=java.io.File(context.getExternalFilesDir(null),"recommendation-review").apply{mkdirs()}
                java.io.File(directory,name).outputStream().use{bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it)}
                bitmap.recycle()
            }
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                scenario.onActivity { activity ->
                    activity.setContent {
                        RadioContent(
                            radio = radio,
                            repository = repository,
                            audio = PlaybackState(),
                            playback = FakePlaybackController(),
                            library = libraryState,
                            onLibrary = {},
                            onPlayer = {},
                        )
                    }
                }

                compose.onNodeWithTag("choose-bootstrap-songs").assertIsDisplayed()
                capture("bootstrap-prompt.png")
                compose.onNodeWithTag("choose-bootstrap-songs").assertIsDisplayed().performClick()
                capture("bootstrap-picker.png")
                compose.onNodeWithTag("bootstrap-picker-search").performTextInput("quiet")
                compose.onNodeWithTag("bootstrap-song-seed-two").assertIsDisplayed()
                compose.onNodeWithTag("bootstrap-song-seed-one").assertDoesNotExist()
                compose.onNodeWithTag("cancel-bootstrap").performClick()
                compose.onNodeWithTag("choose-bootstrap-songs").assertIsDisplayed()
                compose.onNodeWithText("Eligible tracks · 2").assertDoesNotExist()

                compose.onNodeWithTag("choose-bootstrap-songs").performClick()
                compose.onNodeWithTag("bootstrap-song-seed-one").performClick()
                compose.onNodeWithTag("bootstrap-song-seed-one").performClick()
                compose.onNodeWithTag("apply-bootstrap").performClick()
                compose.onNodeWithTag("choose-bootstrap-songs").assertIsDisplayed()

                compose.onNodeWithTag("recommendation-details").assertIsDisplayed().performClick()
                compose.onNodeWithText("Eligible tracks · 2").assertIsDisplayed()
                compose.onNodeWithText("Matched tracks · 0").assertIsDisplayed()
                compose.onNodeWithText("Local-only tracks · 0").assertIsDisplayed()
                compose.onNodeWithText("Tracks with usable text vectors · 1").assertIsDisplayed()
                compose.onNodeWithText("Tracks with usable audio vectors · 2").assertIsDisplayed()
                compose.onNodeWithText("Taste events · 0 (0 contributing)").assertIsDisplayed()
                capture("recommendation-details.png")

                scenario.onActivity { activity ->
                    activity.setContent {
                        RadioContent(
                            radio = radio.copy(
                                diagnostics = radio.diagnostics.copy(textVectorTracks = 0, audioVectorTracks = 0),
                                bootstrapCandidates = emptySet(),
                            ),
                            repository = repository,
                            audio = PlaybackState(),
                            playback = FakePlaybackController(),
                            library = libraryState,
                            onLibrary = {},
                            onPlayer = {},
                        )
                    }
                }
                compose.onNodeWithTag("choose-bootstrap-songs").assertDoesNotExist()

                scenario.onActivity { activity ->
                    activity.setContent {
                        RadioContent(
                            radio = radio.copy(
                                diagnostics = radio.diagnostics.copy(hasUsableTaste = true),
                            ),
                            repository = repository,
                            audio = PlaybackState(),
                            playback = FakePlaybackController(),
                            library = libraryState,
                            onLibrary = {},
                            onPlayer = {},
                        )
                    }
                }
                compose.onNodeWithTag("choose-bootstrap-songs").assertDoesNotExist()

                scenario.onActivity { activity ->
                    activity.setContent {
                        RadioContent(
                            radio = radio.copy(bootstrapLocalIds = setOf("seed-one"), bootstrapDismissed = true),
                            repository = repository,
                            audio = PlaybackState(),
                            playback = FakePlaybackController(),
                            library = libraryState,
                            onLibrary = {},
                            onPlayer = {},
                        )
                    }
                }
                compose.onNodeWithTag("clear-bootstrap").assertIsDisplayed()
                compose.onNodeWithTag("choose-bootstrap-songs").assertDoesNotExist()

                scenario.onActivity { activity ->
                    activity.setContent {
                        RadioContent(
                            radio = radio.copy(legacyVectorTracks = 0),
                            repository = repository,
                            audio = PlaybackState(),
                            playback = FakePlaybackController(),
                            library = libraryState,
                            onLibrary = {},
                            onPlayer = {},
                        )
                    }
                }
                compose.onNodeWithTag("use-desktop-similarity").assertDoesNotExist()

                // The opt-in belongs to Settings, never the Radio screen.
                scenario.onActivity { activity ->
                    activity.setContent {
                        RadioContent(radio.copy(legacyVectorTracks=3),repository,PlaybackState(),FakePlaybackController(),libraryState,{},{})
                    }
                }
                compose.onNodeWithTag("use-desktop-similarity").assertDoesNotExist()
                scenario.onActivity { activity ->
                    activity.setContent {
                        dev.radio.mobile.ui.RadioSimilaritySettings(radio.copy(legacyVectorTracks=3),repository)
                    }
                }
                compose.onNodeWithText("Legacy desktop similarity").assertIsDisplayed()
                compose.onNodeWithTag("use-desktop-similarity").assertIsDisplayed().performClick()
                capture("legacy-desktop-settings-disabled.png")
                scenario.onActivity { activity ->
                    activity.setContent {
                        dev.radio.mobile.ui.RadioSimilaritySettings(radio.copy(legacyVectorTracks=3,legacyVectorsEnabled=true),repository)
                    }
                }
                compose.onNodeWithText("Stop using legacy vectors").assertIsDisplayed()
                capture("legacy-desktop-settings-enabled.png")
            }
        } finally {
            runBlocking { scope.coroutineContext[Job]?.cancelAndJoin() }
            db.close()
            prefs.edit().clear().apply {
                previous.forEach { (key, value) -> when (value) {
                    is String -> putString(key, value)
                    is Boolean -> putBoolean(key, value)
                    is Int -> putInt(key, value)
                    is Long -> putLong(key, value)
                    is Float -> putFloat(key, value)
                } }
            }.commit()
        }
    }
}
