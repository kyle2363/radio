package dev.radio.mobile

import android.content.Context
import android.graphics.Bitmap
import android.provider.Settings
import android.view.WindowInsets
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.*
import dev.radio.mobile.data.*
import dev.radio.mobile.ui.RadioApp
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.io.File

class ServerSettingsKeyboardTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun realSettingsKeepsBundleAndPairAboveSoftwareKeyboard() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        val savedPrefs = prefs.all
        val showIme = Settings.Secure.getString(context.contentResolver, "show_ime_with_hard_keyboard")
        fun shell(command: String) { android.os.ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use { it.readBytes() } }
        shell("settings put secure show_ime_with_hard_keyboard 1")
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val library = LibraryRepository(context, db)
        val store = object : PairingStore {
            override fun load() = StoredPairing()
            override fun installationMarker() = "keyboard-fixture"
            override fun save(record: PairingRecord) = Unit
            override fun forget() = Unit
        }
        val pairing = PairingRepository(store, RadioTransport { _, _, _, _ -> throw AssertionError("Typing must not contact a server") }, EndpointPolicy(true))
        val playback = FakePlaybackController().apply {
            val song = LocalSong("keyboard-song", "fixture-root", "fixture-folder", "content://keyboard-fixture/song", "Keyboard fixture.mp3", "Fixture", 1L, 1L, 10L, 60000L)
            playFrom(listOf(song), song.id, "Keyboard fixture")
            seek(12000L)
        }
        val beforePlayback = playback.state.value
        try {
            prefs.edit().putString("opening", "Library").commit()
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                scenario.onActivity { activity -> activity.setContent { RadioApp(library, playback, pairingRepository = pairing) } }
                compose.onNodeWithTag("nav-Settings").performClick()
                compose.onNodeWithTag("open-server").performScrollTo().performClick()
                compose.waitUntil(5000) { pairing.state.value.loaded }
                compose.onNodeWithTag("pairing-bundle").performScrollTo().performClick().performTextInput("synthetic bundle")
                var keyboardTop = Int.MAX_VALUE
                var lastTop = Int.MAX_VALUE
                var stableSince = android.os.SystemClock.elapsedRealtime()
                compose.waitUntil(5000) {
                    var visible = false
                    scenario.onActivity {
                        val decor = it.window.decorView
                        val insets = decor.rootWindowInsets
                        visible = insets?.isVisible(WindowInsets.Type.ime()) == true
                        if (visible) keyboardTop = decor.height - insets!!.getInsets(WindowInsets.Type.ime()).bottom
                    }
                    if (lastTop != keyboardTop) { lastTop = keyboardTop; stableSince = android.os.SystemClock.elapsedRealtime() }
                    visible && android.os.SystemClock.elapsedRealtime() - stableSince >= 500
                }
                compose.waitForIdle()
                val screenshot = instrumentation.uiAutomation.takeScreenshot()
                val dir = File(context.getExternalFilesDir(null), "pairing-review").apply { mkdirs() }
                File(dir, "settings-keyboard.png").outputStream().use { screenshot.compress(Bitmap.CompressFormat.PNG, 100, it) }
                screenshot.recycle()
                compose.onNodeWithTag("pairing-bundle").assertIsFocused().assertIsDisplayed()
                compose.onNodeWithTag("pair-server").assertIsDisplayed().assertIsEnabled()
                val field = compose.onNodeWithTag("pairing-bundle").fetchSemanticsNode().boundsInRoot
                val button = compose.onNodeWithTag("pair-server").fetchSemanticsNode().boundsInRoot
                assertTrue("Focused field must stay above the visible software keyboard", field.bottom <= keyboardTop)
                assertTrue("Pair must stay above the visible software keyboard", button.bottom <= keyboardTop)
                androidx.test.espresso.Espresso.pressBack()
                compose.onNodeWithTag("nav-Library").assertIsDisplayed()
                compose.onNodeWithText("Keyboard fixture").assertIsDisplayed()
                assertEquals("Typing and keyboard dismissal preserve the playing song, queue and position", beforePlayback, playback.state.value)
                compose.onNodeWithTag("open-matching").performScrollTo().performClick()
                compose.onNodeWithTag("matching-screen").assertIsDisplayed()
                compose.onNodeWithText("Matching", substring = false).assertExists()
                compose.onNodeWithContentDescription("Back").performClick()
                compose.onNodeWithTag("server-settings").assertExists()
                compose.onNodeWithContentDescription("Back").performClick()
                compose.onNodeWithTag("settings-scroll").assertIsDisplayed()
                compose.onNodeWithTag("server-settings").assertDoesNotExist()
                compose.onNodeWithText("Keyboard fixture").assertIsDisplayed()
                assertEquals("Server and Matching navigation preserve the playing song, queue and position", beforePlayback, playback.state.value)

            }
        } finally {
            runBlocking { library.close() }; db.close()
            prefs.edit().clear().apply {
                savedPrefs.forEach { (key, value) -> when (value) { is String -> putString(key, value); is Boolean -> putBoolean(key, value); is Int -> putInt(key, value); is Long -> putLong(key, value); is Float -> putFloat(key, value) } }
            }.commit()
            shell(if (showIme == null) "settings delete secure show_ime_with_hard_keyboard" else "settings put secure show_ime_with_hard_keyboard $showIme")
        }
    }
}

