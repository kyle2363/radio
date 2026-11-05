package dev.radio.mobile.data

import android.content.Context
import android.os.SystemClock
import androidx.room.withTransaction
import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.connection.PairingRepository
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.portable.CatalogJson
import dev.radio.mobile.portable.PortableRegisterCandidate
import dev.radio.mobile.portable.PortableRegisterOrder
import dev.radio.mobile.portable.PortableSyncBatcher
import dev.radio.mobile.portable.PortableProfileKey
import dev.radio.mobile.portable.PortableSyncChanges
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

data class PortableSyncState(
    val busy: Boolean = false,
    val pendingEvents: Int = 0,
    val lastSuccessAt: Long? = null,
    val message: String = "Profile sync has not run on this phone.",
    val error: String? = null,
)

/** Explicit, user-triggered profile sync. It never touches the playback service or active queue. */
class PortableSyncRepository internal constructor(
    context: Context,
    private val pairing: PairingRepository,
    private val db: LibraryDatabase,
    private val now: () -> Long = System::currentTimeMillis,
) {
    private val mutex = Mutex()
    private val mutable = MutableStateFlow(PortableSyncState())
    val state: StateFlow<PortableSyncState> = mutable.asStateFlow()
    private val observerScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    init {
        observerScope.launch {
            db.journal().observePendingSyncCount().collect { refreshPending() }
        }
    }

    suspend fun load() = withContext(Dispatchers.IO) {
        mutex.withLock {
            val dao = db.journal()
            val pairingOwner=pairing.localProfileOwnerSnapshot()
            val savedCursor = if(pairingOwner.serverId!=null && pairingOwner.deviceId!=null) dao.syncCursor(pairingOwner.serverId,pairingOwner.deviceId) else null
            val cursor = savedCursor?.cursor ?: 0L
            val last = savedCursor?.lastSuccessAt?.takeIf { it > 0L }
            val pendingRows=dao.pendingOutbox()
            val pending = pendingRows.count { it.serverId==pairingOwner.serverId && it.deviceId==pairingOwner.deviceId && !pairingOwner.quarantined && !pairingOwner.blocked }
            val preserved=pendingRows.count { it.serverId!=null && !(it.serverId==pairingOwner.serverId && it.deviceId==pairingOwner.deviceId) }
            val quarantined = pairingOwner.quarantined || preserved>0
            mutable.update { current -> current.copy(
                pendingEvents = pending,
                lastSuccessAt = last,
                message = when {
                    quarantined -> "Saved sync history for another installation or device is preserved and will not be uploaded."
                    last != null -> "Last synced ${java.text.DateFormat.getDateTimeInstance().format(java.util.Date(last))} · download cursor $cursor"
                    else -> "Profile sync is manual. Your playback stays local."
                },
                error = null,
            ) }
        }
    }

    internal fun close() { observerScope.cancel() }

    suspend fun syncNow() = withContext(Dispatchers.IO) {
        mutex.withLock {
            mutable.update { it.copy(busy = true, error = null, message = "Syncing listening profile…") }
            try {
                val startedAt=SystemClock.elapsedRealtime()
                val session = pairing.profileSyncSession()
                val dao = db.journal()
                val ownerKey = ownerKey(session.serverId, session.deviceId, session.installationMarker)
                val owner = dao.syncOwner(ownerKey)
                if (owner?.quarantined == true)
                    throw SyncIdentityFailure("Saved sync history is quarantined for this installation. Its events were not sent.")
                if (owner == null) pairing.adoptProfile(session) { db.withTransaction {
                    dao.syncOwner(PortableSyncOwner(ownerKey=ownerKey, serverId = session.serverId, deviceId = session.deviceId,
                        installationMarker = session.installationMarker, quarantined = false))
                } }

                uploadPending(session, dao, startedAt)
                var cursor = dao.syncCursor(session.serverId, session.deviceId)?.cursor ?: 0L
                var pageCount = 0
                do {
                    if (++pageCount > MAX_PAGES || SystemClock.elapsedRealtime()-startedAt > MAX_SYNC_DURATION_MS) throw SyncProtocolFailure("Profile sync reached its time or page limit. Saved page progress is intact; retry later.")
                    val response = pairing.profileRequest(session, "/events?cursor=$cursor&limit=100")
                    requireOk(response, "download")
                    ensureTime(startedAt)
                    val page = parsePage(response.body, session.serverId, session.deviceId, cursor)
                    PortableSyncChanges.applying { pairing.adoptProfile(session) { db.withTransaction {
                        for (event in page.events) {
                            val existing = dao.remoteEvent(event.serverId, event.deviceId, event.eventId)
                            if (existing != null && existing.payload != event.payload)
                                throw SyncProtocolFailure("Server reused an event identity with different content. No page progress was saved.")
                            if (existing == null) dao.remoteEvent(event)
                        }
                        dao.clock(LocalJournalClock(value = maxOf(dao.clock() ?: 0L, page.logicalClock)))
                        val previousSuccess=dao.syncCursor(session.serverId,session.deviceId)?.lastSuccessAt
                        dao.cursor(PortableSyncCursor(session.serverId, session.deviceId, page.cursor, now(), previousSuccess))
                        mergeRegistersInside(session.serverId, session.deviceId, page.events, includeLocal = pageCount==1)
                        cursor = page.cursor
                    } } }
                    if (!page.hasMore) break
                } while (true)

                val pending = dao.pendingOutbox().count { it.serverId==session.serverId && it.deviceId == session.deviceId }
                val last = now()
                PortableSyncChanges.applying { pairing.adoptProfile(session) { db.withTransaction {
                    val saved = dao.syncCursor(session.serverId, session.deviceId)
                    if (saved != null && saved.deviceId == session.deviceId) dao.cursor(saved.copy(lastSuccessAt = last))
                    mergeRegistersInside(session.serverId, session.deviceId, emptyList(), includeLocal = true)
                } } }
                mutable.update { it.copy(busy=false, pendingEvents=pending, lastSuccessAt=last, error=null,
                    message=if (pending == 0) "Profile synced. Playback and the active queue were left unchanged."
                    else "Downloaded shared history; $pending local event${if (pending == 1) "" else "s"} still await acknowledgement.") }
            } catch (cancelled: CancellationException) {
                mutable.update { it.copy(busy = false, message = "Sync paused safely. Acknowledged batches and downloaded pages were saved.") }
                throw cancelled
            } catch (failure: SyncIdentityFailure) {
                fail(failure.message ?: "Sync identity check failed.")
            } catch (failure: SyncProtocolFailure) {
                fail(failure.message ?: "The server returned invalid sync data.")
            } catch (failure: ConnectionFailure) {
                fail(failure.safeMessage)
            } catch (_: Exception) {
                fail("Profile sync could not finish. Saved acknowledgements and downloaded pages are safe; retry when the connection is available.")
            }
        }
    }

    private suspend fun uploadPending(session: PairingRepository.ProfileSyncSession, dao: ListeningJournalDao, startedAt:Long) {
        // Events from a local-only identity or a previously paired device are intentionally never renamed.
        val eligible = dao.pendingOutbox().filter {
            it.serverId == session.serverId && it.deviceId == session.deviceId && runCatching {
                val event = CatalogJson.parseStrict(it.payload.toByteArray(Charsets.UTF_8)).obj()
                event.requiredString("event_id",128) == it.eventId && event.requiredString("device_id",128) == session.deviceId
            }.getOrDefault(false)
        }
        for (batch in PortableSyncBatcher.batches(eligible) { it.payload.toByteArray(Charsets.UTF_8).size }) {
            if (batch.isEmpty()) continue
            ensureTime(startedAt)
            val body = "{\"protocol_version\":1,\"events\":[${batch.joinToString(",") { it.payload }}]}"
            val response = pairing.profileRequest(session, "/events", body)
            requireOk(response, "upload")
            val acknowledged = validateAcknowledgements(response.body, batch.map { it.eventId }.toSet())
            // ACK receipt is durable, but the immutable local journal/origin row remains for learning.
            pairing.adoptProfile(session) { db.withTransaction { dao.acknowledge(acknowledged) } }
            refreshPending()
        }
    }

    private data class EventPage(val events: List<PortableRemoteEvent>, val cursor: Long, val hasMore: Boolean, val logicalClock: Long)

    private fun parsePage(body: String, serverId:String, ownDevice: String, previousCursor: Long): EventPage {
        if (body.toByteArray(Charsets.UTF_8).size > 2 * 1024 * 1024) throw SyncProtocolFailure("Downloaded history page exceeded the 2 MiB limit.")
        val root = parseStrict(body).obj()
        if (root.requiredLong("protocol_version") != 1L) throw SyncProtocolFailure("Unsupported profile sync protocol version.")
        val rows = root.required("events") as? CatalogJson.Arr ?: throw SyncProtocolFailure("Downloaded history page is missing events.")
        if (rows.values.size > 100) throw SyncProtocolFailure("Downloaded history page exceeded the 100 event limit.")
        val events = ArrayList<PortableRemoteEvent>(rows.values.size)
        var lastRevision = previousCursor
        var highestEventClock=0L
        for (value in rows.values) {
            val row = value as? CatalogJson.Obj ?: throw SyncProtocolFailure("Downloaded history page contains a malformed row.")
            val revision = row.requiredLong("revision")
            if (revision <= lastRevision) throw SyncProtocolFailure("Downloaded history revisions were not strictly ordered.")
            lastRevision = revision
            val event = row.required("event") as? CatalogJson.Obj ?: throw SyncProtocolFailure("Downloaded history row is missing its event.")
            val decoded=validateEvent(event)
            highestEventClock=maxOf(highestEventClock,decoded.clock)
            // The server ledger includes this phone's acknowledged events. Store them only in the
            // remote ledger; never add them to the upload outbox or count them twice locally.
            events += PortableRemoteEvent(serverId, decoded.device, decoded.id, decoded.clock, (decoded.occurred * 1000.0).toLong(), decoded.kind, decoded.localId, event.canonical())
        }
        val next = root.requiredLong("cursor")
        val more = (root.required("has_more") as? CatalogJson.Bool)?.value ?: throw SyncProtocolFailure("Downloaded history page has no boolean has_more flag.")
        val logicalClock = root.requiredLong("logical_clock")
        if (next < previousCursor || next != lastRevision && events.isNotEmpty() || events.isEmpty() && next != previousCursor || logicalClock < highestEventClock || logicalClock > MAX_SAFE_INTEGER || more && events.isEmpty())
            throw SyncProtocolFailure("Downloaded history page cursor or logical clock is invalid.")
        return EventPage(events, next, more, logicalClock)
    }

    private data class DecodedEvent(val device:String,val id:String,val clock:Long,val occurred:Double,val kind:String,val localId:String?)
    private fun validateEvent(event:CatalogJson.Obj):DecodedEvent {
        val allowed=setOf("protocol_version","event_id","device_id","logical_clock","kind","song_id","attempt_id","occurred_at","value","key","local_id","reason","completion","catalog_id")
        if(event.fields.keys.any { it !in allowed } || event.requiredLong("protocol_version")!=1L) throw SyncProtocolFailure("Downloaded event has unsupported protocol fields.")
        val device=event.requiredString("device_id",128);val id=event.requiredString("event_id",128)
        val clock=event.requiredLong("logical_clock");val occurred=event.requiredNumber("occurred_at");val kind=event.requiredString("kind",64)
        if(clock !in 0..MAX_SAFE_INTEGER || !occurred.isFinite() || occurred !in 0.0..253402300799.0 || kind !in EVENT_KINDS) throw SyncProtocolFailure("Downloaded event has invalid clock, time, or kind.")
        val songField=event.fields["song_id"] ?: throw SyncProtocolFailure("Downloaded event is missing song_id.")
        val songId=when(songField){CatalogJson.Null->null;is CatalogJson.Str->songField.value.takeIf { it.matches(Regex("[0-9]+")) } ?: throw SyncProtocolFailure("Downloaded event has an invalid song id.");else->throw SyncProtocolFailure("Downloaded event has an invalid song id.")}
        val localId=event.fields["local_id"]?.let { (it as? CatalogJson.Str)?.value?.takeIf(String::isNotBlank) ?: throw SyncProtocolFailure("Downloaded local ID is invalid.") }
        if(songId!=null){event.requiredString("catalog_id",128)} else if(kind!="preference_changed" && localId.isNullOrBlank()) throw SyncProtocolFailure("Downloaded local-only event is missing its origin ID.")
        if(kind.startsWith("song_")) event.requiredString("attempt_id",128)
        if(kind in setOf("favorite_changed","dislike_changed","blacklist_changed")) if(event.required("value") !is CatalogJson.Bool) throw SyncProtocolFailure("Downloaded event requires a boolean value.")
        if(kind=="energy_changed") { val value=event.required("value");if(value!=CatalogJson.Null && ((value as? CatalogJson.Num)?.token?.toDoubleOrNull()?.let { it.isFinite() && it in 0.0..1.0 }!=true)) throw SyncProtocolFailure("Downloaded energy value is invalid.") }
        if(kind=="preference_changed") {val key=event.requiredString("key",64);val value=event.required("value");val valid=key=="exploration_level" && (value as? CatalogJson.Num)?.token?.toDoubleOrNull()?.let { it.isFinite() && it in 0.0..1.0 }==true || key=="llm_reranking_enabled" && value is CatalogJson.Bool;if(!valid)throw SyncProtocolFailure("Downloaded shared preference is invalid.")}
        event.fields["reason"]?.let { val reason=(it as? CatalogJson.Str)?.value;if(reason !in SKIP_REASONS)throw SyncProtocolFailure("Downloaded skip reason is invalid.") }
        event.fields["completion"]?.let { val completion=(it as? CatalogJson.Num)?.token?.toDoubleOrNull();if(completion?.isFinite()!=true || completion !in 0.0..1.0)throw SyncProtocolFailure("Downloaded completion value is invalid.") }
        return DecodedEvent(device,id,clock,occurred,kind,localId)
    }

    private fun validateAcknowledgements(body: String, expected: Set<String>): List<String> {
        if (body.toByteArray(Charsets.UTF_8).size > 128 * 1024) throw SyncProtocolFailure("Upload acknowledgement was too large.")
        val root = parseStrict(body).obj()
        if (root.requiredLong("protocol_version") != 1L) throw SyncProtocolFailure("Upload acknowledgement used an unsupported protocol.")
        val accepted = stringSet(root.required("accepted"), "accepted")
        val duplicates = stringSet(root.required("duplicates"), "duplicates")
        if (accepted.intersect(duplicates).isNotEmpty() || accepted + duplicates != expected)
            throw SyncProtocolFailure("Upload acknowledgement did not exactly match the submitted event IDs. The same events will be retried.")
        return (accepted + duplicates).toList()
    }

    private fun stringSet(value:CatalogJson, name:String):Set<String> {
        val array=value as? CatalogJson.Arr ?: throw SyncProtocolFailure("Upload acknowledgement is missing a bounded $name list.")
        if (array.values.size > 500) throw SyncProtocolFailure("Upload acknowledgement is missing a bounded $name list.")
        val values = array.values.map { (it as? CatalogJson.Str)?.value?.takeIf { id -> id.isNotBlank() && id.length<=128 }
            ?: throw SyncProtocolFailure("Upload acknowledgement contains an invalid event ID.") }
        if (values.toSet().size != values.size) throw SyncProtocolFailure("Upload acknowledgement repeated an event ID.")
        return values.toSet()
    }

    /** Resolves all immutable local/remote edits, including pending local edits, by the protocol tuple. */
    private suspend fun mergeRegistersInside(serverId:String, ownDeviceId:String, incoming:List<PortableRemoteEvent>, includeLocal:Boolean) {
        val dao = db.journal()
        val candidates = mutableMapOf<String, MutableList<Pair<PortableRegisterCandidate, Pair<String, String>>>>()
        fun add(payloadText: String, fallbackLocalId: String? = null) {
            val event = JSONObject(payloadText)
            val kind = event.optString("kind")
            if (kind !in MUTABLE_KINDS) return
            val device = event.optString("device_id")
            val eventId = event.optString("event_id")
            val clock = event.optLong("logical_clock", -1)
            if (device.isBlank() || eventId.isBlank() || clock < 0) return
            val key = when (kind) {
                "preference_changed" -> "preference:${event.optString("key")}"
                else -> {
                    val songId = if (event.isNull("song_id")) null else event.optString("song_id").takeIf { it.isNotBlank() }
                    val identity = if (songId != null) "catalog:${PortableProfileKey.forLocalOnly(event.optString("catalog_id"),songId).removePrefix("local:")}" else PortableProfileKey.forLocalOnly(device, event.optString("local_id", fallbackLocalId.orEmpty()))
                    "$kind:$identity"
                }
            }
            val value = if (event.isNull("value")) "null" else event.get("value").toString()
            val candidate = PortableRegisterCandidate(clock, device, eventId, value)
            candidates.getOrPut(key) { mutableListOf() }.add(candidate to (kind to canonical(event)))
        }
        dao.profileRegisters(serverId).forEach { register ->
            val candidate=PortableRegisterCandidate(register.logicalClock,register.deviceId,register.eventId,register.valueJson)
            candidates.getOrPut(register.registerKey){mutableListOf()}.add(candidate to (register.kind to register.payload))
        }
        if(includeLocal) dao.profileLocalEvents(serverId).filter { it.deviceId==ownDeviceId }.forEach { add(it.payload) }
        incoming.forEach { add(it.payload,it.localId) }
        for ((key, rows) in candidates) {
            val winner = PortableRegisterOrder.winner(rows.map { it.first }) ?: continue
            val (kind, payload) = rows.first { it.first == winner }.second
            dao.profileRegister(PortableProfileRegister(serverId, key, winner.logicalClock, winner.deviceId, winner.eventId, kind, winner.value, payload))
        }
        projectSharedFeedback(serverId, dao)
    }

    private suspend fun projectSharedFeedback(serverId:String, dao:ListeningJournalDao) {
        val catalog = db.catalogs().active(serverId) ?: return
        val mapping = JournalIdentityProvider.mappings.value.entries.values
            .filter { it.serverId == serverId && it.catalogId == catalog.catalogId && it.revision == catalog.revision }
            .associateBy { it.song.id }
        if (mapping.isEmpty()) return
        val feedback = dao.allFeedback().associateBy { it.localId }.toMutableMap()
        val localOnlyFeedback = dao.latestFeedbackEvents().mapNotNull { row ->
            val event=runCatching { JSONObject(row.payload) }.getOrNull() ?: return@mapNotNull null
            val isShared= !event.isNull("song_id") && event.optString("catalog_id")==catalog.catalogId
            if(isShared || !event.optBoolean("value",false)) null else row.localId to row.kind
        }.toSet()
        val registers = dao.profileRegisters(serverId)
        val valuesBySong = mutableMapOf<String, MutableMap<String, Boolean>>()
        for (register in registers) {
            if (register.kind !in setOf("favorite_changed", "dislike_changed")) continue
            val event = JSONObject(register.payload)
            if (event.optString("catalog_id") != catalog.catalogId || event.isNull("song_id")) continue
            val songId = event.optString("song_id")
            val value = event.opt("value") as? Boolean ?: continue
            valuesBySong.getOrPut(songId) { mutableMapOf() }[register.kind] = value
        }
        for (entry in mapping.values) {
            val values = valuesBySong[entry.songId] ?: continue
            val old = feedback[entry.song.id] ?: LocalSongFeedback(entry.song.id, false, false)
            val favorite = if((entry.song.id to "favorite_changed") in localOnlyFeedback) true else values["favorite_changed"] ?: old.favorite
            val disliked = if((entry.song.id to "dislike_changed") in localOnlyFeedback) true else values["dislike_changed"] ?: old.disliked
            feedback[entry.song.id] = old.copy(favorite = favorite, disliked = disliked)
        }
        feedback.values.forEach { dao.feedback(it) }
    }

    private suspend fun refreshPending() {
        val owner=pairing.localProfileOwnerSnapshot()
        val rows=db.journal().pendingOutbox()
        val count=rows.count { it.serverId==owner.serverId && it.deviceId==owner.deviceId && !owner.quarantined && !owner.blocked }
        val preserved=rows.count { it.serverId!=null && !(it.serverId==owner.serverId && it.deviceId==owner.deviceId) }
        mutable.update { current -> current.copy(pendingEvents = count, message=if(preserved>0 && !current.busy)"$preserved event${if(preserved==1)" is" else "s are"} preserved for a previous identity and will not be uploaded." else current.message) }
    }

    private fun ensureTime(startedAt:Long) {
        if(SystemClock.elapsedRealtime()-startedAt>MAX_SYNC_DURATION_MS)
            throw SyncProtocolFailure("Profile sync reached its ten-minute limit. Saved acknowledgements and downloaded pages are intact; retry later.")
    }

    private fun requireOk(response: RadioResponse, operation: String) {
        if (response.status in 200..299) return
        val message = when {
            response.status == 413 -> "Profile sync batch exceeded the server limit. Pending events were kept."
            response.status == 409 && operation == "upload" -> "Server found a conflicting event identity. Pending events were kept for review."
            response.status == 403 -> "Profile sync permission is missing. Allow profile sync for this device in desktop Server Settings, then retry."
            response.status == 401 -> "Profile sync credential expired or was revoked. Repair pairing before retrying."
            else -> "Server could not $operation profile data (HTTP ${response.status}). Saved sync progress is intact."
        }
        throw SyncProtocolFailure(message)
    }

    private suspend fun fail(message: String) {
        val pending=runCatching { pairing.localProfileOwnerSnapshot().let { owner -> db.journal().pendingOutbox().count { it.serverId==owner.serverId && it.deviceId==owner.deviceId && !owner.quarantined && !owner.blocked } } }.getOrDefault(mutable.value.pendingEvents)
        mutable.update { it.copy(busy = false, pendingEvents=pending, error = message, message = message) }
    }

    private fun canonical(value: JSONObject): String = parseStrict(value.toString()).canonical()

    private fun parseStrict(body:String):CatalogJson = try { CatalogJson.parseStrict(body.toByteArray(Charsets.UTF_8)) }
        catch (_:Exception) { throw SyncProtocolFailure("Server returned malformed or duplicate-key profile JSON.") }

    private fun CatalogJson.Obj.required(key:String):CatalogJson = fields[key] ?: throw SyncProtocolFailure("Profile sync response is missing '$key'.")
    private fun CatalogJson.Obj.requiredString(key:String,max:Int):String = (required(key) as? CatalogJson.Str)?.value?.takeIf { it.isNotEmpty() && it.length<=max && it.none { c->c.code<32 } }
        ?: throw SyncProtocolFailure("Profile sync response has an invalid '$key'.")
    private fun CatalogJson.Obj.requiredLong(key:String):Long = try { (required(key) as? CatalogJson.Num)?.integer() ?: throw SyncProtocolFailure("Profile sync response has an invalid '$key'.") }
        catch (_:Exception) { throw SyncProtocolFailure("Profile sync response has an invalid '$key'.") }
    private fun CatalogJson.Obj.requiredNumber(key:String):Double = (required(key) as? CatalogJson.Num)?.token?.toDoubleOrNull()?.takeIf(Double::isFinite)
        ?: throw SyncProtocolFailure("Profile sync response has an invalid '$key'.")

    private fun ownerKey(serverId:String,deviceId:String,marker:String) = "$serverId|$deviceId|$marker"

    private class SyncIdentityFailure(message: String) : Exception(message)
    private class SyncProtocolFailure(message: String) : Exception(message)

    companion object {
        private const val MAX_SAFE_INTEGER = 9007199254740991L
        private val EVENT_KINDS = setOf("song_started", "song_completed", "song_skipped", "song_replayed", "favorite_changed", "dislike_changed", "blacklist_changed", "energy_changed", "preference_changed")
        private val SKIP_REASONS = setOf("too_intense","too_slow","too_samey","wrong_mood","just_skip")
        private val MUTABLE_KINDS = setOf("favorite_changed", "dislike_changed", "blacklist_changed", "energy_changed", "preference_changed")
        private const val MAX_PAGES=1000
        private const val MAX_SYNC_DURATION_MS=10*60*1000L
        @Volatile private var instance: PortableSyncRepository? = null
        fun get(context: Context, pairing: PairingRepository): PortableSyncRepository = instance?.takeIf { it.pairing === pairing } ?: synchronized(this) {
            instance?.takeIf { it.pairing === pairing } ?: run {
                instance?.close()
                PortableSyncRepository(context, pairing, LibraryRepository.get(context).db).also { instance = it }
            }
        }
    }
}
