package dev.radio.mobile.recommendation

import dev.radio.mobile.connection.ConnectionFailure
import java.io.File
import java.security.MessageDigest
import kotlin.math.pow
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay

/**
 * Connected talk (checkpoint 13B). The desktop decides what is said and renders it; the phone decides how much
 * talk it wants, plays the finished clip between two Radio songs and reports what happened to it.
 * Everything here is free of Android types so the policy can be verified in JVM tests.
 */

/** Talk loudness on the phone. The server renders clips at -10 LUFS; the phone only ever attenuates. */
enum class TalkVolume(val label: String, val gainDb: Float) {
    QUIETER("Quieter", -8f), NORMAL("Normal", -4f), LOUDER("Louder", 0f);
    val linear: Float get() = 10f.pow(gainDb / 20f)
}

/** The phone's Talk settings. Null fields follow the desktop's current value. */
data class TalkSettings(
    val enabled: Boolean = false,
    val frequency: Int? = null,
    val callers: Boolean? = null,
    val monologues: Boolean? = null,
    val guests: Boolean? = null,
    val guestSongs: Int? = null,
    val volume: TalkVolume = TalkVolume.NORMAL,
) {
    fun normalized() = copy(frequency = frequency?.coerceIn(MIN_FREQUENCY, MAX_FREQUENCY), guestSongs = guestSongs?.coerceIn(MIN_GUEST_SONGS, MAX_GUEST_SONGS))

    /** "Use desktop defaults" clears every override except On/Off. */
    fun desktopDefaults() = TalkSettings(enabled = enabled)
    val usesDesktopDefaults: Boolean get() = this == desktopDefaults()

    /** The turn's `talk` object, or null when everything follows the desktop. */
    fun toWire(): Map<String, Any?>? {
        val n = normalized()
        val out = linkedMapOf<String, Any?>()
        n.frequency?.let { out["frequency"] = it }
        n.callers?.let { out["callers"] = it }
        n.monologues?.let { out["monologues"] = it }
        n.guests?.let { out["guests"] = it }
        n.guestSongs?.let { out["guest_songs"] = it }
        return out.takeIf { it.isNotEmpty() }
    }

    fun encode(): String = ConnectedJson.write(linkedMapOf("enabled" to enabled, "volume" to volume.name, "talk" to toWire()))

    companion object {
        const val MIN_FREQUENCY = 2; const val MAX_FREQUENCY = 10
        const val MIN_GUEST_SONGS = 2; const val MAX_GUEST_SONGS = 8
        fun decode(text: String?): TalkSettings {
            val root = text?.let { runCatching { ConnectedJson.parse(it) as? Map<*, *> }.getOrNull() } ?: return TalkSettings()
            val talk = root["talk"] as? Map<*, *> ?: emptyMap<String, Any?>()
            return TalkSettings(root["enabled"] == true, (talk["frequency"] as? Long)?.toInt(), talk["callers"] as? Boolean,
                talk["monologues"] as? Boolean, talk["guests"] as? Boolean, (talk["guest_songs"] as? Long)?.toInt(),
                TalkVolume.entries.firstOrNull { it.name == root["volume"] } ?: TalkVolume.NORMAL).normalized()
        }
    }
}

/** What the paired desktop advertises under `connected_talk`. */
data class TalkCapability(val available: Boolean, val reason: String?, val maxClipSeconds: Int = 180) {
    val message: String get() = when {
        available -> "Available"
        reason == "tts_disabled" -> "Speech is turned off on the desktop"
        reason == "tts_unavailable" -> "The desktop's voice engine isn't ready"
        reason == "ffmpeg_missing" -> "The desktop can't encode talk clips (ffmpeg missing)"
        else -> "The desktop can't make talk right now"
    }
    companion object {
        fun parse(value: Any?): TalkCapability? {
            val map = value as? Map<*, *> ?: return null
            return TalkCapability(map["available"] == true, map["reason"] as? String, ((map["max_clip_seconds"] as? Long) ?: 180L).toInt().coerceIn(1, 600))
        }
    }
}

