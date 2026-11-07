package dev.radio.mobile

import android.content.Context
import android.net.Uri
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*
import dev.radio.mobile.ui.*
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Checkpoint 4.2 Radio surfaces as components. */
class RadioRedesignUiTest {
    @get:Rule val compose = createComposeRule()

    private fun song(id: String) = LocalSong(id, "tree", "folder", "content://fixture/$id", "Song $id.mp3", "Music", 1, 1, 1, 200_000, true)
    private val queue = listOf("cur", "a", "b", "c").map { song(it) }
    private val onAir = PlaybackState(queue = queue, playOrder = queue.map { it.id }, currentId = "cur", radioActive = true, positionMs = 58_000, durationMs = 203_000,
        origins = mapOf("a" to StationOrigin.Similar, "b" to StationOrigin.Desktop, "c" to StationOrigin.User), stationSeedTitle = "Sunlight")

    @Test fun onAirCardShowsSeedTuningAndVibeState() {
        var radio by mutableStateOf(RadioState(loaded = true, mood = "discovery", exploration = .30, vibeAvailable = true))
        var vibeTaps = 0
        compose.setContent { OnAirStationCard(onAir, radio, onOpenPlayer = {}, onVibe = { vibeTaps++ }, onNewMix = {}, onEnd = {}) }
        compose.onNodeWithTag("station-seed").assertTextEquals("Station started from Sunlight")
        compose.onNodeWithTag("station-tuning-label").assertTextEquals("Discovery · Balanced")
        compose.onNodeWithTag("station-vibe").assertDoesNotExist()
        compose.onNodeWithTag("station-vibe-lock").assertIsEnabled().performClick()
        assertEquals(1, vibeTaps)
        radio = radio.copy(vibeLocalId = "cur", vibeSongsLeft = 6, vibeUntil = System.currentTimeMillis() + 24 * 60_000)
        compose.onNodeWithTag("station-vibe").assertIsDisplayed()
        compose.onNodeWithText("Release").assertIsDisplayed()
    }

    @Test fun pickForMeStationSaysSo() {
        compose.setContent { OnAirStationCard(onAir.copy(stationSeedTitle = null), RadioState(loaded = true), {}, {}, {}, {}) }
        compose.onNodeWithTag("station-seed").assertTextEquals("Station picked for you")
    }

    @Test fun comingUpShowsReasonsAndTags() {
        val removed = mutableListOf<String>()
        compose.setContent { ComingUp(onAir, onRemove = { removed += it }, onFullQueue = {}) }
        compose.onNodeWithTag("coming-up-a").assertTextContains("Similar sound", substring = true)
        compose.onNodeWithTag("coming-up-b").assertTextContains("Desktop", substring = true)
        compose.onNodeWithTag("coming-up-c").assertTextContains("Added by you", substring = true)
        compose.onNodeWithContentDescription("Remove Song a").performClick()
        assertEquals(listOf("a"), removed)
    }

    @Test fun addToStationSeparatesDesktopAndLocalRows() {
        val added = mutableListOf<String>()
        compose.setContent {
            AddToStation(listOf(StationAddRow(song("d"), "Explore", true), StationAddRow(song("l"), "Similar sound", false)), enabled = true, refreshing = false,
                onAdd = { added += it.song.id }, onRefresh = {})
        }
        compose.onNodeWithTag("station-add-d").assertTextContains("Desktop · Explore", substring = true)
        compose.onNodeWithTag("play-next-d").performClick()
        compose.onNodeWithTag("radio-add-l").performClick()
        assertEquals(listOf("d", "l"), added)
    }

    @Test fun endSheetOffersBothChoices() {
        val chosen = mutableListOf<String>()
        compose.setContent { EndStationSheet(3, onFinish = { chosen += "finish" }, onStopNow = { chosen += "stop" }, onDismiss = { chosen += "dismiss" }) }
        compose.onNodeWithText("The current song and the 3 coming up keep playing. Radio stops adding more.").assertIsDisplayed()
        compose.onNodeWithTag("end-finish-queued").performClick()
        compose.onNodeWithTag("end-stop-now").performClick()
        assertEquals(listOf("finish", "stop"), chosen)
    }

