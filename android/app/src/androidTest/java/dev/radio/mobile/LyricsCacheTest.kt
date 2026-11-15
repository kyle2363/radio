package dev.radio.mobile

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicInteger

@RunWith(AndroidJUnit4::class)
class LyricsCacheTest {
    private val server = "cache-test-server"
    private val catalog = "cache-test-catalog"
    private val revision = 8L
    private val manifest = "c".repeat(64)

    private data class Fixture(
        val db: LibraryDatabase,
        val song: LocalSong,
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
                JournalIdentity("device-cache", entry?.serverId, entry?.catalogId, entry?.songId, song.id,
                    "owner:stable:anchor:${entry?.provenance ?: "local"}")
            },
            scope = scope,
        )
    }

    private fun fixture(): Fixture {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).allowMainThreadQueries().build()
        val song = LocalSong("local-cache", "root", "folder", "content://lyrics-cache/song", "Cache Song.mp3", "Music", 1, 1, 100, 180_000, true)
        val entry = JournalIdentityProvider.Entry(song, server, catalog, revision, "84", "confirmed:$manifest:${"d".repeat(64)}")
        val mapping = MutableStateFlow(JournalIdentityProvider.MappingSnapshot(12, mapOf(song.id to entry), loaded = true))
        val owner = MutableStateFlow(JournalIdentityProvider.OwnerStatus(21, server, paired = true))
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        runBlocking {
            db.library().saveSongs(listOf(song))
            db.catalogs().saveRevision(PortableCatalogRevision(manifest, server, catalog, revision, "{}", 1, 0, 1))
            db.catalogs().activate(PortableCatalogActive(server, catalog, revision, manifest))
        }
        return Fixture(db, song, mapping, owner, scope)
    }

    private class Gateway(private val answer: suspend (String) -> RadioResponse) : LyricsGateway {
        val supportCalls = AtomicInteger()
        val getCalls = AtomicInteger()
        override suspend fun supportsLyrics(): Boolean { supportCalls.incrementAndGet(); return true }
        override suspend fun get(path: String): RadioResponse { getCalls.incrementAndGet(); return answer(path) }
    }

    private fun sha(text: String) = MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    private fun envelope(path: String, text: String): String = JSONObject()
        .put("schema", "radio.portable.lyrics").put("version", 1).put("catalog_id", catalog).put("revision", revision)
        .put("manifest_sha256", manifest).put("song_id", path.substringAfter("&song_id="))
        .put("status", "available").put("text", text).put("text_sha256", sha(text)).toString()

    private fun ready(repository: LyricsRepository) = runBlocking {
        withTimeout(10_000) { repository.state.first { it.status == LyricsStatus.READY } }
    }

    @Test fun privateCacheOpensOfflineWithoutCapabilityOrLyricsRequestEvenAfterCounterChanges() {
        val f = fixture()
        val text = "Saved stanza 🌙\n\nSecond stanza"
        val online = Gateway { path -> RadioResponse(200, envelope(path, text)) }
        val first = f.repository(online)
        val offline = Gateway { error("cache hit must not contact the desktop") }
        val second = f.repository(offline)
        try {
            first.open(f.song)
            assertEquals(text, ready(first).text)
            assertEquals(1, runBlocking { f.db.lyrics().all().size })
            first.close()

            // Volatile counters change after process restart; the stable owner and mapping still match.
            f.mapping.value = f.mapping.value.copy(generation = 99)
            f.owner.value = f.owner.value.copy(generation = 100)
            second.open(f.song)
            val saved = ready(second)
            assertEquals(text, saved.text)
            assertTrue(saved.fromCache)
            assertEquals(0, offline.supportCalls.get())
            assertEquals(0, offline.getCalls.get())
        } finally { first.close(); second.close(); f.scope.cancel(); f.db.close() }
    }

    @Test fun failedExplicitRefreshKeepsTheValidatedSavedLyricsVisible() = runBlocking {
        val f = fixture()
        val calls = AtomicInteger()
        val text = "Cached copy"
        val gateway = Gateway { path ->
            if (calls.incrementAndGet() == 1) RadioResponse(200, envelope(path, text))
            else throw ConnectionFailure("Server temporarily unavailable.")
        }
        val repository = f.repository(gateway)
        try {
            repository.open(f.song)
            assertEquals(text, ready(repository).text)
            repository.refresh()
            val state = repository.state.value
            assertEquals(LyricsStatus.READY, state.status)
            assertEquals(text, state.text)
            assertTrue(state.fromCache)
            assertTrue(state.message!!.contains("Refresh failed"))
            assertEquals(text, runBlocking { f.db.lyrics().all().single().text })
        } finally { repository.close(); f.scope.cancel(); f.db.close() }
    }

    @Test fun closingDuringRefreshCancelsTheTrackedRequest() = runBlocking {
        val f = fixture()
        val calls = AtomicInteger()
        val refreshStarted = CompletableDeferred<Unit>()
        val refreshCancelled = CompletableDeferred<Unit>()
        val text = "Saved"
        val gateway = Gateway { path ->
            if (calls.incrementAndGet() == 1) RadioResponse(200, envelope(path, text))
            else try { refreshStarted.complete(Unit); awaitCancellation() } finally { refreshCancelled.complete(Unit) }
        }
        val repository = f.repository(gateway)
        try {
            repository.open(f.song)
            assertEquals(text, ready(repository).text)
            val refreshJob = launch { repository.refresh() }
            withTimeout(10_000) { refreshStarted.await() }
            repository.close()
            withTimeout(5_000) { refreshCancelled.await() }
            refreshJob.join()
            assertEquals(LyricsStatus.IDLE, repository.state.value.status)
            assertEquals(text, runBlocking { f.db.lyrics().all().single().text })
        } finally { repository.close(); f.scope.cancel(); f.db.close() }
    }

    @Test fun cacheEvictsLeastRecentlyViewedRowsToBothConfiguredLimitsAndKeepsCurrentSong() {
        val small = fixture()
        val smallRepo = small.repository(Gateway { path -> RadioResponse(200, envelope(path, "current")) })
        try {
            runBlocking { repeat(100) { small.db.lyrics().save(cacheRow("row-$it", "x".repeat(20), it.toLong())) } }
            smallRepo.open(small.song)
            ready(smallRepo)
            val rows = runBlocking { small.db.lyrics().all() }
            assertTrue(rows.size <= 100)
            assertTrue(rows.any { it.localSongId == small.song.id })
        } finally { smallRepo.close(); small.scope.cancel(); small.db.close() }

        val large = fixture()
        val largeRepo = large.repository(Gateway { path -> RadioResponse(200, envelope(path, "🎵".repeat(15_000))) })
        try {
            runBlocking { repeat(100) { large.db.lyrics().save(cacheRow("big-$it", "🎶".repeat(15_000), it.toLong())) } }
            largeRepo.open(large.song)
            ready(largeRepo)
            val rows = runBlocking { large.db.lyrics().all() }
            assertTrue(rows.sumOf { it.text.toByteArray(Charsets.UTF_8).size.toLong() } <= 5L * 1024 * 1024)
            assertTrue(rows.any { it.localSongId == large.song.id })
        } finally { largeRepo.close(); large.scope.cancel(); large.db.close() }
    }

    private fun cacheRow(key: String, text: String, at: Long) = LocalLyricsCache(
        cacheKey = key, serverId = server, catalogId = catalog, manifestSha256 = manifest, revision = revision,
        localSongId = "old-$key", portableSongId = "84", localIdentity = "old:$key", ownerGeneration = 1,
        mappingGeneration = 1, text = text, textSha256 = sha(text), fetchedAt = at, lastViewedAt = at,
    )
}
