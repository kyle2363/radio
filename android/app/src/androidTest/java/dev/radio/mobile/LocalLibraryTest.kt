package dev.radio.mobile

import android.net.Uri
import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import org.junit.*
import org.junit.Assert.*
import java.io.File
import java.util.UUID

class LocalLibraryTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var repository: LibraryRepository
    private lateinit var databaseFile: File
    private fun fixture(method: String, arg: String? = null): android.os.Bundle? {
        android.util.Log.d("LibraryFixture", "Starting $method")
        val result = context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), method, arg, null)
        android.util.Log.d("LibraryFixture", "Finished $method")
        return result
    }
    @Before fun setup() = runBlocking {
        fixture("reset"); fixture("grant")
        databaseFile = File(context.cacheDir, ".test-runtime/library-tests/${UUID.randomUUID()}.db").also { it.parentFile!!.mkdirs() }
        db = Room.databaseBuilder(context, LibraryDatabase::class.java, databaseFile.absolutePath).build()
        repository = LibraryRepository(context, db)
        withTimeout(5000) { repository.state.first { !it.loading } }
        android.util.Log.d("LibraryFixture", "Repository ready")
        Unit
    }
    @After fun cleanup() = runBlocking {
        repository.close(); db.close()
        context.deleteDatabase(databaseFile.absolutePath)
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        Unit
    }
    @Test fun scanIsRecursiveReadOnlyStableAndSurvivesDatabaseReopen() = runBlocking {
        repository.chooseFolder(FixtureDocumentsProvider.tree()).join()
        val first = repository.state.value
        assertNull(first.error)
        assertEquals(3, first.songs.size)
        assertEquals(2, first.songs.count { it.fileName.equals("Duplicate.mp3", true) })
        assertTrue(first.songs.all { it.durationMs == null })
        assertEquals(0, fixture("stats")!!.getInt("writes"))
        repository.rescan()!!.join()
        assertEquals(first.songs.map { it.id }.toSet(), repository.state.value.songs.map { it.id }.toSet())
        assertEquals(first.songs.associate { it.id to it.firstSeenAt }, repository.state.value.songs.associate { it.id to it.firstSeenAt })
        repository.close(); db.close()
        db = Room.databaseBuilder(context, LibraryDatabase::class.java, databaseFile.absolutePath).build()
        repository = LibraryRepository(context, db)
        val reopened = withTimeout(5000) { repository.state.first { !it.loading } }
        assertEquals(3, reopened.songs.size)
        assertFalse(reopened.scanning)
        assertFalse(reopened.accessLost)
    }
    @Test fun failedScanPreservesSnapshotAndSuccessfulScanMarksMissingWithoutDuplicates() = runBlocking {
        repository.chooseFolder(FixtureDocumentsProvider.tree()).join()
        val before = repository.state.value
        fixture("remove", "a"); fixture("add"); fixture("fail", "d7")
        repository.rescan()!!.join()
        assertNotNull(repository.state.value.error)
        assertEquals(before.songs, repository.state.value.songs)
        assertEquals(before.root, repository.state.value.root)
        fixture("fail")
        repository.rescan()!!.join()
        val after = repository.state.value
        assertNull(after.error)
        assertEquals(4, after.songs.size)
        assertEquals(3, after.songs.count { it.available })
        assertEquals("Duplicate.mp3", after.songs.single { !it.available }.fileName)
    }
    @Test fun revokedAccessKeepsCachedSongsAndReportsRecovery() = runBlocking {
        repository.chooseFolder(FixtureDocumentsProvider.tree()).join()
        fixture("revoke")
        repository.checkAccess()
        val state = withTimeout(5000) { repository.state.first { it.accessLost } }
        assertEquals(3, state.songs.size)
        repository.rescan()!!.join()
        assertNotNull(repository.state.value.error)
        assertEquals(3, repository.state.value.songs.size)
    }
    @Test fun overlappingFolderSelectionKeepsDocumentIdentity() = runBlocking {
        repository.chooseFolder(FixtureDocumentsProvider.tree()).join()
        val before = repository.state.value.songs.filter { it.folderPath.startsWith("October") }.associateBy { it.id }
        fixture("grant", "d0")
        repository.chooseFolder(FixtureDocumentsProvider.tree("d0")).join()
        val after = repository.state.value
        assertNull(after.error)
        assertEquals(before.keys, after.songs.map { it.id }.toSet())
        after.songs.forEach { assertEquals(before[it.id]!!.firstSeenAt, it.firstSeenAt) }
    }
    @Test fun cancellingScanKeepsThePreviousSnapshot() = runBlocking {
        repository.chooseFolder(FixtureDocumentsProvider.tree()).join()
        val before = repository.state.value
        fixture("slow", "d0")
        val job = repository.rescan()!!
        withTimeout(5000) { repository.state.first { it.scanning && it.scanningFolder == "October 2026" } }
        repository.cancelScan()
        job.join()
        assertFalse(repository.state.value.scanning)
        assertEquals(before.songs, repository.state.value.songs)
        assertEquals(before.root, repository.state.value.root)
        assertTrue(repository.state.value.message!!.contains("cancelled"))
    }
}