    @Test fun leaveRadioDialogReportsEachChoice() {
        val chosen = mutableListOf<String>()
        compose.setContent { LeaveRadioDialog("Sunlight", "NCS Songs", onLeave = { chosen += "leave" }, onPlayNext = { chosen += "next" }, onDismiss = { chosen += "cancel" }) }
        compose.onNodeWithText("Playing “Sunlight” starts a normal queue from NCS Songs and ends the station.", substring = true).assertIsDisplayed()
        compose.onNodeWithTag("leave-radio-play").performClick()
        compose.onNodeWithTag("leave-radio-play-next").performClick()
        compose.onNodeWithTag("leave-radio-cancel").performClick()
        assertEquals(listOf("leave", "next", "cancel"), chosen)
    }

    @Test fun desktopPicksShowEachConnectionState() {
        var ui by mutableStateOf(ConnectedUi())
        var paired by mutableStateOf(false)
        val toggles = mutableListOf<Boolean>()
        compose.setContent { DesktopPicksSection(ui, paired, ConnectedTuning(), onToggle = { toggles += it }, onChange = {}, onPair = {}) }
        fun state() = compose.onNodeWithTag("desktop-picks-state", useUnmergedTree = true)
        state().assertTextEquals("No desktop paired")
        compose.onNodeWithTag("desktop-picks-pair").assertIsDisplayed()
        compose.onNodeWithTag("desktop-picks-toggle").assertIsNotEnabled()
        paired = true
        state().assertTextEquals("Off")
        compose.onNodeWithTag("desktop-picks-toggle").performClick()
        assertEquals(listOf(true), toggles)
        ui = ConnectedUi(ConnectedStatus(enabled = true, permitted = false))
        state().assertTextEquals("Not allowed on desktop")
        ui = ConnectedUi(ConnectedStatus(enabled = true, permitted = true, lastOk = false))
        state().assertTextContains("Desktop didn't answer", substring = true)
        ui = ConnectedUi(ConnectedStatus(enabled = true, permitted = true, lastOk = true, tunable = true))
        state().assertTextEquals("Paired desktop · allowed")
        compose.onNodeWithTag("connected-tuning").assertExists()
    }

    @Test fun stationMiniPlayerShowsOnAir() {
        var skipped = 0
        compose.setContent { StationMiniPlayer(song("cur"), "On air · Discovery", playWhenReady = true, canPlay = true, canNext = true, onOpen = {}, onToggle = {}, onNext = { skipped++ }) }
        compose.onNodeWithText("On air · Discovery").assertIsDisplayed()
        compose.onNodeWithContentDescription("Pause").assertIsDisplayed()
        compose.onNodeWithTag("mini-next").performClick()
        assertEquals(1, skipped)
    }

    @Test fun fullQueueShowsStationReasons() {
        compose.setContent { IntegratedQueue(onAir, true, onSelect = {}) }
        compose.onNodeWithTag("queue-server-ai-b", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithTag("queue-user-c", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithTag("queue-row-a").assertTextContains("Similar sound", substring = true)
    }
}

/** Not for me on the real journal: dislikes, skips and can be undone. */
class RadioNotForMeUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun notForMeDislikesSkipsAndUndoRestores() {
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
            val songs = library.state.value.songs
            assertTrue(songs.size >= 2)
            playback.playFrom(songs, songs.first().id, "Radio")
            playback.startRadio(null)
            prefs.edit().putString("opening", "Now playing").putBoolean("minimal", false).commit()
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                scenario.onActivity { it.setContent { RadioApp(library, playback) } }
                compose.waitUntil(5000) { library.journal.state.value.feedbackLoaded && library.journal.state.value.pendingWrites == 0 }
                compose.onNodeWithTag("radio-now-playing").assertExists()
                compose.onNodeWithTag("not-for-me").performClick()
                compose.waitUntil(5000) { library.journal.state.value.feedback[songs.first().id]?.disliked == true }
                assertEquals(songs[1].id, playback.state.value.currentId)
                compose.onNodeWithText("Undo").performClick()
                compose.waitUntil(5000) { library.journal.state.value.feedback[songs.first().id]?.disliked == false }
                assertEquals(songs[1].id, playback.state.value.currentId)
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
