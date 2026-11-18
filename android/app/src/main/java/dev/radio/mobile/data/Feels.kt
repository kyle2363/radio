package dev.radio.mobile.data

import androidx.room.*
import dev.radio.mobile.portable.FeelRules
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.util.Locale
import java.util.UUID

/**
 * Personal feels (checkpoint 9B). Definitions are last-writer-wins by (clock, device, event); ratings and
 * comparisons are append-only evidence. [LocalSongFeelState] is a cache that [FeelRepository.recompute] can always
 * rebuild from evidence. Feel evidence is keyed by local song id; remote evidence for catalog songs is stored with
 * `catalog:<server song id>` and resolved through the current matches when scoring.
 */
@Entity(tableName = "local_feels")
data class LocalFeel(@PrimaryKey val feelId: String, val name: String, val color: String, val scale: Boolean,
    val archived: Boolean, val sortOrder: Int, val logicalClock: Long, val deviceId: String, val eventId: String)

@Entity(tableName = "local_feel_evidence", indices = [Index(value = ["feelId", "occurredAt"])])
data class LocalFeelEvidence(@PrimaryKey val eventId: String, val feelId: String, val kind: String, val songKey: String,
    val otherKey: String?, val value: Double?, val tie: Boolean, val weight: Double, val occurredAt: Long)

@Entity(tableName = "local_song_feel_state", primaryKeys = ["feelId", "songId"], indices = [Index(value = ["songId"])])
data class LocalSongFeelState(val feelId: String, val songId: String, val score: Double, val confidence: Double,
    val member: Boolean, val updatedAt: Long)

@Dao interface FeelDao {
    @Query("SELECT * FROM local_feels ORDER BY archived, sortOrder, name") fun observeFeels(): Flow<List<LocalFeel>>
    @Query("SELECT * FROM local_feels") suspend fun feels(): List<LocalFeel>
    @Query("SELECT * FROM local_feels WHERE feelId=:id") suspend fun feel(id: String): LocalFeel?
    @Upsert suspend fun saveFeel(value: LocalFeel)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun addEvidence(value: LocalFeelEvidence): Long
    @Query("SELECT * FROM local_feel_evidence WHERE feelId=:id ORDER BY occurredAt, eventId") suspend fun evidence(id: String): List<LocalFeelEvidence>
    @Query("SELECT * FROM local_song_feel_state") fun observeStates(): Flow<List<LocalSongFeelState>>
    @Query("SELECT * FROM local_song_feel_state WHERE feelId=:id") suspend fun states(id: String): List<LocalSongFeelState>
    @Query("DELETE FROM local_song_feel_state WHERE feelId=:id") suspend fun clearStates(id: String)
    @Insert suspend fun insertStates(values: List<LocalSongFeelState>)
}

/** Checkpoint 9B: feel definitions, append-only evidence and the rebuildable score cache. */
val LIBRARY_MIGRATION_12_13 = object : androidx.room.migration.Migration(12, 13) {
    override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_feels (feelId TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL, scale INTEGER NOT NULL, archived INTEGER NOT NULL, sortOrder INTEGER NOT NULL, logicalClock INTEGER NOT NULL, deviceId TEXT NOT NULL, eventId TEXT NOT NULL, PRIMARY KEY(feelId))")
        db.execSQL("CREATE TABLE IF NOT EXISTS local_feel_evidence (eventId TEXT NOT NULL, feelId TEXT NOT NULL, kind TEXT NOT NULL, songKey TEXT NOT NULL, otherKey TEXT, value REAL, tie INTEGER NOT NULL, weight REAL NOT NULL, occurredAt INTEGER NOT NULL, PRIMARY KEY(eventId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_feel_evidence_feelId_occurredAt ON local_feel_evidence (feelId, occurredAt)")
        db.execSQL("CREATE TABLE IF NOT EXISTS local_song_feel_state (feelId TEXT NOT NULL, songId TEXT NOT NULL, score REAL NOT NULL, confidence REAL NOT NULL, member INTEGER NOT NULL, updatedAt INTEGER NOT NULL, PRIMARY KEY(feelId, songId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_song_feel_state_songId ON local_song_feel_state (songId)")
    }
}

