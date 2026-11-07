package dev.radio.mobile

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*
import dev.radio.mobile.ui.ConnectedRadioSection
import dev.radio.mobile.ui.ConnectedTuningSection
import dev.radio.mobile.ui.IntegratedQueue
import dev.radio.mobile.ui.UpNextOptionsCard
import androidx.compose.foundation.verticalScroll
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class ConnectedRadioUiTest {
    @get:Rule val compose = createComposeRule()

    private fun song(id: String) = LocalSong(id, "tree", "folder", "content://fixture/$id", "Song $id.mp3", "Music", 1, 1, 1, 1000, true)
    private val picks = listOf(ConnectedPick(song("a"), "explore", "Something new nearby"), ConnectedPick(song("b"), "wildcard", "A curveball"), ConnectedPick(song("c"), "context", "Fits the evening"))
    private val fence = ConnectedFence("cur", "neutral", .3, null, 1, 0, 1, "d")
    private val held = ConnectedSet("cur", "req", "res", "llm", false, fence, emptyList(), picks, 0, 0)

    /** The card exactly as RadioContent and Now Playing compose it: empty option list means nothing is composed at all. */
    private fun shown(ui: ConnectedUi, current: String? = "cur", radio: Boolean = true, queued: Set<String> = emptySet(), disliked: Set<String> = emptySet(), available: Set<String> = setOf("a", "b", "c"),
        onPlayNext: (ConnectedPick) -> Unit = {}) {
        compose.setContent {
            val options = ConnectedPolicy.visibleOptions(ui, current, radio, queued, { it in disliked }) { it in available }
            if (options.isNotEmpty()) UpNextOptionsCard(options, onPlayNext)
        }
    }
    private fun ready() = ConnectedUi(ConnectedStatus(enabled = true), held)
    private fun hidden() = compose.onNodeWithTag("up-next-options").assertDoesNotExist()

    @Test fun cardShowsTitleSlotAndServerReasonForEachOption() {
        shown(ready())
        compose.onNodeWithTag("up-next-options").assertIsDisplayed()
        compose.onNodeWithText("Up next options").assertIsDisplayed()
        picks.forEach { compose.onNodeWithTag("up-next-option-${it.song.id}").assertIsDisplayed() }
        compose.onNodeWithText("Explore · Something new nearby").assertIsDisplayed()
        compose.onNodeWithText("Wildcard · A curveball").assertIsDisplayed()
        compose.onNodeWithText("Fits now · Fits the evening").assertIsDisplayed()
        compose.onNodeWithText("Song a").assertIsDisplayed()
    }

    @Test fun tappingPlayNextReportsTheChosenPickOnly() {
        val chosen = mutableListOf<String>()
        shown(ready(), onPlayNext = { chosen += it.song.id })
        compose.onNodeWithTag("play-next-b").performClick()
        assertEquals(listOf("b"), chosen)
    }

    @Test fun cardIsHiddenWhenConnectedRadioIsOff() { shown(ConnectedUi(ConnectedStatus(enabled = false), held)); hidden() }
    @Test fun cardIsHiddenWithoutAnySet() { shown(ConnectedUi(ConnectedStatus(enabled = true), null)); hidden() }
    @Test fun cardIsHiddenWhileTheRequestIsPending() { shown(ConnectedUi(ConnectedStatus(enabled = true, pendingAnchor = "cur"), held)); hidden() }
    @Test fun cardIsHiddenAfterAFailedRequestLeftNoSet() { shown(ConnectedUi(ConnectedStatus(enabled = true, lastOk = false, lastFailure = ConnectedFailure(ConnectedFailureKind.UNREACHABLE)), null)); hidden() }
    @Test fun cardIsHiddenOnceTheSongHasChanged() { shown(ready(), current = "next"); hidden() }
    @Test fun cardIsHiddenOutsideRadio() { shown(ready(), radio = false); hidden() }
    @Test fun cardIsHiddenWhenEveryOptionWasQueuedDislikedOrRemoved() { shown(ready(), queued = setOf("a"), disliked = setOf("b"), available = setOf("a", "b")); hidden() }

    @Test fun cardDropsAnOptionOnceItIsQueued() {
        var queued by mutableStateOf(emptySet<String>())
        compose.setContent {
            val options = ConnectedPolicy.visibleOptions(ready(), "cur", true, queued, { false }) { true }
            if (options.isNotEmpty()) UpNextOptionsCard(options, { queued = queued + it.song.id })
        }
        compose.onNodeWithTag("play-next-a").performClick()
        compose.onNodeWithTag("up-next-option-a").assertDoesNotExist()
        compose.onNodeWithTag("up-next-option-b").assertIsDisplayed()
        compose.onNodeWithTag("play-next-b").performClick(); compose.onNodeWithTag("play-next-c").performClick()
        hidden()
    }

    @Test fun serverAiMarkerAppearsOnlyOnMarkedQueueRows() {
        val queue = listOf("cur", "x", "y").map(::song)
        val state = PlaybackState(queue = queue, playOrder = queue.map { it.id }, currentId = "cur", radioActive = true)
        compose.setContent { IntegratedQueue(state, true, onSelect = {}, serverAiIds = setOf("x", "cur")) }
        compose.onNodeWithTag("queue-server-ai-x", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithTag("queue-server-ai-y", useUnmergedTree = true).assertDoesNotExist()
        compose.onNodeWithTag("queue-server-ai-cur", useUnmergedTree = true).assertDoesNotExist()
    }

    @Test fun settingsToggleDefaultsOffAndReportsChanges() {
        val toggles = mutableListOf<Boolean>()
        var status by mutableStateOf(ConnectedStatus())
        compose.setContent { ConnectedRadioSection(status, paired = true, onToggle = { toggles += it; status = status.copy(enabled = it) }, onCheck = {}) }
        compose.onNodeWithTag("connected-radio-toggle").assertIsOff()
        compose.onNodeWithTag("connected-radio-result").assertDoesNotExist()
        compose.onNodeWithTag("connected-radio-toggle").performClick()
        assertEquals(listOf(true), toggles)
        compose.onNodeWithTag("connected-radio-toggle").assertIsOn()
        compose.onNodeWithTag("connected-radio-result").assertIsDisplayed()
    }

    @Test fun settingsShowNotPermittedAndTheLastReason() {
        compose.setContent {
            ConnectedRadioSection(ConnectedStatus(enabled = true, permitted = false, lastOk = false, lastMessage = ConnectedFailure(ConnectedFailureKind.NOT_PERMITTED).message,
                lastFailure = ConnectedFailure(ConnectedFailureKind.NOT_PERMITTED)), paired = true, onToggle = {}, onCheck = {})
        }
        compose.onNodeWithTag("connected-radio-permission").assertTextContains("Not permitted on desktop", substring = true)
        compose.onNodeWithTag("connected-radio-result").assertTextContains("Not permitted on desktop", substring = true)
    }

    @Test fun settingsShowPermittedPendingAndUnpairedStates() {
        var status by mutableStateOf(ConnectedStatus(enabled = true, permitted = true, lastOk = true, lastMessage = "2 similar and 3 optional picks, ranked by llm"))
        var paired by mutableStateOf(true)
        var checks = 0
        compose.setContent { ConnectedRadioSection(status, paired, onToggle = {}, onCheck = { checks++ }) }
        compose.onNodeWithTag("connected-radio-permission").assertTextContains("Permitted on desktop")
        compose.onNodeWithTag("connected-radio-result").assertTextContains("ranked by llm", substring = true)
        compose.onNodeWithTag("connected-radio-check").performClick()
        assertEquals(1, checks)
        status = status.copy(pendingAnchor = "cur")
        compose.onNodeWithTag("connected-radio-result").assertTextContains("waiting for the desktop", substring = true)
        paired = false
        compose.onNodeWithTag("connected-radio-permission").assertTextContains("Pair this phone", substring = true)
        compose.onNodeWithTag("connected-radio-check").assertIsNotEnabled()
    }
    // --- checkpoint 4.1 tuning ---------------------------------------------------------------

    @Test fun cardShowsTheServerSummary() {
        compose.setContent { UpNextOptionsCard(picks, {}, summary = "LLM · 2 similar · 3 options · variety 40%") }
        compose.onNodeWithTag("up-next-summary").assertTextEquals("LLM · 2 similar · 3 options · variety 40%")
    }

    @Test fun tuningSectionReportsEachChangeAndResets() {
        var tuning by mutableStateOf(ConnectedTuning())
        compose.setContent { androidx.compose.foundation.layout.Column(androidx.compose.ui.Modifier.verticalScroll(androidx.compose.foundation.rememberScrollState())) { ConnectedTuningSection(tuning, tunable = true, pocketsInactive = false, onChange = { tuning = it }) } }
        compose.onNodeWithTag("connected-tuning-reset").assertIsNotEnabled()
        compose.onNodeWithTag("connected-picks-5").assertIsSelected()
        compose.onNodeWithTag("connected-picks-7").performClick()
        compose.onNodeWithTag("connected-similar-2").performScrollTo().performClick()
        compose.onNodeWithTag("connected-mix").assertTextContains("Up to 2 similar songs join the queue; 5 more appear as options.")
        compose.onNodeWithTag("connected-energy-rising").performScrollTo().performClick()
        compose.onNodeWithTag("connected-advanced").performScrollTo().performClick()
        compose.onNodeWithTag("connected-ranking-off").performScrollTo().performClick()
        compose.onNodeWithTag("connected-pockets").performScrollTo().performClick()
        assertEquals(ConnectedTuning(picks = 7, similar = 2, energy = "rising", reranking = "off", pockets = true), tuning)
        compose.onNodeWithTag("connected-picks-5").performScrollTo().performClick()
        compose.onNodeWithTag("connected-similar-auto").performScrollTo().performClick()
        assertNull(tuning.picks); assertNull(tuning.similar)
        compose.onNodeWithTag("connected-tuning-reset").performScrollTo().performClick()
        assertTrue(tuning.isDefault)
    }

    @Test fun pocketsExplainWhenTheDesktopHasTooLittleHistory() {
        compose.setContent { ConnectedTuningSection(ConnectedTuning(pockets = true), tunable = true, pocketsInactive = true, onChange = {}) }
        compose.onNodeWithTag("connected-pockets-note", useUnmergedTree = true).assertTextContains("Needs more listening history", substring = true)
    }

    @Test fun anOlderDesktopDisablesTuning() {
        compose.setContent { ConnectedTuningSection(ConnectedTuning(), tunable = false, pocketsInactive = false, onChange = { fail("No change expected") }) }
        compose.onNodeWithTag("connected-tuning-unsupported").assertIsDisplayed()
        compose.onNodeWithTag("connected-picks-7").assertIsNotEnabled()
        compose.onNodeWithTag("connected-variety").assertIsNotEnabled()
    }

    @Test fun settingsShowTheCurrentSetSummary() {
        compose.setContent { ConnectedRadioSection(ConnectedStatus(enabled = true, permitted = true, tunable = true), paired = true, onToggle = {}, onCheck = {}, summary = "Fallback ranking · 1 similar · 1 option") }
        compose.onNodeWithTag("connected-radio-summary").assertTextContains("Fallback ranking", substring = true)
    }
}