data class TalkSpeaker(val id: String, val name: String)
data class TalkLine(val speaker: String, val text: String)
data class TalkShift(val mood: String?, val feel: String?, val restoreAtEnd: Boolean)
data class TalkGuestSet(val persona: String?, val name: String?, val songs: Int?, val ends: Boolean)
data class TalkResource(val id: String, val sha256: String, val byteLength: Int, val expiresAt: Long)

/** A ready job's metadata (plan section 4.2). Unknown fields are ignored so later servers can add some. */
data class TalkClipInfo(
    val jobId: String, val kind: String, val segmentType: String, val title: String,
    val speakers: List<TalkSpeaker>, val transcript: List<TalkLine>, val durationMs: Long,
    val shift: TalkShift?, val guestSet: TalkGuestSet?, val resource: TalkResource,
) {
    val artist: String get() = "DJ · Talk"
    fun speakerName(id: String) = speakers.firstOrNull { it.id == id }?.name ?: id
    /** Short label for the queue row. */
    val typeLabel: String get() = when (segmentType) {
        "caller" -> "Caller"; "dj_monologue" -> "DJ"; "guest_takeover_intro" -> "Guest DJ"; "guest_takeover_outro" -> "Guest DJ"
        "announcement" -> "DJ"; else -> "Talk"
    }

    companion object {
        private val token = Regex("[A-Za-z0-9_-]{16,64}")
        private val hex = Regex("[0-9a-f]{64}")
        const val MAX_BYTES = 2 * 1024 * 1024
        fun parse(jobId: String, map: Map<*, *>): TalkClipInfo? {
            if (map["state"] != "ready" || map["job_id"] != jobId) return null
            val resource = map["resource"] as? Map<*, *> ?: return null
            val rid = resource["id"] as? String ?: return null
            val sha = resource["sha256"] as? String ?: return null
            val length = (resource["byte_length"] as? Long)?.takeIf { it in 1..MAX_BYTES.toLong() } ?: return null
            if (!token.matches(rid) || !hex.matches(sha) || resource["mime"] != "audio/mp4") return null
            val speakers = (map["speakers"] as? List<*>).orEmpty().mapNotNull { raw ->
                val s = raw as? Map<*, *>; val id = s?.get("id") as? String; val name = s?.get("name") as? String
                if (id != null && name != null) TalkSpeaker(id.take(64), name.take(80)) else null
            }
            val transcript = (map["transcript"] as? List<*>).orEmpty().mapNotNull { raw ->
                val l = raw as? Map<*, *>; val sp = l?.get("speaker") as? String; val text = l?.get("text") as? String
                if (sp != null && !text.isNullOrBlank()) TalkLine(sp.take(64), text.take(4000)) else null
            }.take(40)
            val shift = (map["shift"] as? Map<*, *>)?.let { TalkShift(it["mood"] as? String, it["feel"] as? String, it["restore_at_end"] == true) }
            val guest = (map["guest_set"] as? Map<*, *>)?.let { TalkGuestSet(it["persona"] as? String, it["name"] as? String, (it["songs"] as? Long)?.toInt(), it["ends"] == true) }
            val title = (map["title"] as? String)?.takeIf { it.isNotBlank() }?.take(120) ?: "Talk"
            return TalkClipInfo(jobId, map["kind"] as? String ?: "talk", map["segment_type"] as? String ?: "talk", title, speakers, transcript,
                ((map["duration_ms"] as? Long) ?: 0L).coerceIn(0L, 600_000L), shift, guest,
                TalkResource(rid, sha, length.toInt(), (resource["expires_at"] as? Long) ?: 0L))
        }
    }
}

/** A clip downloaded, verified and stored in the private talk cache. */
data class TalkClip(val info: TalkClipInfo, val file: File) {
    val mediaId: String get() = TalkPolicy.MEDIA_PREFIX + info.jobId
}

/** Timing rules for talk (plan sections 2 and 3.2). */
object TalkPolicy {
    const val MEDIA_PREFIX = "talk:"
    /** A clip is inserted only while the song before it has at least this much left. */
    const val INSERTION_MARGIN_MS = 3_000L
    /** Stop at end of song with a trailing clip: the clip fades over its last 10 s (or all of it when shorter). */
    const val STOP_FADE_MS = 10_000L

