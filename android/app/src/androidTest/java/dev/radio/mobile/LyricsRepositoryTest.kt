package dev.radio.mobile

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.*
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.security.MessageDigest

@RunWith(AndroidJUnit4::class)
class LyricsRepositoryTest {
    private val server = "lyrics-test-server"
    private val catalog = "lyrics-test-catalog"
    private val revision = 4L
    private val manifest = "a".repeat(64)

    private data class Fixture(
        val db: LibraryDatabase,
        val songs: List<LocalSong>,
        val mapping: MutableStateFlow<JournalIdentityProvider.MappingSnapshot>,
        val owner: MutableStateFlow<JournalIdentityProvider.OwnerStatus>,
        val scope: CoroutineScope,
    ) {
        fun repository(gateway: LyricsGateway) = LyricsRepository(
            db = db,
            gateway = gateway,
            paired = { owner.value.paired && !owner.value.blocked && !owner.value.quarantined },
            mappings = { mapping.value },
            mappingFlow = mapping,
            ownerFlow = owner,
            awaitMappings = { mapping.value.takeIf { it.loaded } },
            ownerGeneration = { owner.value.generation },
            identityFor = { song ->
                val entry = mapping.value.entries[song.id]
                JournalIdentity("device-lyrics-test", entry?.serverId, entry?.catalogId, entry?.songId, song.id,
                    entry?.provenance ?: "local_only")
            },
            scope = scope,
        )
    }

