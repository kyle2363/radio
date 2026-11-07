package dev.radio.mobile.recommendation

import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.JournalIdentityProvider.MappingSnapshot
import dev.radio.mobile.data.LocalSong
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** What the desktop currently allows this phone to do. */
data class ConnectedAccess(val key: String, val capable: Boolean, val permitted: Boolean, val tunable: Boolean = false) {
    companion object {
        /** Parses GET /capabilities. [key] identifies the paired server and device. */
        fun parse(key: String, body: String): ConnectedAccess {
            val root = runCatching { ConnectedJson.parse(body) as? Map<*, *> }.getOrNull() ?: throw ConnectionFailure("Invalid server response.")
            val scopes = (root["scopes"] as? List<*>)?.filterIsInstance<String>().orEmpty()
            return ConnectedAccess(key, root["connected_recommendations"] == true, "recommend:read" in scopes, root["connected_tuning"] == true)
        }
    }
}

/** Authenticated channel to the paired server. Production wraps PairingRepository; tests wrap a fake RadioTransport. */
interface ConnectedServerLink {
    /** Fresh identity-checked capability probe. Throws [ConnectionFailure] when unpaired, blocked or unreachable. */
    suspend fun access(): ConnectedAccess
    /** POST JSON to a path under /api/portable/v1. The recommendations path uses the long deadline. */
    suspend fun post(path: String, body: String): RadioResponse
}

enum class ConnectedFailureKind(val label: String) {
    NOT_CAPABLE("This desktop doesn't offer connected recommendations"),
    NOT_PERMITTED("Not permitted on desktop"),
    UNREACHABLE("Can't reach the server"),
    TIMEOUT("The server didn't answer in time"),
    STALE("The server's view of this phone was out of date"),
    SUPERSEDED("Replaced by a newer song"),
    INSUFFICIENT("The server had no usable picks"),
    NO_MATCHES("No matched songs to share with the server"),
    CATALOG_MISMATCH("The desktop catalog doesn't match this phone's"),
    REJECTED("The server rejected the request"),
    SERVER_ERROR("The server couldn't complete the request"),
    INVALID_REPLY("The server's reply didn't match the request"),
}
data class ConnectedFailure(val kind: ConnectedFailureKind, val detail: String? = null) {
    val message: String get() = detail?.let { "${kind.label} ($it)" } ?: kind.label
}

data class ConnectedPick(val song: LocalSong, val slot: String, val reason: String)

/** Everything that must still hold for a set to be usable (see [ConnectedRadioSource.revalidate]). */
data class ConnectedFence(val anchorLocalId: String, val mood: String, val exploration: Double, val vibe: String?,
    val mappingGeneration: Long, val syncEpoch: Long, val connectedEpoch: Long = 0, val availabilityDigest: String = "",
    val tuning: ConnectedTuning = ConnectedTuning())

data class ConnectedSet(val anchorLocalId: String, val requestId: String, val resultId: String, val rankedBy: String, val vibeLocked: Boolean,
    val fence: ConnectedFence, val continuation: List<ConnectedPick>, val options: List<ConnectedPick>, val dropped: Int, val receivedAt: Long,
    val applied: ConnectedApplied? = null)

data class ConnectedRecent(val localId: String, val outcome: String)

/** Inputs captured on the phone when a song actually starts. */
data class ConnectedRequest(
    val anchor: LocalSong, val mood: String, val exploration: Double, val vibeLocalId: String?,
    val mappings: MappingSnapshot, val available: Map<String, LocalSong>, val recent: List<ConnectedRecent>,
    val excludeLocalIds: Set<String>, val isDisliked: (String) -> Boolean, val fence: ConnectedFence,
    val tuning: ConnectedTuning = ConnectedTuning(), val played: List<ConnectedPlayed> = emptyList(),
)

data class ConnectedStatus(
    val enabled: Boolean = false, val permitted: Boolean? = null, val pendingAnchor: String? = null, val pendingSince: Long = 0,
    val lastAt: Long = 0, val lastOk: Boolean? = null, val lastMessage: String = "Off", val lastFailure: ConnectedFailure? = null,
    /** Whether the paired desktop accepts phone tuning; null until it has been asked. */
    val tunable: Boolean? = null,
)