    fun isTalk(mediaId: String?) = mediaId?.startsWith(MEDIA_PREFIX) == true
    fun jobOf(mediaId: String?) = mediaId?.takeIf(::isTalk)?.removePrefix(MEDIA_PREFIX)

    /** True while there is still time to add a clip after the current song. Unknown length: never. */
    fun canInsert(positionMs: Long, durationMs: Long?, marginMs: Long = INSERTION_MARGIN_MS): Boolean =
        durationMs != null && durationMs > 0 && durationMs - positionMs.coerceAtLeast(0) >= marginMs

    /** Poll backoff while the desktop generates: quick at first, then every 5 s. */
    fun pollDelayMs(attempt: Int): Long = longArrayOf(1_000, 1_500, 2_000, 3_000, 4_000, 5_000)[attempt.coerceIn(0, 5)]

    fun stopFadeGain(positionMs: Long, durationMs: Long?): Float {
        val duration = durationMs?.takeIf { it > 0 } ?: return 1f
        val window = minOf(STOP_FADE_MS, duration)
        val remaining = (duration - positionMs).coerceAtLeast(0)
        return (remaining.toFloat() / window).coerceIn(0f, 1f)
    }

    /** What to tell the server when a clip leaves the timeline. */
    fun outcome(started: Boolean, completed: Boolean): String = when {
        started && completed -> "played"
        started -> "skipped"
        else -> "discarded"
    }
}

/** The song references a turn sends: catalog id when matched, else the filename title the Library shows. */
object TalkWire {
    fun song(title: String, serverId: String?, slot: String? = null): Map<String, Any?> = linkedMapOf<String, Any?>().apply {
        if (serverId != null) put("song_id", serverId) else { put("title", title.take(200)); put("source", "filename") }
        if (slot != null && serverId != null) put("slot", slot)
    }

    data class Feedback(val title: String, val serverId: String?, val liked: Boolean)
    data class Feel(val id: String, val name: String)
    data class History(val playCount: Int, val daysSince: Int?)

    data class Turn(
        val turnId: String, val seq: Long, val after: Map<String, Any?>, val next: Map<String, Any?>, val mood: String,
        val recent: List<Map<String, Any?>> = emptyList(), val feel: Feel? = null, val feels: List<Feel> = emptyList(),
        val skipStreak: Int = 0, val feedback: List<Feedback> = emptyList(), val milestones: List<Pair<String, String>> = emptyList(),
        val history: History? = null, val localIso: String? = null, val weekday: String? = null,
        val sessionMinutes: Int = 0, val sessionSongs: Int = 0, val talk: Map<String, Any?>? = null,
    )

    fun turn(t: Turn): String {
        val body = linkedMapOf<String, Any?>("protocol_version" to 1, "turn_id" to t.turnId, "turn_seq" to t.seq,
            "after_song" to t.after, "next_song" to t.next, "mood" to t.mood)
        if (t.recent.isNotEmpty()) body["recent_songs"] = t.recent.take(5)
        t.feel?.let { body["feel"] = feel(it) }
        if (t.feels.isNotEmpty()) body["feels"] = t.feels.take(24).map(::feel)
        body["skip_streak"] = t.skipStreak.coerceIn(0, 1000)
        if (t.feedback.isNotEmpty()) body["session_feedback"] = t.feedback.take(10).map { linkedMapOf("song" to song(it.title, it.serverId), "liked" to it.liked) }
        if (t.milestones.isNotEmpty()) body["milestones"] = t.milestones.take(8).map { linkedMapOf("id" to it.first.take(64), "text" to it.second.take(160)) }
        t.history?.let { body["next_song_history"] = linkedMapOf("play_count" to it.playCount.coerceAtLeast(0), "days_since" to it.daysSince?.coerceAtLeast(0)) }
        if (t.localIso != null && t.weekday != null) body["local_time"] = linkedMapOf("iso" to t.localIso, "weekday" to t.weekday)
        body["session_minutes"] = t.sessionMinutes.coerceIn(0, 100_000)
        body["session_songs"] = t.sessionSongs.coerceIn(0, 100_000)
        t.talk?.let { body["talk"] = it }
        return ConnectedJson.write(body)
    }

