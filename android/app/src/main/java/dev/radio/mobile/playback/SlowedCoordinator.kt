package dev.radio.mobile.playback

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.*
import kotlin.random.Random
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

/** What the station card shows about Slowed (checkpoint 14). */
data class SlowedUi(
    /** Slowed is on, Talk is on and this station allows it: the station card offers "Slow it down". */
    val offered: Boolean = false,
    /** "Slow it down" would work now; otherwise [reason] says why. */
    val canStart: Boolean = false,
    val reason: String? = null,
    val running: Boolean = false,
    val manual: Boolean = false,
    val preset: SlowedPreset? = null,
    val songsLeft: Int = 0,
)

/**
 * Runs Slowed sets on the phone (checkpoint 14C/14D). Decides at a song's actual start whether the next gap opens a
 * set, asks the desktop for a live line on that song's talk turn, and falls back to the phone's bank shortly before the
 * boundary. The effect starts only once the opener has started playing ("no voice, no effect"); the set's songs are
 * handed to the player as a [SlowedPlan], and the closer's start (or the next boundary without one) reverts it.
 *
 * Everything is in memory: after process death Radio comes back normal, like the sleep timer.
 */
internal class SlowedCoordinator(
    private val engine: LocalPlaybackController,
    private val radio: RadioRepository,
    private val scope: CoroutineScope,
    private val random: Random = Random.Default,
) {
    private val bank get() = radio.slowedBank
    private var set: SlowedSet? = null
    /** The line this gap should get, and the song it follows. */
    private var pending: SlowedRequest? = null
    private var pendingAfter: String? = null
    private var fallback: Job? = null
    private var songsSinceLast = SlowedPolicy.COOLDOWN_SONGS
    private var lastGuest: SlowedGuest? = null
    private var mixing = MutableStateFlow(false)
    private var effected: Set<String> = emptySet()
    /** The desktop voices Slowed lines on turns; refreshed by talk turns and syncs. */
    @Volatile var desktopSlowed = false
    private val ui = MutableStateFlow(SlowedUi())
    val state: StateFlow<SlowedUi> = ui.asStateFlow()

    init {
        // The player's plan follows the queue: refills, New mix and talk items change which songs are next.
        scope.launch {
            engine.state.map { Triple(it.currentId, it.playOrder, it.talk?.takeIf { t -> t.current }?.nextId) }.distinctUntilChanged().collect { publishPlan() }
        }
        scope.launch {
            engine.state.map { it.radioActive }.distinctUntilChanged().collect { active ->
                if (!active) stopSilently()
                else scope.launch { runCatching { bank.sync() }; publishUi() }
            }
        }
        // Talk off or Slowed off ends a set silently; a station that doesn't allow Slowed ends it with its closer.
        scope.launch {
            combine(radio.talkSettings.map { it.enabled }, radio.slowedSettings, radio.slowedFeels, radio.state.map { it.feel }.distinctUntilChanged()) { talkOn, settings, feels, feel ->
                Triple(talkOn && settings.enabled, feel == null || feel in feels, settings)
            }.distinctUntilChanged().collect { (on, feelAllowed, _) ->
                when {
                    !on -> stopSilently()
                    !feelAllowed -> endWithCloser()
                }
                if (on && radio.slowedSettings.value.enabled) scope.launch { runCatching { bank.sync() }; publishUi() }
                publishUi()
            }
        }
        scope.launch { bank.state.collect { publishUi() } }
        radio.onSlowedStart = { preset -> startManual(preset) }
        radio.onSlowedStop = { backToNormal() }
    }

    fun ownsFile(file: java.io.File) = bank.owns(file)

    // -- song starts --------------------------------------------------------------------

    /** Called on every actual song start, before the talk turn for this song is sent. */
    fun songStarted(song: LocalSong) {
        val playback = engine.state.value
        if (!playback.radioActive) { stopSilently(); return }
        fallback?.cancel(); fallback = null
        pending = null; pendingAfter = null
        var current = set
        if (current != null && current.committed) {
            val before = current.played.size
            current = SlowedPolicy.songStarted(current, song.id, effected)
            set = current
            if (current.played.size == before && song.id !in current.played) {
                // The set's songs are over (the closer was missed or the queue moved on): back to normal at this boundary.
                if (current.closing || current.songsLeft == 0) finish()
            } else if (current.closing) request(SlowedMoment.CLOSER, song)
            else if (SlowedPolicy.wantsMid(current, random)) { set = current.copy(midsAt = current.midsAt + current.played.size); request(SlowedMoment.MID, song) }
        } else if (current != null && !current.committed) {
            // An opener that never played: the set never started.
            abandon()
        }
        if (set == null) {
            songsSinceLast++
            maybeStartAuto(song)
        }
        publishPlan(); publishUi()
    }

    private fun maybeStartAuto(song: LocalSong) {
        val settings = radio.slowedSettings.value
        val talk = radio.talkSettings.value
        val station = radio.state.value
        if (!SlowedPolicy.allowed(settings, talk.enabled, engine.state.value.radioActive, station.feel, radio.slowedFeels.value)) return
        val guestSet = engine.state.value.talk?.info?.guestSet != null
        if (!SlowedPolicy.shouldStartAuto(settings, talk.frequency, songsSinceLast, guestSet, running = false, random = random)) return
        val preset = SlowedPreset.pick(settings.presets, random) ?: return
        val lines = bank.state.value.lines
        // Offline sets wait for a usable bank; with the desktop voicing lines a live opener can carry the first sets.
        if (!desktopSlowed && !SlowedBankPolicy.ready(lines, settings.presets)) return
        val guests = if (desktopSlowed) SlowedGuest.entries.toSet() else SlowedBankPolicy.guestsWithOpeners(lines, preset, manual = false)
        val guest = SlowedPolicy.pickGuest(guests, lastGuest, random) ?: return
        scope.launch {
            val pool = poolFor(station.feel)
            val length = SlowedPolicy.fit(SlowedPolicy.autoLength(settings, random), pool?.let { unplayed(it) }) ?: return@launch
            // The decision may have taken a moment: still the same song, nothing else started.
            if (engine.state.value.currentId != song.id || set != null) return@launch
            set = SlowedSet(preset, guest, manual = false, total = length, afterId = song.id)
            applyPool(pool)
            request(SlowedMoment.OPENER, song)
            publishUi()
        }
    }

    // -- lines ----------------------------------------------------------------------------

    private fun request(moment: SlowedMoment, song: LocalSong) {
        val s = set ?: return
        pending = SlowedRequest(moment, s.preset, s.guest, s.total, s.songsLeft, s.manual)
        pendingAfter = song.id
        fallback?.cancel()
        // The live line comes through this song's talk turn; shortly before the boundary the bank covers a late one.
        fallback = scope.launch { bankFallback(song.id) }
    }

    /** For the talk turn about to be sent for [songId]: the `effects` object, and whether a set is playing. */
    fun turnExtras(songId: String): Pair<Map<String, Any?>?, Boolean> {
        val request = pending?.takeIf { pendingAfter == songId }
        return request?.toWire() to (set?.committed == true)
    }

    /**
     * Whether a live clip may go into the gap after [afterId]. A Slowed line is accepted only while this gap still waits
     * for exactly that line (Slowed may have been turned off, or the set ended, while the desktop was writing it).
     */
    fun accepts(info: TalkClipInfo, afterId: String): Boolean {
        val line = info.slowed ?: return true
        val request = pending?.takeIf { pendingAfter == afterId } ?: return false
        return request.moment.wire == line.moment && radio.slowedSettings.value.enabled && radio.talkSettings.value.enabled
    }

    /** A gap waiting for a Slowed line skips a normal turn when the desktop can't voice one, so the bank line fits. */
    fun reservesGap(songId: String) = pendingAfter == songId && pending != null

    /** A talk turn waits for a Slowed set's New mix, so it introduces the right next song. */
    suspend fun awaitMix() { withTimeoutOrNull(5_000) { mixing.first { !it } } }

    private suspend fun bankFallback(songId: String) {
        while (true) {
            val s = engine.state.value
            if (s.currentId != songId || !s.radioActive) return
            val remaining = (s.durationMs ?: return) - s.positionMs
            if (remaining <= SlowedPolicy.BANK_FALLBACK_MS) break
            delay(250)
        }
        val request = pending?.takeIf { pendingAfter == songId } ?: return
        val now = engine.state.value
        // A live line for this gap is already queued.
        if (now.talk?.afterId == songId && now.talk.info.slowed != null) return
        val inserted = insertBankLine(request, songId, margin = TalkPolicy.INSERTION_MARGIN_MS)
        if (!inserted) noLine(request)
    }

    private fun insertBankLine(request: SlowedRequest, afterId: String, margin: Long): Boolean {
        val state = engine.state.value
        val order = state.playOrder
        val next = order.getOrNull(order.indexOf(afterId) + 1) ?: return false
        val line = SlowedBankPolicy.choose(bank.state.value.lines, bank.state.value.recent, request.guest, request.moment, request.preset, request.manual, random) ?: return false
        val clip = bank.clip(line, request.moment, title(request), request.guest.displayName) ?: return false
        // A normal clip queued for this gap gives way: the Slowed line is what the listener must hear.
        if (state.talk != null && !state.talk.current && state.talk.info.slowed == null) engine.removeTalk()
        return engine.insertTalk(clip, afterId, next, margin)
    }

    private fun noLine(request: SlowedRequest) {
        when (request.moment) {
            SlowedMoment.OPENER -> abandon()                 // No voice, no effect.
            SlowedMoment.MID -> Unit
            SlowedMoment.CLOSER -> Unit                      // Silent revert at the boundary (the next song isn't in the set).
        }
    }

    private fun title(request: SlowedRequest) = when (request.moment) {
        SlowedMoment.OPENER -> "${request.guest.displayName} slows it down"
        SlowedMoment.MID -> "${request.guest.displayName}: still slowed"
        SlowedMoment.CLOSER -> "${request.guest.displayName}: back to normal"
    }

    /** From the talk coordinator: a Slowed line started or left the timeline. */
    fun onTalk(event: LocalPlaybackController.TalkEvent) {
        val slowed = event.clip.info.slowed ?: return
        when (event) {
            is LocalPlaybackController.TalkEvent.Started -> {
                if (slowed.bank) bank.played(event.clip.info.resource.id)
                when (slowed.moment) {
                    "opener" -> set?.let { s ->
                        if (!s.committed) { set = s.copy(committed = true); lastGuest = s.guest; pending = null; pendingAfter = null }
                    }
                    "closer" -> finish()
                }
            }
            is LocalPlaybackController.TalkEvent.Finished -> {
                // An opener that never started: the set never starts.
                if (slowed.moment == "opener" && event.outcome == "discarded" && set?.committed == false) abandon()
            }
        }
        publishPlan(); publishUi()
    }

    // -- manual runs (14D) --------------------------------------------------------------------

    fun startManual(choice: SlowedPreset?) {
        val settings = radio.slowedSettings.value
        val station = radio.state.value
        val playback = engine.state.value
        val songId = playback.currentId ?: return
        if (playback.talkPlaying || !SlowedPolicy.allowed(settings, radio.talkSettings.value.enabled, playback.radioActive, station.feel, radio.slowedFeels.value)) return
        val preset = choice ?: SlowedPreset.pick(settings.presets, random) ?: return
        val guests = SlowedBankPolicy.guestsWithOpeners(bank.state.value.lines, preset, manual = true)
        val guest = SlowedPolicy.pickGuest(guests, lastGuest, random) ?: run { engine.notify("No Slowed lines yet. Connect to the desktop once to fetch them."); return }
        scope.launch {
            val pool = poolFor(station.feel)
            val length = SlowedPolicy.fit(SlowedPolicy.manualLength(settings, random), pool?.let { unplayed(it) })
                ?: run { engine.notify("Not enough songs in feels that allow Slowed."); return@launch }
            if (engine.state.value.currentId != songId) return@launch
            // Replaces a running automatic set: the new preset applies and the length starts over, with no closer.
            fallback?.cancel(); pending = null; pendingAfter = null
            if (engine.state.value.talk?.current == false) engine.removeTalk()
            set = SlowedSet(preset, guest, manual = true, total = length, afterId = songId)
            applyPool(pool)
            awaitMix()
            val request = SlowedRequest(SlowedMoment.OPENER, preset, guest, length, length, manual = true)
            if (!insertBankLine(request, songId, margin = 0)) { abandon(); engine.notify("Couldn't start Slowed right now."); return@launch }
            // The interrupted song fades out (not a skip) and the opener plays at once.
            engine.fadeIntoNext(SlowedPolicy.MANUAL_FADE_MS, "slowed_run")
            publishUi()
        }
    }

    /** "Back to normal": the current song fades out, the closer plays, the next song is normal. */
    fun backToNormal() {
        val s = set ?: return
        if (!s.committed) { engine.removeTalk(); abandon(); return }
        val playback = engine.state.value
        val songId = playback.currentId ?: return
        fallback?.cancel(); pending = null; pendingAfter = null
        set = s.copy(closing = true, total = s.played.size)
        publishPlan()
        if (playback.talkPlaying) { finish(); return }
        if (playback.talk?.current == false) engine.removeTalk()
        val closer = SlowedRequest(SlowedMoment.CLOSER, s.preset, s.guest, s.total, 0, s.manual)
        insertBankLine(closer, songId, margin = 0)
        engine.fadeIntoNext(SlowedPolicy.MANUAL_FADE_MS, "slowed_run")
        publishUi()
    }

    /** New mix from the listener ends a set (closer rules); a station shift from talk doesn't. */
    fun userNewMix() { if (set?.committed == true) endWithCloser() else if (set != null) abandon() }

    // -- ending -----------------------------------------------------------------------------

    private fun endWithCloser() {
        val s = set ?: return
        if (!s.committed) { abandon(); return }
        val current = engine.state.value.currentId ?: return finish()
        set = s.copy(closing = true, total = s.played.size)
        if (current in s.played) request(SlowedMoment.CLOSER, engine.state.value.queue.firstOrNull { it.id == current } ?: return finish())
        else finish()
        publishPlan(); publishUi()
    }

    private fun finish() {
        fallback?.cancel(); fallback = null
        set = null; pending = null; pendingAfter = null
        songsSinceLast = 0
        applyPool(null, mix = false)
        publishPlan(); publishUi()
    }

    private fun abandon() {
        val queued = engine.state.value.talk
        if (queued != null && !queued.current && queued.info.slowed?.moment == "opener") engine.removeTalk()
        fallback?.cancel(); fallback = null
        set = null; pending = null; pendingAfter = null
        applyPool(null, mix = false)
        publishPlan(); publishUi()
    }

    /** Leaving Radio stops it immediately and silently. */
    fun stopSilently() {
        // A queued Slowed line would announce a slowdown that no longer happens.
        val queued = engine.state.value.talk
        if (queued != null && !queued.current && queued.info.slowed != null) engine.removeTalk()
        if (set == null && pending == null && effected.isEmpty()) return
        fallback?.cancel(); fallback = null
        set = null; pending = null; pendingAfter = null
        radio.slowedPool = null
        effected = emptySet()
        engine.setSlowed(SlowedPlan.OFF)
        publishUi()
    }

    // -- pool, plan and UI -----------------------------------------------------------------------

    /** Plain Radio picks from every feel that allows Slowed; a feel station keeps its own feel (null pool). */
    private suspend fun poolFor(feel: String?): Set<String>? {
        if (feel != null) return null
        val feels = radio.slowedFeels.value
        return withContext(Dispatchers.IO) { feels.flatMap { runCatching { radio.feelMembers(it) }.getOrDefault(emptySet()) }.toSet() }
    }

    private fun unplayed(pool: Set<String>): Int {
        val state = engine.state.value
        val heard = state.playOrder.take(state.playOrder.indexOf(state.currentId) + 1).toSet()
        return pool.count { it !in heard }
    }

    private fun applyPool(pool: Set<String>?, mix: Boolean = true) {
        val changed = radio.slowedPool != pool
        radio.slowedPool = pool
        if (!mix || !changed || pool == null) return
        // The set's songs come from its pool: replace Radio's upcoming picks now, before the opener names the next song.
        mixing.value = true
        val before = engine.state.value.queueRevision
        engine.stationMix()
        scope.launch {
            withTimeoutOrNull(4_000) { engine.state.first { it.queueRevision != before } }
            delay(150)
            mixing.value = false
        }
    }

    private fun publishPlan() {
        val s = set
        val playback = engine.state.value
        val ids = SlowedPolicy.effected(s, playback.currentId, playback.playOrder, playback.talk?.takeIf { it.current }?.nextId)
        effected = ids
        engine.setSlowed(if (s != null && ids.isNotEmpty()) SlowedPlan(s.preset, ids) else SlowedPlan.OFF)
    }

    private fun publishUi() {
        val settings = radio.slowedSettings.value
        val playback = engine.state.value
        val offered = SlowedPolicy.allowed(settings, radio.talkSettings.value.enabled, playback.radioActive, radio.state.value.feel, radio.slowedFeels.value)
        val s = set
        val hasLines = settings.presets.any { SlowedBankPolicy.guestsWithOpeners(bank.state.value.lines, it, manual = true).isNotEmpty() }
        ui.value = SlowedUi(offered = offered, canStart = offered && hasLines && !playback.talkPlaying,
            reason = if (offered && !hasLines) "No Slowed lines yet" else null,
            running = s?.committed == true, manual = s?.manual == true, preset = s?.preset, songsLeft = s?.songsLeft ?: 0)
        radio.publishSlowedUi(ui.value)
    }
}
