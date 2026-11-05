package dev.radio.mobile.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

/** Immutable observations. Extra diagnostic fields deliberately stay outside the wire payload. */
@Entity(tableName = "local_listening_events", indices = [Index(value = ["attemptId", "kind"], unique = true)])
data class LocalListeningEvent(@PrimaryKey val eventId: String, val localId: String, val attemptId: String?,
    val kind: String, val occurredAt: Long, val logicalClock: Long, val payload: String,
    val listenedMs: Long, val completion: Double?, val terminationReason: String?, val provenance: String)

@Entity(tableName = "local_event_outbox")
data class LocalEventOutbox(@PrimaryKey val eventId: String, val serverId: String?, val deviceId: String, val payload: String,
    @ColumnInfo(defaultValue = "0") val acknowledged: Boolean = false)

@Entity(tableName = "portable_sync_cursor", primaryKeys = ["serverId", "deviceId"])
data class PortableSyncCursor(val serverId: String, val deviceId: String, val cursor: Long, val updatedAt: Long, val lastSuccessAt:Long?=null)

@Entity(tableName = "portable_remote_events", primaryKeys = ["serverId", "deviceId", "eventId"], indices = [Index(value = ["serverId", "occurredAt"]), Index(value = ["serverId", "logicalClock"])])
data class PortableRemoteEvent(val serverId:String, val deviceId: String, val eventId: String, val logicalClock: Long, val occurredAt: Long,
    val kind: String, val localId: String?, val payload: String)

/** Materialized last-writer registers. Payload is retained so future readers can inspect the exact winning event. */
@Entity(tableName = "portable_profile_registers", primaryKeys = ["serverId", "registerKey"])
data class PortableProfileRegister(val serverId:String, val registerKey: String, val logicalClock: Long, val deviceId: String,
    val eventId: String, val kind: String, val valueJson: String, val payload: String)

@Entity(tableName = "portable_sync_owner")
data class PortableSyncOwner(@PrimaryKey val ownerKey:String, val serverId: String, val deviceId: String,
    val installationMarker: String, val quarantined: Boolean)

@Entity(tableName = "local_journal_clock")
data class LocalJournalClock(@PrimaryKey val key: Int = 1, val value: Long)

@Entity(tableName = "local_song_feedback")
data class LocalSongFeedback(@PrimaryKey val localId: String, val favorite: Boolean, val disliked: Boolean)