    private fun feel(f: Feel) = linkedMapOf("id" to f.id.take(64), "name" to f.name.take(80).ifBlank { "Feel" })

    fun outcome(outcome: String, heardS: Double?) = ConnectedJson.write(linkedMapOf<String, Any?>("protocol_version" to 1, "outcome" to outcome).apply {
        if (heardS != null) put("heard_s", (heardS.coerceIn(0.0, 240.0) * 10).toLong() / 10.0)
    })
}

/** Milestones are sent once each, in the turn after they were hit. */
class MilestoneOutbox {
    private val sent = HashSet<String>()
    @Synchronized fun take(hit: List<Pair<String, String>>): List<Pair<String, String>> = hit.filter { sent.add(it.first) }
    @Synchronized fun seed(ids: Collection<String>) { sent.addAll(ids) }
}

/** Bounded private storage for verified clips. Only files inside [dir] may ever become speech items. */
class TalkCache(val dir: File, private val maxFiles: Int = 6, private val now: () -> Long = System::currentTimeMillis) {
    fun owns(file: File): Boolean = runCatching { file.canonicalFile.parentFile == dir.canonicalFile }.getOrDefault(false)

    /** Verifies length and SHA-256 before anything is written. Returns null when the bytes don't match. */
    fun store(info: TalkClipInfo, bytes: ByteArray): TalkClip? {
        if (bytes.size != info.resource.byteLength || sha256(bytes) != info.resource.sha256) return null
        dir.mkdirs()
        sweep(keep = null)
        val target = File(dir, info.jobId + ".m4a")
        val temp = File(dir, info.jobId + ".part")
        temp.writeBytes(bytes)
        if (!temp.renameTo(target)) { temp.delete(); return null }
        trim()
        return TalkClip(info, target)
    }

    fun delete(clip: TalkClip) { if (owns(clip.file)) clip.file.delete() }

    /** Removes expired clips (and stray partial files). [keep] protects a clip that is in the timeline right now. */
    fun sweep(keep: TalkClip?, expiries: Map<String, Long> = emptyMap()) {
        val files = dir.listFiles() ?: return
        val nowSeconds = now() / 1000
        for (file in files) {
            if (keep != null && file.name == keep.file.name) continue
            val job = file.name.substringBeforeLast('.')
            val expired = expiries[job]?.let { it in 1..nowSeconds } ?: false
            if (file.name.endsWith(".part") || expired) file.delete()
        }
    }

    fun clear() { dir.listFiles()?.forEach { it.delete() } }

    private fun trim() {
        val files = dir.listFiles()?.filter { it.name.endsWith(".m4a") }?.sortedBy { it.lastModified() } ?: return
        files.dropLast(maxFiles).forEach { it.delete() }
    }

    companion object {
        fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }
}

/** One turn's state on the phone. */
sealed interface TalkTurnResult {
    data object None : TalkTurnResult
    data class Failed(val reason: String) : TalkTurnResult
    data class Pending(val jobId: String) : TalkTurnResult
}

/**
 * Network side of connected talk: start a turn, wait for the job, download and verify the clip, report outcomes.
 * It owns no playback; the service decides when a clip is inserted.
 */
class ConnectedTalkSource(private val link: ConnectedServerLink, private val cache: TalkCache) {
    private val jobPattern = Regex("[A-Za-z0-9_-]{16,64}")

    private fun obj(body: String): Map<*, *>? = runCatching { ConnectedJson.parse(body) as? Map<*, *> }.getOrNull()

    suspend fun start(contextId: String, body: String): TalkTurnResult = try {
        val response = link.post("/contexts/$contextId/talk", body)
        val reply = obj(response.body)
        when {
            response.status != 200 -> TalkTurnResult.Failed("HTTP ${response.status}")
            reply?.get("state") == "pending" && (reply["job_id"] as? String)?.let(jobPattern::matches) == true -> TalkTurnResult.Pending(reply["job_id"] as String)
            reply?.get("state") == "none" -> TalkTurnResult.None
            else -> TalkTurnResult.Failed("invalid reply")
        }
    } catch (cancelled: CancellationException) { throw cancelled }
    catch (failure: ConnectionFailure) { TalkTurnResult.Failed(failure.safeMessage) }
    catch (_: Exception) { TalkTurnResult.Failed("unreachable") }

