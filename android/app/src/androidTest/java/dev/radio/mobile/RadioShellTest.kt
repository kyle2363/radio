package dev.radio.mobile

import android.content.Context
import android.graphics.Bitmap
import java.io.File
import androidx.test.core.app.ActivityScenario
import androidx.activity.compose.setContent
import androidx.room.Room
import dev.radio.mobile.data.*
import dev.radio.mobile.ui.RadioApp
import kotlinx.coroutines.runBlocking
import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.toPixelMap
import androidx.lifecycle.Lifecycle
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue

class RadioShellTest {
    @get:Rule val compose = createEmptyComposeRule()
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var db: LibraryDatabase
    private lateinit var repository: LibraryRepository
    private val playback = FakePlaybackController()
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Before fun launch() {
        context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE).edit().clear().commit()
        context.contentResolver.call(android.net.Uri.parse("content://dev.radio.mobile.test.control"), "reset", null, null)
        context.contentResolver.call(android.net.Uri.parse("content://dev.radio.mobile.test.control"), "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val uri = FixtureDocumentsProvider.tree().toString()
        runBlocking {
            db.library().saveRoot(LibraryRoot(uri = uri, folderId = "root", name = "Music", scannedAt = 1L))
            db.library().saveFolders(listOf(LocalFolder("root", uri, null, "Music", "", 1L)) +
                listOf("October 2026", "September 2026", "August 2026", "July 2026", "June 2026", "May 2026", "April 2026", "March 2026").mapIndexed { index, name -> LocalFolder("folder$index", uri, "root", name, name, 9000L - index) })
            db.library().saveSongs(listOf("After Hours", "Morning Light", "A Long Way Home (Live Session Recording)", "Slow Motion", "Open Water", "Night Drive", "Untitled_2026-10-18_final_mix", "Between Stations", "Blue Hour", "Home Again").mapIndexed { index, name ->
                LocalSong("sample$index", uri, "folder0", "$uri/document/sample$index", "$name.mp3", "October 2026", 9000L - index, 1L, 32L, if (index == 0) 222000L else 258000L)
            })
        }
        repository = LibraryRepository(context, db)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        render()
        compose.waitUntil(5000) { !repository.state.value.loading }
        compose.onNodeWithText("Songs").performClick()
        compose.onNodeWithText("After Hours").performClick()
        scenario.onActivity { playback.pause() }
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithText("Folders").performClick()
    }
    private fun render() { scenario.onActivity { activity -> activity.setContent { RadioApp(repository, playback) } } }
    private fun recreate() { scenario.recreate(); render() }
    @After fun close() {
        scenario.close()
        runBlocking { repository.close() }
        db.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val image = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        val folder = File(context.getExternalFilesDir(null), "shell-review").apply { mkdirs() }
        File(folder, "$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
        image.recycle()
    }

    @Test fun eightFoldersFitAndSearchHasItsOwnScreen() {
        compose.onNodeWithText("March 2026").assertIsDisplayed()
        val list = compose.onNodeWithTag("library-list").fetchSemanticsNode().boundsInRoot
        val last = compose.onNodeWithText("March 2026").fetchSemanticsNode().boundsInRoot
        assertTrue("All eight folder titles should fit on the reference device", last.bottom <= list.bottom)
        screenshot("ocean-light-library")
        compose.onNodeWithTag("search-input").assertDoesNotExist()
        compose.onNodeWithContentDescription("Search library").performClick()
        compose.onNodeWithTag("search-input").performTextInput("Morning")
        compose.onNodeWithText("Morning Light").assertIsDisplayed()
        compose.onNodeWithText("Blue Hour").assertDoesNotExist()
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithText("March 2026").assertIsDisplayed()
    }

    @Test fun nextButtonsWorkInMiniFullAndMinimalPlayers() {
        compose.onNodeWithTag("mini-next").performClick()
        compose.onNodeWithText("Morning Light").assertIsDisplayed().performClick()
        compose.onNodeWithTag("player-next").performClick()
        compose.onNodeWithText("A Long Way Home (Live Session Recording)").assertIsDisplayed()
        screenshot("full-player")
        compose.onNodeWithContentDescription("Minimal mode").performClick()
        compose.onNodeWithTag("player-next").assertIsDisplayed().performClick()
        compose.onNodeWithText("Slow Motion").assertIsDisplayed()
        compose.onNodeWithText("Skip reason").assertDoesNotExist()
        screenshot("minimal-player")
    }

    @Test fun themeAndAppearanceSurviveActivityRecreation() {
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Deep Teal").performClick()
        compose.onNodeWithText("Dark").performClick()
        compose.waitForIdle()
        recreate()
        compose.onNodeWithText("Deep Teal").assertIsDisplayed()
        compose.onNodeWithText("Deep Teal").assertIsSelected()
        compose.onNodeWithText("Dark").assertIsSelected()
        val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        assertEquals("Teal", prefs.getString("palette", ""))
        assertEquals("Dark", prefs.getString("appearance", ""))
        compose.onNodeWithTag("nav-Library").performClick()
        screenshot("teal-dark-library")
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Light").performClick()
        compose.onNodeWithTag("nav-Library").performClick()
        screenshot("teal-light-library")
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Ocean Blue").performClick()
        compose.onNodeWithText("Dark").performClick()
        compose.onNodeWithTag("nav-Library").performClick()
        screenshot("ocean-dark-library")
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Light").performClick()
        compose.onNodeWithTag("nav-Library").performClick()
        compose.onNodeWithText("March 2026").assertIsDisplayed()
    }

    @Test fun thinSeekBarSupportsAccessibilityAndResetsForNextSong() {
        compose.onNodeWithText("After Hours").performClick()
        compose.onNodeWithTag("player-seek").performSemanticsAction(SemanticsActions.SetProgress) { it(111f) }
        compose.onNodeWithText("1:51").assertIsDisplayed()
        compose.onNodeWithTag("player-seek").assertHeightIsAtLeast(48.dp)
        screenshot("thin-seek-static-ocean")
        compose.onNodeWithTag("player-next").performClick()
        compose.onNodeWithText("0:00").assertIsDisplayed()
    }

    @Test fun integratedQueueSharesTransportAndBackPreservesSelectedSong() {
        compose.onNodeWithText("After Hours").performClick()
        compose.onNodeWithTag("player-seek").performSemanticsAction(SemanticsActions.SetProgress) { it(111f) }
        compose.onNodeWithContentDescription("Queue").performClick()
        compose.onNodeWithTag("playback-queue").assertIsDisplayed()
        compose.onNodeWithTag("player-transport").assertIsDisplayed()
        compose.onNodeWithText("1:51").assertIsDisplayed()
        compose.onNodeWithTag("queue-row-sample0").assertIsSelected()
        compose.onNodeWithTag("queue-row-sample1").performClick()
        compose.onNodeWithTag("queue-row-sample1").assertIsSelected()
        compose.onNodeWithText("2 / 10").assertIsDisplayed()
        compose.onNodeWithContentDescription("Pause").performClick()
        screenshot("integrated-queue-ocean")
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithTag("playback-queue").assertDoesNotExist()
        compose.onNodeWithText("Morning Light").assertIsDisplayed()
        assertEquals("sample1", playback.state.value.currentId)
        assertEquals(10, playback.state.value.queue.size)
        assertTrue(!playback.state.value.isPlaying)
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithTag("nav-Settings").performClick()
        compose.onNodeWithText("Deep Teal").performClick()
        compose.onNodeWithText("Dark").performClick()
        compose.onNodeWithTag("nav-Library").performClick()
        compose.onNodeWithText("Morning Light").performClick()
        compose.onNodeWithContentDescription("Queue").performClick()
        compose.onNodeWithText("2 / 10").assertIsDisplayed()
        screenshot("integrated-queue-teal-dark")
    }

    @Test fun queueKeepsEightRowsVisibleAndPositionTicksDoNotResetBrowsing() {
        val source = playback.state.value.queue.first()
        val largeQueue = List(550) { source.copy(id = "dense$it", fileName = "Track $it.mp3") }
        scenario.onActivity { playback.playFrom(largeQueue, "dense15", "Queue test"); playback.pause() }
        compose.onNodeWithText("Track 15").performClick()
        compose.onNodeWithContentDescription("Queue").performClick()
        compose.onNodeWithText("16 / 550").assertIsDisplayed()
        compose.onNodeWithTag("queue-row-dense22").assertIsDisplayed()
        val list = compose.onNodeWithTag("playback-queue").fetchSemanticsNode().boundsInRoot
        val eighth = compose.onNodeWithTag("queue-row-dense22").fetchSemanticsNode().boundsInRoot
        assertTrue("Eight complete rows should fit at the reference device's standard text size", eighth.bottom <= list.bottom)
        screenshot("integrated-queue-large")
        compose.onNodeWithTag("playback-queue").performScrollToIndex(100)
        val beforeSelection = compose.onNodeWithTag("queue-row-dense100").fetchSemanticsNode().boundsInRoot
        compose.onNodeWithTag("queue-row-dense104").performClick()
        compose.onNodeWithTag("queue-row-dense104").assertIsSelected()
        assertEquals("Selecting a song must not move the queue", beforeSelection,
            compose.onNodeWithTag("queue-row-dense100").fetchSemanticsNode().boundsInRoot)
        compose.onNodeWithTag("player-next").performClick()
        compose.onNodeWithTag("queue-row-dense105").assertIsSelected()
        assertEquals("Advancing must not move the queue", beforeSelection,
            compose.onNodeWithTag("queue-row-dense100").fetchSemanticsNode().boundsInRoot)
        scenario.onActivity { playback.seek(3000) }
        compose.onNodeWithTag("queue-row-dense100").assertIsDisplayed()
        compose.onNodeWithTag("player-seek").assertIsDisplayed()
        compose.onNodeWithTag("player-next").assertIsDisplayed()
        compose.onNodeWithContentDescription("Shuffle").performClick()
        assertEquals("dense105", playback.state.value.playOrder.first())
        compose.onNodeWithTag("queue-row-dense105").assertIsNotDisplayed()
        assertTrue("Shuffle must not scroll to the newly first song",
            compose.onNodeWithTag("playback-queue").fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value() > 0f)
        compose.onNodeWithTag("queue-position").performClick()
        compose.onNodeWithTag("queue-row-dense105").assertIsDisplayed()
    }

    @Test fun backgroundModesPersistAndMotionStopsOnPauseAndBackground() {
        compose.onNodeWithTag("nav-Settings").performClick()
        compose.onNodeWithText("Animated").performClick()
        recreate()
        compose.onNodeWithText("Animated").assertIsSelected()
        compose.onNodeWithTag("nav-Library").performClick()
        compose.onNodeWithText("After Hours").performClick()
        val background = compose.onNodeWithTag("player-background")
        background.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Background motion paused"))
        // Keep the test clock under our control while the decorative animation is running.
        compose.mainClock.autoAdvance = false
        compose.onNodeWithContentDescription("Play").performClick()
        compose.mainClock.advanceTimeBy(100)
        background.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Background moving"))
        val before = background.captureToImage().toPixelMap()
        compose.mainClock.advanceTimeBy(6000)
        val after = background.captureToImage().toPixelMap()
        assertTrue("Gradient pixels should move", before[before.width / 4, before.height * 3 / 4] != after[after.width / 4, after.height * 3 / 4])
        screenshot("animated-ocean-player")
        // Android removes the visible Compose hierarchy while the activity is backgrounded.
        // Exercise the real lifecycle here; the unit test covers the motion gate while hidden.
        scenario.moveToState(Lifecycle.State.CREATED)
        assertEquals(Lifecycle.State.CREATED, scenario.state)
        scenario.moveToState(Lifecycle.State.RESUMED)
        compose.mainClock.advanceTimeBy(100)
        background.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Background moving"))
        compose.onNodeWithContentDescription("Pause").performClick()
        compose.mainClock.advanceTimeBy(100)
        background.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Background motion paused"))
        compose.mainClock.autoAdvance = true
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithTag("nav-Settings").performClick()
        compose.onNodeWithText("Off").performClick()
        compose.onNodeWithTag("nav-Library").performClick()
        compose.onNodeWithText("After Hours").performClick()
        background.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Background off"))
    }
}