/** Starter feels (plan section "Starter feels"). Stable ids so the same starter on two devices is one feel. */
data class StarterFeel(val key: String, val name: String, val color: String, val meaning: String,
    val hintTags: Set<String> = emptySet(), val hintEnergy: ClosedFloatingPointRange<Double>? = null) {
    val feelId: String get() = if (key == "energy") FeelCatalog.ENERGY_FEEL_ID else UUID.nameUUIDFromBytes("radio-feel:$key".toByteArray()).toString()
}

object FeelCatalog {
    const val ENERGY_FEEL_ID = "00000000-0000-4000-8000-0000000000e1"
    /** Energy buckets shared with the desktop calibration panel. */
    val ENERGY_BUCKETS = listOf("Hot" to 0.875, "Warm" to 0.625, "Mellow" to 0.375, "Cold" to 0.125)
    val energy = StarterFeel("energy", "Energy", "#FDBA74", "How much a song lifts you, from Cold to Hot")
    /** Colours are light tints drawn from the checkpoint 6 mood palette family, readable on the station surface. */
    val starters = listOf(
        StarterFeel("hype", "Hype", "#FB923C", "Pump-up songs you turn all the way up", setOf("euphoric", "explosive", "energetic"), 0.7..1.0),
        StarterFeel("wind-down", "Wind down", "#5EEAD4", "Calm songs for the end of the day", setOf("dreamy", "intimate"), 0.0..0.35),
        StarterFeel("sad-good", "Sad-good", "#C4B5FD", "Sad songs that hurt in a good way", setOf("melancholic", "bittersweet", "cathartic")),
        StarterFeel("nostalgic", "Nostalgic", "#CBD5E1", "Songs that take you back to a time or memory", setOf("nostalgic")),
        StarterFeel("focus", "Focus", "#A5B4FC", "Background songs to work or study to", setOf("ambient", "chillstep", "lo-fi")),
        StarterFeel("drive", "Drive", "#FCD34D", "Steady songs for the road or commute", setOf("driving"), 0.5..0.9),
        StarterFeel("main-character", "Main character", "#F9A8D4", "Makes you feel like you're in a film", setOf("cinematic", "empowering", "confident")),
        StarterFeel("late-night", "Late night", "#93C5FD", "Moody, quiet songs for after midnight", setOf("atmospheric", "ethereal", "introspective")),
        StarterFeel("butterflies", "Butterflies", "#FDA4AF", "Crushes, new love and being in love", setOf("romantic", "passionate", "devotional")),
        StarterFeel("heartache", "Heartache", "#D8B4FE", "Breakups and missing someone", setOf("heartbreak", "longing", "yearning", "regretful")),
        StarterFeel("euphoria", "Euphoria", "#FDE047", "Big builds and drops that make you feel huge", setOf("euphoric", "anthemic", "soaring")),
        StarterFeel("floating", "Floating", "#BAE6FD", "Dreamy, weightless songs to drift to", setOf("ethereal", "dreamy")),
        StarterFeel("epic", "Epic", "#FCA5A5", "Huge, dramatic, boss-fight songs", setOf("epic", "dramatic", "intense")),
        StarterFeel("second-wind", "Second wind", "#86EFAC", "Lifts you back up when you're feeling low", setOf("hopeful", "uplifting", "resilient", "determined")),
    )
    private val color = Regex("^#[0-9A-Fa-f]{6}$")
    fun validName(value: String) = value.length in 1..40 && value.trim() == value && value.none { it.code < 32 }
    fun validColor(value: String) = color.matches(value)
}

