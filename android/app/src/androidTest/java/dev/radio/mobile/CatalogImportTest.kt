package dev.radio.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.*
import dev.radio.mobile.data.*
import dev.radio.mobile.portable.*
import java.io.File
import java.io.RandomAccessFile
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class CatalogImportTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private fun fixture() = JSONObject(instrumentation.context.assets.open("mobile_catalog_golden.json").bufferedReader().use { it.readText() })
    private fun cases(): Map<String, JSONObject> = fixture().getJSONArray("cases").let { rows -> (0 until rows.length()).associate { rows.getJSONObject(it).getString("name") to rows.getJSONObject(it) } }
    private fun bytes(row: JSONObject) = row.getString("manifest_utf8").toByteArray(Charsets.UTF_8)
    private fun blobs(row: JSONObject): Map<String, ByteArray> = row.getJSONObject("blobs").let { obj -> obj.keys().asSequence().associateWith { Base64.getDecoder().decode(obj.getString(it)) } }
    private fun resigned(fields: Map<String, CatalogJson>): ByteArray {
        val unsigned = CatalogJson.Obj(fields - "manifest_sha256")
        return CatalogJson.Obj(unsigned.fields + ("manifest_sha256" to CatalogJson.Str(catalogSha256(unsigned.canonical().toByteArray())))).canonical().toByteArray()
    }

    @Test fun sharedPythonCanonicalAndBinaryFixturesAgreeIncludingUnicodeNumbersDeltaAndFloat16() {
        val imported = mutableMapOf<String, ValidatedCatalog>()
        val allBlobs = mutableMapOf<String, ByteArray>()
        for ((name, row) in cases()) {
            val base = row.optString("base_case").let { imported[it] }
            fun validate(): ValidatedCatalog {
                val catalog = CatalogValidator.manifest(bytes(row), base)
                val supplied = blobs(row)
                for (descriptor in catalog.descriptors) CatalogValidator.validateBlob(descriptor, supplied[descriptor.sha256] ?: allBlobs[descriptor.sha256] ?: throw CatalogException("Missing fixture blob"))
                return catalog
            }
            if (row.getBoolean("valid")) { imported[name] = validate(); allBlobs.putAll(blobs(row)) }
            else assertThrows("Fixture $name must be refused", CatalogException::class.java) { validate() }
        }
        assertTrue(imported.size >= 4)
        assertEquals(3, imported.getValue("full_unicode_numeric").descriptors.size)
    }

    private class MemoryStore(var record: PairingRecord?) : PairingStore {
        override fun load() = StoredPairing(record)
        override fun installationMarker() = "synthetic-marker"
        override fun save(record: PairingRecord) { this.record = record }
        override fun forget() { record = null }
    }
    private inner class Environment : AutoCloseable {
        val rows = cases()
        val signed = JSONObject(instrumentation.context.assets.open("mobile_pairing_golden.json").bufferedReader().use { it.readText() })
        val clock = signed.getLong("verification_time")
        var currentTime = clock
        val policy = EndpointPolicy(true)
        val bundle = PairingBundle.parse(signed.getJSONObject("bundle").toString(), policy)
        val store = MemoryStore(PairingRecord(bundle, WireBase64.encode(ByteArray(32) { 9 }), UUID.randomUUID().toString(), "synthetic-marker", "synthetic-device", clock + 99999))
        var manifest = bytes(rows.getValue("full_unicode_numeric"))
        var delta: ByteArray? = null
        val binary = blobs(rows.getValue("full_unicode_numeric")).toMutableMap()
        val calls = mutableListOf<String>()
        var status = 200
        val statuses = ArrayDeque<Int>()
        var retryAfter: Int? = 1
        var identityRequests = 0
        var identitiesAllowed = Int.MAX_VALUE
        var renewedIdentity = false
        var identityValid = true
        var beforeDownload: suspend (String) -> Unit = {}
        var batching = false
        var batchTransform: (ByteArray) -> ByteArray = { it }
        val api: RadioTransport = object : RadioTransport, RadioBinaryTransport {
            override suspend fun request(endpoint: String, path: String, body: String?, bearer: String?): RadioResponse {
                assertEquals("/identity", path); assertNull(bearer)
                calls += path
                if (++identityRequests > identitiesAllowed) return RadioResponse(429, "", 60)
                return RadioResponse(200, if (identityValid) signed.getJSONObject(if (renewedIdentity) "renewed_identity" else "identity").toString() else "{}")
            }
            override suspend fun download(endpoint: String, path: String, bearer: String, maxBytes: Int): RadioBinaryResponse {
                assertEquals(store.record!!.bearer, bearer)
                assertTrue(calls.contains("/identity")) // A still-fresh verified lease can span blobs.
                calls += path
                beforeDownload(path)
                val responseStatus = if (statuses.isEmpty()) status else statuses.removeFirst()
                if (responseStatus != 200) return RadioBinaryResponse(responseStatus, ByteArray(0), retryAfter)
                val content = when {
                    path.startsWith("/vector-batch?") -> {
                        assertTrue(batching)
                        val query = android.net.Uri.parse(path)
                        val catalog = CatalogValidator.manifest(manifest)
                        assertEquals(catalog.revision.toString(), query.getQueryParameter("revision"))
                        assertEquals(catalog.sha256, query.getQueryParameter("manifest_sha256"))
                        val digests = query.getQueryParameter("digests")!!.split(',')
                        assertTrue(digests.size <= CatalogTransfers.MAX_COUNT)
                        val content = digests.flatMap { binary.getValue(it).asIterable() }.toByteArray()
                        assertTrue(content.size <= CatalogTransfers.MAX_BYTES)
                        batchTransform(content)
                    }
                    path.contains("base_revision") -> delta ?: throw AssertionError("No synthetic delta")
                    path.startsWith("/catalog") -> manifest
                    else -> binary[path.removePrefix("/vectors/")] ?: return RadioBinaryResponse(404, ByteArray(0))
                }
                if (!path.startsWith("/vector-batch?")) assertTrue(content.size <= maxBytes)
                return RadioBinaryResponse(200, content, catalogBatches = batching && path == "/catalog")
            }
        }
        val pairing = PairingRepository(store, api, policy) { currentTime }
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val directory = File(context.cacheDir, ".test-runtime/catalog/${UUID.randomUUID()}").apply { mkdirs() }
        fun repository(retryDelay: suspend (Long) -> Unit = { delay(it) }, timeoutMs: Long = 300000L,
            afterActivate: suspend () -> Unit = {}, beforeActivate: suspend () -> Unit = {}) = CatalogRepository(db, pairing, directory, { 1234L }, beforeActivate, retryDelay, timeoutMs, afterActivate)
        fun prepare(row: JSONObject) {
            val old = CatalogValidator.manifest(manifest)
            val nextBytes = bytes(row); val next = CatalogValidator.manifest(nextBytes)
            val fields = CatalogJson.parse(nextBytes).obj().fields + mapOf("base_revision" to CatalogJson.Num(old.revision.toString()), "base_sha256" to CatalogJson.Str(old.sha256),
                "included_blobs" to CatalogJson.Arr((next.uniqueBlobs.keys - old.uniqueBlobs.keys).sorted().map { CatalogJson.Str(it) }),
                "removed_song_ids" to CatalogJson.Arr((old.songs.keys - next.songs.keys).sorted().map { CatalogJson.Str(it) }))
            delta = resigned(fields); manifest = nextBytes; binary.putAll(blobs(row))
        }
        suspend fun seedLocal() {
            db.library().saveSongs(listOf(LocalSong("local-only", "content://fixture/tree", "folder", "content://fixture/song", "Keep.mp3", "", 1, 1, 8, 12000)))
            db.library().savePlaybackStart(LocalPlaybackStart("keep-attempt", "local-only", 1, 12000))
            db.library().savePlaybackSnapshot(LocalPlaybackSnapshot(payload = "unchanged bookmark"))
        }
        suspend fun assertLocalKept() {
            assertEquals("content://fixture/song", db.library().allSongs().single().uri)
            assertEquals("keep-attempt", db.library().playbackStarts().single().attemptId)
            assertEquals("unchanged bookmark", db.library().playbackSnapshot()!!.payload)
        }
        override fun close() { db.close(); directory.deleteRecursively() }
    }

    @Test fun batchesReduceRequestsAndCorruptTruncatedOrExtraBytesKeepPreviousCatalog() = runBlocking {
        Environment().use { env ->
            env.batching = true
            val repo = env.repository(); repo.importCatalog()
            assertNull(repo.state.value.error)
            assertEquals(1, env.calls.count { it.startsWith("/vector-batch?") })
            assertEquals(0, env.calls.count { it.startsWith("/vectors/") })
            val old = env.db.catalogs().latest()
            env.prepare(env.rows.getValue("float16"))
            for (change in listOf<(ByteArray)->ByteArray>(
                { it.copyOf(it.size - 1) }, { it + byteArrayOf(0) },
                { it.clone().apply { this[lastIndex] = (this[lastIndex].toInt() xor 1).toByte() } })) {
                env.batchTransform = change
                repo.importCatalog()
                assertNotNull(repo.state.value.error)
                assertEquals(old, env.db.catalogs().latest())
                assertTrue(repo.state.value.ready!!.usable)
            }
            env.batchTransform = { it }
            repo.importCatalog()
            assertNull(repo.state.value.error)
            assertEquals(3L, repo.state.value.ready!!.revision)
        }
    }

    @Test fun interruptedBatchesResumeVerifiedBlobsAfterRepositoryRestart() = runBlocking {
        Environment().use { env ->
            env.batching = true
            val full = CatalogJson.parse(env.manifest).obj()
            val template = full.array("embeddings").first().obj()
            val descriptors = (1..130).map { index ->
                val data = java.nio.ByteBuffer.allocate(4).order(java.nio.ByteOrder.LITTLE_ENDIAN).putFloat(index.toFloat()).array()
                val digest = catalogSha256(data); env.binary[digest] = data
                CatalogJson.Obj(template.fields + mapOf("song_id" to CatalogJson.Str("song-$index"), "dimension" to CatalogJson.Num("1"), "byte_length" to CatalogJson.Num("4"), "sha256" to CatalogJson.Str(digest), "blob" to CatalogJson.Str("vectors/$digest.bin"), "normalization" to CatalogJson.Str("none")))
            }
            env.manifest = resigned(full.fields + mapOf("songs" to CatalogJson.Arr((1..130).map { CatalogJson.Obj(mapOf("id" to CatalogJson.Str("song-$it"))) }), "embeddings" to CatalogJson.Arr(descriptors),
                "included_blobs" to CatalogJson.Arr(descriptors.map { it["sha256"] }.sortedBy { it.text() })))
            val blocked = CompletableDeferred<Unit>()
            var batches = 0
            env.beforeDownload = { path -> if (path.startsWith("/vector-batch?") && ++batches == 2) { blocked.complete(Unit); awaitCancellation() } }
            val repo = env.repository()
            val job = launch { repo.importCatalog() }
            blocked.await(); repo.cancelImport(); job.join()
            assertNull(env.db.catalogs().latest())
            assertEquals(64, env.directory.listFiles()!!.count { it.name.endsWith(".bin") })
            env.beforeDownload = {}; env.calls.clear()
            val restarted = env.repository(); restarted.importCatalog()
            assertNull(restarted.state.value.error)
            assertEquals(2, env.calls.count { it.startsWith("/vector-batch?") })
            assertTrue(restarted.state.value.ready!!.usable)
        }
    }

    @Test fun explicitFullImportPublishesOnlyAfterEveryBlobThenRestartsOfflineWithoutLocalSongChanges() = runBlocking {
        Environment().use { env ->
            env.seedLocal()
            val repo = env.repository(); repo.load()
            assertTrue(env.calls.isEmpty()); assertNull(repo.state.value.ready)
            repo.importCatalog()
            assertNull(repo.state.value.error)
            assertEquals(1L, repo.state.value.ready!!.revision)
            assertEquals(3, env.db.catalogs().vectors(env.db.catalogs().latest()!!.manifestSha256).size)
            env.assertLocalKept()
            val calls = env.calls.size
            env.store.forget()
            val restarted = env.repository(); restarted.load()
            assertEquals(calls, env.calls.size)
            assertTrue(restarted.state.value.ready!!.usable)
        }
    }
    @Test fun corruptAndMissingBlobKeepPreviousRevisionAndRetryReusesVerifiedBytes() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog()
            val old = env.db.catalogs().latest()!!
            val newer = env.rows.getValue("float16"); env.prepare(newer)
            val digest = blobs(newer).keys.first()
            val original = env.binary.getValue(digest)
            env.binary[digest] = original.clone().apply { this[0] = (this[0].toInt() xor 1).toByte() }
            repo.importCatalog(); assertNotNull(repo.state.value.error); assertEquals(old, env.db.catalogs().latest())
            env.binary.remove(digest)
            repo.importCatalog(); assertEquals(old, env.db.catalogs().latest())
            env.binary[digest] = original
            repo.importCatalog(); assertNull(repo.state.value.error); assertEquals(3L, repo.state.value.ready!!.revision)
        }
    }
    @Test fun cancelAndProcessRestartReuseOnlyCompletedVerifiedBlobs() = runBlocking {
        Environment().use { env ->
            val repo = env.repository()
            val blocked = CompletableDeferred<Unit>()
            var requests = 0
            env.beforeDownload = { path -> if (path.startsWith("/vectors/") && ++requests == 2) { blocked.complete(Unit); awaitCancellation() } }
            val job = launch { repo.importCatalog() }
            blocked.await(); repo.cancelImport(); job.join()
            assertNull(env.db.catalogs().latest())
            assertEquals(1, env.directory.listFiles()!!.count { it.name.endsWith(".bin") })
            env.beforeDownload = {}; env.calls.clear()
            val restarted = env.repository(); restarted.importCatalog()
            assertEquals(2, env.calls.count { it.startsWith("/vectors/") })
            assertTrue(restarted.state.value.ready!!.usable)
        }
    }
    @Test fun transactionFailureAndCancelledPublicationCannotExposePartialCatalog() = runBlocking {
        Environment().use { env ->
            val repo = env.repository { throw IllegalStateException("synthetic transaction fault") }
            repo.importCatalog()
            assertNotNull(repo.state.value.error)
            assertNull(env.db.catalogs().latest())
            assertNull(env.db.catalogs().revision(env.bundle.serverId, CatalogValidator.manifest(env.manifest).sha256))
            env.repository().importCatalog()
            assertNotNull(env.db.catalogs().latest())
        }
    }
    @Test fun cancellationBeforeCommitRollsBackButCancellationAfterCommitReportsSavedCatalog() = runBlocking {
        Environment().use { env ->
            val before = env.repository(beforeActivate = { currentCoroutineContext().cancel(); yield() })
            val cancelled = launch { before.importCatalog() }; cancelled.join()
            assertTrue(cancelled.isCancelled); assertNull(env.db.catalogs().latest())
            val after = env.repository(afterActivate = { currentCoroutineContext().cancel(); yield() })
            val committed = launch { after.importCatalog() }; committed.join()
            assertTrue(committed.isCancelled); assertNotNull(env.db.catalogs().latest())
            assertTrue(after.state.value.ready!!.usable)
            assertTrue(after.state.value.message!!.contains("completed"))
        }
    }
    @Test fun forgottenPairingDuringTransferCannotPublishEvenAfterBytesWereVerified() = runBlocking {
        Environment().use { env ->
            val repo = env.repository()
            env.beforeDownload = { path -> if (path.startsWith("/vectors/")) env.store.forget() }
            repo.importCatalog()
            assertNull(env.db.catalogs().latest()); assertNotNull(repo.state.value.error)
        }
    }
    @Test fun revokedCredentialAndWrongSignedIdentityNeverEraseImportedMetadata() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog(); val old = env.db.catalogs().latest()
            env.status = 401; repo.importCatalog()
            assertTrue(env.pairing.state.value.blocked); assertEquals(old, env.db.catalogs().latest())
            val calls = env.calls.size; repo.importCatalog(); assertEquals(calls, env.calls.size)
            env.store.record = env.store.record!!.copy(authorizationBlocked = false)
            env.identityValid = false; env.calls.clear(); repo.importCatalog()
            assertEquals(listOf("/identity"), env.calls); assertEquals(old, env.db.catalogs().latest())
        }
    }
    @Test fun wrongCatalogRollbackAndMutatedSameRevisionAreRejectedBeforeBlobRequests() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog(); val old = env.db.catalogs().latest()
            val fields = CatalogJson.parse(env.manifest).obj().fields
            for (change in listOf("catalog_id" to CatalogJson.Str("different"), "revision" to CatalogJson.Num("0"), "future" to CatalogJson.Bool(false))) {
                env.calls.clear(); env.manifest = resigned(fields + change); repo.importCatalog()
                assertNotNull(repo.state.value.error); assertEquals(old, env.db.catalogs().latest()); assertEquals(0, env.calls.count { it.startsWith("/vectors/") })
            }
        }
    }
    @Test fun verifiedExactBaseDeltaAdoptsFullIdentityAndNeverDownloadsInheritedVectors() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog()
            val deltaBytes = bytes(env.rows.getValue("delta_removed_and_inherited")); val delta = CatalogValidator.manifest(deltaBytes, CatalogValidator.manifest(env.manifest))
            val fields = CatalogJson.parse(deltaBytes).obj().fields + mapOf("base_revision" to CatalogJson.Null, "base_sha256" to CatalogJson.Null,
                "included_blobs" to CatalogJson.Arr(delta.uniqueBlobs.keys.sorted().map { CatalogJson.Str(it) }))
            env.manifest = resigned(fields); env.delta = deltaBytes; env.calls.clear()
            repo.importCatalog()
            assertNull(repo.state.value.error); assertEquals(2L, repo.state.value.ready!!.revision)
            assertEquals(0, env.calls.count { it.startsWith("/vectors/") })
            assertTrue(env.calls.contains("/catalog?revision=2&base_revision=1"))
            assertEquals(CatalogValidator.manifest(env.manifest).sha256, env.db.catalogs().latest()!!.manifestSha256)
            repo.importCatalog(); assertNull(repo.state.value.error)
            assertEquals(1, repo.state.value.ready!!.songCount)
        }
    }
    @Test fun initialFullEndpointCannotReturnADeltaAndRetireTheOnlyValidBase() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog(); val old = env.db.catalogs().latest()
            env.manifest = bytes(env.rows.getValue("delta_removed_and_inherited"))
            env.calls.clear(); repo.importCatalog()
            assertNotNull(repo.state.value.error); assertEquals(old, env.db.catalogs().latest())
            assertTrue(repo.state.value.ready!!.usable)
            assertEquals(0, env.calls.count { it.startsWith("/vectors/") })
        }
    }
    @Test fun restoredMetadataWithMissingPrivateBlobsReportsReimportAndExplicitRepairWorks() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog()
            env.directory.listFiles()!!.first().delete()
            val restarted = env.repository(); val count = env.calls.size; restarted.load()
            assertFalse(restarted.state.value.ready!!.usable); assertEquals(count, env.calls.size)
            restarted.importCatalog(); assertTrue(restarted.state.value.ready!!.usable)
            assertEquals(1, env.calls.drop(count).count { it.startsWith("/vectors/") })
        }
    }
    @Test fun stalePrivateBlobsAndStagingAndInactiveRoomRowsAreReclaimedBeforeQuotaAdmission() = runBlocking {
        Environment().use { env ->
            RandomAccessFile(File(env.directory, "synthetic-quota"), "rw").use { it.setLength(CatalogValidator.MAX_CACHE_BYTES) }
            File(env.directory, ".staging-dead-process").writeBytes(byteArrayOf(1))
            env.db.catalogs().saveRevision(PortableCatalogRevision("inactive", "server", "catalog", 99, "orphan", 1, 1, 1))
            env.db.catalogs().saveSongs(listOf(PortableCatalogSong("inactive", "orphan", "{}")))
            val repo = env.repository(); repo.importCatalog()
            assertNull(repo.state.value.error); assertNotNull(env.db.catalogs().latest())
            assertFalse(File(env.directory, "synthetic-quota").exists()); assertFalse(File(env.directory, ".staging-dead-process").exists())
            assertNull(env.db.catalogs().revision("server", "inactive")); assertTrue(env.db.catalogs().songs("inactive").isEmpty())
            val oldDigest = env.db.catalogs().latest()!!.manifestSha256
            val oldBlobDigests = CatalogValidator.manifest(env.manifest).uniqueBlobs.keys
            env.prepare(env.rows.getValue("float16")); repo.importCatalog(); assertNull(repo.state.value.error)
            assertNull(env.db.catalogs().revision(env.bundle.serverId, oldDigest)); assertTrue(env.db.catalogs().songs(oldDigest).isEmpty())
            assertTrue(oldBlobDigests.none { File(env.directory, "$it.bin").exists() })
        }
    }
    @Test fun quotaNeverReclaimsActiveServerBlobsToMakeSpaceForAnImport() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog(); val old = env.db.catalogs().latest()
            val active = env.directory.listFiles()!!.first()
            RandomAccessFile(active, "rw").use { it.setLength(CatalogValidator.MAX_CACHE_BYTES) }
            env.calls.clear(); repo.importCatalog()
            assertNotNull(repo.state.value.error); assertEquals(old, env.db.catalogs().latest()); assertTrue(active.exists())
            assertEquals(0, env.calls.count { it.startsWith("/vectors/") })
        }
    }
    @Test fun rateLimitedBlobWaitsCancellablyAndRetriesWithFreshAuthorizationChecks() = runBlocking {
        Environment().use { env ->
            var waited = 0L
            val repo = env.repository(retryDelay = { waited += it; yield() })
            env.statuses.addAll(listOf(200, 429, 200))
            repo.importCatalog()
            assertNull(repo.state.value.error); assertEquals(1000L, waited)
            assertEquals(1, env.identityRequests)
            assertEquals(4, env.calls.count { it.startsWith("/vectors/") })
            val vectorCalls = env.calls.filter { it.startsWith("/vectors/") }
            assertEquals(vectorCalls[0], vectorCalls[1])
        }
    }
    @Test fun leaseExpiringDuringRateLimitWaitIsRenewedBeforeRetrySendsBearer() = runBlocking {
        Environment().use { env ->
            val expiry = env.signed.getJSONObject("descriptor_payload").getLong("expires_at")
            val repo = env.repository(retryDelay = { env.currentTime = expiry; env.renewedIdentity = true; yield() })
            env.statuses.addAll(listOf(200, 429, 200))
            repo.importCatalog()
            assertNull(repo.state.value.error); assertEquals(2, env.identityRequests)
            assertEquals(2L, env.store.record!!.revision)
        }
    }
    @Test fun repeatedRateLimitsAndDeadlineKeepPreviousCatalogAndCancelWaitingPromptly() = runBlocking {
        Environment().use { env ->
            val repo = env.repository(); repo.importCatalog(); val old = env.db.catalogs().latest()
            env.status = 429
            val bounded = env.repository(retryDelay = { yield() }); bounded.importCatalog()
            assertNotNull(bounded.state.value.error); assertEquals(old, env.db.catalogs().latest())
            val timed = env.repository(timeoutMs = 30)
            val job = launch { timed.importCatalog() }; job.join()
            assertTrue(job.isCancelled); assertEquals(old, env.db.catalogs().latest())
            assertTrue(timed.state.value.message!!.contains("timed out"))
            val waiting = CompletableDeferred<Unit>()
            val cancelled = env.repository(retryDelay = { waiting.complete(Unit); awaitCancellation() })
            val cancelJob = launch { cancelled.importCatalog() }; waiting.await(); cancelled.cancelImport(); cancelJob.join()
            assertEquals(old, env.db.catalogs().latest()); assertTrue(cancelJob.isCancelled)
        }
    }
    @Test fun manyBlobsReuseOneFreshSignedIdentityLeaseBelowAnonymousIdentityRateLimit() = runBlocking {
        Environment().use { env ->
            val full = CatalogJson.parse(env.manifest).obj()
            val template = full.array("embeddings").first().obj()
            val descriptors = (1..30).map { index ->
                val data = java.nio.ByteBuffer.allocate(4).order(java.nio.ByteOrder.LITTLE_ENDIAN).putFloat(index.toFloat()).array()
                val digest = catalogSha256(data); env.binary[digest] = data
                CatalogJson.Obj(template.fields + mapOf("song_id" to CatalogJson.Str("song-$index"), "dimension" to CatalogJson.Num("1"), "byte_length" to CatalogJson.Num("4"), "sha256" to CatalogJson.Str(digest), "blob" to CatalogJson.Str("vectors/$digest.bin"), "normalization" to CatalogJson.Str("none")))
            }
            env.manifest = resigned(full.fields + mapOf("songs" to CatalogJson.Arr((1..30).map { CatalogJson.Obj(mapOf("id" to CatalogJson.Str("song-$it"))) }), "embeddings" to CatalogJson.Arr(descriptors),
                "included_blobs" to CatalogJson.Arr(descriptors.map { it["sha256"] }.sortedBy { it.text() })))
            env.identitiesAllowed = 20
            val repo = env.repository(); repo.importCatalog()
            assertNull(repo.state.value.error); assertEquals(30, repo.state.value.ready!!.vectorCount)
            assertEquals(1, env.identityRequests)
        }
    }
}