/** Digest rule shared with the desktop (plan section 8). */
object ConnectedDigest {
    private val byteOrder = Comparator<ByteArray> { a, b ->
        val n = minOf(a.size, b.size)
        for (i in 0 until n) { val d = (a[i].toInt() and 255) - (b[i].toInt() and 255); if (d != 0) return@Comparator d }
        a.size - b.size
    }
    fun of(ids: Collection<String>): String {
        val sorted = ids.distinct().map { it.toByteArray(Charsets.UTF_8) }.sortedWith(byteOrder)
        val md = MessageDigest.getInstance("SHA-256")
        sorted.forEachIndexed { i, bytes -> if (i > 0) md.update('\n'.code.toByte()); md.update(bytes) }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}

/** The part of the mapping snapshot the server is told about. */
internal class AvailabilitySnapshot(val catalogId: String, val revision: Long, val ids: List<String>, val digest: String,
    val byServerId: Map<String, LocalSong>, val serverIdByLocal: Map<String, String>) {
    companion object {
        const val MAX_IDS = 20000
        fun from(mappings: MappingSnapshot, available: Map<String, LocalSong>): AvailabilitySnapshot? {
            val live = mappings.entries.values.filter { it.song.available && available[it.song.id] == it.song }
            val best = live.groupBy { it.catalogId to it.revision }.entries.maxWithOrNull(compareBy({ it.key.second }, { it.value.size })) ?: return null
            val entries = best.value.distinctBy { it.songId }.sortedBy { it.songId }.take(MAX_IDS)
            val ids = entries.map { it.songId }
            return AvailabilitySnapshot(best.key.first, best.key.second, ids, ConnectedDigest.of(ids),
                entries.associate { it.songId to it.song }, entries.associate { it.song.id to it.songId })
        }
    }
}

private typealias Outcome = Pair<ConnectedSet?, ConnectedFailure?>
private fun fail(kind: ConnectedFailureKind, detail: String? = null): Outcome = null to ConnectedFailure(kind, detail)

/**
 * Per-song connected recommendation requests. It owns no playback: callers read [set] and [status]
 * and decide. One request is in flight at a time; a newer song start replaces it, and the older result
 * is discarded even if it arrives.
 */
class ConnectedRadioSource(
    private val link: ConnectedServerLink, private val scope: CoroutineScope,
    private val timeoutMs: Long = RECOMMEND_TIMEOUT_MS, private val now: () -> Long = System::currentTimeMillis,
    private val newId: () -> String = { UUID.randomUUID().toString() },
) {
    companion object {
        const val RECOMMEND_TIMEOUT_MS = 75_000L
        private const val ACCESS_TTL_MS = 5 * 60_000L
        private const val DENIED_TTL_MS = 30_000L
        private val SLOTS = setOf("exploit", "explore", "wildcard", "context")
        const val MAX_PLAYED = 100
    }
    private val mutable = MutableStateFlow(ConnectedStatus())
    val status: StateFlow<ConnectedStatus> = mutable.asStateFlow()
    private val currentSet = MutableStateFlow<ConnectedSet?>(null)
    val set: StateFlow<ConnectedSet?> = currentSet.asStateFlow()
    private val lock = Mutex()
    @Volatile private var generation = 0L
    private var job: Job? = null
    @Volatile var enabled = false; private set
    @Volatile var latestEpoch = 0L; private set

    private var access: ConnectedAccess? = null
    private var accessAt = 0L
    private var linkKey: String? = null
    private var uploadedDigest: String? = null
    private var uploadedRevision = -1L
    private var contextId: String? = null
    private var contextEpoch = 0L
    private var contextBody: String? = null

    fun setEnabled(value: Boolean) {
        if (enabled == value) return
        enabled = value
        generation++
        job?.cancel(); job = null
        currentSet.value = null
        mutable.update { if (value) ConnectedStatus(enabled = true, lastMessage = "Waiting for the next song", tunable = access?.let { it.capable && it.tunable }) else ConnectedStatus() }
    }

    /** Marks the anchor pending right away so a refill that fires at song start waits for the answer. */
    fun begin(request: ConnectedRequest): Job? {
        if (!enabled) return null
        val mine = ++generation
        job?.cancel()
        currentSet.value = null
        mutable.update { it.copy(pendingAnchor = request.anchor.id, pendingSince = now()) }
        val started = scope.launch {
            val (set, failure) = fetch(request)
            if (mine != generation) return@launch // A newer song start made this result stale.
            if (set == null) {
                mutable.update { it.copy(pendingAnchor = null, lastAt = now(), lastOk = false, lastFailure = failure, lastMessage = failure!!.message,
                    permitted = if (failure.kind == ConnectedFailureKind.NOT_PERMITTED) false else it.permitted) }
            } else {
                currentSet.value = set
                mutable.update { it.copy(pendingAnchor = null, permitted = true, lastAt = now(), lastOk = true, lastFailure = null,
                    lastMessage = "${set.continuation.size} similar and ${set.options.size} optional picks, ranked by ${set.rankedBy}") }
            }
        }
        job = started
        return started
    }

    /** Drops the held set when its fence no longer matches the phone's current state. */
    fun revalidate(current: ConnectedFence?) {
        val held = currentSet.value ?: return
        val f = held.fence
        val ok = current != null && f.anchorLocalId == current.anchorLocalId && f.mood == current.mood && f.exploration == current.exploration &&
            f.vibe == current.vibe && f.mappingGeneration == current.mappingGeneration && f.syncEpoch == current.syncEpoch && f.connectedEpoch == latestEpoch &&
            f.tuning.normalized() == current.tuning.normalized()
        if (!ok) currentSet.compareAndSet(held, null)
    }

    /** Forget cached capability so the next request probes again. */
    fun forgetAccess() { access = null }

    /** Explicit permission check for Settings. Never touches playback or the held set. */
    suspend fun checkAccess() {
        if (!enabled) return
        val result = try {
            lock.withLock { access = null; probe() }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (failure: ConnectionFailure) {
            mutable.update { it.copy(lastAt = now(), lastOk = false, lastFailure = ConnectedFailure(ConnectedFailureKind.UNREACHABLE, failure.safeMessage), lastMessage = ConnectedFailure(ConnectedFailureKind.UNREACHABLE, failure.safeMessage).message) }
            return
        } catch (_: Exception) { return }
        val failure = when {
            !result.capable -> ConnectedFailure(ConnectedFailureKind.NOT_CAPABLE)
            !result.permitted -> ConnectedFailure(ConnectedFailureKind.NOT_PERMITTED)
            else -> null
        }
        mutable.update {
            if (failure == null) it.copy(permitted = true, lastMessage = if (it.lastOk == true) it.lastMessage else "Permitted on desktop")
            else it.copy(permitted = false, lastAt = now(), lastOk = false, lastFailure = failure, lastMessage = failure.message)
        }
    }

    private suspend fun fetch(request: ConnectedRequest): Outcome = try {
        withTimeout(timeoutMs) { lock.withLock { run(request) } }
    } catch (_: TimeoutCancellationException) {
        fail(ConnectedFailureKind.TIMEOUT, "${timeoutMs / 1000} s")
    } catch (cancelled: CancellationException) { throw cancelled }
    catch (failure: ConnectionFailure) { fail(ConnectedFailureKind.UNREACHABLE, failure.safeMessage) }
    catch (_: Exception) { fail(ConnectedFailureKind.UNREACHABLE) }

    private suspend fun probe(): ConnectedAccess {
        val cached = access
        val ttl = if (cached?.permitted == true && cached.capable) ACCESS_TTL_MS else DENIED_TTL_MS
        if (cached != null && now() - accessAt < ttl) return cached
        val fresh = link.access()
        if (fresh.key != linkKey) { uploadedDigest = null; uploadedRevision = -1; contextId = null; contextBody = null; contextEpoch = 0; latestEpoch = 0; linkKey = fresh.key }
        access = fresh; accessAt = now()
        mutable.update { it.copy(tunable = fresh.capable && fresh.tunable) }
        return fresh
    }

    private fun reasonOf(body: String): String? = runCatching {
        val detail = (ConnectedJson.parse(body) as Map<*, *>)["detail"]
        (detail as? Map<*, *>)?.get("reason") as? String ?: detail as? String
    }.getOrNull()

    private fun httpFailure(response: RadioResponse, what: String): Outcome = when (response.status) {
        403 -> { access = null; fail(ConnectedFailureKind.NOT_PERMITTED) }
        409 -> reasonOf(response.body).let { if (it == "catalog_mismatch") fail(ConnectedFailureKind.CATALOG_MISMATCH) else fail(ConnectedFailureKind.STALE, it) }
        429 -> fail(ConnectedFailureKind.SERVER_ERROR, "$what rate limited")
        in 400..499 -> fail(ConnectedFailureKind.REJECTED, "$what HTTP ${response.status}")
        else -> fail(ConnectedFailureKind.SERVER_ERROR, "$what HTTP ${response.status}")
    }

    private fun obj(body: String): Map<*, *>? = runCatching { ConnectedJson.parse(body) as? Map<*, *> }.getOrNull()

    private suspend fun run(request: ConnectedRequest): Outcome {
        var forceUpload = false
        var forceContext = false
        var retried = false
        while (true) {
            val granted = probe()
            if (!granted.capable) return fail(ConnectedFailureKind.NOT_CAPABLE)
            if (!granted.permitted) return fail(ConnectedFailureKind.NOT_PERMITTED)
            val snapshot = AvailabilitySnapshot.from(request.mappings, request.available) ?: return fail(ConnectedFailureKind.NO_MATCHES)
            val anchorServer = snapshot.serverIdByLocal[request.anchor.id] ?: return fail(ConnectedFailureKind.NO_MATCHES, "the current song isn't matched")
            if (forceUpload || uploadedDigest != snapshot.digest || uploadedRevision != snapshot.revision) {
                val body = ConnectedJson.write(linkedMapOf("protocol_version" to 1, "catalog_id" to snapshot.catalogId, "catalog_revision" to snapshot.revision,
                    "song_ids" to snapshot.ids, "digest" to snapshot.digest))
                val response = link.post("/availability", body)
                if (response.status != 200) return httpFailure(response, "Availability")
                if (obj(response.body)?.get("digest") != snapshot.digest) return fail(ConnectedFailureKind.INVALID_REPLY, "availability digest")
                uploadedDigest = snapshot.digest; uploadedRevision = snapshot.revision; forceUpload = false
            }
            val ctx = contextBody(request, snapshot, granted.tunable)
            if (contextId == null || forceContext || ctx != contextBody) {
                val create = contextId == null
                val response = link.post(if (create) "/contexts" else "/contexts/$contextId", ctx)
                if (response.status == 404 && !create) { contextId = null; contextBody = null; if (!retried) { retried = true; continue }; return httpFailure(response, "Context") }
                if (response.status != 200) return httpFailure(response, "Context")
                val reply = obj(response.body)
                val id = reply?.get("context_id") as? String
                val epoch = reply?.get("epoch") as? Long
                if (id == null || epoch == null || (!create && id != contextId)) return fail(ConnectedFailureKind.INVALID_REPLY, "context")
                contextId = id; contextEpoch = epoch; contextBody = ctx; latestEpoch = epoch; forceContext = false
            }
            val id = contextId!!
            val requestId = newId()
            val exclude = request.excludeLocalIds.mapNotNull { snapshot.serverIdByLocal[it] }.distinct().take(200)
            val body = ConnectedJson.write(linkedMapOf("protocol_version" to 1, "request_id" to requestId, "epoch" to contextEpoch,
                "availability_digest" to snapshot.digest, "catalog_revision" to snapshot.revision, "anchor_song_id" to anchorServer, "exclude_song_ids" to exclude))
            val response = link.post("/contexts/$id/recommendations", body)
            when (response.status) {
                200 -> return parseReply(request, snapshot, requestId, id, anchorServer, response.body)
                404 -> { contextId = null; contextBody = null; if (!retried) { retried = true; continue }; return httpFailure(response, "Recommendations") }
                409 -> {
                    val reason = reasonOf(response.body)
                    when (reason) {
                        "superseded" -> return fail(ConnectedFailureKind.SUPERSEDED)
                        "epoch_stale" -> forceContext = true
                        "availability_stale", "no_availability" -> forceUpload = true
                        else -> return httpFailure(response, "Recommendations")
                    }
                    // Re-sync once and retry; a second stale answer is reported, not looped on.
                    if (retried) return fail(ConnectedFailureKind.STALE, reason)
                    retried = true
                }
                else -> return httpFailure(response, "Recommendations")
            }
        }
    }

    private fun contextBody(request: ConnectedRequest, snapshot: AvailabilitySnapshot, tunable: Boolean): String {
        val recent = request.recent.mapNotNull { r -> snapshot.serverIdByLocal[r.localId]?.let { linkedMapOf("song_id" to it, "outcome" to r.outcome) } }.takeLast(20)
        val body = linkedMapOf<String, Any?>("protocol_version" to 1, "mood" to request.mood, "exploration" to request.exploration.coerceIn(0.0, 1.0),
            "vibe_lock_song_id" to request.vibeLocalId?.let { snapshot.serverIdByLocal[it] }, "recent" to recent)
        // Older desktops reject unknown fields, so tuning and phone plays are only sent where advertised.
        if (tunable) {
            request.tuning.toWire()?.let { body["tuning"] = it }
            val played = request.played.mapNotNull { p -> snapshot.serverIdByLocal[p.localId]?.let { it to p.ageS.coerceAtLeast(0) } }
                .distinctBy { it.first }.take(MAX_PLAYED).map { linkedMapOf("song_id" to it.first, "age_s" to it.second) }
            if (played.isNotEmpty()) body["played"] = played
        }
        return ConnectedJson.write(body)
    }

    private fun parseReply(request: ConnectedRequest, snapshot: AvailabilitySnapshot, requestId: String, id: String, anchorServer: String, text: String): Outcome {
        val reply = obj(text) ?: return fail(ConnectedFailureKind.INVALID_REPLY, "not JSON")
        // Every fence is echoed; a mismatch means this is not the answer to what was asked.
        if (reply["request_id"] != requestId || reply["context_id"] != id || reply["epoch"] != contextEpoch || reply["availability_digest"] != snapshot.digest ||
            reply["catalog_revision"] != snapshot.revision || reply["anchor_song_id"] != anchorServer) return fail(ConnectedFailureKind.INVALID_REPLY, "fence mismatch")
        val picks = reply["picks"] as? List<*> ?: return fail(ConnectedFailureKind.INVALID_REPLY, "picks")
        if (reply["status"] == "insufficient") return fail(ConnectedFailureKind.INSUFFICIENT)
        if (reply["status"] != "ok") return fail(ConnectedFailureKind.INVALID_REPLY, "status")
        val vibeLocked = reply["vibe_locked"] == true
        val seen = HashSet<String>()
        var dropped = 0
        val mapped = ArrayList<ConnectedPick>()
        for (raw in picks) {
            val pick = raw as? Map<*, *>
            val song = (pick?.get("song_id") as? String)?.let { snapshot.byServerId[it] }
            val slot = pick?.get("slot") as? String
            if (song == null || slot !in SLOTS || !song.available || request.available[song.id] != song || song.id == request.anchor.id ||
                song.id in request.excludeLocalIds || request.isDisliked(song.id) || !seen.add(song.id)) { dropped++; continue }
            mapped += ConnectedPick(song, slot!!, (pick["reason"] as? String).orEmpty().take(200))
        }
        val continuation = mapped.filter { it.slot == "exploit" }
        val options = mapped.filter { it.slot != "exploit" && (!vibeLocked || it.slot == "explore") }
        if (continuation.isEmpty() && options.isEmpty()) return fail(ConnectedFailureKind.INSUFFICIENT, if (dropped > 0) "$dropped picks weren't usable on this phone" else null)
        val fence = request.fence.copy(connectedEpoch = contextEpoch, availabilityDigest = snapshot.digest)
        return ConnectedSet(request.anchor.id, requestId, reply["result_id"] as? String ?: "", reply["ranked_by"] as? String ?: "unknown", vibeLocked,
            fence, continuation, options, dropped, now(), ConnectedApplied.parse(reply["applied"])) to null
    }
}