data class FeelSummary(val feel: LocalFeel, val members: Int, val rated: Int, val settled: Double)
data class FeelsState(val feels: List<FeelSummary> = emptyList(), val bySong: Map<String, List<LocalSongFeelState>> = emptyMap(),
    val loaded: Boolean = false) {
    val active get() = feels.filter { !it.feel.archived }
    val needsSetup get() = loaded && feels.none { it.feel.feelId != FeelCatalog.ENERGY_FEEL_ID }
}

/**
 * Feel definitions and evidence. Every write is one Room transaction that also queues the wire event in the
 * journal outbox (paired: for the server; unpaired: kept local like other local-only events). Energy changes also
 * emit `energy_changed` through [emitEnergy], so both Radios keep using `user_energy` unchanged.
 */
class FeelRepository(private val db: LibraryDatabase, scope: CoroutineScope,
    private val identityOf: (LocalSong) -> JournalIdentity,
    private val emitEnergy: suspend (LocalSong, Double) -> Unit,
    private val now: () -> Long = System::currentTimeMillis) {
    private val dao = db.feels()
    private val fallbackDevice = "local:" + UUID.randomUUID()

    val state: StateFlow<FeelsState> = combine(dao.observeFeels(), dao.observeStates()) { feels, states ->
        val byFeel = states.groupBy { it.feelId }
        FeelsState(feels.map { feel ->
            val rows = byFeel[feel.feelId].orEmpty()
            FeelSummary(feel, rows.count { it.member }, rows.size,
                FeelRules.settledPercent(rows.associate { it.songId to FeelRules.FeelState(it.score, it.confidence, it.member) }))
        }, states.groupBy { it.songId }, loaded = true)
    }.catch { emit(FeelsState(loaded = true)) }.stateIn(scope, SharingStarted.Eagerly, FeelsState())

    private suspend fun <T> attempt(block: suspend () -> T): Result<T> = try { Result.success(block()) }
        catch (cancelled: CancellationException) { throw cancelled } catch (error: Exception) { Result.failure(error) }

    /** First run: keep the ticked starters (Energy is always present). Idempotent. */
    suspend fun setUp(keys: Set<String>): Result<Unit> = attempt { db.withTransaction {
        ensureEnergy()
        FeelCatalog.starters.forEachIndexed { index, starter ->
            if (starter.key in keys && dao.feel(starter.feelId) == null) define(starter.feelId, starter.name, starter.color, false, false, index + 1)
        }
    } }

    suspend fun create(name: String, color: String): Result<String> = attempt { db.withTransaction {
        val id = UUID.randomUUID().toString()
        define(id, name.trim(), color, false, false, (dao.feels().maxOfOrNull { it.sortOrder } ?: 0) + 1); id
    } }

    suspend fun edit(feelId: String, name: String? = null, color: String? = null, archived: Boolean? = null): Result<Unit> = attempt { db.withTransaction {
        val feel = requireNotNull(dao.feel(feelId)) { "That feel no longer exists." }
        require(!(feelId == FeelCatalog.ENERGY_FEEL_ID && archived == true)) { "Energy is built in and can't be removed." }
        define(feelId, name?.trim() ?: feel.name, color ?: feel.color, feel.scale, archived ?: feel.archived, feel.sortOrder)
    } }

    /** Bucket rating or In (1.0) / Not (0.0) for tag feels. */
    suspend fun rate(feelId: String, song: LocalSong, value: Double): Result<Unit> = attempt { db.withTransaction {
        require(value in 0.0..1.0) { "Rating must be between 0 and 1." }
        if (feelId == FeelCatalog.ENERGY_FEEL_ID) ensureEnergy()
        requireNotNull(dao.feel(feelId)) { "That feel no longer exists." }
        val identity = identityOf(song)
        val eventId = emit(identity, "feel_rated", feelId, value, song = identity)
        dao.addEvidence(LocalFeelEvidence(eventId, feelId, "rated", song.id, null, value, false, 1.0, now()))
        recomputeInside(feelId)
    } }

    suspend fun tag(feelId: String, song: LocalSong, member: Boolean) = rate(feelId, song, if (member) 1.0 else 0.0)

    suspend fun compare(feelId: String, winner: LocalSong, loser: LocalSong, tie: Boolean = false): Result<Unit> = attempt { db.withTransaction {
        require(winner.id != loser.id) { "Pick two different songs." }
        requireNotNull(dao.feel(feelId)) { "That feel no longer exists." }
        val first = identityOf(winner); val second = identityOf(loser)
        val eventId = emit(first, "feel_compared", feelId, if (tie) "tie" else "win", song = first, other = second)
        dao.addEvidence(LocalFeelEvidence(eventId, feelId, "compared", winner.id, loser.id, null, tie, 1.0, now()))
        recomputeInside(feelId)
    } }

    /** Merge downloaded feel events (inside the sync transaction). Returns the feels whose evidence changed. */
    internal suspend fun applyRemote(events: List<PortableRemoteEvent>): Set<String> {
        val changed = mutableSetOf<String>()
        for (row in events) {
            val event = runCatching { JSONObject(row.payload) }.getOrNull() ?: continue
            val feelId = event.optString("feel_id").takeIf { it.isNotEmpty() } ?: continue
            when (row.kind) {
                "feel_defined" -> {
                    val value = event.optJSONObject("value") ?: continue
                    val old = dao.feel(feelId)
                    val newer = old == null || compareValuesBy(Triple(old.logicalClock, old.deviceId, old.eventId),
                        Triple(row.logicalClock, row.deviceId, row.eventId), { it.first }, { it.second }, { it.third }) < 0
                    if (newer) dao.saveFeel(LocalFeel(feelId, value.optString("name"), value.optString("color"), value.optBoolean("scale"),
                        value.optBoolean("archived"), old?.sortOrder ?: ((dao.feels().maxOfOrNull { it.sortOrder } ?: 0) + 1),
                        row.logicalClock, row.deviceId, row.eventId))
                }
                "feel_rated", "feel_compared" -> {
                    val key = remoteKey(event, "song_id", "local_id", row.deviceId) ?: continue
                    val other = if (row.kind == "feel_compared") remoteKey(event, "other_song_id", "other_local_id", row.deviceId) ?: continue else null
                    val rated = row.kind == "feel_rated"
                    val inserted = dao.addEvidence(LocalFeelEvidence("${row.deviceId}/${row.eventId}", feelId, if (rated) "rated" else "compared",
                        key, other, if (rated) event.optDouble("value") else null, !rated && event.optString("value") == "tie",
                        if (rated) event.optDouble("weight", 1.0) else 1.0, row.occurredAt))
                    if (inserted != -1L) changed += feelId
                }
            }
        }
        changed.forEach { recomputeInside(it, emit = false) }
        return changed
    }

    private fun remoteKey(event: JSONObject, songField: String, localField: String, device: String): String? {
        val song = event.opt(songField)
        if (song is String && song.isNotEmpty()) return "catalog:$song"
        val local = event.optString(localField).takeIf { it.isNotEmpty() } ?: return null
        return "remote:$device:$local"
    }

    /** Rebuild the cache for one feel from its evidence; Energy changes queue `energy_changed` for local edits. */
    suspend fun recompute(feelId: String) = db.withTransaction { recomputeInside(feelId) }

    private suspend fun recomputeInside(feelId: String, emit: Boolean = true) {
        val catalogToLocal = HashMap<String, String>()
        JournalIdentityProvider.mappings.value.entries.values.filter { it.song.available }
            .forEach { catalogToLocal.putIfAbsent("catalog:${it.songId}", it.song.id) }
        fun resolve(key: String) = if (key.startsWith("catalog:")) catalogToLocal[key] ?: key else key
        val evidence = dao.evidence(feelId)
        val nowSeconds = now() / 1000.0
        val ratings = evidence.filter { it.kind == "rated" && it.value != null }
            .map { FeelRules.Rating(resolve(it.songKey), it.value!!, it.occurredAt / 1000.0, it.weight) }
        val comparisons = evidence.filter { it.kind == "compared" && it.otherKey != null }
            .map { FeelRules.Comparison(resolve(it.songKey), resolve(it.otherKey!!), it.occurredAt / 1000.0, it.tie) }
        val scored = FeelRules.scoreFeel(ratings, comparisons, nowSeconds)
            .filterKeys { !it.startsWith("catalog:") && !it.startsWith("remote:") }
        val previous = dao.states(feelId).associateBy { it.songId }
        dao.clearStates(feelId)
        val at = now()
        dao.insertStates(scored.map { (song, s) -> LocalSongFeelState(feelId, song, s.score, s.confidence, s.member, at) })
        if (feelId == FeelCatalog.ENERGY_FEEL_ID && emit) for ((songId, s) in scored) {
            if (FeelRules.energyChanged(previous[songId]?.score, s.score)) db.library().song(songId)?.let { emitEnergy(it, s.score) }
        }
    }

    private suspend fun ensureEnergy() {
        if (dao.feel(FeelCatalog.ENERGY_FEEL_ID) == null)
            define(FeelCatalog.ENERGY_FEEL_ID, FeelCatalog.energy.name, FeelCatalog.energy.color, true, false, 0)
    }

    private suspend fun define(feelId: String, name: String, color: String, scale: Boolean, archived: Boolean, order: Int) {
        require(FeelCatalog.validName(name)) { "Use a feel name between 1 and 40 characters." }
        require(FeelCatalog.validColor(color)) { "Pick a colour." }
        val identity = JournalIdentityProvider.currentProfileIdentity()
        val value = JSONObject().put("name", name).put("color", color.uppercase(Locale.ROOT)).put("scale", scale).put("archived", archived)
        val (clock, device, eventId) = emitRaw(identity, "feel_defined", feelId, value)
        dao.saveFeel(LocalFeel(feelId, name, color.uppercase(Locale.ROOT), scale, archived, order, clock, device, eventId))
    }

    private suspend fun emit(identity: JournalIdentity, kind: String, feelId: String, value: Any,
        song: JournalIdentity, other: JournalIdentity? = null): String =
        emitRaw(identity, kind, feelId, value, song, other).third

    /** Queues one wire event; returns (clock, device, event id). */
    private suspend fun emitRaw(identity: JournalIdentity?, kind: String, feelId: String, value: Any,
        song: JournalIdentity? = null, other: JournalIdentity? = null): Triple<Long, String, String> {
        val journal = db.journal()
        val clock = (journal.clock() ?: 0L) + 1
        check(clock <= 9007199254740991L) { "Journal clock exhausted" }
        val eventId = UUID.randomUUID().toString()
        val device = identity?.deviceId ?: fallbackDevice
        val payload = JSONObject().apply {
            put("protocol_version", 1); put("device_id", device); put("event_id", eventId)
            put("logical_clock", clock); put("kind", kind); put("occurred_at", now() / 1000)
            put("feel_id", feelId); put("value", value)
            if (song != null) {
                if (song.songId != null) { put("song_id", song.songId); put("catalog_id", song.catalogId) }
                else { put("song_id", JSONObject.NULL); put("local_id", song.localId) }
            }
            if (other != null) {
                if (other.songId != null) { put("other_song_id", other.songId); if (!has("catalog_id")) put("catalog_id", other.catalogId) }
                else put("other_local_id", other.localId)
            }
        }.toString()
        journal.clock(LocalJournalClock(value = clock))
        // Only a paired identity has a device id the server recognises; unpaired feels stay local.
        if (identity?.serverId != null && (song == null || song.serverId == identity.serverId))
            journal.enqueue(LocalEventOutbox(eventId, identity.serverId, identity.deviceId, payload))
        return Triple(clock, device, eventId)
    }
}
