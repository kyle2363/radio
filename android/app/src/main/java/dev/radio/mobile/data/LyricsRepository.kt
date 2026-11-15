package dev.radio.mobile.data

import android.content.Context
import androidx.room.withTransaction
import dev.radio.mobile.connection.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import org.json.JSONObject
import java.security.MessageDigest

enum class LyricsStatus {
    IDLE, CHECKING_MATCH, UNPAIRED, UNMATCHED, UNSUPPORTED_SERVER, LOADING,
    READY, NO_LYRICS, AUTH_REQUIRED, REVISION_UNAVAILABLE, ERROR
}

data class LyricsState(
    val songId: String? = null,
    val status: LyricsStatus = LyricsStatus.IDLE,
    val text: String? = null,
    val message: String? = null,
    val fromCache: Boolean = false,
)

/** Authenticated lyric access, kept behind PairingRepository's trusted identity/credential lease. */
interface LyricsGateway {
    suspend fun supportsLyrics(): Boolean
    suspend fun get(path: String): RadioResponse
}

class LyricsPermissionFailure : Exception()

private class PairedLyricsGateway(private val pairing: PairingRepository) : LyricsGateway {
    private var session: PairingRepository.ProfileSyncSession? = null
    override suspend fun supportsLyrics(): Boolean {
        val current = pairing.connectedSession()
        val response = pairing.connectedRequest(current, "/capabilities")
        if (response.status == 403) throw LyricsPermissionFailure()
        if (response.status in 500..599) throw ConnectionFailure("Desktop unavailable. Retry to get lyrics.")
        if (response.status != 200) throw ConnectionFailure("Desktop could not verify lyric access.")
        val caps = try { JSONObject(response.body) } catch (_: Exception) { throw ConnectionFailure("Desktop returned invalid capability data.") }
        val scopes = caps.optJSONArray("scopes") ?: throw LyricsPermissionFailure()
        val allowed = (0 until scopes.length()).map { scopes.optString(it) }.contains("catalog:read")
        if (!allowed) throw LyricsPermissionFailure()
        val supported = caps.optBoolean("plain_lyrics", false) && caps.optInt("plain_lyrics_version", 0) == 1 &&
            caps.optInt("plain_lyrics_max_text_bytes", 0) == LyricsEnvelopeValidator.MAX_TEXT_BYTES
        if (allowed && supported) session = current
        return supported
    }
    override suspend fun get(path: String): RadioResponse {
        val current = session ?: throw ConnectionFailure("Pair this phone before requesting lyrics.")
        return pairing.connectedRequest(current, path)
    }
}

