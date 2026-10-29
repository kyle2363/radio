package dev.radio.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test

class PlaybackSnapshotStoreTest {
    @Test fun queuedCheckpointsAreAtomicOrderedAndAnEmptyStartupCannotEraseThem() = runBlocking {
        val db = Room.inMemoryDatabaseBuilder(InstrumentationRegistry.getInstrumentation().targetContext, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val store = PlaybackSnapshotStore(db.library(), scope)
            val songs = List(2000) { i -> LocalSong("id$i", "tree", "folder", "content://fixture/$i", "$i.mp3", "", 1, 1, 1, 60000) }
            repeat(20) { position -> store.save(PlaybackState(queue = songs, playOrder = songs.map { it.id }.reversed(), currentId = "id99", positionMs = position.toLong(), source = "Large queue", shuffled = true)) }
            store.save(PlaybackState())
            val saved = store.load()!!
            assertEquals(19L, saved.positionMs)
            assertEquals(2000, saved.queueIds.size)
            assertEquals("id1999", saved.playOrder.first())
            assertEquals("id99", saved.currentId)
            assertEquals(saved, PlaybackSnapshotStore(db.library(), scope).load())
            db.library().savePlaybackSnapshot(LocalPlaybackSnapshot(payload = "malformed"))
            assertTrue(runCatching { store.load() }.isFailure)
            assertTrue(db.library().playbackStarts().isEmpty())
        } finally { scope.coroutineContext[Job]?.cancelAndJoin(); db.close() }
    }
}
