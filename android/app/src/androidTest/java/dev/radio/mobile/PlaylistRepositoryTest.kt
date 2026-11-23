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

    @Test fun importedM3uKeepsOrderAndNeverReplacesAnExistingPlaylist() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File(context.cacheDir, ".test-runtime/playlists/${UUID.randomUUID()}.db").also { it.parentFile!!.mkdirs() }
        val db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val tracks = listOf(LocalSong("a", "tree", "f", "content://fixture/a", "One.mp3", "Rock", 1, 1, 1, 60000),
                LocalSong("b", "tree", "f", "content://fixture/b", "Two.mp3", "Rock", 1, 1, 1, 60000))
            db.library().saveSongs(tracks)
            val repo = PlaylistRepository(db, scope)
            val existing = repo.create("Road Trip", listOf("a")).getOrThrow()
            val parsed = M3u.parse("#EXTM3U\r\nC:\\Music\\Rock\\Two.mp3\r\nC:\\Music\\Rock\\One.mp3\r\nC:\\Music\\Gone.mp3\r\n")
            val match = M3u.match(parsed.entries, db.library().allSongs())
            val id = repo.import(M3u.playlistName(parsed.name, "Road Trip.m3u"), match.songIds).getOrThrow()
            assertNotEquals(existing, id)
            assertEquals("Road Trip (2)", db.playlists().playlist(id)!!.name)
            assertEquals(listOf("b", "a"), db.playlists().songs(id))
            assertEquals(listOf("a"), db.playlists().songs(existing))
            assertEquals(1, match.missing.size)
        } finally { scope.coroutineContext[Job]?.cancelAndJoin(); db.close(); context.deleteDatabase(file.absolutePath) }
    }
}
