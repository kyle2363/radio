package dev.radio.mobile

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.ui.RadioApp
import kotlinx.coroutines.runBlocking
import org.junit.Rule
import org.junit.Test
import java.io.File

class LibraryUiTest {
    @get:Rule val compose = createEmptyComposeRule()
    @Test fun emptyScanNestedBrowsingMissingFilesAndRevokedAccessAreVisible() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        fun fixture(method: String, arg: String? = null) { context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), method, arg, null) }
        fun screenshot(name: String) {
            compose.waitForIdle()
            val image = instrumentation.uiAutomation.takeScreenshot()
            val folder = File(context.getExternalFilesDir(null), "library-review").apply { mkdirs() }
            File(folder, "$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle()
        }
        val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
        val opening = prefs.getString("opening", null)
        prefs.edit().putString("opening", "Library").commit()
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val repository = LibraryRepository(context, db)
        fixture("reset"); fixture("grant")
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { it.setContent { RadioApp(repository, FakePlaybackController()) } }
            compose.waitUntil(5000) { !repository.state.value.loading }
            compose.onNodeWithText("Choose Music folder").assertIsDisplayed()
            compose.onNodeWithContentDescription("Search library").assertIsNotEnabled()
            compose.onNodeWithText("After Hours").assertDoesNotExist()
            screenshot("empty-library")
            runBlocking { repository.chooseFolder(FixtureDocumentsProvider.tree()).join() }
            compose.onNodeWithText("October 2026").assertIsDisplayed().performClick()
            compose.onNodeWithText("Nested").assertIsDisplayed().performClick()
            compose.onNodeWithText("Duplicate").assertIsDisplayed()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithText("Nested").assertIsDisplayed()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithText("Songs").performClick()
            compose.onNodeWithText("Root song").assertIsDisplayed()
            compose.onNodeWithContentDescription("Search library").performClick()
            compose.onNodeWithTag("search-input").performTextInput("Root song")
            compose.onNode(hasText("Root song") and !hasSetTextAction()).assertIsDisplayed()
            compose.onNodeWithText("Duplicate").assertDoesNotExist()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.waitUntil(5000) {
                var visible = false
                scenario.onActivity { visible = it.window.decorView.rootWindowInsets?.isVisible(android.view.WindowInsets.Type.ime()) == true }
                !visible
            }
            screenshot("scanned-library")
            fixture("remove", "a")
            runBlocking { repository.rescan()!!.join() }
            compose.onNodeWithText("Missing file", substring = true).assertIsDisplayed()
            fixture("revoke")
            repository.checkAccess()
            compose.waitUntil(5000) { repository.state.value.accessLost }
            compose.onNodeWithText("Choose folder").assertIsDisplayed()
            compose.onNodeWithText("Root song").assertIsDisplayed()
            screenshot("folder-access-lost")
        } finally {
            scenario.close()
            runBlocking { repository.close() }
            db.close()
            runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            prefs.edit().apply { if (opening == null) remove("opening") else putString("opening", opening) }.commit()
        }
    }
}