/** A private, bounded offline cache. Requests exist only while the lyrics sheet is open. */
class LyricsRepository internal constructor(
    private val db: LibraryDatabase,
    private val gateway: LyricsGateway,
    private val paired: suspend () -> Boolean,
    private val mappings: () -> JournalIdentityProvider.MappingSnapshot = { JournalIdentityProvider.mappings.value },
    private val mappingFlow: Flow<JournalIdentityProvider.MappingSnapshot> = JournalIdentityProvider.mappings,
    private val ownerFlow: StateFlow<JournalIdentityProvider.OwnerStatus> = JournalIdentityProvider.ownerStatus,
    private val awaitMappings: suspend () -> JournalIdentityProvider.MappingSnapshot? = {
        withTimeoutOrNull(30_000L) { mappingFlow.first { it.loaded } }
    },
    private val ownerGeneration: () -> Long = { JournalIdentityProvider.pairingGeneration() },
    private val identityFor: (LocalSong) -> JournalIdentity = { JournalIdentityProvider.captureCurrent(it) },
    private val now: () -> Long = System::currentTimeMillis,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
) {
    private val mutable = MutableStateFlow(LyricsState())
    val state: StateFlow<LyricsState> = mutable.asStateFlow()
    private var requestJob: Job? = null
    private var watchJob: Job? = null
    private var requestGeneration = 0L
    private var openGeneration = 0L

    @Synchronized fun open(song: LocalSong) {
        val openToken = ++openGeneration
        watchJob?.cancel()
        mutable.value = LyricsState(songId = song.id, status = LyricsStatus.CHECKING_MATCH)
        requestJob?.cancel()
        requestGeneration++
        watchJob = scope.launch {
            var observed: JournalIdentityProvider.MappingSnapshot? = null
            var observedOwner: JournalIdentityProvider.OwnerStatus? = null
            combine(mappingFlow, ownerFlow) { next, owner -> next to owner }.collect { (next, owner) ->
                val previous = observed
                val previousOwner = observedOwner
                if (openToken != openGeneration || mutable.value.songId != song.id ||
                    previous != null && previousOwner != null && next.generation == previous.generation &&
                    next.loaded == previous.loaded && owner.generation == previousOwner.generation) return@collect
                val changedEntry = previous?.entries?.get(song.id) != next.entries[song.id] ||
                    previous?.loaded != next.loaded || owner.generation != previousOwner?.generation
                observed = next
                observedOwner = owner
                synchronized(this@LyricsRepository) {
                    if (openToken != openGeneration || mutable.value.songId != song.id) return@synchronized
                    requestJob?.cancel()
                    val nextGeneration = ++requestGeneration
                    mutable.value = when {
                        owner.blocked || owner.quarantined -> LyricsState(song.id, LyricsStatus.AUTH_REQUIRED, message = "Reconnect to your desktop to get lyrics.")
                        !owner.paired -> LyricsState(song.id, LyricsStatus.UNPAIRED, message = "Connect to your desktop to get lyrics.")
                        else -> LyricsState(songId = song.id, status = LyricsStatus.CHECKING_MATCH,
                            message = if (changedEntry || !next.loaded) "Checking this song’s match…" else null)
                    }
                    if (owner.paired && !owner.blocked && !owner.quarantined) {
                        requestJob = scope.launch { resolve(song, nextGeneration, force = false) }
                    }
                }
            }
        }
    }

    suspend fun refresh() {
        val (current, expectedGeneration) = synchronized(this) { mutable.value to requestGeneration }
        if (current.songId == null || current.status == LyricsStatus.LOADING) return
        val song = db.library().song(current.songId) ?: return
        val job = synchronized(this) {
            if (requestGeneration != expectedGeneration || mutable.value.songId != current.songId) return
            requestJob?.cancel()
            val generation = ++requestGeneration
            scope.launch { resolve(song, generation, force = true) }.also { requestJob = it }
        }
        job.join()
    }

    @Synchronized fun close() {
        openGeneration++
        requestGeneration++
        requestJob?.cancel()
        requestJob = null
        watchJob?.cancel()
        watchJob = null
        mutable.value = LyricsState()
    }

    private suspend fun resolve(song: LocalSong, generation: Long, force: Boolean) = withContext(Dispatchers.IO) {
        var bindingEntry: JournalIdentityProvider.Entry? = null
        var bindingIdentity: JournalIdentity? = null
        var bindingMappingGeneration: Long? = null
        var bindingOwner: Long? = null
        var bindingCatalog: PortableCatalogRevision? = null
        var fallbackText: String? = mutable.value.takeIf { it.songId == song.id }?.text
        var fallbackCache = mutable.value.takeIf { it.songId == song.id }?.fromCache == true
        try {
            if (!paired()) { publish(generation, LyricsState(song.id, LyricsStatus.UNPAIRED, message = "Connect to your desktop to get lyrics.")); return@withContext }
            var mapping = mappings()
            if (!mapping.loaded) {
                publish(generation, LyricsState(song.id, LyricsStatus.CHECKING_MATCH, message = "Checking this song’s match…"))
                mapping = awaitMappings() ?: run {
                    publish(generation, LyricsState(song.id, LyricsStatus.ERROR, message = "Matching is still unavailable. Check the Matching screen and retry."))
                    return@withContext
                }
            }
            val entry = mapping.entries[song.id]?.takeIf {
                it.song == song && song.available && it.provenance.substringBefore(':') in setOf("confirmed", "high_confidence") &&
                    it.serverId.isNotBlank() && it.catalogId.isNotBlank() && it.songId.isNotBlank()
            }
            if (entry == null) { publish(generation, LyricsState(song.id, LyricsStatus.UNMATCHED, message = "Match this song to get desktop lyrics.")); return@withContext }
            bindingEntry = entry
            bindingMappingGeneration = mapping.generation
            val catalog = db.catalogs().active(entry.serverId)
            if (catalog == null || catalog.catalogId != entry.catalogId || catalog.revision != entry.revision) {
                publish(generation, LyricsState(song.id, LyricsStatus.REVISION_UNAVAILABLE, message = "Prepare and import a new desktop catalog to get lyrics.")); return@withContext
            }
            val owner = ownerGeneration()
            val identity = identityFor(song)
            bindingIdentity = identity
            bindingOwner = owner
            bindingCatalog = catalog
            if (identity.serverId != entry.serverId || identity.catalogId != entry.catalogId || identity.songId != entry.songId) {
                publish(generation, LyricsState(song.id, LyricsStatus.UNMATCHED, message = "Match this song to get desktop lyrics.")); return@withContext
            }
            val key = cacheKey(identity, song, entry)
            val dao = db.lyrics()
            // Catalog revisions are immutable. Reclaim obsolete text after confirming the new active mapping.
            runCatching { dao.deleteObsolete(entry.serverId, entry.catalogId, entry.revision, catalog.manifestSha256) }
            if (!force) {
                val cached = runCatching { dao.get(key) }.getOrNull()
                if (cached != null && cached.text.toByteArray(Charsets.UTF_8).size in 1..LyricsEnvelopeValidator.MAX_TEXT_BYTES &&
                    digest(cached.text) == cached.textSha256 && cached.localIdentity == identity.provenance && cached.portableSongId == entry.songId) {
                    fallbackText = cached.text
                    fallbackCache = true
                    if (stillCurrent(song, entry, mapping.generation, owner, identity.provenance, generation)) {
                        runCatching { dao.viewed(key, now()) }
                        publish(generation, LyricsState(song.id, LyricsStatus.READY, cached.text, fromCache = true))
                        return@withContext
                    }
                    return@withContext
                }
            } else {
                val cached = runCatching { dao.get(key) }.getOrNull()
                if (cached != null && cached.text.toByteArray(Charsets.UTF_8).size in 1..LyricsEnvelopeValidator.MAX_TEXT_BYTES &&
                    digest(cached.text) == cached.textSha256 && cached.localIdentity == identity.provenance && cached.portableSongId == entry.songId) {
                    fallbackText = cached.text
                    fallbackCache = true
                }
            }
            if (!stillCurrent(song, entry, mapping.generation, owner, identity.provenance, generation)) return@withContext
            publish(generation, LyricsState(song.id, LyricsStatus.LOADING, text = fallbackText, message = "Loading lyrics…", fromCache = fallbackCache))
            val response = withTimeout(15_000L) {
                if (!gateway.supportsLyrics()) return@withTimeout null
                val path = "/lyrics?revision=${entry.revision}&manifest_sha256=${catalog.manifestSha256}&song_id=${entry.songId}"
                if (path.toByteArray(Charsets.UTF_8).size > 1024 || !RadioApiClient.isLyricsPath(path))
                    throw ConnectionFailure("The matched recording has an unsupported identifier.")
                gateway.get(path)
            }
            if (response == null) {
                if (!stillCurrent(song, entry, mapping.generation, owner, identity.provenance, generation)) return@withContext
                if (fallbackText != null) publish(generation, LyricsState(song.id, LyricsStatus.READY, fallbackText,
                    "Saved on this phone. Update the desktop app to refresh lyrics.", fromCache = true))
                else publish(generation, LyricsState(song.id, LyricsStatus.UNSUPPORTED_SERVER, message = "Update the desktop app to get lyrics."))
                return@withContext
            }
            if (!stillCurrent(song, entry, mapping.generation, owner, identity.provenance, generation)) return@withContext
            when (response.status) {
                200 -> when (val envelope = LyricsEnvelopeValidator.parse(response.body, entry.catalogId, entry.revision, catalog.manifestSha256, entry.songId)) {
                    is LyricsEnvelope.Available -> {
                        val current = LocalLyricsCache(key, entry.serverId, entry.catalogId, catalog.manifestSha256, entry.revision,
                            song.id, entry.songId, identity.provenance, owner, mapping.generation, envelope.text, envelope.textSha256, now(), now())
                        if (stillCurrent(song, entry, mapping.generation, owner, identity.provenance, generation)) {
                            runCatching { saveBounded(current) }
                            publish(generation, LyricsState(song.id, LyricsStatus.READY, envelope.text))
                        }
                    }
                    is LyricsEnvelope.Unavailable -> if (fallbackText != null) publish(generation, LyricsState(song.id, LyricsStatus.READY,
                        fallbackText, message = "Saved on this phone. The desktop has no lyrics in this revision.", fromCache = true))
                    else publish(generation, LyricsState(song.id, LyricsStatus.NO_LYRICS,
                        message = if (envelope.reason == "too_large") "Lyrics are too large to display." else "No lyrics available for this song."))
                }
                401, 403 -> {
                    if (response.status == 401) purgeServer(entry.serverId)
                    publish(generation, LyricsState(song.id, LyricsStatus.AUTH_REQUIRED, message = "Reconnect to your desktop to get lyrics."))
                }
                404, 409 -> publish(generation, LyricsState(song.id, LyricsStatus.REVISION_UNAVAILABLE, message = "Prepare and import a new desktop catalog to get lyrics."))
                else -> if (response.status >= 500 && fallbackText != null) publish(generation, LyricsState(song.id, LyricsStatus.READY,
                    fallbackText, "Saved on this phone. Desktop is unavailable; refresh to retry.", fromCache = true))
                    else publish(generation, LyricsState(song.id, LyricsStatus.ERROR, message = "Desktop unavailable. Retry to get lyrics."))
            }
        } catch (_: LyricsPermissionFailure) {
            if (current(generation)) {
                val entry = bindingEntry; val identity = bindingIdentity; val owner = bindingOwner; val mapGeneration = bindingMappingGeneration
                val stillBound = entry != null && identity != null && owner != null && mapGeneration != null &&
                    runCatching { stillCurrent(song, entry, mapGeneration, owner, identity.provenance, generation) }.getOrDefault(false)
                if (fallbackText != null && stillBound) publish(generation, LyricsState(song.id, LyricsStatus.READY,
                    fallbackText, "Saved on this phone. Desktop lyric access needs reconnecting.", fromCache = true))
                else if (stillBound) publish(generation, LyricsState(song.id, LyricsStatus.AUTH_REQUIRED, message = "Reconnect to your desktop to get lyrics."))
            }
        } catch (timeout: TimeoutCancellationException) {
            if (current(generation)) {
                val entry = bindingEntry; val identity = bindingIdentity; val owner = bindingOwner; val mapGeneration = bindingMappingGeneration
                val stillBound = entry != null && identity != null && owner != null && mapGeneration != null &&
                    runCatching { stillCurrent(song, entry, mapGeneration, owner, identity.provenance, generation) }.getOrDefault(false)
                if (fallbackText != null && stillBound) publish(generation, LyricsState(song.id, LyricsStatus.READY,
                    fallbackText, "Saved on this phone. Refresh timed out.", fromCache = fallbackCache))
                else if (stillBound) publish(generation, LyricsState(song.id, LyricsStatus.ERROR, message = "Lyrics request timed out. Retry."))
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (failure: ConnectionFailure) {
            if (current(generation)) {
                val entry = bindingEntry
                val identity = bindingIdentity
                val owner = bindingOwner
                val mapGeneration = bindingMappingGeneration
                val catalog = bindingCatalog
                val stillBound = entry != null && identity != null && owner != null && mapGeneration != null && catalog != null &&
                    runCatching { stillCurrent(song, entry, mapGeneration, owner, identity.provenance, generation) }.getOrDefault(false)
                if (!paired()) {
                    entry?.let { purgeServer(it.serverId) }
                    publish(generation, LyricsState(song.id, LyricsStatus.AUTH_REQUIRED, message = "Reconnect to your desktop to get lyrics."))
                } else if (fallbackText != null && stillBound) publish(generation, LyricsState(song.id, LyricsStatus.READY,
                    fallbackText, "Saved on this phone. Refresh failed.", fromCache = fallbackCache))
                else if (stillBound) publish(generation, LyricsState(song.id, LyricsStatus.ERROR, message = failure.safeMessage))
            }
        } catch (_: Exception) {
            if (current(generation)) {
                val entry = bindingEntry; val identity = bindingIdentity; val owner = bindingOwner; val mapGeneration = bindingMappingGeneration
                val stillBound = entry != null && identity != null && owner != null && mapGeneration != null &&
                    runCatching { stillCurrent(song, entry, mapGeneration, owner, identity.provenance, generation) }.getOrDefault(false)
                if (fallbackText != null && stillBound) publish(generation, LyricsState(song.id, LyricsStatus.READY,
                    fallbackText, "Saved on this phone. Refresh failed.", fromCache = fallbackCache))
                else if (stillBound || entry == null || identity == null || owner == null || mapGeneration == null)
                    publish(generation, LyricsState(song.id, LyricsStatus.ERROR, message = "Could not load lyrics. Retry."))
            }
        }
    }

    private suspend fun saveBounded(entry: LocalLyricsCache) = db.withTransaction {
        val dao = db.lyrics()
        dao.save(entry)
        var rows = dao.all()
        var bytes = rows.sumOf { it.text.toByteArray(Charsets.UTF_8).size.toLong() }
        while ((rows.size > MAX_ENTRIES || bytes > MAX_CACHE_BYTES) && rows.size > 1) {
            val victim = rows.firstOrNull { it.cacheKey != entry.cacheKey } ?: break
            dao.delete(victim.cacheKey)
            bytes -= victim.text.toByteArray(Charsets.UTF_8).size
            rows = rows.filterNot { it.cacheKey == victim.cacheKey }
        }
    }

    private suspend fun stillCurrent(song: LocalSong, entry: JournalIdentityProvider.Entry, mappingGeneration: Long,
        owner: Long, provenance: String, request: Long): Boolean {
        if (!current(request) || ownerGeneration() != owner || !paired()) return false
        val latestMapping = mappings()
        if (!latestMapping.loaded || latestMapping.generation != mappingGeneration || latestMapping.entries[song.id] != entry) return false
        if (db.library().song(song.id) != song) return false
        val active = db.catalogs().active(entry.serverId) ?: return false
        if (active.catalogId != entry.catalogId || active.revision != entry.revision || active.manifestSha256 !=
            entry.provenance.split(':').getOrNull(1) || identityFor(song).provenance != provenance) return false
        return true
    }

    @Synchronized private fun current(generation: Long) = requestGeneration == generation && mutable.value.songId != null
    @Synchronized private fun publish(generation: Long, value: LyricsState) { if (current(generation)) mutable.value = value }

    private fun cacheKey(identity: JournalIdentity, song: LocalSong, entry: JournalIdentityProvider.Entry): String = digest(
        listOf(identity.serverId, identity.catalogId, entry.revision.toString(), identity.provenance, song.id, entry.songId).joinToString("\u0000"))
    private fun digest(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    private suspend fun purgeServer(serverId: String) { runCatching { db.lyrics().deleteServer(serverId) } }

    companion object {
        private const val MAX_ENTRIES = 100
        private const val MAX_CACHE_BYTES = 5L * 1024 * 1024
        @Volatile private var instance: LyricsRepository? = null

        fun get(context: Context): LyricsRepository = get(context, LibraryRepository.get(context).db)

        fun get(context: Context, db: LibraryDatabase): LyricsRepository = instance?.takeIf { it.db === db } ?: synchronized(this) {
            instance?.takeIf { it.db === db } ?: run {
                val app = context.applicationContext
                val policy = EndpointPolicy(dev.radio.mobile.BuildConfig.DEBUG)
                val store = PairedServerStore(app, policy)
                val pairing = PairingRepository(store, RadioApiClient(policy), policy)
                LyricsRepository(db, PairedLyricsGateway(pairing), {
                    val snapshot = store.load()
                    !snapshot.quarantined && snapshot.record?.let { it.deviceId != null && !it.authorizationBlocked } == true
                }).also { instance = it }
            }
        }

        /** Pairing deletion and known credential revocation discard this server's private lyric text. */
        internal suspend fun purgeForServer(serverId: String) { instance?.purgeServer(serverId) }

        internal suspend fun purgeForContext(context: Context, serverId: String) {
            val db = LibraryRepository.get(context.applicationContext).db
            runCatching { db.lyrics().deleteServer(serverId) }
        }
    }
}
