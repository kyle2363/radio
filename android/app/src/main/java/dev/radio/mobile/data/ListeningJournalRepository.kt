package dev.radio.mobile.data

import android.content.Context
import androidx.room.withTransaction
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger

data class JournalIdentity(val deviceId: String, val serverId: String? = null, val catalogId: String? = null,
    val songId: String? = null, val localId: String, val provenance: String = "local_only")
data class ListeningJournalState(val history: List<LocalListeningEvent> = emptyList(),
    val feedback: Map<String, LocalSongFeedback> = emptyMap(), val error: String? = null, val pendingWrites: Int = 0,
    val feedbackLoaded: Boolean = false)

/** One ordered IO writer. A failed head stays queued; Retry never invents new event IDs. */
class ListeningJournalRepository(context: Context, private val db: LibraryDatabase, private val scope: CoroutineScope,
    private val beforeCommit: suspend () -> Unit = {}) {
    private val mutable = MutableStateFlow(ListeningJournalState())
    val state: StateFlow<ListeningJournalState> = mutable.asStateFlow()
    private val fallbackDevice = UUID.randomUUID().toString()
    private val identities = try { JournalIdentityProvider(context, db, scope) }
        catch (_: Exception) { mutable.update { it.copy(error = "Journal identity storage is unavailable. Playback remains local; restart after checking storage.") }; null }
    private val identityError = mutable.value.error
    private val requests = Channel<suspend () -> Unit>(Channel.UNLIMITED)
    private val retries = Channel<Unit>(Channel.CONFLATED)
    private val pending = AtomicInteger()
    init {
        scope.launch { db.journal().observeEvents().catch { mutable.update { it.copy(error = "Couldn't read listening history. Restart the app to retry.") } }.collect { rows -> mutable.update { it.copy(history = rows) } } }
        scope.launch { db.journal().observeFeedback().catch { mutable.update { it.copy(error = "Couldn't read saved feedback. Restart the app to retry.") } }.collect { rows -> mutable.update { it.copy(feedback = rows.associateBy { row -> row.localId }, feedbackLoaded = true) } } }
        scope.launch {
            for (operation in requests) {
                var done = false
                while (!done) {
                    for (attempt in 0..2) {
                        try { operation(); val feedback=db.journal().allFeedback(); mutable.update{it.copy(feedback=feedback.associateBy{row->row.localId},feedbackLoaded=true)}; done = true; break }
                        catch (cancelled: CancellationException) { throw cancelled }
                        catch (_: Exception) { if (attempt < 2) delay(100L shl attempt) }
                    }
                    if (!done) { mutable.update { it.copy(error = "Listening history couldn't be saved. Retry when storage is available.") }; retries.receive() }
                }
                pending.decrementAndGet()
                mutable.update { it.copy(error = identityError, pendingWrites = pending.get()) }
            }
        }
    }
    fun identity(song: LocalSong) = identities?.capture(song)
        ?: JournalIdentity(deviceId = fallbackDevice, localId = song.id, provenance = "identity_unavailable")
    fun retry() { retries.trySend(Unit) }
    private fun submit(operation: suspend () -> Unit) {
        pending.incrementAndGet()
        mutable.update { it.copy(pendingWrites = pending.get()) }
        requests.trySend(operation)
    }
    private fun envelope(identity: JournalIdentity, eventId: String, attempt: String?, kind: String, at: Long, clock: Long,
        completion: Double? = null, value: Boolean? = null) = JSONObject().apply {
        put("protocol_version", 1); put("device_id", identity.deviceId); put("event_id", eventId)
        put("logical_clock", clock); put("kind", kind); put("occurred_at", at / 1000)
        put("song_id", identity.songId ?: JSONObject.NULL)
        if (identity.songId == null) put("local_id", identity.localId) else put("catalog_id", identity.catalogId)
        if (attempt != null) put("attempt_id", attempt)
        if (completion != null) put("completion", completion)
        if (value != null) put("value", value)
    }.toString()
    private suspend fun append(identity: JournalIdentity, eventId: String, attempt: String?, kind: String, at: Long,
        listened: Long = 0, completion: Double? = null, reason: String? = null, value: Boolean? = null) {
        val dao = db.journal(); val previousClock = dao.clock() ?: 0
        check(previousClock < 9007199254740991L) { "Journal clock exhausted" }
        val clock = previousClock + 1
        val wireCompletion = completion ?: if (kind == "song_completed" || kind == "song_skipped") 0.0 else null
        val payload = JSONObject(envelope(identity, eventId, attempt, kind, at, clock, wireCompletion, value)).apply {
            if (kind == "song_skipped" && reason in setOf("too_intense", "too_slow", "too_samey", "wrong_mood", "just_skip")) put("reason", reason)
        }.toString()
        dao.clock(LocalJournalClock(value = clock))
        dao.insert(LocalListeningEvent(eventId, identity.localId, attempt, kind, at, clock, payload, listened, completion, reason, identity.provenance))
        // Local-only installation identity is never silently relabeled to a future paired device.
        if (kind != "attempt_stopped") dao.enqueue(LocalEventOutbox(eventId, identity.serverId, identity.deviceId, payload))
    }
    fun started(song: LocalSong, attempt: String, positionMs: Long, identity: JournalIdentity = identity(song)) {
        val eventId = UUID.randomUUID().toString(); val at = System.currentTimeMillis()
        submit { db.withTransaction {
            if (db.journal().start(attempt) == null && db.library().playbackStart(attempt) == null) {
                beforeCommit(); append(identity, eventId, attempt, "song_started", at)
                db.library().savePlaybackStart(LocalPlaybackStart(attempt, song.id, at, positionMs))
            }
        } }
    }
    fun finished(identity: JournalIdentity, attempt: String, kind: String, listenedMs: Long, durationMs: Long?, reason: String?) {
        val eventId = UUID.randomUUID().toString(); val at = System.currentTimeMillis()
        val completion = durationMs?.takeIf { it > 0 }?.let { (listenedMs.toDouble() / it).coerceIn(0.0, 1.0) }
        submit { db.withTransaction {
            if (db.journal().start(attempt) != null && db.journal().terminal(attempt) == null) {
                beforeCommit(); append(identity, eventId, attempt, kind, at, listenedMs, completion, reason)
            }
        } }
    }
    fun replay(song: LocalSong, attempt: String, identity: JournalIdentity = identity(song)) {
        val id = UUID.randomUUID().toString(); val at = System.currentTimeMillis()
        submit { db.withTransaction {
            if (db.journal().event(attempt, "song_replayed") == null) { beforeCommit(); append(identity, id, attempt, "song_replayed", at) }
        } }
    }
    fun setFavorite(song: LocalSong, value: Boolean) = setFeedback(song, true, value)
    fun setDisliked(song: LocalSong, value: Boolean) = setFeedback(song, false, value)
    private fun setFeedback(song: LocalSong, favorite: Boolean, value: Boolean) {
        val identity = identity(song); val at = System.currentTimeMillis()
        val ids = listOf(UUID.randomUUID().toString(), UUID.randomUUID().toString())
        submit { db.withTransaction {
            val previous = db.journal().feedback(song.id) ?: LocalSongFeedback(song.id, false, false)
            val next = if (favorite) previous.copy(favorite = value, disliked = if (value) false else previous.disliked)
                else previous.copy(disliked = value, favorite = if (value) false else previous.favorite)
            beforeCommit()
            if (next.favorite != previous.favorite) append(identity, ids[0], null, "favorite_changed", at, value = next.favorite)
            if (next.disliked != previous.disliked) append(identity, ids[1], null, "dislike_changed", at, value = next.disliked)
            db.journal().feedback(next)
        } }
    }
}
