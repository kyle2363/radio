package dev.radio.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.UUID
import org.json.JSONObject

class ListeningJournalTest {
    @Test fun onlyCanonicalSkipReasonsEnterWirePayloads() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val repo = ListeningJournalRepository(context, db, scope)
        val song = LocalSong("song", "root", "folder", "content://fixture/song", "Song.mp3", "", 1, 1, 1, 10000)
        val identity = JournalIdentity("device", localId = song.id)
        try {
            repo.started(song, "skip", 0, identity)
            repo.finished(identity, "skip", "song_skipped", 500, 10000, "wrong_mood")
            repo.started(song, "technical", 0, identity)
            repo.finished(identity, "technical", "attempt_stopped", 500, 10000, "decoder_error")
            repo.started(song, "navigation", 0, identity)
            repo.finished(identity, "navigation", "song_skipped", 500, 10000, "explicit_navigation")
            withTimeout(5000) { repo.state.first { it.pendingWrites == 0 && it.history.size == 6 } }
            val rows = db.journal().events()
            assertEquals("wrong_mood", JSONObject(rows.single { it.attemptId == "skip" && it.kind == "song_skipped" }.payload).getString("reason"))
            assertFalse(JSONObject(rows.single { it.attemptId == "technical" && it.kind == "attempt_stopped" }.payload).has("reason"))
            assertFalse(JSONObject(rows.single { it.attemptId == "navigation" && it.kind == "song_skipped" }.payload).has("reason"))
            assertEquals(5, db.journal().outbox().size)
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin(); db.close() }
    }
    @Test fun historicStartsAreNotBackfilledAndExplicitReplayRetryIsIdempotent() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val repo = ListeningJournalRepository(context, db, scope)
        val song = LocalSong("song", "root", "folder", "content://fixture/song", "Song.mp3", "", 1, 1, 1, 10000)
        val identity = JournalIdentity("device", localId = song.id)
        try {
            db.library().savePlaybackStart(LocalPlaybackStart("historic", song.id, 1, 0))
            repo.started(song, "historic", 0, identity)
            repo.finished(identity, "historic", "song_completed", 10000, 10000, "natural_end")
            repo.replay(song, "replay", identity); repo.replay(song, "replay", identity)
            withTimeout(5000) { repo.state.first { it.pendingWrites == 0 && it.history.size == 1 } }
            assertEquals(listOf("song_replayed"), db.journal().events().map { it.kind })
            assertEquals(1, db.journal().outbox().size)
            assertEquals(1, db.library().playbackStarts().size)
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin(); db.close() }
    }
    @Test fun failedTransactionRetriesInOrderAndCommittedPayloadsSurviveRepositoryRestart() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File(context.cacheDir, ".test-runtime/journal/${UUID.randomUUID()}.db").also { it.parentFile!!.mkdirs() }
        var db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val song = LocalSong("song", "root", "folder", "content://fixture/song", "Song.mp3", "", 1, 1, 1, 10000)
        var failures = 2
        val repo = ListeningJournalRepository(context, db, scope) { if (failures-- > 0) error("Injected rollback") }
        val identity = JournalIdentity("device", localId = song.id)
        try {
            repo.started(song, "attempt", 0, identity)
            repo.finished(identity, "attempt", "song_completed", 2500, 10000, "natural_end")
            withTimeout(10000) { repo.state.first { it.history.size == 2 && it.pendingWrites == 0 } }
            val before = db.journal().events()
            assertEquals(listOf("song_started", "song_completed"), before.map { it.kind })
            assertEquals(listOf(1L, 2L), before.map { it.logicalClock })
            assertEquals(0.25, before.last().completion!!, 0.0)
            assertEquals(1, db.library().playbackStarts().size)
            assertEquals(before.map { it.payload }, db.journal().outbox().sortedBy { row -> before.indexOfFirst { it.eventId == row.eventId } }.map { it.payload })
            scope.coroutineContext[Job]!!.cancelAndJoin()
            db.close()
            db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath).build()
            val secondScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val restarted = ListeningJournalRepository(context, db, secondScope)
            restarted.started(song, "attempt", 0, identity)
            restarted.finished(identity, "attempt", "song_skipped", 9999, 10000, "explicit_next")
            withTimeout(5000) { restarted.state.first { it.pendingWrites == 0 && it.history.size == 2 } }
            assertEquals(before, db.journal().events())
            secondScope.coroutineContext[Job]!!.cancelAndJoin()
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin(); db.close(); context.deleteDatabase(file.absolutePath) }
    }
    @Test fun exhaustedAutomaticRetriesExposeFailureAndManualRetryKeepsStartBeforeTerminal() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val song = LocalSong("song", "root", "folder", "content://fixture/song", "Song.mp3", "", 1, 1, 1, 10000)
        var failing = true
        val repo = ListeningJournalRepository(context, db, scope) { if (failing) error("Storage full") }
        val identity = JournalIdentity("device", localId = song.id)
        try {
            repo.started(song, "attempt", 0, identity)
            repo.finished(identity, "attempt", "song_skipped", 500, 10000, "explicit_next")
            withTimeout(5000) { repo.state.first { it.error != null } }
            assertTrue(db.journal().events().isEmpty())
            assertTrue(db.journal().outbox().isEmpty())
            assertTrue(db.library().playbackStarts().isEmpty())
            assertNull(db.journal().clock())
            failing = false; repo.retry()
            withTimeout(5000) { repo.state.first { it.pendingWrites == 0 && it.history.size == 2 } }
            assertEquals(listOf("song_started", "song_skipped"), db.journal().events().map { it.kind })
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin(); db.close() }
    }
    @Test fun favoriteAndDislikePersistTogetherAndDoNotFabricatePlaybackStarts() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val repo = ListeningJournalRepository(context, db, scope)
        val song = LocalSong("song", "root", "folder", "content://fixture/song", "Song.mp3", "", 1, 1, 1, 10000)
        try {
            repo.setFavorite(song, true); repo.setDisliked(song, true)
            withTimeout(5000) { repo.state.first { it.pendingWrites == 0 && it.feedback[song.id]?.disliked == true } }
            assertEquals(LocalSongFeedback(song.id, false, true), db.journal().feedback(song.id))
            assertEquals(listOf("favorite_changed", "favorite_changed", "dislike_changed"), db.journal().events().map { it.kind })
            assertTrue(db.library().playbackStarts().isEmpty())
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin(); db.close() }
    }
}
