package dev.radio.mobile.recommendation

import dev.radio.mobile.playback.SlowedPreset
import java.io.File
import java.security.MessageDigest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject

/** What the phone holds of the desktop's Slowed lines (plan §5). */
data class SlowedBankState(
    val lines: List<SlowedLine> = emptyList(),
    val revision: Long = 0,
    val serverKey: String? = null,
    val updatedAt: Long = 0,
    val syncing: Boolean = false,
    val error: String? = null,
    /** The last few line ids played per kind, for variety. */
    val recent: Map<String, List<String>> = emptyMap(),
) {
    val bytes: Long get() = lines.sumOf { it.bytes.toLong() }
    val count: Int get() = lines.size
}

/**
 * The phone's Slowed line bank: desktop-voiced, generic lines in `noBackupFilesDir/slowed_lines`, kept without expiry
 * and separate from the talk cache (which is cleared at service start). Every file is verified (length, SHA-256 and
 * MIME) before it is renamed in; only files inside [dir] can become speech items.
 */
class SlowedBank(val dir: File, private val link: ConnectedServerLink, private val now: () -> Long = System::currentTimeMillis,
    /** Metered networks get at most 5 MB per sync; Wi-Fi and USB have no per-sync limit. */
    private val metered: () -> Boolean = { false }) {
    private val mutex = Mutex()
    private val index = File(dir, "index.json")
    private val mutable = MutableStateFlow(load())
    val state: StateFlow<SlowedBankState> = mutable.asStateFlow()
    /** Line ids played since the last report, with counts; sent with the next sync. */
    private val plays = HashMap<String, Int>()
    /** Files that must not be deleted right now (the current or next speech item). */
    @Volatile var inUse: (File) -> Boolean = { false }

    fun owns(file: File): Boolean = runCatching { file.canonicalFile.parentFile == dir.canonicalFile }.getOrDefault(false)
    fun file(line: SlowedLine) = File(dir, line.id + ".m4a")

    /** A stored line as a speech item for the player. */
    fun clip(line: SlowedLine, moment: SlowedMoment, title: String, guestName: String): TalkClip? {
        val file = file(line)
        if (!file.isFile || file.length() != line.bytes.toLong()) return null
        val info = TalkClipInfo("slowed-" + line.id, "talk", "slowed_" + moment.wire, title, listOf(TalkSpeaker(line.guest, guestName)),
            emptyList(), line.durationMs, null, null, TalkResource(line.id, line.sha256, line.bytes, 0),
            TalkSlowed(moment.wire, line.preset ?: "", line.guest, bank = true))
        return TalkClip(info, file)
    }

    /** A bank line reached the player: least-recently-played order and the no-repeat window move on. */
    fun played(lineId: String) {
        val line = mutable.value.lines.firstOrNull { it.id == lineId } ?: return
        synchronized(plays) { plays[lineId] = (plays[lineId] ?: 0) + 1 }
        val at = now()
        mutable.value = mutable.value.let { s ->
            val recent = s.recent + (line.kind to (s.recent[line.kind].orEmpty() + lineId).takeLast(SlowedBankPolicy.RECENT))
            s.copy(lines = s.lines.map { if (it.id == lineId) it.copy(lastPlayed = at, plays = it.plays + 1) else it }, recent = recent)
        }
        save(mutable.value)
    }

    /** Unpairing or switching desktops: every line goes. */
    suspend fun clear() = mutex.withLock { clearLocked(null) }

    private fun clearLocked(key: String?) {
        dir.listFiles()?.forEach { if (it.name != index.name) it.delete() }
        synchronized(plays) { plays.clear() }
        mutable.value = SlowedBankState(serverKey = key)
        save(mutable.value)
    }

    /**
     * Brings the bank up to the desktop's manifest: removes retired lines (unless in use), downloads missing ones one at a
     * time and reports plays. Returns false when the desktop could not be reached; the bank stays as it was.
     */
    suspend fun sync(): Boolean = mutex.withLock {
        mutable.value = mutable.value.copy(syncing = true)
        try {
            val access = link.access()
            if (access.talk?.slowed != true) { mutable.value = mutable.value.copy(syncing = false, error = "This desktop doesn't voice Slowed lines"); return@withLock false }
            if (mutable.value.serverKey != access.key) clearLocked(access.key)
            reportPlays()
            val since = mutable.value.revision
            val response = link.post("/talk/slowed-lines", ConnectedJson.write(linkedMapOf("protocol_version" to 1, "since" to since)))
            if (response.status != 200) { mutable.value = mutable.value.copy(syncing = false, error = "HTTP ${response.status}"); return@withLock false }
            val manifest = parseManifest(response.body) ?: run { mutable.value = mutable.value.copy(syncing = false, error = "invalid reply"); return@withLock false }
            var lines = mutable.value.lines.associateBy { it.id }.toMutableMap()
            val gone = if (manifest.full) lines.keys - manifest.lines.map { it.id }.toSet() else manifest.retired.toSet()
            for (id in gone) {
                val line = lines[id] ?: continue
                if (inUse(file(line))) continue
                file(line).delete(); lines.remove(id)
            }
            val budget = if (metered()) SlowedBankPolicy.METERED_BYTES_PER_SYNC else null
            val wanted = SlowedBankPolicy.toDownload(manifest.lines, lines, lines.values.sumOf { it.bytes.toLong() }, budget)
            var complete = wanted.size == manifest.lines.count { it.id !in lines }
            dir.mkdirs()
            for (line in wanted) {
                val stored = download(line)
                if (stored) { lines[line.id] = line; mutable.value = mutable.value.copy(lines = lines.values.toList()); save(mutable.value) }
                else complete = false
            }
            // Only a complete pass moves the revision on: anything skipped (budget, cap, a failed file) is listed again.
            mutable.value = mutable.value.copy(lines = lines.values.sortedBy { it.created }, revision = if (complete) manifest.revision else since,
                updatedAt = now(), syncing = false, error = null)
            save(mutable.value)
            true
        } catch (cancelled: CancellationException) { mutable.value = mutable.value.copy(syncing = false); throw cancelled }
        catch (failure: Exception) { mutable.value = mutable.value.copy(syncing = false, error = "Can't reach the desktop"); false }
    }

    private suspend fun download(line: SlowedLine): Boolean {
        val response = link.download("/talk/slowed-lines/${line.id}", TalkClipInfo.MAX_BYTES)
        if (response.status != 200) return false
        val bytes = response.body
        if (bytes.size != line.bytes || sha256(bytes) != line.sha256 || !looksLikeMp4(bytes)) return false
        val temp = File(dir, line.id + ".part"); val target = file(line)
        temp.writeBytes(bytes)
        if (!temp.renameTo(target)) { temp.delete(); return false }
        return true
    }

    private suspend fun reportPlays() {
        val batch = synchronized(plays) { HashMap(plays) }
        if (batch.isEmpty()) return
        val body = ConnectedJson.write(linkedMapOf("protocol_version" to 1, "plays" to batch.entries.take(300).associate { it.key to it.value }))
        val status = runCatching { link.post("/talk/slowed-lines/plays", body).status }.getOrNull()
        if (status == 200 || status == 422) synchronized(plays) { batch.forEach { (id, n) -> val left = (plays[id] ?: 0) - n; if (left > 0) plays[id] = left else plays.remove(id) } }
    }

    internal data class Manifest(val revision: Long, val full: Boolean, val lines: List<SlowedLine>, val retired: List<String>)

    internal fun parseManifest(body: String): Manifest? {
        val root = runCatching { ConnectedJson.parse(body) as? Map<*, *> }.getOrNull() ?: return null
        if (root["protocol_version"] != 1L) return null
        val revision = root["revision"] as? Long ?: return null
        val lines = (root["lines"] as? List<*>).orEmpty().mapNotNull { raw ->
            val m = raw as? Map<*, *> ?: return@mapNotNull null
            val id = (m["id"] as? String)?.takeIf { TOKEN.matches(it) } ?: return@mapNotNull null
            val guest = (m["guest"] as? String)?.takeIf { SlowedGuest.fromId(it) != null } ?: return@mapNotNull null
            val kind = (m["kind"] as? String)?.takeIf { it in SlowedKinds.ALL } ?: return@mapNotNull null
            val preset = m["preset"] as? String
            if (SlowedKinds.perPreset(kind) != (preset != null) || preset != null && SlowedPreset.fromId(preset) == null) return@mapNotNull null
            val sha = (m["sha256"] as? String)?.takeIf { HEX.matches(it) } ?: return@mapNotNull null
            val bytes = (m["byte_length"] as? Long)?.takeIf { it in 1..TalkClipInfo.MAX_BYTES.toLong() } ?: return@mapNotNull null
            if (m["mime"] != "audio/mp4") return@mapNotNull null
            SlowedLine(id, guest, kind, preset, sha, bytes.toInt(), ((m["duration_ms"] as? Long) ?: 0L).coerceIn(0, 120_000), (m["created"] as? Long) ?: 0L)
        }
        val retired = (root["retired"] as? List<*>).orEmpty().mapNotNull { (it as? String)?.takeIf(TOKEN::matches) }
        return Manifest(revision, root["full"] == true, lines, retired)
    }

    private fun load(): SlowedBankState = runCatching {
        val root = JSONObject(index.readText())
        val lines = root.getJSONArray("lines").let { a -> (0 until a.length()).map { i -> a.getJSONObject(i) }.map { o ->
            SlowedLine(o.getString("id"), o.getString("guest"), o.getString("kind"), o.optString("preset").ifEmpty { null }, o.getString("sha256"),
                o.getInt("bytes"), o.getLong("duration_ms"), o.getLong("created"), o.optLong("last_played"), o.optInt("plays"))
        } }.filter { file(it).let { f -> f.isFile && f.length() == it.bytes.toLong() } }
        val recent = root.optJSONObject("recent")?.let { r -> r.keys().asSequence().associateWith { k -> r.getJSONArray(k).let { a -> (0 until a.length()).map(a::getString) } } }.orEmpty()
        SlowedBankState(lines, root.optLong("revision"), root.optString("server").ifEmpty { null }, root.optLong("updated"), recent = recent)
    }.getOrDefault(SlowedBankState())

    private fun save(state: SlowedBankState) {
        runCatching {
            dir.mkdirs()
            val root = JSONObject().put("revision", state.revision).put("server", state.serverKey ?: "").put("updated", state.updatedAt)
                .put("lines", JSONArray(state.lines.map { l -> JSONObject().put("id", l.id).put("guest", l.guest).put("kind", l.kind).put("preset", l.preset ?: "")
                    .put("sha256", l.sha256).put("bytes", l.bytes).put("duration_ms", l.durationMs).put("created", l.created).put("last_played", l.lastPlayed).put("plays", l.plays) }))
                .put("recent", JSONObject(state.recent.mapValues { JSONArray(it.value) }))
            val temp = File(dir, "index.json.part")
            temp.writeText(root.toString())
            if (!temp.renameTo(index)) { index.delete(); temp.renameTo(index) }
        }
    }

    companion object {
        private val TOKEN = Regex("[A-Za-z0-9_-]{16,64}")
        private val HEX = Regex("[0-9a-f]{64}")
        fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        /** AAC in MP4: an `ftyp` box near the start. */
        fun looksLikeMp4(bytes: ByteArray) = bytes.size > 12 && String(bytes, 4, 4, Charsets.US_ASCII) == "ftyp"
    }
}
