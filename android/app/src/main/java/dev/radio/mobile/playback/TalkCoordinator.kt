package dev.radio.mobile.playback

import android.content.SharedPreferences
import androidx.core.content.edit
import dev.radio.mobile.data.FeelCatalog
import dev.radio.mobile.data.JournalIdentityProvider
import dev.radio.mobile.data.ListeningJournalRepository
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.RadioRepository
import dev.radio.mobile.recommendation.GuestRestore
import dev.radio.mobile.recommendation.OfflineRecommender
import dev.radio.mobile.recommendation.StationPick
import dev.radio.mobile.recommendation.TalkClip
import dev.radio.mobile.recommendation.TalkClipInfo
import dev.radio.mobile.recommendation.TalkShiftPolicy
import dev.radio.mobile.recommendation.TalkPolicy
import dev.radio.mobile.recommendation.TalkTurnResult
import dev.radio.mobile.recommendation.TalkWire
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Connected talk on the phone (checkpoint 13B): one turn per song gap in Radio. With Desktop picks off (13D) the
 * phone's own Radio and feel stations choose the songs and the desktop only writes and voices the talk, against a
 * talk-only context.
 *
 * When a Radio song actually starts, the turn is sent at once so the desktop has the whole song to generate. A clip
 * is inserted only once it is downloaded and verified, and only while the song still has the insertion margin left
 * and the planned next song is still next. Music never waits. Every clip is reported as played, skipped, missed or
 * discarded; reports are sent before the next turn so the desktop's show never drifts.
 */
