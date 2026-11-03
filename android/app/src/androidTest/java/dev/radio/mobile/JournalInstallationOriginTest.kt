package dev.radio.mobile

import android.content.Context
import android.content.ContextWrapper
import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.StoredPairing
import dev.radio.mobile.data.*
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.UUID

class JournalInstallationOriginTest {
    @Test fun immediateCaptureSurvivesRepositoryRestartAndBackupRestoreGetsNewOrigin() {
        val base = InstrumentationRegistry.getInstrumentation().targetContext
        val directory = File(base.cacheDir, "journal-origin-test-${UUID.randomUUID()}").apply { mkdirs() }
        val context = object : ContextWrapper(base) {
            override fun getApplicationContext(): Context = this
            override fun getNoBackupFilesDir(): File = directory
        }
        val db = Room.inMemoryDatabaseBuilder(base, LibraryDatabase::class.java).build()
        // A cancelled scope proves synchronous capture does not depend on asynchronous warmup.
        val scope = CoroutineScope(Job().apply { cancel() })
        val song = LocalSong("song", "root", "folder", "content://song", "song.mp3", "", 1, 1, 1, 1)
        try {
            JournalIdentityProvider.publishPairing(StoredPairing())
            val first = JournalIdentityProvider(context, db, scope).capture(song)
            val restarted = JournalIdentityProvider(context, db, scope).capture(song)
            assertEquals(first.deviceId, restarted.deviceId)
            assertTrue(File(directory, "radio_journal_origin").delete())
            val restored = JournalIdentityProvider(context, db, scope).capture(song)
            assertNotEquals(first.deviceId, restored.deviceId)
            assertEquals(first.deviceId, first.copy().deviceId)
        } finally { scope.cancel(); db.close(); directory.deleteRecursively() }
    }
}
