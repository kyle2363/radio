package dev.radio.mobile

import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import dev.radio.mobile.ui.ProfileSyncSection
import dev.radio.mobile.ui.theme.RadioTheme
import dev.radio.mobile.ui.theme.Appearance
import androidx.test.platform.app.InstrumentationRegistry
import android.graphics.Bitmap
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ProfileSyncUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun syncIsExplicitAndBusyOrUnpairedStatesDisableIt() {
        var requests = 0
        val enabled = mutableStateOf(false)
        val busy = mutableStateOf(false)
        val appearance = mutableStateOf(Appearance.Light)
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                RadioTheme(appearance = appearance.value) { Surface { Column {
                    ProfileSyncSection(busy.value, 12, null, null, null, enabled.value) { requests++ }
                } } }
            } }
            compose.onNodeWithTag("sync-now").assertIsNotEnabled()
            compose.onNodeWithText("12 events waiting to sync").assertIsDisplayed()
            compose.onNodeWithText("Last synced: Never").assertIsDisplayed()
            compose.runOnIdle { enabled.value = true }
            compose.onNodeWithTag("sync-now").performClick()
            compose.runOnIdle { assertEquals(1, requests); busy.value = true }
            compose.onNodeWithTag("sync-now").assertIsNotEnabled()
            compose.onNodeWithTag("sync-progress").assertIsDisplayed()
            compose.runOnIdle { assertEquals(1, requests) }
            screenshot("sync-light")
            compose.runOnIdle { appearance.value = Appearance.Dark }
            screenshot("sync-dark")
        }
    }

    @Test fun failureKeepsPendingCountAndLastSuccessVisibleForRetry() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                RadioTheme { Surface { Column {
                    ProfileSyncSection(false, 3, 1793790000000L, "Sync interrupted. Saved progress kept.",
                        "Cannot reach the server. Check USB forwarding.", true) {}
                } } }
            } }
            compose.onNodeWithText("3 events waiting to sync").assertIsDisplayed()
            compose.onNodeWithTag("sync-last-success").assertTextContains("Last synced:", substring = true)
            compose.onNodeWithTag("sync-error").assertTextEquals("Cannot reach the server. Check USB forwarding.")
            compose.onNodeWithTag("sync-now").assertIsEnabled()
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val bitmap = instrumentation.uiAutomation.takeScreenshot()
        val directory = File(instrumentation.targetContext.getExternalFilesDir(null), "sync-review").apply { mkdirs() }
        File(directory, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }
}