    private fun fixture(count: Int = 1): Fixture {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).allowMainThreadQueries().build()
        val songs = (0 until count).map { index ->
            LocalSong("local-$index", "root", "folder", "content://lyrics-fixture/$index", "Song $index.mp3", "Music", 1, 1, 100, 180_000, true)
        }
        val entries = songs.mapIndexed { index, song ->
            JournalIdentityProvider.Entry(song, server, catalog, revision, "${42 + index}", "confirmed:$manifest:${index.toString().padStart(64, 'b')}")
        }
        val mapping = MutableStateFlow(JournalIdentityProvider.MappingSnapshot(9, entries.associateBy { it.song.id }, loaded = true))
        val owner = MutableStateFlow(JournalIdentityProvider.OwnerStatus(11, server, paired = true))
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        runBlocking {
            db.library().saveSongs(songs)
            db.catalogs().saveRevision(PortableCatalogRevision(manifest, server, catalog, revision, "{}", count, 0, 1))
            db.catalogs().activate(PortableCatalogActive(server, catalog, revision, manifest))
        }
        return Fixture(db, songs, mapping, owner, scope)
    }

    private class FakeLyricsGateway(
        private val responder: suspend (String) -> RadioResponse,
    ) : LyricsGateway {
        override suspend fun supportsLyrics() = true
        override suspend fun get(path: String) = responder(path)
    }

    private fun envelope(path: String, text: String): String {
        val songId = path.substringAfter("&song_id=")
        val digest = MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
        return JSONObject()
            .put("schema", "radio.portable.lyrics")
            .put("version", 1)
            .put("catalog_id", catalog)
            .put("revision", revision)
            .put("manifest_sha256", manifest)
            .put("song_id", songId)
            .put("status", "available")
            .put("text", text)
            .put("text_sha256", digest)
            .toString()
    }

    private fun awaitState(repo: LyricsRepository, songId: String, status: LyricsStatus) = runBlocking {
        withTimeout(10_000) { repo.state.first { it.songId == songId && it.status == status } }
    }

    @Test fun lateReplyAfterMappingInvalidationIsDroppedAndNotCached() {
        val fixture = fixture()
        val requestStarted = CompletableDeferred<Unit>()
        val reply = CompletableDeferred<RadioResponse>()
        val text = "This answer belongs to the old mapping"
        val gateway = FakeLyricsGateway {
            requestStarted.complete(Unit)
            reply.await()
        }
        val repository = fixture.repository(gateway)
        try {
            repository.open(fixture.songs.single())
            runBlocking { withTimeout(10_000) { requestStarted.await() } }
            fixture.mapping.value = fixture.mapping.value.copy(generation = 10, entries = emptyMap())
            awaitState(repository, "local-0", LyricsStatus.UNMATCHED)
            reply.complete(RadioResponse(200, envelope("/lyrics?revision=$revision&manifest_sha256=$manifest&song_id=42", text)))
            runBlocking { withTimeout(5_000) { repository.state.first { it.status == LyricsStatus.UNMATCHED } } }
            assertEquals(null, repository.state.value.text)
            assertTrue(runBlocking { fixture.db.lyrics().all() }.isEmpty())
        } finally {
            repository.close(); fixture.scope.cancel(); fixture.db.close()
        }
    }

    @Test fun dismissCancelsTheRequestAndDoesNotPersistItsLateReply() {
        val fixture = fixture()
        val requestStarted = CompletableDeferred<Unit>()
        val reply = CompletableDeferred<RadioResponse>()
        val gateway = FakeLyricsGateway {
            requestStarted.complete(Unit)
            reply.await()
        }
        val repository = fixture.repository(gateway)
        try {
            repository.open(fixture.songs.single())
            runBlocking { withTimeout(10_000) { requestStarted.await() } }
            repository.close()
            reply.complete(RadioResponse(200, envelope("/lyrics?revision=$revision&manifest_sha256=$manifest&song_id=42", "late")))
            Thread.sleep(100)
            assertEquals(LyricsStatus.IDLE, repository.state.value.status)
            assertTrue(runBlocking { fixture.db.lyrics().all() }.isEmpty())
        } finally {
            repository.close(); fixture.scope.cancel(); fixture.db.close()
        }
    }

    @Test fun songChangeFencesLatePriorSongReply() {
        val fixture = fixture(count = 2)
        val oldStarted = CompletableDeferred<Unit>()
        val oldReply = CompletableDeferred<RadioResponse>()
        val gateway = FakeLyricsGateway { path ->
            if (path.endsWith("song_id=42")) {
                oldStarted.complete(Unit)
                oldReply.await()
            } else RadioResponse(200, envelope(path, "Lyrics for second song"))
        }
        val repository = fixture.repository(gateway)
        try {
            repository.open(fixture.songs[0])
            runBlocking { withTimeout(10_000) { oldStarted.await() } }
            repository.open(fixture.songs[1])
            val current = awaitState(repository, "local-1", LyricsStatus.READY)
            assertEquals("Lyrics for second song", current.text)
            oldReply.complete(RadioResponse(200, envelope("/lyrics?revision=$revision&manifest_sha256=$manifest&song_id=42", "Stale first song")))
            Thread.sleep(100)
            assertEquals("local-1", repository.state.value.songId)
            assertEquals("Lyrics for second song", repository.state.value.text)
            val cached = runBlocking { fixture.db.lyrics().all() }
            assertTrue(cached.any { it.localSongId == "local-1" })
            assertTrue(cached.none { it.localSongId == "local-0" })
        } finally {
            repository.close(); fixture.scope.cancel(); fixture.db.close()
        }
    }

    @Test fun pairingOwnerChangeDropsAnInFlightReply() {
        val fixture = fixture()
        val requestStarted = CompletableDeferred<Unit>()
        val reply = CompletableDeferred<RadioResponse>()
        val repository = fixture.repository(FakeLyricsGateway {
            requestStarted.complete(Unit)
            reply.await()
        })
        try {
            repository.open(fixture.songs.single())
            runBlocking { withTimeout(10_000) { requestStarted.await() } }
            fixture.owner.value = fixture.owner.value.copy(generation = 12, paired = false)
            awaitState(repository, "local-0", LyricsStatus.UNPAIRED)
            reply.complete(RadioResponse(200, envelope("/lyrics?revision=$revision&manifest_sha256=$manifest&song_id=42", "From old server")))
            Thread.sleep(100)
            assertEquals(LyricsStatus.UNPAIRED, repository.state.value.status)
            assertEquals(null, repository.state.value.text)
            assertTrue(runBlocking { fixture.db.lyrics().all() }.isEmpty())
        } finally {
            repository.close(); fixture.scope.cancel(); fixture.db.close()
        }
    }
}
