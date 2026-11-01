package dev.radio.mobile.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

/** Immutable observations. Extra diagnostic fields deliberately stay outside the wire payload. */
@Entity(tableName = "local_listening_events", indices = [Index(value = ["attemptId", "kind"], unique = true)])
data class LocalListeningEvent(@PrimaryKey val eventId: String, val localId: String, val attemptId: String?,
    val kind: String, val occurredAt: Long, val logicalClock: Long, val payload: String,
    val listenedMs: Long, val completion: Double?, val terminationReason: String?, val provenance: String)

@Entity(tableName = "local_event_outbox")
data class LocalEventOutbox(@PrimaryKey val eventId: String, val serverId: String?, val deviceId: String, val payload: String)

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
    @Query("SELECT * FROM local_listening_events WHERE occurredAt >= :since AND kind IN ('song_started','song_skipped','dislike_changed')") suspend fun radioRecentEvents(since:Long): List<LocalListeningEvent>
    @Query("SELECT * FROM local_listening_events WHERE kind='song_started' ORDER BY logicalClock DESC LIMIT 5") suspend fun radioLastStarts(): List<LocalListeningEvent>
    @Query("SELECT * FROM local_event_outbox WHERE eventId IN (:ids)") suspend fun radioOrigins(ids:List<String>): List<LocalEventOutbox>
    @Query("SELECT * FROM local_event_outbox") suspend fun outbox(): List<LocalEventOutbox>
    @Query("SELECT value FROM local_journal_clock WHERE `key`=1") suspend fun clock(): Long?
    @Query("SELECT * FROM local_song_feedback WHERE localId=:id") suspend fun feedback(id: String): LocalSongFeedback?
    @Insert suspend fun insert(event: LocalListeningEvent)
    @Insert suspend fun enqueue(event: LocalEventOutbox)
    @Upsert suspend fun clock(value: LocalJournalClock)
    @Upsert suspend fun feedback(value: LocalSongFeedback)
}
