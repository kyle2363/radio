package dev.radio.mobile

import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.PreviewController
import dev.radio.mobile.playback.PreviewState
import dev.radio.mobile.ui.FeelSheet
import dev.radio.mobile.ui.FeelsNav
import dev.radio.mobile.ui.FeelsScreen
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** Checkpoint 9B: Feels setup, list, sprint (with preview enter/exit) and the Now Playing sheet. */
class FeelsUiTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var scope: CoroutineScope
    private lateinit var feels: FeelRepository
    private val tracks = List(4) { LocalSong("song$it", "tree", "folder", "content://fixture/$it", "Artist - Song $it.mp3", "", 1, 1, 1, 60000) }

    private class FakePreview : PreviewController {
        val log = mutableListOf<String>()
        override val previewState: StateFlow<PreviewState> = MutableStateFlow(PreviewState.Idle)
        override fun enterPreview() { log += "enter" }
        override fun playPreview(song: LocalSong, startFraction: Float) { log += "play:${song.id}" }
        override fun stopPreview() { log += "stop" }
        override fun exitPreview() { log += "exit" }
    }

    @Before fun open() = runBlocking {
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        db.library().saveSongs(tracks)
        feels = FeelRepository(db, scope, identityOf = { JournalIdentity("device", localId = it.id) }, emitEnergy = { _, _ -> })
    }
    @After fun close() = runBlocking { scope.coroutineContext[Job]?.cancelAndJoin(); db.close() }

    @Composable private fun Screen(preview: PreviewController?, nav: MutableState<FeelsNav>) {
        val state by feels.state.collectAsState()
        FeelsScreen(feels, state, tracks, nav.value, { nav.value = it }, preview, sessions = { emptyList() }, onMessage = {})
    }

    @Test fun setupThenListThenSprintTagsAndExitsPreview() {
        val preview = FakePreview()
        val nav = mutableStateOf(FeelsNav())
        var shown by mutableStateOf(true)
        compose.setContent { if (shown) Screen(preview, nav) }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("feel-setup-done").fetchSemanticsNodes().isNotEmpty() }
        // Keep only Hype.
        dev.radio.mobile.data.FeelCatalog.starters.filter { it.key != "hype" }.forEach {
            compose.onNodeWithTag("feel-setup").performScrollToNode(hasTestTag("starter-${it.key}"))
            compose.onNodeWithTag("starter-${it.key}").performClick()
        }
        compose.onNodeWithText("Keep 1 feels").performClick()
        compose.waitUntil(5000) { compose.onAllNodesWithTag("feel-Hype").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("feel-Energy").assertExists()
        compose.onNodeWithTag("feel-Hype").performClick()
        compose.onNodeWithTag("start-sprint").performClick()
        compose.waitForIdle()
        assertEquals("enter", preview.log.first())
        compose.onNodeWithTag("preview-toggle").performClick()
        assertTrue(preview.log.any { it.startsWith("play:") })
        compose.onNodeWithTag("sprint-yes").performClick()
        compose.onNodeWithTag("sprint-no").performClick()
        compose.onNodeWithTag("sprint-skip").performClick()
        compose.onNodeWithTag("sprint-yes").performClick()
        compose.waitUntil(5000) { compose.onAllNodesWithTag("sprint-done").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Done · 2 added to Hype.").assertExists()
        val hype = runBlocking { db.feels().feels().first { it.name == "Hype" }.feelId }
        compose.waitUntil(5000) { runBlocking { db.feels().states(hype).size == 3 } }
        assertEquals(2, runBlocking { db.feels().states(hype).count { it.member } })
        shown = false
        compose.waitForIdle()
        assertEquals("exit", preview.log.last())
    }

    @Test fun thisOrThatComparesTiesAndRemovesMisfits() {
        val hype = runBlocking {
            feels.setUp(setOf("hype")).getOrThrow()
            val id = db.feels().feels().first { it.name == "Hype" }.feelId
            tracks.forEach { feels.tag(id, it, true).getOrThrow() }
            id
        }
        val preview = FakePreview()
        val nav = mutableStateOf(FeelsNav(hype))
        var shown by mutableStateOf(true)
        compose.setContent { if (shown) Screen(preview, nav) }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("ranking-header").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("start-pairs").assertIsEnabled().performClick()
        compose.waitForIdle()
        assertEquals("enter", preview.log.first())
        compose.onNodeWithText("Which is more Hype?").assertExists()
        compose.onNodeWithTag("pair-card-left").performClick()
        assertTrue(preview.log.any { it.startsWith("play:") })
        compose.onNodeWithTag("pick-left").performClick()
        compose.onNodeWithTag("pick-tie").performClick()
        compose.onNodeWithTag("pair-misfit-right").performClick()
        compose.waitUntil(5000) { runBlocking { db.feels().evidence(hype).size >= tracks.size + 3 } }
        // Play through the rest of the round to the summary.
        while (compose.onAllNodesWithTag("pick-right").fetchSemanticsNodes().isNotEmpty()) compose.onNodeWithTag("pick-right").performClick()
        compose.onNodeWithTag("pairs-settled").assertExists()
        compose.onNodeWithTag("pairs-again").assertExists()
        assertTrue(runBlocking { db.feels().states(hype).count { !it.member } } >= 1)
        nav.value = nav.value.copy(sprint = false) // the app's Back
        compose.waitUntil(5000) { compose.onAllNodesWithTag("feel-detail").fetchSemanticsNodes().isNotEmpty() }
        assertEquals("exit", preview.log.last())
    }

    @Test fun thisOrThatNeedsTwoSongs() {
        val hype = runBlocking { feels.setUp(setOf("hype")).getOrThrow(); db.feels().feels().first { it.name == "Hype" }.feelId }
        val nav = mutableStateOf(FeelsNav(hype))
        compose.setContent { Screen(null, nav) }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("start-pairs").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("start-pairs").assertIsNotEnabled()
        compose.onNodeWithText("Add at least 2 songs to Hype to compare them.").assertExists()
    }

    @Test fun nowPlayingSheetTagsInTwoTaps() {
        runBlocking { feels.setUp(setOf("hype")).getOrThrow() }
        compose.setContent {
            val state by feels.state.collectAsState()
            FeelSheet(tracks[0], feels, state, onSetUp = {}, onMessage = {}, onDismiss = {})
        }
        compose.waitUntil(5000) { compose.onAllNodesWithTag("sheet-feel-Hype").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("sheet-feel-Hype").performClick()
        compose.onNodeWithTag("sheet-bucket-Hot").performClick()
        compose.waitUntil(5000) { feels.state.value.bySong["song0"].orEmpty().size == 2 }
        compose.onNodeWithTag("sheet-feel-Hype").assertIsSelected()
        compose.onNodeWithTag("sheet-bucket-Hot").assertIsSelected()
    }
}
