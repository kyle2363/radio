package dev.radio.mobile

import android.content.Context
import android.net.Uri
import androidx.activity.compose.setContent
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.compose.ui.semantics.SemanticsProperties
import dev.radio.mobile.ui.theme.Appearance
import dev.radio.mobile.ui.theme.RadioPalette
import dev.radio.mobile.ui.theme.RadioTheme
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.LyricsGateway
import dev.radio.mobile.data.LyricsRepository
import dev.radio.mobile.data.JournalIdentityProvider
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.data.LibraryDatabase
import dev.radio.mobile.data.LibraryRepository
import dev.radio.mobile.ui.LyricsSheet
import dev.radio.mobile.ui.LyricsSheetState
import dev.radio.mobile.ui.RadioApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LyricsUiTest {
    @get:Rule val compose = createComposeRule()

    private val song = LocalSong("song-1", "tree", "folder", "content://fixture/song-1", "A Long Song Title.mp3", "Music/Albums", 1, 1, 1, 200_000, true)

    @Test fun loadingKeepsHeadingAndRefreshOutsideLyricsScroller() {
        var refreshes = 0
        compose.setContent {
            LyricsSheet(song, LyricsSheetState(status = "LOADING", loading = true), onRefresh = { refreshes++ }, onDismiss = {}, onServer = {}, onMatching = {})
        }

        compose.onNodeWithTag("lyrics-title").assertTextEquals("Lyrics")
        compose.onNodeWithTag("lyrics-song-title").assertTextEquals("A Long Song Title")
        compose.onNodeWithTag("lyrics-folder").assertTextEquals("Music/Albums")
        compose.onNodeWithTag("lyrics-loading").assertIsDisplayed()
        compose.onNodeWithTag("lyrics-refresh").assertIsDisplayed().assertIsNotEnabled()
        compose.onNodeWithTag("lyrics-text").assertDoesNotExist()
        assertEquals(0, refreshes)
    }

    @Test fun readyLyricsRemainSelectableAndRefreshDoesNotChangeSong() {
        var refreshes = 0
        var palette by androidx.compose.runtime.mutableStateOf(RadioPalette.Ocean)
        var appearance by androidx.compose.runtime.mutableStateOf(Appearance.Light)
        compose.setContent {
            RadioTheme(palette, appearance) {
                LyricsSheet(song, LyricsSheetState(status = "READY", text = "Αθήνα 🌙\n\nFirst stanza\n\nSecond stanza", source = "From Desktop", canRefresh = true),
                    onRefresh = { refreshes++ }, onDismiss = {}, onServer = {}, onMatching = {})
            }
        }

        compose.onNodeWithTag("lyrics-source").assertTextEquals("From Desktop")
        compose.onNodeWithTag("lyrics-text").assertTextEquals("Αθήνα 🌙\n\nFirst stanza\n\nSecond stanza")
        compose.onNodeWithTag("lyrics-refresh").assertIsDisplayed().assertIsEnabled().performClick()
        assertEquals(1, refreshes)
        compose.onNodeWithTag("lyrics-song-title").assertTextEquals("A Long Song Title")
        if (InstrumentationRegistry.getArguments().getString("captureLyrics") == "true") {
            captureLyrics("lyrics-ocean-light.png")
            palette = RadioPalette.Teal
            appearance = Appearance.Dark
            compose.waitForIdle()
            captureLyrics("lyrics-teal-dark.png")
        }
    }

    private fun captureLyrics(name: String) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        val dir = java.io.File(context.getExternalFilesDir(null), "android-lyrics").apply { mkdirs() }
        val file = java.io.File(dir, name)
        file.outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
        android.util.Log.i("LyricsUiTest", "Saved review capture: ${file.absolutePath}")
    }

    @Test fun recoveryActionsRouteWithoutReplacingTheSheetContext() {
        var server = 0
        var matching = 0
        compose.setContent {
            LyricsSheet(song, LyricsSheetState(status = "UNMATCHED", message = "Match this song to get desktop lyrics.", action = "MATCHING"),
                onRefresh = {}, onDismiss = {}, onServer = { server++ }, onMatching = { matching++ })
        }
        compose.onNodeWithTag("lyrics-message").assertTextEquals("Match this song to get desktop lyrics.")
        compose.onNodeWithTag("lyrics-matching").performClick()
        assertEquals(1, matching)
        assertEquals(0, server)
        compose.onNodeWithTag("lyrics-song-title").assertTextEquals("A Long Song Title")
    }

    @Test fun longLyricsScrollAtLargeFontAndResetForTheNextSongWhileRefreshStaysVisible() {
        var activeSong by androidx.compose.runtime.mutableStateOf(song)
        var text by androidx.compose.runtime.mutableStateOf((0..80).joinToString("\n\n") { index ->
            if (index == 0) "Αθήνα 🌙" else "Verse $index — Καληνύχτα"
        })
        compose.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 1.5f)) {
                RadioTheme(RadioPalette.Ocean, Appearance.Light) {
                    LyricsSheet(activeSong, LyricsSheetState(status = "READY", text = text, source = "From Desktop", canRefresh = true),
                        onRefresh = {}, onDismiss = {}, onServer = {}, onMatching = {})
                }
            }
        }

        compose.onNodeWithTag("lyrics-refresh").assertIsDisplayed()
        compose.onNodeWithTag("lyrics-selection").performTouchInput { swipeUp() }
        compose.onNodeWithTag("lyrics-refresh").assertIsDisplayed()
        val scrolled = compose.onNodeWithTag("lyrics-scroll").fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        assertTrue("long lyrics should scroll", scrolled > 0f)

        activeSong = song.copy(id = "song-2", fileName = "Next Song.mp3")
        text = "Second song lyrics\n\nA clean opening stanza"
        compose.waitForIdle()
        compose.onNodeWithTag("lyrics-song-title").assertTextEquals("Next Song")
        compose.onNodeWithTag("lyrics-text").assertTextEquals(text)
        val reset = compose.onNodeWithTag("lyrics-scroll").fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        assertEquals("song changes reset lyrics to the first stanza", 0f, reset, .01f)
        compose.onNodeWithTag("lyrics-refresh").assertIsDisplayed()
    }
}

class LyricsRadioAppUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun lyricsEntryWorksOnNormalRadioQueueAndMinimalWithoutChangingPlaybackOrJournal() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        val previousPrefs = prefs.all
        context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "reset", null, null)
        context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "grant", null, null)
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val library = LibraryRepository(context, db)
        val playback = FakePlaybackController()
        val mapping = MutableStateFlow(JournalIdentityProvider.MappingSnapshot(loaded = true))
        val lyricsScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val gateway = object : LyricsGateway {
            override suspend fun supportsLyrics() = error("unpaired sheet must not query capability")
            override suspend fun get(path: String): RadioResponse = error("unpaired sheet must not request lyrics")
        }
        val lyrics = LyricsRepository(db, gateway, paired = { false }, mappings = { mapping.value },
            mappingFlow = mapping, awaitMappings = { mapping.value }, scope = lyricsScope)
        try {
            prefs.edit().putString("opening", "Now playing").putBoolean("minimal", false).commit()
            runBlocking { library.chooseFolder(FixtureDocumentsProvider.tree()).join() }
            val songs = library.state.value.songs
            assertTrue(songs.size >= 2)
            playback.playFrom(songs, songs.first().id, "Test queue")
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                scenario.onActivity { it.setContent { RadioApp(library, playback, lyricsRepositoryOverride = lyrics) } }
                compose.waitUntil(10_000) { library.journal.state.value.feedbackLoaded }
                val normal = playback.state.value
                val starts = runBlocking { db.library().playbackStarts() }

                fun openAndCloseLyrics() {
                    compose.onNodeWithTag("player-options").performClick()
                    compose.onNodeWithTag("open-lyrics").performClick()
                    compose.waitUntil(5_000) { compose.onAllNodesWithTag("lyrics-title").fetchSemanticsNodes().isNotEmpty() }
                    compose.onNodeWithTag("lyrics-song-title").assertTextEquals(songs.first().title)
                    compose.waitUntil(5_000) { compose.onAllNodesWithText("Connect to your desktop to get lyrics.").fetchSemanticsNodes().isNotEmpty() }
                    compose.onNodeWithText("Connect to your desktop to get lyrics.").assertExists()
                    compose.onNodeWithTag("lyrics-close").performClick()
                    compose.waitUntil(5_000) { compose.onAllNodesWithTag("lyrics-title").fetchSemanticsNodes().isEmpty() }
                }

                openAndCloseLyrics()
                assertEquals(normal, playback.state.value)

                compose.onNodeWithContentDescription("Queue").performClick()
                compose.onNodeWithTag("queue-position").assertExists()
                openAndCloseLyrics()
                assertEquals(normal, playback.state.value)

                compose.onNodeWithContentDescription("Show artwork").performClick()
                compose.onNodeWithContentDescription("Minimal mode").performClick()
                openAndCloseLyrics()
                assertEquals(normal, playback.state.value)
                compose.onNodeWithContentDescription("Full controls").performClick()

                playback.startRadio(null)
                val radio = playback.state.value
                openAndCloseLyrics()
                assertEquals(radio, playback.state.value)
                compose.onNodeWithTag("player-options").performClick()
                compose.onNodeWithTag("show-queue").performClick()
                compose.onNodeWithTag("queue-position").assertExists()
                openAndCloseLyrics()
                assertEquals(radio, playback.state.value)
                compose.onNodeWithTag("player-options").performClick()
                compose.onNodeWithTag("show-queue").performClick()
                compose.onNodeWithTag("player-options").performClick()
                compose.onNodeWithText("Minimal view").performClick()
                openAndCloseLyrics()
                assertEquals(radio, playback.state.value)
                assertEquals(1, playback.startRadioCount)
                assertEquals(starts, runBlocking { db.library().playbackStarts() })
            }
        } finally {
            lyrics.close()
            lyricsScope.cancel()
            runBlocking { library.close() }
            db.close()
            prefs.edit().clear().apply { previousPrefs.forEach { (key, value) -> when (value) {
                is String -> putString(key, value); is Boolean -> putBoolean(key, value); is Int -> putInt(key, value)
                is Long -> putLong(key, value); is Float -> putFloat(key, value)
            } } }.commit()
        }
    }
}
