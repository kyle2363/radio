package dev.radio.mobile.data

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import dev.radio.mobile.recommendation.TalkClipInfo
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.flow.Flow

/**
 * One connected-talk clip that reached the player (checkpoint 13C). Local only, never synced, and never a song play.
 * [shift] is the change the clip made when it started ("mood:<mood>" or "feel:<name>"), if any.
 */
@Entity(tableName = "local_talk_events", indices = [Index(value = ["finishedAt"])])
data class LocalTalkEvent(
    @PrimaryKey val jobId: String,
    val segmentType: String,
    val personaId: String?,
    val personaName: String?,
    val startedAt: Long?,
    val finishedAt: Long,
    val heardMs: Long,
    val clipMs: Long,
    val outcome: String,
    val shift: String?,
)

@Dao
interface TalkEventDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun save(event: LocalTalkEvent)
    @Query("SELECT * FROM local_talk_events ORDER BY finishedAt") suspend fun all(): List<LocalTalkEvent>
    @Query("SELECT COUNT(*) FROM local_talk_events") fun observeCount(): Flow<Int>
}

val LIBRARY_MIGRATION_13_14 = object : Migration(13, 14) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_talk_events (jobId TEXT NOT NULL, segmentType TEXT NOT NULL, personaId TEXT, personaName TEXT, startedAt INTEGER, finishedAt INTEGER NOT NULL, heardMs INTEGER NOT NULL, clipMs INTEGER NOT NULL, outcome TEXT NOT NULL, shift TEXT, PRIMARY KEY(jobId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_talk_events_finishedAt ON local_talk_events (finishedAt)")
    }
}

data class TalkSummary(
    val heardMs: Long, val clips: Int, val byType: Map<String, Int>, val topCaller: Pair<String, Int>?, val topGuest: Pair<String, Int>?,
    val skipRate: Double?, val guestChanges: List<String>,
) {
    val empty: Boolean get() = clips == 0
}

object TalkStats {
    private val HEARD = setOf("played", "skipped")

    /** The row for a clip the player finished with [outcome]. The persona is the voice that isn't the host DJ. */
    fun event(info: TalkClipInfo, outcome: String, heardMs: Long, startedAt: Long?, finishedAt: Long, shift: String?): LocalTalkEvent {
        val other = when {
            info.segmentType.startsWith("guest_takeover") -> info.guestSet?.persona?.let { id -> info.speakers.firstOrNull { it.id == id } }
                ?: info.speakers.firstOrNull()
            info.segmentType == "caller" -> info.speakers.firstOrNull { it.id != "dj" }
            else -> null
        }
        return LocalTalkEvent(info.jobId, info.segmentType, other?.id, other?.name, startedAt, finishedAt, heardMs.coerceAtLeast(0),
            info.durationMs, outcome, shift)
    }

    private fun typeLabel(type: String) = when {
        type == "caller" -> "Callers"
        type.startsWith("guest_takeover") -> "Guest DJs"
        else -> "DJ"
    }

    /** Talk heard in [period] (the Stats periods, so numbers agree with the rest of the screen). */
    fun summarize(events: List<LocalTalkEvent>, period: StatsPeriod, now: Long, zone: ZoneId): TalkSummary {
        val range = StatsCalculator.range(period, Instant.ofEpochMilli(now).atZone(zone).toLocalDate(), zone)
        val heard = events.filter { it.outcome in HEARD && (range == null || it.finishedAt in range.first until range.second) }
        fun top(type: (String) -> Boolean) = heard.filter { type(it.segmentType) && it.personaName != null }
            .groupingBy { it.personaName!! }.eachCount().entries.sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenBy { it.key })
            .firstOrNull()?.let { it.key to it.value }
        return TalkSummary(
            heardMs = heard.sumOf { it.heardMs },
            clips = heard.size,
            byType = heard.groupingBy { typeLabel(it.segmentType) }.eachCount().toSortedMap(),
            topCaller = top { it == "caller" },
            topGuest = top { it == "guest_takeover_intro" },
            skipRate = if (heard.isEmpty()) null else heard.count { it.outcome == "skipped" }.toDouble() / heard.size,
            guestChanges = heard.filter { it.segmentType == "guest_takeover_intro" && it.shift != null }.map { it.shift!!.substringAfter(':') },
        )
    }
}