internal class TalkCoordinator(
    private val engine: LocalPlaybackController,
    private val radio: RadioRepository,
    private val journal: ListeningJournalRepository,
    private val prefs: SharedPreferences,
    private val scope: CoroutineScope,
) {
    private val net = Mutex()
    private var turn: Job? = null
    private var lastSeq = 0L
    private var sessionStart = 0L
    private var sessionSongs = 0
    private val sessionRecent = ArrayDeque<LocalSong>()
    private var feedbackAtStart = emptyMap<String, dev.radio.mobile.data.LocalSongFeedback>()
    /** Clips that were inserted; their outcome comes from the engine. */
    private val inserted = HashSet<String>()

    init {
        radio.talkCache.clear()  // Clips never outlive the process that fetched them.
        engine.talkFileAllowed = { radio.talkCache.owns(it) }
        engine.onTalk = ::onTalkEvent
        prefs.getString(OPEN_JOB, null)?.let { stale -> scope.launch { report(stale, "missed", null) } }
        scope.launch { radio.talkSettings.collect { engine.setTalkGain(it.volume.linear); if (!it.enabled) stop("Off") } }
        // Ask the desktop what it offers as soon as talk is on, so Tune shows the controls and the server chip is current.
        scope.launch { radio.talkSettings.map { it.enabled }.distinctUntilChanged().collect { if (it) radio.connected.talkAccess() } }
        // The planned next song changed (skip, queue edit, mood change, refill): a queued clip is discarded.
        scope.launch {
            engine.state.map { Triple(it.talk, it.playOrder, it.currentId) }.distinctUntilChanged().collect { (talk, order, current) ->
                if (talk == null || talk.current) return@collect
                val next = order.indexOf(current).takeIf { it >= 0 }?.let { order.getOrNull(it + 1) }
                if (current != talk.afterId || next != talk.nextId || !engine.state.value.radioActive) engine.removeTalk()
            }
        }
        scope.launch {
            engine.state.map { it.radioActive }.distinctUntilChanged().collect { active ->
                if (active) { sessionStart = System.currentTimeMillis(); sessionSongs = 0; sessionRecent.clear(); feedbackAtStart = journal.state.value.feedback }
                else stop(null)
            }
        }
    }

    private fun stop(message: String?) {
        turn?.cancel(); turn = null
        engine.removeTalk()
        // The show is over: a guest set without its outro still ends, so put the listener's station back.
        guestRestore()?.let { pending -> saveRestore(null); TalkShiftPolicy.restore(station(), pending)?.let(::changeStation) }
        if (message != null) radio.setTalkStatus(message)
    }

    /** Called for every actual song start. Never delays playback. */
    fun songStarted(song: LocalSong) {
        turn?.cancel(); turn = null
        val state = engine.state.value
        if (!state.radioActive) return
        sessionSongs++
        sessionRecent.addFirst(song); while (sessionRecent.size > 6) sessionRecent.removeLast()
        if (!radio.talkSettings.value.enabled) return
        val seq = maxOf(lastSeq + 1, System.currentTimeMillis()).also { lastSeq = it }
        turn = scope.launch { runTurn(song, seq) }
    }

    private suspend fun runTurn(song: LocalSong, seq: Long) {
        var jobId: String? = null
        var handedOff = false
        try {
            val access = withContext(Dispatchers.IO) { radio.connected.talkAccess() }
            val capability = access?.talk
            if (access == null) { radio.setTalkStatus("Can't reach the desktop; music continues"); return }
            if (capability == null) { radio.setTalkStatus("This desktop doesn't offer talk"); return }
            if (!access.permitted) { radio.setTalkStatus("Not allowed on the desktop; music continues"); return }
            if (!capability.available) { radio.setTalkStatus(capability.message); return }
            // Desktop picks makes its own context at song start; give it a moment, then use (or make) a talk-only one.
            val contextId = radio.connected.context.value
                ?: (if (radio.connected.enabled) withTimeoutOrNull(CONTEXT_WAIT_MS) { radio.connected.context.first { it != null } } else null)
                ?: radio.state.value.let { withContext(Dispatchers.IO) { radio.connected.talkContext(it.mood, it.exploration) } }
                ?: run { radio.setTalkStatus("Can't reach the desktop; music continues"); return }
            // The song this clip will introduce. Desktop-first Radio may still be filling the queue.
            val next = withTimeoutOrNull(NEXT_WAIT_MS) {
                engine.state.first { s -> s.currentId != song.id || nextAfter(s, song.id) != null }
            }?.takeIf { it.currentId == song.id }?.let { nextAfter(it, song.id) } ?: return
            val nextSong = engine.state.value.queue.firstOrNull { it.id == next } ?: return
            val body = withContext(Dispatchers.Default) { turnBody(song, nextSong, seq) }
            val started = net.withLock { radio.talk.start(contextId, body) }
            radio.connected.noteReachable(started !is TalkTurnResult.Failed || started.reason.startsWith("HTTP") || started.reason == "invalid reply")
            val pending = when (started) {
                is TalkTurnResult.Pending -> started
                TalkTurnResult.None -> { radio.setTalkStatus("No talk this time"); return }
                is TalkTurnResult.Failed -> {
                    if (started.reason == "HTTP 404") radio.connected.contextLost(contextId)
                    radio.setTalkStatus("Talk unavailable (${started.reason}); music continues"); return
                }
            }
            jobId = pending.jobId
            prefs.edit { putString(OPEN_JOB, pending.jobId) }
            radio.setTalkStatus("The desktop is preparing something…")
            val info = withContext(Dispatchers.IO) { radio.talk.await(pending.jobId) { stillInGap(song.id, next) } }
                ?: run { radio.setTalkStatus("No talk this time"); return }
            val clip = withContext(Dispatchers.IO) { radio.talk.download(info) } ?: run { radio.setTalkStatus("Talk clip didn't arrive in time"); return }
            val ok = withContext(Dispatchers.Main.immediate) { stillInGap(song.id, next) && engine.insertTalk(clip, song.id, next) }
            if (!ok) { radio.talkCache.delete(clip); radio.setTalkStatus("Missed the gap; music continues"); return }
            inserted += clip.info.jobId
            handedOff = true
            radio.setTalkStatus("Up next: ${clip.info.title}")
        } finally {
            val open = jobId
            if (open != null && !handedOff) {
                // Not inserted (late, failed, superseded or cancelled): the desktop rolls the show back.
                scope.launch { report(open, "missed", null) }
            }
        }
    }

    private fun stillInGap(songId: String, next: String): Boolean {
        val s = engine.state.value
        return s.radioActive && s.currentId == songId && nextAfter(s, songId) == next &&
            TalkPolicy.canInsert(s.positionMs, s.durationMs) && radio.talkSettings.value.enabled
    }

    private fun nextAfter(state: PlaybackState, id: String): String? =
        state.playOrder.indexOf(id).takeIf { it >= 0 }?.let { state.playOrder.getOrNull(it + 1) }

    private fun onTalkEvent(event: LocalPlaybackController.TalkEvent) {
        when (event) {
            is LocalPlaybackController.TalkEvent.Started -> {
                radio.setTalkStatus("On air: ${event.clip.info.title}")
                applyShift(event.clip.info)
            }
            is LocalPlaybackController.TalkEvent.Finished -> {
                val clip = event.clip
                inserted -= clip.info.jobId
                radio.talkCache.delete(clip)
                val outcome = event.outcome
                if (outcome != "played" && outcome != "skipped") {
                    guestRestore()?.let { pending -> TalkShiftPolicy.onUnplayed(clip.info, station(), pending)?.let { saveRestore(null); changeStation(it) } }
                    if (clip.info.guestSet?.ends == true) saveRestore(null)
                }
                radio.setTalkStatus(when (outcome) { "played" -> "Last talk played"; "skipped" -> "Last talk skipped"; else -> "Talk removed" })
                scope.launch { report(clip.info.jobId, outcome, if (outcome == "skipped") event.heardMs / 1000.0 else null) }
            }
        }
    }

    private fun station() = radio.state.value.let { StationPick(it.mood, it.feel) }
    private fun guestRestore() = GuestRestore.decode(prefs.getString(GUEST_RESTORE, null))
    private fun saveRestore(value: GuestRestore?) = prefs.edit { if (value == null) remove(GUEST_RESTORE) else putString(GUEST_RESTORE, value.encode()) }

    /** A clip just started: its mood/feel shift (or a guest set's revert) replaces the upcoming Radio picks. */
    private fun applyShift(info: TalkClipInfo) {
        val feels = radio.feelChoices.value.filter { !it.archived }.map { it.feelId }.toSet()
        val result = TalkShiftPolicy.onStart(info, station(), guestRestore(), OfflineRecommender.MOODS.keys, feels)
        saveRestore(result.pending)
        result.apply?.let(::changeStation)
    }

    private fun changeStation(pick: StationPick) {
        radio.applyStation(pick)
        engine.newMix()
    }

    private suspend fun report(jobId: String, outcome: String, heardS: Double?) {
        val done = net.withLock { withContext(Dispatchers.IO) { radio.talk.report(jobId, outcome, heardS) } }
        if (done && prefs.getString(OPEN_JOB, null) == jobId) prefs.edit { remove(OPEN_JOB) }
    }

    /** The turn and its context extras (plan section 3.3). */
    private fun turnBody(after: LocalSong, next: LocalSong, seq: Long): String {
        val settings = radio.talkSettings.value
        val radioState = radio.state.value
        val mappings = JournalIdentityProvider.mappings.value
        fun ref(song: LocalSong, slot: String? = null) = TalkWire.song(song.title, mappings.entries[song.id]?.takeIf { it.song.id == song.id }?.songId, slot)
        val journalState = journal.state.value
        val history = journalState.history
        val streak = history.asSequence().filter { it.kind == "song_completed" || it.kind == "song_skipped" }
            .sortedByDescending { it.occurredAt }.takeWhile { it.kind == "song_skipped" }.count()
        val nextPlays = history.filter { it.localId == next.id && (it.kind == "song_completed" || it.kind == "song_skipped") }
        val lastHeard = nextPlays.maxOfOrNull { it.occurredAt }
        val days = lastHeard?.let { ((System.currentTimeMillis() - it) / 86_400_000L).toInt() }
        val feedback = journalState.feedback.values.filter { row -> feedbackAtStart[row.localId] != row && (row.favorite || row.disliked) }
            .mapNotNull { row -> engine.state.value.queue.firstOrNull { it.id == row.localId }?.let { song ->
                TalkWire.Feedback(song.title, mappings.entries[song.id]?.songId, row.favorite) } }.take(10)
        val feels = radio.feelChoices.value.filter { !it.archived && it.feelId != FeelCatalog.ENERGY_FEEL_ID }.map { TalkWire.Feel(it.feelId, it.name) }
        val activeFeel = radioState.feel?.let { id -> radio.feelChoices.value.firstOrNull { it.feelId == id } }?.let { TalkWire.Feel(it.feelId, it.name) }
        val now = LocalDateTime.now().truncatedTo(ChronoUnit.SECONDS)
        val slot = engine.state.value.origins[next.id]?.name?.lowercase(Locale.ROOT)
        return TalkWire.turn(TalkWire.Turn(
            turnId = UUID.randomUUID().toString(), seq = seq, after = ref(after), next = ref(next, slot), mood = radioState.mood,
            recent = sessionRecent.take(5).map { ref(it) }, feel = activeFeel, feels = feels, skipStreak = streak, feedback = feedback,
            history = TalkWire.History(nextPlays.count { it.kind == "song_completed" }, days),
            localIso = now.toString().let { if (it.length == 16) "$it:00" else it }, weekday = now.dayOfWeek.name.lowercase(Locale.ROOT),
            sessionMinutes = ((System.currentTimeMillis() - sessionStart) / 60_000L).toInt().coerceAtLeast(0), sessionSongs = sessionSongs,
            talk = settings.toWire(),
        ))
    }

    companion object {
        private const val OPEN_JOB = "talk_open_job"
        private const val GUEST_RESTORE = "talk_guest_restore"
        private const val CONTEXT_WAIT_MS = 20_000L
        private const val NEXT_WAIT_MS = 30_000L
    }
}