@Dao interface ListeningJournalDao {
    @Query("SELECT * FROM local_listening_events ORDER BY logicalClock DESC LIMIT 200") fun observeEvents(): Flow<List<LocalListeningEvent>>
    @Query("SELECT * FROM local_song_feedback") fun observeFeedback(): Flow<List<LocalSongFeedback>>
    @Query("SELECT * FROM local_song_feedback") suspend fun allFeedback(): List<LocalSongFeedback>
    @Query("SELECT * FROM local_listening_events WHERE attemptId=:attempt AND kind='song_started' LIMIT 1") suspend fun start(attempt: String): LocalListeningEvent?
    @Query("SELECT * FROM local_listening_events WHERE attemptId=:attempt AND kind=:kind LIMIT 1") suspend fun event(attempt: String, kind: String): LocalListeningEvent?
    @Query("SELECT * FROM local_listening_events WHERE attemptId=:attempt AND kind IN ('song_completed','song_skipped','attempt_stopped') LIMIT 1") suspend fun terminal(attempt: String): LocalListeningEvent?
    @Query("SELECT * FROM local_listening_events ORDER BY logicalClock") suspend fun events(): List<LocalListeningEvent>
    @Query("SELECT * FROM local_listening_events WHERE kind IN ('song_completed','song_skipped','song_replayed','favorite_changed') OR (kind='dislike_changed' AND payload LIKE '%\"value\":true%') ORDER BY occurredAt DESC,eventId DESC LIMIT 2000") suspend fun radioTasteEvents(): List<LocalListeningEvent>
    @Query("SELECT e.* FROM local_listening_events e WHERE e.kind IN ('favorite_changed','dislike_changed') AND e.eventId=(SELECT e2.eventId FROM local_listening_events e2 WHERE e2.localId=e.localId AND e2.kind=e.kind ORDER BY e2.logicalClock DESC,e2.eventId DESC LIMIT 1)") suspend fun latestFeedbackEvents():List<LocalListeningEvent>
    @Query("SELECT * FROM local_listening_events WHERE occurredAt >= :since AND kind IN ('song_started','song_skipped','dislike_changed')") suspend fun radioRecentEvents(since:Long): List<LocalListeningEvent>
    @Query("SELECT * FROM local_listening_events WHERE kind='song_started' ORDER BY logicalClock DESC LIMIT 5") suspend fun radioLastStarts(): List<LocalListeningEvent>
    @Query("SELECT * FROM local_event_outbox WHERE eventId IN (:ids)") suspend fun radioOrigins(ids:List<String>): List<LocalEventOutbox>
    @Query("SELECT o.* FROM local_event_outbox o INNER JOIN local_listening_events e ON e.eventId=o.eventId WHERE o.serverId=:serverId AND e.kind IN ('favorite_changed','dislike_changed','blacklist_changed','energy_changed','preference_changed')") suspend fun profileLocalEvents(serverId:String):List<LocalEventOutbox>
    @Query("SELECT * FROM local_event_outbox WHERE acknowledged=0 ORDER BY rowid") suspend fun pendingOutbox(): List<LocalEventOutbox>
    @Query("SELECT COUNT(*) FROM local_event_outbox WHERE acknowledged=0 AND serverId IS NOT NULL") fun observePendingSyncCount():Flow<Int>
    @Query("SELECT * FROM local_event_outbox") suspend fun outbox(): List<LocalEventOutbox>
    @Query("SELECT value FROM local_journal_clock WHERE `key`=1") suspend fun clock(): Long?
    @Query("SELECT * FROM local_song_feedback WHERE localId=:id") suspend fun feedback(id: String): LocalSongFeedback?
    @Query("SELECT * FROM portable_sync_cursor WHERE serverId=:serverId AND deviceId=:deviceId") suspend fun syncCursor(serverId:String,deviceId:String): PortableSyncCursor?
    @Query("SELECT * FROM portable_sync_cursor ORDER BY updatedAt DESC") fun observeSyncCursors():Flow<List<PortableSyncCursor>>
    @Query("SELECT * FROM portable_sync_cursor ORDER BY updatedAt DESC LIMIT 1") suspend fun latestSyncCursor(): PortableSyncCursor?
    @Query("SELECT * FROM portable_remote_events WHERE serverId=:serverId ORDER BY logicalClock,eventId") suspend fun remoteEvents(serverId:String):List<PortableRemoteEvent>
    @Query("SELECT * FROM portable_remote_events WHERE serverId=:serverId AND kind IN ('song_started','song_completed','song_skipped','song_replayed','favorite_changed','dislike_changed') ORDER BY occurredAt DESC,eventId DESC LIMIT 2000") suspend fun remoteRadioEvents(serverId:String):List<PortableRemoteEvent>
    @Query("SELECT * FROM portable_remote_events WHERE serverId=:serverId AND kind IN ('favorite_changed','dislike_changed','blacklist_changed','energy_changed','preference_changed') ORDER BY logicalClock,eventId") suspend fun remoteProfileEvents(serverId:String):List<PortableRemoteEvent>
    @Query("SELECT * FROM portable_remote_events WHERE serverId=:serverId AND deviceId=:deviceId AND eventId=:eventId") suspend fun remoteEvent(serverId:String,deviceId:String,eventId:String):PortableRemoteEvent?
    @Query("SELECT * FROM portable_profile_registers WHERE serverId=:serverId") suspend fun profileRegisters(serverId:String):List<PortableProfileRegister>
    @Query("SELECT * FROM portable_profile_registers WHERE serverId=:serverId") fun observeProfileRegisters(serverId:String):Flow<List<PortableProfileRegister>>
    @Query("SELECT * FROM portable_sync_owner WHERE ownerKey=:ownerKey") suspend fun syncOwner(ownerKey:String):PortableSyncOwner?
    @Query("SELECT * FROM portable_sync_owner ORDER BY ownerKey") suspend fun syncOwners():List<PortableSyncOwner>
    @Insert suspend fun insert(event: LocalListeningEvent)
    @Insert suspend fun enqueue(event: LocalEventOutbox)
    @Upsert suspend fun clock(value: LocalJournalClock)
    @Upsert suspend fun feedback(value: LocalSongFeedback)
    @Upsert suspend fun cursor(value:PortableSyncCursor)
    @Insert(onConflict=OnConflictStrategy.IGNORE) suspend fun remoteEvent(value:PortableRemoteEvent):Long
    @Upsert suspend fun profileRegister(value:PortableProfileRegister)
    @Upsert suspend fun syncOwner(value:PortableSyncOwner)
    @Query("UPDATE local_event_outbox SET acknowledged=1 WHERE eventId IN (:ids)") suspend fun acknowledge(ids:List<String>)
    @Query("UPDATE local_journal_clock SET value=MAX(value,:clock) WHERE `key`=1") suspend fun advanceClock(clock:Long)
}
