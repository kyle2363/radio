package dev.radio.mobile

import android.graphics.Bitmap
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.*
import dev.radio.mobile.ui.ServerSettings
import dev.radio.mobile.ui.theme.*
import java.io.File
import org.junit.Rule
import org.junit.Test

class ServerSettingsTest {
    @get:Rule val compose = createEmptyComposeRule()
    @Test fun compactServerSectionRendersLightAndDarkWithoutAPlaybackDependency() {
        val store = object : PairingStore {
            override fun load() = StoredPairing()
            override fun installationMarker() = "synthetic"
            override fun save(record: PairingRecord) = Unit
            override fun forget() = Unit
        }
        val repository = PairingRepository(store, RadioTransport { _, _, _, _ -> throw AssertionError("Rendering must not contact server") }, EndpointPolicy(true))
        val appearance = mutableStateOf(Appearance.Light)
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity -> activity.setContent {
                RadioTheme(appearance = appearance.value) {
                    Surface(Modifier.fillMaxSize()) {
                        Column(Modifier.padding(20.dp).verticalScroll(rememberScrollState())) {
                            Text("Settings", style = MaterialTheme.typography.headlineSmall)
                            ServerSettings(repository)
                        }
                    }
                }
            } }
            compose.waitUntil(5000) { repository.state.value.loaded }
            compose.onNodeWithText("Server", useUnmergedTree = true).assertIsDisplayed()
            compose.onNodeWithTag("pairing-bundle").assertIsDisplayed()
            compose.onNodeWithTag("pair-server").assertIsNotEnabled()
            compose.waitUntil(5000) { compose.onAllNodesWithText("No imported catalog").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("import-catalog").performScrollTo().assertIsNotEnabled()
            compose.onNodeWithText("No imported catalog").assertIsDisplayed()
            fun screenshot(name: String) {
                compose.waitForIdle()
                val instrumentation = InstrumentationRegistry.getInstrumentation()
                val bitmap = instrumentation.uiAutomation.takeScreenshot()
                val dir = File(instrumentation.targetContext.getExternalFilesDir(null), "pairing-review").apply { mkdirs() }
                File(dir, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                bitmap.recycle()
            }
            screenshot("server-light")
            compose.runOnIdle { appearance.value = Appearance.Dark }
            screenshot("server-dark")
        }
    }
}