    /**
     * Polls until the job is ready, finished without a clip, or [stillWanted] says the gap has passed.
     * Returns the clip metadata, or null for no clip.
     */
    suspend fun await(jobId: String, stillWanted: () -> Boolean): TalkClipInfo? {
        var attempt = 0
        while (stillWanted()) {
            val reply = try {
                val response = link.get("/talk/jobs/$jobId")
                if (response.status == 404) return null
                if (response.status == 200) obj(response.body) else null
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { null }
            when (reply?.get("state")) {
                "ready" -> return TalkClipInfo.parse(jobId, reply)
                "none", "failed", "cancelled" -> return null
            }
            delay(TalkPolicy.pollDelayMs(attempt++))
        }
        return null
    }

    suspend fun download(info: TalkClipInfo): TalkClip? = try {
        val response = link.download("/talk/resources/${info.resource.id}", TalkClipInfo.MAX_BYTES)
        if (response.status == 200) cache.store(info, response.body) else null
    } catch (cancelled: CancellationException) { throw cancelled }
    catch (_: Exception) { null }

    /** Best effort; the server rolls back a clip nobody reported. Returns true when the server took it. */
    suspend fun report(jobId: String, outcome: String, heardS: Double? = null): Boolean = try {
        val status = link.post("/talk/jobs/$jobId/outcome", TalkWire.outcome(outcome, heardS)).status
        status == 200 || status == 404 || status == 409
    } catch (cancelled: CancellationException) { throw cancelled }
    catch (_: Exception) { false }
}

/** The station Radio is playing: a mood, optionally narrowed to a feel. */
data class StationPick(val mood: String, val feel: String?)

/** What to put back when a guest set ends, and what the guest chose (a listener change since then wins). */
data class GuestRestore(val before: StationPick, val applied: StationPick) {
    fun encode() = listOf(before.mood, before.feel.orEmpty(), applied.mood, applied.feel.orEmpty()).joinToString("|")
    companion object {
        fun decode(text: String?): GuestRestore? {
            val p = text?.split("|")?.takeIf { it.size == 4 && it[0].isNotEmpty() && it[2].isNotEmpty() } ?: return null
            return GuestRestore(StationPick(p[0], p[1].ifEmpty { null }), StationPick(p[2], p[3].ifEmpty { null }))
        }
    }
}

/**
 * Show effects (plan section 3.4): a clip's shift applies when it starts playing and replaces the upcoming Radio picks.
 * A guest's shift reverts when the outro starts or the set ends without one; caller shifts stay.
 */
object TalkShiftPolicy {
    data class Result(val apply: StationPick?, val pending: GuestRestore?)

    fun onStart(info: TalkClipInfo, current: StationPick, pending: GuestRestore?, moods: Set<String>, feels: Set<String>): Result {
        if (info.guestSet?.ends == true) return Result(restore(current, pending), null)
        val shift = info.shift ?: return Result(null, pending)
        val mood = shift.mood?.takeIf { it in moods }
        val feel = shift.feel?.takeIf { it in feels }
        if (mood == null && feel == null) return Result(null, pending)
        val target = StationPick(mood ?: current.mood, feel)
        if (target == current) return Result(null, pending)
        val next = if (shift.restoreAtEnd) GuestRestore(pending?.before ?: current, target) else pending
        return Result(target, next)
    }

    /** A clip that never started: only a guest outro matters, since the set ends anyway. */
    fun onUnplayed(info: TalkClipInfo, current: StationPick, pending: GuestRestore?): StationPick? =
        if (info.guestSet?.ends == true) restore(current, pending) else null

    fun restore(current: StationPick, pending: GuestRestore?): StationPick? =
        pending?.takeIf { it.applied == current && it.before != current }?.before
}
