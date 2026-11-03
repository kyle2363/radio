package dev.radio.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.UUID

class PlaylistRepositoryTest {
    @Test fun playlistsPersistOrderDeduplicateAndDeleteOnlyMembership() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File(context.cacheDir, ".test-runtime/playlists/${UUID.randomUUID()}.db").also { it.parentFile!!.mkdirs() }
        var db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath).build()
        var scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val tracks = List(3) { LocalSong("song$it", "tree", "folder", "content://fixture/$it", "$it.mp3", "", 1, 1, 1, 60000) }
            db.library().saveSongs(tracks)
            db.library().savePlaybackStart(LocalPlaybackStart("keep-history", "song0", 1, 0))
            db.library().savePlaybackSnapshot(LocalPlaybackSnapshot(payload = "keep bookmark"))
            val repo = PlaylistRepository(db, scope)
            val id = repo.create("  Night Drive  ", listOf("song0", "song1", "song1")).getOrThrow()
            assertTrue(repo.create("night drive").isFailure)
            repo.add(id, listOf("song2", "song0")).getOrThrow()
            repo.move(id, "song2", 0).getOrThrow()
            repo.remove(id, "song1").getOrThrow()
            repo.rename(id, "Late Night").getOrThrow()
            assertEquals(listOf("song2", "song0"), db.playlists().songs(id))
            scope.coroutineContext[Job]?.cancelAndJoin(); db.close()
            db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath).build()
            scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            assertEquals("Late Night", db.playlists().playlist(id)!!.name)
            assertEquals(listOf("song2", "song0"), db.playlists().songs(id))
            PlaylistRepository(db, scope).delete(id).getOrThrow()
            assertNull(db.playlists().playlist(id))
            assertTrue(db.playlists().songs(id).isEmpty())
            assertEquals(tracks.size, db.library().allSongs().size)
            assertEquals(1, db.library().playbackStarts().size)
            assertEquals("keep bookmark", db.library().playbackSnapshot()!!.payload)
        } finally { scope.coroutineContext[Job]?.cancelAndJoin(); db.close(); context.deleteDatabase(file.absolutePath) }
    }
}
