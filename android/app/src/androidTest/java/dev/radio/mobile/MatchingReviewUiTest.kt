package dev.radio.mobile

import android.graphics.Bitmap
import android.view.WindowInsets
import java.io.File
import androidx.test.platform.app.InstrumentationRegistry
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import dev.radio.mobile.ui.MatchingReviewSection
import dev.radio.mobile.portable.*
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class MatchingReviewUiTest {
    @get:Rule val compose = createEmptyComposeRule()
    private fun capture(name: String) {
        compose.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val image = instrumentation.uiAutomation.takeScreenshot()
        val directory = File(instrumentation.targetContext.getExternalFilesDir(null), "pairing-review").apply { mkdirs() }
        File(directory, name).outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
        image.recycle()
    }

    @Test fun offlineReviewOffersExplicitConfirmRejectAndCorrection() {
        val actions = mutableListOf<String>()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                MaterialTheme { Column(Modifier.fillMaxSize()) {
                    MatchingReviewSection(
                        rows = listOf(MatchResult("local", "Renamed.mp3", "ambiguous", null,
                            listOf(MatchCandidate("catalog", "Song", "Artist", 0.8, listOf("filename"))))),
                        busy = false, enabled = true, progress = "", error = null,
                        onMatch = {}, onConfirm = { local, song -> actions += "confirm:$local:$song" },
                        onReject = { local, song -> actions += "reject:$local:$song" },
                        onReset = { local, song -> actions += "reset:$local:$song" }, onCancel = {}
                    )
                } }
            } }
            compose.onNodeWithText("0 matched · 1 needs review · 0 unmatched").assertExists()
            compose.onNodeWithTag("matching-row-local").performClick()
            compose.onAllNodesWithText("Renamed.mp3").assertCountEquals(2)
            compose.onNodeWithText("Confirm").performClick()
            compose.onNodeWithText("Reject").performScrollTo().performClick()
            compose.onNodeWithText("Reset decision").performScrollTo().performClick()
            compose.runOnIdle { assertEquals(listOf("confirm:local:catalog", "reject:local:catalog", "reset:local:catalog"), actions) }
        }
    }
    @Test fun busyMatchingOffersCancellationAndBlocksDecisions() {
        var canceled = false
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                MaterialTheme { Column(Modifier.fillMaxSize()) {
                    MatchingReviewSection(
                        rows = listOf(MatchResult("local", "Song.mp3", "high_confidence", "catalog",
                            listOf(MatchCandidate("catalog", "Song", "Artist", 1.0, listOf("exact_content_sha256"))))),
                        busy = true, enabled = false, progress = "Reading local files", error = null,
                        onMatch = { error("Matching already running") },
                        onConfirm = { _, _ -> error("Busy decision") }, onReject = { _, _ -> error("Busy decision") },
                        onReset = { _, _ -> error("Busy decision") }, onCancel = { canceled = true }
                    )
                } }
            } }
            compose.onNodeWithText("1 matched · 0 needs review · 0 unmatched").assertExists()
            compose.onNodeWithText("Reading local files").assertExists()
            compose.onNodeWithTag("match-local-songs").assertDoesNotExist()
            compose.onNodeWithTag("cancel-matching").performClick()
            compose.runOnIdle { assertEquals(true, canceled) }
        }
    }

    @Test fun largeLibrarySearchAndBatchRequireExplicitCandidateChoice() {
        val applied = mutableListOf<Pair<String, String>>()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                MaterialTheme {
                    MatchingReviewSection(
                        rows = (0 until 1800).map { index -> MatchResult("local-$index", "Recording $index.mp3", "ambiguous", null,
                            listOf(MatchCandidate("a-$index", "First", "Artist", 0.8, listOf("filename")),
                                MatchCandidate("b-$index", "Second", "Artist", 0.8, listOf("filename")))) },
                        busy = false, enabled = true, progress = "", error = null, onMatch = {},
                        onConfirm = { _, _ -> }, onReject = { _, _ -> }, onReset = { _, _ -> }, onCancel = {},
                        onConfirmSelected = { applied += it })
                }
            } }
            capture("matching-list.png")
            compose.onNodeWithTag("confirm-selected-matches").assertIsNotEnabled()
            compose.onNodeWithTag("matching-list").performScrollToNode(hasTestTag("matching-row-local-1799"))
            compose.onNodeWithTag("matching-row-local-1799").assertIsDisplayed()
            compose.onNodeWithTag("matching-search").performTextInput("Recording 1799.mp3")
            compose.onNodeWithTag("select-match-local-1799").assertDoesNotExist()
            scenario.onActivity { it.window.insetsController?.hide(WindowInsets.Type.ime()) }
            compose.onNodeWithTag("matching-row-local-1799").performClick()
            capture("matching-candidates.png")
            compose.onAllNodesWithText("Select for batch")[1].performScrollTo().performClick()
            compose.onNodeWithText("Done").performClick()
            compose.onNodeWithTag("confirm-selected-matches").performClick()
            compose.runOnIdle { assertEquals(listOf("local-1799" to "b-1799"), applied) }
        }
    }

    @Test fun confirmingWithinReviewKeepsRowAndDetailInPlace() {
        val row = mutableStateOf(MatchResult("local", "Stable.mp3", "ambiguous", null,
            listOf(MatchCandidate("catalog", "Song", "Artist", 0.8, listOf("filename")))))
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                MaterialTheme {
                    MatchingReviewSection(listOf(row.value), false, true, "", null, {},
                        onConfirm = { _, song -> row.value = row.value.copy(status = "confirmed", songId = song) },
                        onReject = { _, _ -> }, onReset = { _, _ -> }, onCancel = {})
                }
            } }
            compose.onNodeWithText("Review").performClick()
            compose.onNodeWithTag("matching-row-local").performClick()
            compose.onNodeWithText("Confirm").performScrollTo().performClick()
            compose.onNodeWithText("Current match").assertExists()
            compose.onNodeWithText("Done").performClick()
            compose.onNodeWithTag("matching-row-local").assertIsDisplayed()
        }
    }

    @Test fun uniqueCandidateCanBeExplicitlySelectedDirectlyFromList() {
        val applied = mutableListOf<Pair<String, String>>()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                MaterialTheme {
                    MatchingReviewSection(listOf(MatchResult("unique", "Only.mp3", "high_confidence", "song",
                        listOf(MatchCandidate("song", "Only song", "Artist", 1.0, listOf("filename"))))),
                        false, true, "", null, {}, { _, _ -> }, { _, _ -> }, { _, _ -> }, {},
                        onConfirmSelected = { applied += it })
                }
            } }
            compose.onNodeWithTag("select-match-unique").performClick()
            compose.onNodeWithTag("confirm-selected-matches").performClick()
            compose.runOnIdle { assertEquals(listOf("unique" to "song"), applied) }
        }
    }
}
