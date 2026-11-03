package dev.radio.mobile.portable

import android.content.Context
import android.system.Os
import androidx.room.withTransaction
import dev.radio.mobile.connection.*
import dev.radio.mobile.data.*
import java.io.File
import java.io.FileOutputStream
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class CatalogInfo(val serverId: String, val catalogId: String, val revision: Long, val songCount: Int,
    val vectorCount: Int, val importedAt: Long, val usable: Boolean = true)
data class CatalogState(val loaded: Boolean = false, val busy: Boolean = false, val progress: String = "",
    val ready: CatalogInfo? = null, val error: String? = null, val message: String? = null)

/** Explicit metadata import only. No content resolver, media player, library write or scheduler. */
class CatalogRepository internal constructor(private val db: LibraryDatabase, private val pairing: PairingRepository,
    private val directory: File, private val now: () -> Long = { System.currentTimeMillis() },
    private val beforeActivate: suspend () -> Unit = {},
    private val retryDelay: suspend (Long) -> Unit = { delay(it) }, private val importTimeoutMs: Long = 5 * 60 * 1000L,
    private val afterActivate: suspend () -> Unit = {}) {
    init { require(importTimeoutMs in 1..(5 * 60 * 1000L)) }
    private val mutable = MutableStateFlow(CatalogState())
    val state = mutable.asStateFlow()
    // All UI repository instances share one private store. Serializing storage work also
    // prevents one import's reclamation from deleting another import's staged blobs.
    private val mutex get() = storageMutex
    @Volatile private var importJob: Job? = null
    private var target: Pair<String, ValidatedCatalog>? = null

    suspend fun load() = withContext(Dispatchers.IO) { mutex.withLock { refresh() } }
    private suspend fun refresh(message: String? = null) {
        val record = db.catalogs().latest()
        var usable = true
        if (record != null) usable = try { val catalog = saved(record); verifyAll(catalog); true } catch (_: Exception) { false }
        mutable.value = CatalogState(loaded = true, ready = record?.info(usable), message = message ?: if (!usable) "Imported metadata kept. Vectors need reimport before they can be used." else null)
    }
    fun cancelImport() { importJob?.cancel() }
    suspend fun importCatalog() = withContext(Dispatchers.IO) {
        if (!mutex.tryLock()) return@withContext
        JournalIdentityProvider.invalidateMappings()
        importJob = currentCoroutineContext()[Job]
        target = null
        try {
            mutable.value = mutable.value.copy(busy = true, progress = "Checking trusted server…", error = null, message = null)
            withTimeout(importTimeoutMs) { importNow() }
            withContext(NonCancellable) { refresh("Catalog imported. Local music and playback are unchanged.") }
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) {
                val committed = target?.let { (server, catalog) -> db.catalogs().active(server)?.manifestSha256 == catalog.sha256 && catalog.descriptors.all { verified(it) } } == true
                refresh(if (committed) "Catalog import completed. The verified catalog is saved."
                    else if (cancelled is TimeoutCancellationException) "Import timed out. Previous catalog kept; retry resumes verified blobs." else "Import cancelled. Previous catalog kept; retry resumes verified blobs.")
            }
            throw cancelled
        } catch (failure: Exception) {
            val message = when (failure) { is CatalogException -> failure.message; is ConnectionFailure -> failure.safeMessage; else -> "Catalog import failed. Previous catalog kept; retry when ready." }
            withContext(NonCancellable) { refresh(); mutable.value = mutable.value.copy(error = message) }
        } finally { target = null; importJob = null; mutex.unlock() }
    }

    private suspend fun importNow() {
        val session = pairing.catalogSession()
        val prior = db.catalogs().active(session.serverId)
        // Validation of an existing snapshot is local only. Corrupt/missing cached bytes never
        // acquire authority from the filename; full imports can repair them explicitly.
        val base = prior?.let { try { saved(it) } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { null } }
        mutable.value = mutable.value.copy(progress = "Downloading catalog metadata…")
        val next = CatalogValidator.manifest(fetch(session, "/catalog", CatalogJson.MAX_BYTES), base)
        catalogRequire(next.baseRevision == null && next.baseSha256 == null, "Server must return a full prepared catalog before import. Previous catalog kept.")
        if (prior != null) {
            catalogRequire(next.catalogId == prior.catalogId, "Server returned a different catalog. Previous catalog kept.")
            catalogRequire(next.revision >= prior.revision, "Server catalog revision went backwards. Previous catalog kept.")
            if (next.revision == prior.revision) catalogRequire(next.sha256 == prior.manifestSha256, "Server changed an immutable catalog revision. Previous catalog kept.")
            // Persist the full canonical manifest as the revision identity. Delta and full
            // representations have different self-hashes for the same logical revision.
            // Validate the exact-base delta as a transfer plan, never change active identity.
            if (base != null && base.baseRevision == null && next.revision > base.revision && base.descriptors.all { verified(it) }) {
                val delta = CatalogValidator.manifest(fetch(session, "/catalog?revision=${next.revision}&base_revision=${base.revision}", CatalogJson.MAX_BYTES), base)
                catalogRequire(delta.catalogId == next.catalogId && delta.revision == next.revision && delta.songs == next.songs && delta.descriptors == next.descriptors, "Delta does not describe the prepared revision.")
                val representationFields = setOf("base_revision", "base_sha256", "included_blobs", "removed_song_ids", "manifest_sha256")
                catalogRequire(CatalogJson.parse(delta.bytes).obj().fields - representationFields == CatalogJson.parse(next.bytes).obj().fields - representationFields, "Delta changed prepared revision metadata.")
                for (descriptor in delta.descriptors) if (descriptor.sha256 !in delta.included) catalogRequire(verified(descriptor), "Exact-base vector is missing or corrupt.")
            }
        }
        target = session.serverId to next
        currentCoroutineContext().ensureActive()
        directory.mkdirs()
        catalogRequire(directory.isDirectory, "Private catalog storage is unavailable.")
        val blobs = next.uniqueBlobs
        reclaim(blobs.keys)
        val missing = blobs.values.filterNot { verified(it) }
        val required = missing.sumOf { it.byteLength.toLong() }
        val cached = directory.listFiles().orEmpty().sumOf { if (it.isFile) it.length() else 0L }
        // Reserve bytes for both vectors and metadata/SQLite journals before any blob request.
        val metadataReserve = next.bytes.size.toLong() * 4 + 8L * 1024 * 1024
        catalogRequire(db.catalogs().retainedManifestBytes(next.sha256) * 4 + metadataReserve <= 128L * 1024 * 1024, "Retained catalog metadata reaches the phone storage limit. Previous catalogs kept.")
        catalogRequire(cached + required <= CatalogValidator.MAX_CACHE_BYTES, "Private catalog cache limit reached. Previous catalog kept.")
        catalogRequire(directory.usableSpace >= required + metadataReserve, "Not enough free storage for a safe catalog import.")
        // Omitted delta blobs must be present and verified locally. Never substitute a blob
        // from an unrelated cached revision just because its digest was advertised.
        for (descriptor in next.descriptors) if (descriptor.sha256 !in next.included) catalogRequire(verified(descriptor), "Exact-base vector is missing or corrupt. Reimport a full prepared catalog.")
        var done = 0
        val batches = if (session.catalogBatches) CatalogTransfers.batches(missing) else missing.map { listOf(it) }
        for ((index, batch) in batches.withIndex()) {
            currentCoroutineContext().ensureActive()
            mutable.value = mutable.value.copy(progress = "Importing batch ${index + 1}/${batches.size} · $done/${missing.size} vectors…")
            val path = if (session.catalogBatches) CatalogTransfers.path(next, batch) else "/vectors/${batch.single().sha256}"
            val bytes = fetch(session, path, batch.sumOf { it.byteLength })
            // Validate the whole bounded response before publishing any of its blobs.
            for ((descriptor, vector) in CatalogTransfers.validate(batch, bytes)) {
                currentCoroutineContext().ensureActive()
                publishBlob(descriptor, vector)
                done++
            }
        }
        // Shared bytes can have distinct normalization/provenance declarations; verify every
        // descriptor, not only the representative used for download deduplication.
        verifyAll(next)
        currentCoroutineContext().ensureActive()
        mutable.value = mutable.value.copy(progress = "Saving verified catalog…")
        pairing.adoptCatalog(session) {
            db.withTransaction {
                currentCoroutineContext().ensureActive()
                val current = db.catalogs().active(session.serverId)
                catalogRequire(current == null || current.catalogId == next.catalogId && current.revision <= next.revision, "A newer catalog is already active.")
                if (current?.revision == next.revision) catalogRequire(current.manifestSha256 == next.sha256, "Conflicting immutable catalog revision.")
                val record = PortableCatalogRevision(next.sha256, session.serverId, next.catalogId, next.revision, next.bytes.toString(Charsets.UTF_8), next.songs.size, next.descriptors.size, now())
                db.catalogs().saveRevision(record)
                for (chunk in next.songs.entries.chunked(250)) db.catalogs().saveSongs(chunk.map { PortableCatalogSong(next.sha256, it.key, it.value) })
                for (chunk in next.descriptors.chunked(250)) db.catalogs().saveVectors(chunk.map { PortableCatalogVector(next.sha256, it.songId, it.space, it.model, it.modelVersion, it.dimension, it.metric, it.normalization, it.dtype, it.byteLength, it.sha256) })
                currentCoroutineContext().ensureActive()
                beforeActivate()
                currentCoroutineContext().ensureActive()
                db.catalogs().activate(PortableCatalogActive(session.serverId, next.catalogId, next.revision, next.sha256))
                db.catalogs().deleteInactiveSongs()
                db.catalogs().deleteInactiveVectors()
                db.catalogs().deleteInactiveRevisions()
            }
        }
        afterActivate()
        // Once the active pointer committed, best-effort cache housekeeping cannot turn a
        // complete adoption into a reported import failure. Next admission retries cleanup.
        withContext(NonCancellable) { runCatching { reclaim(emptySet()) } }
    }
    private suspend fun fetch(session: PairingRepository.CatalogSession, path: String, limit: Int): ByteArray {
        repeat(7) { attempt ->
            currentCoroutineContext().ensureActive()
            // Identity and credentials are revalidated after every rate-limit wait.
            val response = try { pairing.catalogDownload(session, path, limit) }
                catch (failure: ConnectionFailure) { if (failure.rateLimited) RadioBinaryResponse(429, ByteArray(0), failure.retryAfterSeconds) else throw failure }
            if (response.status == 429 && attempt < 6) {
                val wait = response.retryAfterSeconds?.takeIf { it in 1..60 } ?: 5
                mutable.value = mutable.value.copy(progress = "Waiting for the server… retrying in ${wait}s")
                repeat(wait) { currentCoroutineContext().ensureActive(); retryDelay(1000) }
            } else {
                catalogRequire(response.status == 200, when (response.status) { 409 -> "No prepared catalog is ready on the desktop. Prepare it there, then retry."; 404 -> "Prepared catalog or vector is missing. Previous catalog kept."; 429 -> "Server is busy. Previous catalog kept; retry shortly."; else -> "Server could not complete catalog import. Previous catalog kept." })
                catalogRequire(response.body.size <= limit, "Catalog response exceeded its limit.")
                return response.body
            }
        }
        throw CatalogException("Server is busy. Previous catalog kept; retry shortly.")
    }
    /** Delete only private, inactive data. All active server catalogs and the current import's
     * digest references survive. Interrupted verified blobs are reused if the next attempted
     * manifest still references them; unrelated leftovers never consume permanent quota. */
    private suspend fun reclaim(inflight: Set<String>) {
        val retained = db.withTransaction {
            db.catalogs().deleteInactiveSongs(); db.catalogs().deleteInactiveVectors(); db.catalogs().deleteInactiveRevisions()
            db.catalogs().activeBlobDigests().toSet() + inflight
        }
        val canonicalName = Regex("([0-9a-f]{64})\\.bin")
        for (file in directory.listFiles().orEmpty()) {
            currentCoroutineContext().ensureActive()
            if (!file.isFile) continue
            val digest = canonicalName.matchEntire(file.name)?.groupValues?.get(1)
            if (digest == null || digest !in retained) catalogRequire(file.delete(), "Could not reclaim private catalog storage. Previous catalog kept.")
        }
    }
    private suspend fun saved(record: PortableCatalogRevision, depth: Int = 0): ValidatedCatalog {
        catalogRequire(depth <= 1, "Catalog base chain exceeds the supported limit.")
        val raw = CatalogJson.parse(record.manifest.toByteArray(Charsets.UTF_8)).obj()
        val base = if (raw["base_revision"] == CatalogJson.Null) null else {
            val prior = db.catalogs().revision(record.serverId, raw.text("base_sha256", 64)) ?: throw CatalogException("Saved catalog base is missing.")
            catalogRequire(prior.serverId == record.serverId)
            saved(prior, depth + 1)
        }
        val catalog = CatalogValidator.manifest(record.manifest.toByteArray(Charsets.UTF_8), base)
        catalogRequire(catalog.catalogId == record.catalogId && catalog.revision == record.revision && catalog.sha256 == record.manifestSha256)
        return catalog
    }
    private fun blob(descriptor: VectorDescriptor) = File(directory, descriptor.sha256 + ".bin")
    private fun verified(descriptor: VectorDescriptor): Boolean = try {
        val file = blob(descriptor)
        catalogRequire(file.isFile && file.length() == descriptor.byteLength.toLong())
        CatalogValidator.validateBlob(descriptor, file.readBytes()); true
    } catch (_: Exception) { false }
    private suspend fun verifyAll(catalog: ValidatedCatalog) {
        for (descriptor in catalog.descriptors) { currentCoroutineContext().ensureActive(); catalogRequire(verified(descriptor), "Saved catalog vector is missing or corrupt. Previous metadata kept.") }
    }
    private fun publishBlob(descriptor: VectorDescriptor, bytes: ByteArray) {
        val temporary = File(directory, ".staging-${UUID.randomUUID()}")
        try {
            FileOutputStream(temporary).use { it.write(bytes); it.fd.sync() }
            // Both are private same-directory names; atomic replacement can repair a corrupt
            // cached digest without exposing partial bytes or affecting another valid digest.
            Os.rename(temporary.absolutePath, blob(descriptor).absolutePath)
        } finally { temporary.delete() }
    }
    private fun PortableCatalogRevision.info(usable: Boolean) = CatalogInfo(serverId, catalogId, revision, songCount, vectorCount, importedAt, usable)
    companion object {
        internal val storageMutex = Mutex()
        fun get(context: Context, pairing: PairingRepository): CatalogRepository = CatalogRepository(LibraryRepository.get(context).db, pairing, File(context.applicationContext.noBackupFilesDir, "portable_catalog_blobs"))
    }
}
