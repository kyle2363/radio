package dev.radio.mobile.playback

import android.content.Context
import android.net.Uri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.common.Timeline
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.extractor.mp3.Mp3Extractor
import androidx.media3.exoplayer.source.ShuffleOrder.DefaultShuffleOrder
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.StationOrigin
import dev.radio.mobile.recommendation.StationRecord
import dev.radio.mobile.recommendation.TalkClip
import dev.radio.mobile.recommendation.TalkPolicy
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID
import kotlin.random.Random

/** Main-thread engine owned by RadioPlaybackService; also usable by isolated playback tests. */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class LocalPlaybackController(
    context: Context,
    private val onCheckpoint: (PlaybackState) -> Unit = {},
    private val onQueueCleared: () -> Unit = {},
    private val onAttemptFinished: (String, String, String, Long, Long?, String) -> Unit = { _, _, _, _, _, _ -> },
    private val onExplicitReplay: (LocalSong, String) -> Unit = { _, _ -> },
    private val onAudibleStart: (LocalSong, String, Long) -> Unit = { _, _, _ -> }
) : PlaybackController {
    /** Connected talk lifecycle (checkpoint 13B). Talk never reaches the journal callbacks above. */
    sealed interface TalkEvent {
        val clip: TalkClip
        data class Started(override val clip: TalkClip) : TalkEvent
        /** [outcome] is played, skipped or discarded; [heardMs] is how far it got. */
        data class Finished(override val clip: TalkClip, val outcome: String, val heardMs: Long) : TalkEvent
    }
    internal var onTalk: (TalkEvent) -> Unit = {}
    /** Only files this returns true for may become speech items (the service allows its private talk cache). */
    internal var talkFileAllowed: (java.io.File) -> Boolean = { false }
    private var talk: TalkClip? = null
    private var talkAfter: String? = null
    private var talkNext: String? = null
    private var talkStarted = false
    private var talkCompleted = false
    private var talkHeardMs = 0L
    private var talkWasCurrent = false
    private var talkGain = dev.radio.mobile.recommendation.TalkVolume.NORMAL.linear
    private var songProfile = NormalizationProfile()
    private fun isTalkId(id: String?) = TalkPolicy.isTalk(id)
    private fun talkCurrent() = talk != null && player.currentMediaItem?.mediaId == talk?.mediaId
    /**
     * The song Radio's upcoming picks follow: the current song, or while a clip plays, the song it introduces (the DJ
     * already announced it, so a show shift keeps it and replaces only what comes after).
     */
    internal fun radioAnchorId(): String? = if (talkCurrent()) talkNext ?: state.value.currentId else state.value.currentId
    internal val normalizationProcessor = NormalizationAudioProcessor(meter = true)
    private val renderers = object : DefaultRenderersFactory(context.applicationContext) {
        override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink =
            DefaultAudioSink.Builder(context).setEnableFloatOutput(false).setAudioProcessors(arrayOf(normalizationProcessor)).build()
    }
    private val appContext = context.applicationContext
    private val musicAttributes = AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build()
    private val player = ExoPlayer.Builder(context.applicationContext, renderers).build().apply {
        setAudioAttributes(musicAttributes, true)
        setHandleAudioBecomingNoisy(true)
        setWakeMode(C.WAKE_MODE_LOCAL)
        repeatMode = Player.REPEAT_MODE_OFF
    }
    // Equaliser and bass boost act on the player's audio session, which the crossfade tail shares.
    private val effects = AudioEffects(player.audioSessionId)

    // Crossfade: near the end of a song a second player (the "tail") takes over the last seconds of the outgoing song
    // and fades it out, while the real player moves on to the next song and fades it in. The real player stays the only
    // one the session, journal and queue know about; the tail is just the end of a song.
    private val tailNormalization = NormalizationAudioProcessor()
    private val tailRenderers = object : DefaultRenderersFactory(context.applicationContext) {
        override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink =
            DefaultAudioSink.Builder(context).setEnableFloatOutput(false).setAudioProcessors(arrayOf(tailNormalization)).build()
    }
    private var tail: ExoPlayer? = null
    private var crossfadeMs = 0L
    private var fadeJob: Job? = null
    /** The real player's gain from the crossfade and from a fading sleep timer; its volume is their product. */
    private var fadeGain = 1f
    private var sleepGain = 1f
    private fun applyVolume() { player.volume = fadeGain * sleepGain }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val mutable = MutableStateFlow(PlaybackState())
    override val state: StateFlow<PlaybackState> = mutable.asStateFlow()
    private var queue = emptyList<LocalSong>()
    private var queueById = emptyMap<String, LocalSong>()
    private var cachedTimeline: Timeline? = null
    private var cachedShuffle = false
    private var cachedOrder = emptyList<String>()
    private var source = PlaySource("", PlaySource.QUEUE_EDIT)
    private var radioActive = false
    private var radioStarting = false
    /** The service sets this while it builds the first Radio batch, so the UI can show that Radio is tuning in. */
    internal fun setRadioStarting(value: Boolean) { if (radioStarting != value) { radioStarting = value; publish() } }
    private var queueRevision = 0L
    private var notice: PlaybackNotice? = null
    /** A one-off message for the UI, such as a Radio start that could not be completed. */
    internal fun notify(message: String) { notice = PlaybackNotice((notice?.id ?: 0) + 1, message); publish() }
    internal var onRadioStart: (String?) -> Unit = {}
    internal var onStationStart: (dev.radio.mobile.recommendation.SeedSpec) -> Unit = {}
    internal var onNewMix: () -> Unit = {}
    // Station context (checkpoint 4.2): why each queued song is there and where the station started.
    private var origins = emptyMap<String, StationOrigin>()
    private var seedLocalId: String? = null
    private var seedTitle: String? = null
    private fun clearStation() { origins = emptyMap(); seedLocalId = null; seedTitle = null }
    override fun startRadio(seedLocalId: String?) = onRadioStart(seedLocalId)
    override fun startStation(station: dev.radio.mobile.recommendation.SeedSpec) = onStationStart(station)
    override fun newMix() { if (radioActive) onNewMix() }
    override fun stopRadio() { if(talk!=null && !talkCurrent())dropTalk(skipToNext=false);radioActive=false;radioStarting=false;clearStation();queueRevision++;publish(checkpoint=true) }
    override fun endRadio(stopNow: Boolean) {
        if (stopNow) { queueRevision++; player.pause(); if (queue.isNotEmpty()) source = PlaySource.CustomQueue }
        stopRadio()
    }
    /** The running station, for the bookmark beside the playback snapshot; null outside Radio. */
    internal fun station(): StationRecord? {
        val value = state.value
        val current = value.songCurrentId
        if (!radioActive || current == null) return null
        return StationRecord(seedLocalId, seedTitle, origins, value.playOrder, current)
    }
    /** Called right after [restorePaused]: resumes the station only when it describes exactly this queue. Never plays. */
    internal fun restoreStation(record: StationRecord?) {
        val value = state.value
        if (record == null || !record.matches(value.playOrder, value.currentId)) return
        radioActive = true; origins = record.pruned(value.playOrder).origins
        seedLocalId = record.seedLocalId; seedTitle = record.seedTitle; source = PlaySource.Radio
        publish()
    }
    private var error: String? = null
    private var accessLost = false
    private var attemptSong: String? = null
    private var attemptId = ""
    private var started = false
    /** Files that failed to play in a row; an audible start resets it (see [PlaybackErrorPolicy]). */
    private var consecutiveErrors = 0
    private var listeningAttempt: ListeningAttempt? = null
    private var attemptDurationMs: Long? = null
    private var attemptTrigger: String? = null
    private var attemptExternal: String? = null
    private var attemptSource = PlaySource("", PlaySource.QUEUE_EDIT)
    /** Where the attempt now starting came from; read inside [onAudibleStart] (checkpoint 8A, local only). */
    internal var startContext: PlayContext? = null
        private set
    private var ticker: Job? = null
    private var closed = false
    private var lastCheckpointAt = 0L
    private var sleepDeadline: SleepDeadline? = null
    private var sleepEndOfTrack = false
    /** Checkpoint 11: "Finish this playlist" — pause when the last song in play order ends. */
    private var sleepEndOfQueue = false
    private var sleepJob: Job? = null

    /** Set by the media session for the command it is about to dispatch: [PlayTrigger.WIDGET] or [PlayTrigger.MEDIA_BUTTON]. */
    internal var commandOrigin: String? = null
    private var externalTrigger: String? = null
    private var latestCommand: String? = null
    private inline fun external(action: () -> Unit) {
        externalTrigger = commandOrigin ?: PlayTrigger.MEDIA_BUTTON
        try { action() } finally { externalTrigger = null; commandOrigin = null }
    }
    // Every system transport command uses the same guards, shuffle and journal as the app.
    internal val sessionPlayer: Player = object : ForwardingPlayer(player) {
        override fun play() = external { resume() }
        override fun pause() = this@LocalPlaybackController.pause()
        override fun setPlayWhenReady(playWhenReady: Boolean) { if (playWhenReady) external { resume() } else pause() }
        override fun seekTo(positionMs: Long) = seek(positionMs)
        override fun seekTo(mediaItemIndex: Int, positionMs: Long) = external { seekToItem(mediaItemIndex, positionMs) }
        override fun seekToDefaultPosition() = seek(0)
        override fun seekToDefaultPosition(mediaItemIndex: Int) = external { seekToItem(mediaItemIndex, 0) }
        override fun seekToNext() = external { next() }
        override fun seekToNextMediaItem() = external { next() }
        override fun seekToPrevious() = external { previous() }
        override fun seekToPreviousMediaItem() = external { previous() }
        override fun setShuffleModeEnabled(shuffleModeEnabled: Boolean) = shuffle(shuffleModeEnabled)
        override fun setRepeatMode(repeatMode: Int) = setRepeat(repeatMode.toRepeatMode())
        override fun stop() { cancelCrossfade();queueRevision++;finishAttempt("attempt_stopped", "system_stop"); player.pause(); player.stop(); publish(checkpoint = true) }
    }

    init {
        player.addListener(object : Player.Listener {
            override fun onPositionDiscontinuity(oldPosition: Player.PositionInfo, newPosition: Player.PositionInfo, reason: Int) {
                if (reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
                    if (talk != null && oldPosition.mediaItem?.mediaId == talk?.mediaId) talkCompleted = true
                    finishAttempt("song_completed", "natural_end")
                    newPosition.mediaItem?.mediaId?.let { beginAttempt(it, PlayTrigger.AUTO_ADVANCE) }
                }
            }
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (!playWhenReady && reason == Player.PLAY_WHEN_READY_CHANGE_REASON_END_OF_MEDIA_ITEM) {
                    // Stop at end of song with a trailing clip: the clip ended, so it was heard. Drop it and rest on the next song.
                    if (talkCurrent()) { talkCompleted = true; talkHeardMs = maxOf(talkHeardMs, player.duration.coerceAtLeast(0)); scope.launch { dropTalk(skipToNext = true) } }
                    finishAttempt("song_completed", "natural_end"); if (sleepEndOfTrack || sleepEndOfQueue) cancelSleepTimer()
                }
            }
            override fun onEvents(player: Player, events: Player.Events) {
                if (sleepEndOfQueue) this@LocalPlaybackController.player.pauseAtEndOfMediaItems = atQueueEnd()
                // Stop at end of song: a song followed by a clip doesn't pause; the clip pauses at its own end.
                if (sleepEndOfTrack) this@LocalPlaybackController.player.pauseAtEndOfMediaItems = !(talk != null && !talkCurrent() && nextWindowIsTalk())
                trackTalk()
                val id = player.currentMediaItem?.mediaId
                if (id != null && id != attemptSong) beginAttempt(id)
                if (id != null && player.isPlaying && listeningAttempt?.isFinished == true) beginAttempt(id)
                listeningAttempt?.observe(android.os.SystemClock.elapsedRealtime(), player.isPlaying, player.playbackParameters.speed)
                // Pausing, losing audio focus or unplugging headphones ends a crossfade at once.
                if (!player.playWhenReady && fadeJob != null) cancelCrossfade()
                if (player.playbackState == Player.STATE_ENDED) finishAttempt("song_completed", "natural_end")
                // Preparing/prefetching, buffering, and pause/resume cannot manufacture starts.
                if (player.isPlaying && player.playbackState == Player.STATE_READY && !started) {
                    queueById[id]?.let { song ->
                        started = true
                        consecutiveErrors = 0
                        attemptDurationMs = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: song.durationMs
                        startContext = PlayContext(attemptSource.kind, attemptSource.id,
                            if (attemptSource.kind == PlaySource.RADIO) origins[song.id]?.name else null,
                            PlayTrigger.resolve(attemptTrigger, attemptExternal, latestCommand))
                        onAudibleStart(song, attemptId, player.currentPosition.coerceAtLeast(0))
                    }
                }
                publish(checkpoint = events.containsAny(Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                    Player.EVENT_PLAY_WHEN_READY_CHANGED, Player.EVENT_PLAYBACK_STATE_CHANGED, Player.EVENT_SHUFFLE_MODE_ENABLED_CHANGED,
                    Player.EVENT_REPEAT_MODE_CHANGED, Player.EVENT_TIMELINE_CHANGED, Player.EVENT_PLAYER_ERROR))
                if (player.isPlaying && ticker?.isActive != true) ticker = scope.launch {
                    while (isActive && player.isPlaying) { trackTalk(); publish(); maybeCrossfade(); delay(250) }
                } else if (!player.isPlaying) { ticker?.cancel(); ticker = null }
            }
            override fun onPlayerError(exception: PlaybackException) {
                cancelCrossfade()
                finishAttempt("attempt_stopped", "decoder_error")
                consecutiveErrors++
                val failed = queueById[player.currentMediaItem?.mediaId]
                val next = if (accessLost) C.INDEX_UNSET else adjacent(true)
                if (PlaybackErrorPolicy.shouldSkip(consecutiveErrors, player.playWhenReady, next != C.INDEX_UNSET)) {
                    notice = PlaybackNotice((notice?.id ?: 0) + 1, "Skipped “${failed?.title ?: "a song"}”. The file couldn't be played.")
                    move(next, PlayTrigger.AUTO_ADVANCE)
                    return
                }
                error = "Couldn't play this file. It may be missing, unreadable or unsupported. Try Next, or check the Music folder and rescan."
                player.pause()
                publish(checkpoint = true)
            }
        })
    }

    private fun finishAttempt(kind: String, reason: String) {
        val attempt = listeningAttempt ?: return
        val listened = attempt.finish(android.os.SystemClock.elapsedRealtime()) ?: return
        val duration = attemptDurationMs ?: queueById[attempt.songId]?.durationMs
        val late = kind == "song_skipped" && isLateNavigation(duration, player.currentPosition)
        onAttemptFinished(attempt.songId, attempt.id, if (late) "attempt_stopped" else kind, listened, duration, if (late) "late_navigation" else reason)
    }
    private fun beginAttempt(id: String, trigger: String? = null) {
        val active = player.playWhenReady && started
        finishAttempt(if (active) "song_skipped" else "attempt_stopped", if (active) "explicit_navigation" else "paused_navigation")
        attemptSong = id; attemptId = UUID.randomUUID().toString(); started = false
        attemptTrigger = trigger; attemptExternal = externalTrigger; attemptSource = source
        attemptDurationMs = null
        // Talk is never a song start, completion or skip.
        listeningAttempt = if (isTalkId(id)) null else ListeningAttempt(attemptId, id)
    }
    override fun replay() {
        cancelCrossfade()
        queueRevision++
        val song = queueById[player.currentMediaItem?.mediaId] ?: return
        if (!canReadCurrent()) return
        finishAttempt("attempt_stopped", "explicit_replay")
        beginAttempt(song.id, PlayTrigger.TAP)
        onExplicitReplay(song, attemptId)
        player.seekTo(0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish(checkpoint = true)
    }
    private fun canReadCurrent() = !accessLost && (talkCurrent() || queueById[player.currentMediaItem?.mediaId]?.available == true)
    private fun validUri(song: LocalSong) = Uri.parse(song.uri).scheme == "content"
    private fun publish(checkpoint: Boolean = false) {
        if (closed) return
        val id = player.currentMediaItem?.mediaId
        val current = queueById[id]
        val timeline = player.currentTimeline
        // Position ticks don't rebuild a 2,000-track queue or its shuffle order.
        if (timeline !== cachedTimeline || cachedShuffle != player.shuffleModeEnabled) {
            val order = mutableListOf<String>()
            var index = timeline.getFirstWindowIndex(player.shuffleModeEnabled)
            var walked = 0
            // Talk clips are timeline items but not queue songs, so they stay out of the play order.
            while (index != C.INDEX_UNSET && walked < player.mediaItemCount && order.size < queue.size) {
                val mediaId = player.getMediaItemAt(index).mediaId
                if (!isTalkId(mediaId)) order += mediaId
                walked++
                index = timeline.getNextWindowIndex(index, Player.REPEAT_MODE_OFF, player.shuffleModeEnabled)
            }
            cachedTimeline = timeline; cachedShuffle = player.shuffleModeEnabled; cachedOrder = order
        }
        if (origins.isNotEmpty() && origins.keys.any { it !in queueById }) origins = origins.filterKeys { it in queueById }
        val next = if (id != null) adjacent(true) else C.INDEX_UNSET
        val playingTalk = talkCurrent()
        val duration = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: current?.durationMs ?: talk?.takeIf { playingTalk }?.info?.durationMs?.takeIf { it > 0 }
        val talkNow = talk?.let { TalkNow(it.info, it.mediaId, playingTalk, talkAfter, talkNext) }
        mutable.value = PlaybackState(talk = talkNow,
            queue = queue, playOrder = cachedOrder, source = source.label, currentId = id, radioActive = radioActive, queueRevision = queueRevision,
            origins = if (radioActive) origins else emptyMap(), stationSeedTitle = if (radioActive) seedTitle else null, radioStarting = radioStarting && !radioActive, notice = notice,
            positionMs = player.currentPosition.coerceAtLeast(0),
            durationMs = duration,
            isPlaying = player.isPlaying, playWhenReady = player.playWhenReady && player.playbackState != Player.STATE_ENDED,
            buffering = player.playbackState == Player.STATE_BUFFERING, ended = player.playbackState == Player.STATE_ENDED,
            seekable = (player.isCurrentMediaItemSeekable || player.playbackState == Player.STATE_IDLE) && duration != null && duration > 0 && error == null && canReadCurrent(),
            canNext = !accessLost && next != C.INDEX_UNSET && queue.any { it.available && it.id != id },
            canPrevious = canReadCurrent(), shuffled = player.shuffleModeEnabled, repeat = player.repeatMode.toRepeatMode(), error = error,
            sleepTimer = SleepTimerState(sleepDeadline?.remaining(android.os.SystemClock.elapsedRealtime()) ?: 0L, sleepEndOfTrack, sleepDeadline?.fade == true, sleepEndOfQueue)
        )
        val now = android.os.SystemClock.elapsedRealtime()
        if (checkpoint || now - lastCheckpointAt >= 5000) {
            lastCheckpointAt = now
            onCheckpoint(mutable.value)
        }
    }

    internal fun restorePaused(saved: PlaybackSnapshot, catalog: List<LocalSong>, accessLost: Boolean) {
        val restored = resolvePlaybackSnapshot(saved, catalog, accessLost)
        if (restored == null) { restorationFailed("The saved queue is no longer in this library. Choose a song to start a new queue."); return }
        this.accessLost = accessLost
        queue = restored.songs; queueById = queue.associateBy { it.id }; source = PlaySource(restored.source, PlaySource.RESTORE)
        error = restored.error
        beginAttempt(restored.currentId)
        player.pause()
        player.setMediaItems(queue.map { MediaItem.Builder().setMediaId(it.id).setUri(it.uri).setMediaMetadata(metadata(it)).build() },
            queue.indexOfFirst { it.id == restored.currentId }, restored.positionMs)
        val indices = queue.mapIndexed { index, song -> song.id to index }.toMap()
        player.setShuffleOrder(DefaultShuffleOrder(restored.playOrder.map { indices.getValue(it) }.toIntArray(), Random.nextLong()))
        player.shuffleModeEnabled = restored.shuffled
        // No prepare or play: restoration must not read/decode audio, acquire focus or
        // manufacture a listening start. Explicit Play will prepare the restored item.
        publish()
    }
    internal fun restorationFailed(message: String) { error = message; publish() }
    internal fun updateNormalization(profile: NormalizationProfile) {
        songProfile = profile
        val combined = profile.copy(talkGain = talkGain)
        normalizationProcessor.profile = combined; tailNormalization.profile = combined; publish()
    }
    /** Talk volume (Quieter / Normal / Louder) as a linear gain applied to speech items only. */
    internal fun setTalkGain(gain: Float) { if (gain != talkGain) { talkGain = gain; updateNormalization(songProfile) } }

    /** Speed, skip silence, crossfade length and the equaliser. Takes effect immediately, mid-song. */
    internal fun applyAudio(settings: AudioSettings) {
        val parameters = PlaybackParameters(settings.speed)
        player.playbackParameters = parameters; tail?.playbackParameters = parameters
        player.skipSilenceEnabled = settings.skipSilence
        crossfadeMs = settings.crossfadeSeconds * 1000L
        if (crossfadeMs == 0L) cancelCrossfade()
        effects.apply(settings)
    }
    /** What the phone's equaliser offers, or null when it has none. */
    internal fun describeEffects(): EqualizerInfo? = effects.describe()

    private fun tailPlayer(): ExoPlayer = tail ?: ExoPlayer.Builder(appContext, tailRenderers)
        // Index seeking makes the tail start at exactly the outgoing song's position, even for variable-bitrate MP3s.
        .setMediaSourceFactory(DefaultMediaSourceFactory(appContext, DefaultExtractorsFactory().setMp3ExtractorFlags(Mp3Extractor.FLAG_ENABLE_INDEX_SEEKING)))
        .build().apply {
            // No audio focus of its own: the real player holds it, and pausing that ends the tail.
            setAudioAttributes(musicAttributes, false)
            setAudioSessionId(player.audioSessionId)
            playbackParameters = player.playbackParameters
        }.also { tail = it }

    private fun maybeCrossfade() {
        if (crossfadeMs <= 0 || fadeJob != null || accessLost || error != null || !player.isPlaying) return
        if (talkCurrent() || nextWindowIsTalk()) return
        val duration = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: return
        val next = adjacent(true)
        val hasNext = next != C.INDEX_UNSET && next != player.currentMediaItemIndex
        // The window includes the moment it takes to get the tail ready, so the audible overlap is the chosen length.
        if (!Crossfade.shouldStart(crossfadeMs + CROSSFADE_LEAD_MS, player.currentPosition, duration, hasNext,
                player.repeatMode == Player.REPEAT_MODE_ONE, player.pauseAtEndOfMediaItems, fading = false)) return
        startCrossfade(next, duration)
    }

    private fun startCrossfade(nextIndex: Int, duration: Long) {
        val outgoing = player.currentMediaItem ?: return
        val handover = player.currentPosition + CROSSFADE_LEAD_MS
        val tail = tailPlayer()
        tail.volume = 0f
        tail.setMediaItem(outgoing, handover)
        tail.prepare()
        // Lazy, so fadeJob is set before the job can run (and finish) on the immediate dispatcher.
        val job = scope.launch(start = CoroutineStart.LAZY) {
            val mine = coroutineContext[Job]
            try {
                // Wait for the tail to be ready, then for the real player to reach the handover point.
                while (tail.playbackState != Player.STATE_READY) {
                    if (tail.playbackState == Player.STATE_IDLE || tail.playbackState == Player.STATE_ENDED || !stillOn(outgoing)) return@launch
                    delay(10)
                }
                // Starting audio takes a moment; ask for it slightly early so the two copies line up at the handover.
                while (player.currentPosition < handover - TAIL_START_LATENCY_MS) { if (!stillOn(outgoing)) return@launch; delay(5) }
                // A coarse position clock can step past the handover: line the tail up with the song, or let it end.
                val start = Crossfade.tailStart(player.currentPosition, handover, duration) ?: return@launch
                if (start != handover) tail.seekTo(start)
                tail.volume = sleepGain
                tail.play()
                while (player.currentPosition < handover) { if (!stillOn(outgoing)) return@launch; delay(2) }
                if (nextIndex >= player.mediaItemCount || adjacent(true) != nextIndex) return@launch
                // Hand over: the outgoing song finished as far as the journal is concerned, and the next one begins.
                finishAttempt("song_completed", "natural_end")
                fadeGain = 0f; applyVolume()
                beginAttempt(player.getMediaItemAt(nextIndex).mediaId, PlayTrigger.CROSSFADE)
                player.seekTo(nextIndex, 0)
                publish(checkpoint = true)
                val overlapMs = ((duration - start) / player.playbackParameters.speed).toLong().coerceAtLeast(1)
                val begun = android.os.SystemClock.elapsedRealtime()
                while (true) {
                    val progress = (android.os.SystemClock.elapsedRealtime() - begun).toFloat() / overlapMs
                    fadeGain = Crossfade.incoming(progress); applyVolume()
                    tail.volume = Crossfade.outgoing(progress) * sleepGain
                    if (progress >= 1f || tail.playbackState == Player.STATE_ENDED) break
                    delay(40)
                }
            } finally {
                // A cancelled fade was already cleaned up by cancelCrossfade, and a newer one may own the tail now.
                if (fadeJob === mine) {
                    fadeJob = null
                    tail.stop(); tail.clearMediaItems()
                    fadeGain = 1f; applyVolume()
                }
            }
        }
        fadeJob = job
        job.start()
    }
    private fun stillOn(item: MediaItem) = player.playWhenReady && player.currentMediaItem?.mediaId == item.mediaId

    /** Any command that changes what plays ends a crossfade first: the tail stops and the real player is at full volume. */
    private fun cancelCrossfade() {
        val job = fadeJob ?: return
        fadeJob = null
        job.cancel()
        tail?.let { it.stop(); it.clearMediaItems() }
        fadeGain = 1f; applyVolume()
    }

    override fun playFrom(items: List<LocalSong>, selectedId: String, source: PlaySource) {
        cancelCrossfade()
        radioActive=false;radioStarting=false;clearStation();queueRevision++
        val snapshot = playbackQueue(items, selectedId).filter(::validUri)
        val index = snapshot.indexOfFirst { it.id == selectedId }
        if (accessLost || index < 0) { player.pause(); error = "This file is unavailable. Check folder access and rescan."; publish(); return }
        finishAttempt(if (player.playWhenReady && started) "song_skipped" else "attempt_stopped", "queue_replacement")
        queue = snapshot; queueById = snapshot.associateBy { it.id }; this.source = source; error = null; beginAttempt(selectedId, PlayTrigger.TAP)
        player.setMediaItems(snapshot.map { song ->
            MediaItem.Builder().setMediaId(song.id).setUri(song.uri)
                .setMediaMetadata(metadata(song)).build()
        }, index, 0)
        if (player.shuffleModeEnabled) reshuffle()
        player.prepare()
        player.play()
        publish(checkpoint = true)
    }
    override fun toggle() {
        if (player.playWhenReady && player.playbackState != Player.STATE_ENDED) { pause(); return }
        resume()
    }
    override fun play() { if (closed || (player.playWhenReady && player.playbackState != Player.STATE_ENDED)) return; resume() }
    /** Checkpoint 11: what Play does with nothing queued (the service resumes the bookmark or starts Radio). */
    internal var onEmptyPlay: () -> Unit = {}
    private fun resume() {
        if (player.mediaItemCount == 0 && queue.isEmpty()) { onEmptyPlay(); return }
        queueRevision++
        latestCommand = externalTrigger ?: PlayTrigger.TAP
        if (!canReadCurrent()) { error = "This file is unavailable. Check folder access and rescan."; publish(); return }
        if (listeningAttempt?.isFinished == true && player.playbackState != Player.STATE_ENDED) beginAttempt(player.currentMediaItem!!.mediaId)
        if (player.playbackState == Player.STATE_ENDED) { beginAttempt(player.currentMediaItem!!.mediaId); player.seekTo(0) }
        error = null
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish(checkpoint = true)
    }
    private fun talkItem(clip: TalkClip) = MediaItem.Builder().setMediaId(clip.mediaId).setUri(Uri.fromFile(clip.file))
        .setMediaMetadata(MediaMetadata.Builder().setTitle(clip.info.title).setArtist(clip.info.artist).setAlbumTitle("Talk").build()).build()
    private fun metadata(song: LocalSong) = MediaMetadata.Builder().setTitle(song.title).setArtist(song.artist.ifBlank { null })
        .setAlbumTitle(song.folderPath.ifEmpty { source.label }).build()
    override fun pause() { if (!closed) { queueRevision++;player.pause(); publish(checkpoint = true) } }
    override fun seek(positionMs: Long) {
        if (!state.value.seekable) return
        cancelCrossfade()
        queueRevision++
        player.seekTo(positionMs.coerceIn(0, state.value.durationMs ?: Long.MAX_VALUE)); publish(checkpoint = true)
    }
    private fun adjacent(forward: Boolean): Int {
        val timeline = player.currentTimeline
        // Only repeat-all wraps the walk at the end of the queue. Repeat-one must keep Next
        // advancing to the following song instead of returning the current item again.
        val repeatMode = if (player.repeatMode == Player.REPEAT_MODE_ALL) Player.REPEAT_MODE_ALL else Player.REPEAT_MODE_OFF
        var index = player.currentMediaItemIndex
        repeat(queue.size) {
            index = if (forward) timeline.getNextWindowIndex(index, repeatMode, player.shuffleModeEnabled)
                else timeline.getPreviousWindowIndex(index, repeatMode, player.shuffleModeEnabled)
            if (index == C.INDEX_UNSET) return C.INDEX_UNSET
            if (queueById[player.getMediaItemAt(index).mediaId]?.available == true) return index
        }
        return C.INDEX_UNSET
    }
    private fun move(index: Int, trigger: String) {
        if (accessLost || index == C.INDEX_UNSET) return
        cancelCrossfade()
        queueRevision++
        val continuePlaying = player.playWhenReady || error != null
        error = null
        beginAttempt(player.getMediaItemAt(index).mediaId, trigger)
        player.seekTo(index, 0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.playWhenReady = continuePlaying
        publish(checkpoint = true)
    }
    override fun next() { if (queue.isNotEmpty()) move(adjacent(true), PlayTrigger.NEXT) }
    override fun next(reason: String) {
        if (accessLost || adjacent(true) == C.INDEX_UNSET) return
        if (started && player.playWhenReady) finishAttempt("song_skipped", reason.takeIf {
            it in setOf("too_intense", "too_slow", "too_samey", "wrong_mood", "just_skip")
        } ?: "just_skip")
        next()
    }
    override fun previous() {
        if (talkCurrent()) { move(adjacent(false), PlayTrigger.PREVIOUS); return }
        if (!canReadCurrent()) return
        val previous = adjacent(false)
        if (player.currentPosition > 3000 || previous == C.INDEX_UNSET) seek(0) else move(previous, PlayTrigger.PREVIOUS)
    }
    override fun jumpTo(songId: String) {
        val index = (0 until player.mediaItemCount).firstOrNull { player.getMediaItemAt(it).mediaId == songId } ?: return
        if (accessLost || queue.none { it.id == songId && it.available }) return
        cancelCrossfade()
        queueRevision++
        error = null; beginAttempt(songId, PlayTrigger.TAP); player.seekTo(index, 0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish(checkpoint = true)
    }
    private fun seekToItem(index: Int, positionMs: Long) {
        if (index !in 0 until player.mediaItemCount || accessLost) return
        val id = player.getMediaItemAt(index).mediaId
        if (queueById[id]?.available != true) return
        cancelCrossfade()
        queueRevision++
        if (id != player.currentMediaItem?.mediaId) beginAttempt(id, PlayTrigger.TAP)
        error = null
        player.seekTo(index, positionMs.coerceAtLeast(0))
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        publish(checkpoint = true)
    }
    override fun shuffle(enabled: Boolean) {
        queueRevision++
        if (enabled && !player.shuffleModeEnabled) reshuffle()
        player.shuffleModeEnabled = enabled
        publish(checkpoint = true)
    }
    override fun setRepeat(mode: RepeatMode) {
        queueRevision++
        player.repeatMode = when (mode) {
            RepeatMode.OFF -> Player.REPEAT_MODE_OFF
            RepeatMode.ALL -> Player.REPEAT_MODE_ALL
            RepeatMode.ONE -> Player.REPEAT_MODE_ONE
        }
        publish(checkpoint = true)
    }
    private fun reshuffle() {
        // Media3 retains its permutation when shuffle is disabled. Replace that order
        // on each activation without resetting the media items, song, or position.
        val previous = buildList {
            val timeline = player.currentTimeline
            var index = timeline.getFirstWindowIndex(true)
            while (index != C.INDEX_UNSET && size < player.mediaItemCount) {
                add(index)
                index = timeline.getNextWindowIndex(index, Player.REPEAT_MODE_OFF, true)
            }
        }
        val current = player.currentMediaItemIndex
        val order = (0 until player.mediaItemCount).filter { it != current }.shuffled().toMutableList()
        if (current in 0 until player.mediaItemCount) order.add(0, current)
        // Keep the current song first and make the remaining order fresh when possible.
        // With only one upcoming track, there is only one valid order.
        if (order.size > 2 && order == previous) {
            val second = order[1]; order[1] = order[2]; order[2] = second
        }
        player.setShuffleOrder(DefaultShuffleOrder(order.toIntArray(), Random.nextLong()))
    }

    private fun mediaItem(song: LocalSong) = MediaItem.Builder().setMediaId(song.id).setUri(song.uri).setMediaMetadata(metadata(song)).build()
    internal fun beginRadio(items: List<LocalSong>, origins: Map<String, StationOrigin> = emptyMap(), seedLocalId: String? = null, seedTitle: String? = null) {
        val first=items.firstOrNull() ?: return
        // Radio recommendations are already ranked. Clear any manual queue shuffle
        // before playFrom installs them so their recommendation order is preserved.
        player.shuffleModeEnabled = false
        playFrom(items.take(5),first.id,PlaySource.Radio)
        radioActive=true
        this.origins=origins.filterKeys{it in queueById};this.seedLocalId=seedLocalId;this.seedTitle=seedTitle
        publish(checkpoint=true)
    }
    /** Main-thread atomic append. Retire only played Radio prefix; never seek or prepare. */
    internal fun appendRadio(expected:PlaybackState,items:List<LocalSong>,newOrigins:Map<String,StationOrigin> = emptyMap()):Boolean {
        val actual=state.value
        if(!radioActive || accessLost || actual.queueRevision!=expected.queueRevision || actual.currentId!=expected.currentId || actual.playOrder!=expected.playOrder || actual.sleepTimer.active)return false
        val current=actual.playOrder.indexOf(actual.currentId)
        if(current<0)return false
        val upcoming=actual.playOrder.drop(current+1).toSet()
        val incoming=items.filter{it.available && validUri(it) && it.id!=actual.currentId && it.id !in upcoming}.distinctBy{it.id}.take((5-upcoming.size).coerceAtLeast(0))
        if(incoming.isEmpty())return false
        // Keep up to five played entries for Previous. Exclude any past identity being
        // appended again so Media3's local identity remains unique in the timeline.
        val retired=(actual.playOrder.take((current-5).coerceAtLeast(0))+incoming.map{it.id}).toSet()-upcoming-setOfNotNull(actual.currentId)
        for(index in player.mediaItemCount-1 downTo 0)if(player.getMediaItemAt(index).mediaId in retired)player.removeMediaItem(index)
        queue=queue.filterNot{it.id in retired};queueById=queue.associateBy{it.id}
        val additions=incoming.filter{it.id !in queueById}
        if(queue.size+additions.size>16)return false
        val before=actual.playOrder.filterNot{it in retired}
        queue=queue+additions;queueById=queue.associateBy{it.id}
        player.addMediaItems(additions.map(::mediaItem))
        if(player.shuffleModeEnabled)setVisibleOrder(before+additions.map{it.id})
        origins=origins+newOrigins.filterKeys{id->additions.any{it.id==id}}
        queueRevision++;publish(checkpoint=true)
        return additions.isNotEmpty()
    }
    /**
     * New mix: atomically replace the upcoming songs Radio added with [items]. Songs the listener added, the current
     * song and its position stay; nothing is prepared, sought or started. Rejects a stale [expected] like [appendRadio].
     */
    internal fun replaceRadioUpcoming(expected:PlaybackState,items:List<LocalSong>,newOrigins:Map<String,StationOrigin>):Boolean {
        val actual=state.value
        if(!radioActive || accessLost || actual.queueRevision!=expected.queueRevision || actual.currentId!=expected.currentId || actual.playOrder!=expected.playOrder)return false
        val anchor=radioAnchorId()
        val current=actual.playOrder.indexOf(anchor)
        if(current<0)return false
        val upcoming=actual.playOrder.drop(current+1)
        val kept=upcoming.filter{origins[it]==StationOrigin.User}
        val incoming=items.filter{it.available && validUri(it) && it.id!=anchor && it.id !in kept}.distinctBy{it.id}.take((5-kept.size).coerceAtLeast(0))
        if(incoming.isEmpty())return false
        // Drop Radio's upcoming songs, plus any earlier copy of an incoming song so Media3 identities stay unique.
        val removed=(upcoming.filterNot{it in kept}+incoming.map{it.id}).toSet()-setOfNotNull(anchor)
        for(index in player.mediaItemCount-1 downTo 0)if(player.getMediaItemAt(index).mediaId in removed)player.removeMediaItem(index)
        queue=queue.filterNot{it.id in removed}+incoming;queueById=queue.associateBy{it.id}
        player.addMediaItems(incoming.map(::mediaItem))
        if(player.shuffleModeEnabled)setVisibleOrder(actual.playOrder.filterNot{it in removed}+incoming.map{it.id})
        origins=origins.filterKeys{it !in removed}+newOrigins.filterKeys{id->incoming.any{it.id==id}}
        queueRevision++;publish(checkpoint=true)
        return true
    }
    private fun nativeIndex(id: String) = (0 until player.mediaItemCount).firstOrNull { player.getMediaItemAt(it).mediaId == id }
    private fun setVisibleOrder(order: List<String>) {
        if (player.shuffleModeEnabled) {
            val indices = (0 until player.mediaItemCount).associateBy { player.getMediaItemAt(it).mediaId }
            player.setShuffleOrder(DefaultShuffleOrder(order.mapNotNull(indices::get).toIntArray(), Random.nextLong()))
        }
    }
    override fun addToQueue(items: List<LocalSong>, playNext: Boolean) {
        if (accessLost) return
        if (playNext && talk != null && !talkCurrent()) dropTalk(skipToNext = false)
        val incoming = items.filter { it.available && validUri(it) }.distinctBy { it.id }
        if (incoming.isEmpty()) return
        queueRevision++
        val before = state.value.playOrder
        val wasEmpty = player.mediaItemCount == 0
        if (wasEmpty) player.pause()
        val present = (0 until player.mediaItemCount).mapTo(HashSet()) { player.getMediaItemAt(it).mediaId }
        val additions = incoming.filter { it.id !in present }
        val incomingById = incoming.associateBy { it.id }
        queue = queue.map { incomingById[it.id] ?: it } + additions.filter { it.id !in queueById }
        queueById = queue.associateBy { it.id }
        if (additions.isNotEmpty()) player.addMediaItems(additions.map(::mediaItem))
        source = if(radioActive) PlaySource.Radio else PlaySource.CustomQueue; error = null
        if (radioActive) origins = origins + incoming.map { it.id }.filter { it != player.currentMediaItem?.mediaId }.associateWith { StationOrigin.User }
        val combined = (before + additions.map { it.id }).distinct()
        if (playNext && !wasEmpty) {
            val current = player.currentMediaItem?.mediaId
            val selected = incoming.map { it.id }.filter { it != current }
            val order = combined.filterNot { it in selected }.toMutableList()
            order.addAll((order.indexOf(current) + 1).coerceAtLeast(0), selected)
            if (player.shuffleModeEnabled) setVisibleOrder(order)
            else selected.forEachIndexed { offset, id ->
                val from = nativeIndex(id) ?: return@forEachIndexed
                val currentIndex = player.currentMediaItemIndex
                val target = currentIndex + 1 + offset - if (from <= currentIndex) 1 else 0
                player.moveMediaItem(from, target.coerceIn(0, player.mediaItemCount - 1))
            }
        } else if (player.shuffleModeEnabled) setVisibleOrder(combined)
        if (!player.shuffleModeEnabled) queue = (0 until player.mediaItemCount).mapNotNull { queueById[player.getMediaItemAt(it).mediaId] }
        queueById = queue.associateBy { it.id }
        publish(checkpoint = true)
    }
    override fun moveInQueue(songId: String, target: Int) {
        if (talk != null && !talkCurrent()) dropTalk(skipToNext = false)
        val order = state.value.playOrder.toMutableList()
        val from = order.indexOf(songId)
        if (from < 0 || target !in order.indices || from == target) return
        queueRevision++
        order.add(target, order.removeAt(from))
        if (player.shuffleModeEnabled) setVisibleOrder(order)
        else {
            player.moveMediaItem(nativeIndex(songId) ?: return, target)
            queue = order.mapNotNull(queueById::get); queueById = queue.associateBy { it.id }
        }
        source = if(radioActive) PlaySource.Radio else PlaySource.CustomQueue
        publish(checkpoint = true)
    }
    override fun removeFromQueue(songId: String) {
        if (isTalkId(songId)) { removeTalk(); return }
        val index = nativeIndex(songId) ?: return
        queueRevision++
        val current = player.currentMediaItem?.mediaId == songId
        if (current) cancelCrossfade()
        if (current) finishAttempt(if (player.isPlaying) "song_skipped" else "attempt_stopped", "queue_removal")
        val oldOrder = state.value.playOrder
        val successor = oldOrder.drop(oldOrder.indexOf(songId) + 1).firstOrNull { queueById[it]?.available == true }
            ?: oldOrder.take(oldOrder.indexOf(songId)).lastOrNull { queueById[it]?.available == true }
        val continuePlaying = player.playWhenReady
        queue = queue.filterNot { it.id == songId }; queueById = queue.associateBy { it.id }
        player.removeMediaItem(index)
        if (current && successor != null) {
            beginAttempt(successor, PlayTrigger.NEXT)
            player.seekTo(nativeIndex(successor) ?: 0, 0)
            player.playWhenReady = continuePlaying
        }
        if (queue.isEmpty() || player.mediaItemCount == 0) {
            radioActive=false;clearStation()
            queue = emptyList(); queueById = emptyMap()
            player.pause(); player.clearMediaItems(); cancelSleepTimer(); source = PlaySource("", PlaySource.QUEUE_EDIT); error = null
            onQueueCleared()
        } else { source = if(radioActive) PlaySource.Radio else PlaySource.CustomQueue; error = null }
        publish(checkpoint = true)
    }
    override fun clearUpcoming() {
        radioActive=false;clearStation();queueRevision++
        val order = state.value.playOrder
        val current = order.indexOf(state.value.currentId)
        if (current < 0 || current == order.lastIndex) {publish(checkpoint=true);return}
        val keep = order.take(current + 1).toSet()
        queue = queue.filter { it.id in keep }; queueById = queue.associateBy { it.id }
        var index = player.mediaItemCount - 1
        while (index >= 0) {
            if (player.getMediaItemAt(index).mediaId !in keep) {
                val end = index + 1
                while (index >= 0 && player.getMediaItemAt(index).mediaId !in keep) index--
                player.removeMediaItems(index + 1, end)
            } else index--
        }
        source = PlaySource.CustomQueue
        publish(checkpoint = true)
    }
    override fun setSleepTimer(durationMs: Long, fade: Boolean) {
        queueRevision++
        cancelSleepTimer()
        sleepDeadline = SleepDeadline(android.os.SystemClock.elapsedRealtime() + durationMs.coerceIn(100L, 24 * 60 * 60 * 1000L), fade)
        sleepJob = scope.launch {
            while (isActive) {
                val deadline = sleepDeadline ?: break
                val now = android.os.SystemClock.elapsedRealtime()
                if (deadline.remaining(now) == 0L) {
                    queueRevision++
                    finishAttempt("attempt_stopped", "sleep_timer"); player.pause(); sleepDeadline = null; sleepGain = 1f; applyVolume()
                    publish(checkpoint = true); break
                }
                sleepGain = deadline.gain(now); applyVolume()
                publish()
                delay(250)
            }
        }
        publish()
    }
    override fun sleepAtTrackEnd() {
        queueRevision++
        cancelSleepTimer()
        if (player.currentMediaItem == null) return
        sleepEndOfTrack = true; player.pauseAtEndOfMediaItems = true
        publish()
    }
    override fun sleepAtQueueEnd() {
        queueRevision++
        cancelSleepTimer()
        if (player.currentMediaItem == null || radioActive) return
        sleepEndOfQueue = true; player.pauseAtEndOfMediaItems = atQueueEnd()
        publish()
    }
    /** The current song is the last one that will play: last in (shuffled) order, or held by Repeat one. */
    private fun atQueueEnd(): Boolean {
        val timeline = player.currentTimeline
        if (timeline.isEmpty) return true
        if (player.repeatMode == Player.REPEAT_MODE_ONE) return true
        return player.currentMediaItemIndex == timeline.getLastWindowIndex(player.shuffleModeEnabled)
    }
    override fun cancelSleepTimer() {
        queueRevision++
        sleepJob?.cancel(); sleepJob = null; sleepDeadline = null; sleepEndOfTrack = false; sleepEndOfQueue = false
        player.pauseAtEndOfMediaItems = false; sleepGain = 1f; applyVolume()
        publish()
    }
    override fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean) {
        this.accessLost = accessLost
        if (queue.isEmpty()) return
        val byId = songs.associateBy { it.id }
        val updated = queue.map { byId[it.id] ?: it.copy(available = false) }
        val uriChanged = queue.zip(updated).any { (before, after) -> before.uri != after.uri }
        val bookmark = state.value.snapshot()
        queue = updated
        queueById = updated.associateBy { it.id }
        if (!canReadCurrent()) {
            cancelCrossfade()
            finishAttempt("attempt_stopped", "access_lost")
            player.stop()
            error = "Folder access or the current file is unavailable. Choose the Music folder again or rescan."
        } else if (uriChanged) {
            finishAttempt("attempt_stopped", "source_uri_changed")
            bookmark?.let { restorePaused(it, queue, accessLost = false) }
        } else {
            // One pass with a lookup: a 2,000-song queue must not cost millions of timeline reads on the main thread.
            for (index in player.mediaItemCount - 1 downTo 0) {
                val mediaId = player.getMediaItemAt(index).mediaId
                if (!isTalkId(mediaId) && queueById[mediaId]?.available != true) player.removeMediaItem(index)
            }
        }
        publish(checkpoint = true)
    }
    private fun nextWindowIsTalk(): Boolean {
        val index = player.currentTimeline.takeIf { !it.isEmpty }?.getNextWindowIndex(player.currentMediaItemIndex, Player.REPEAT_MODE_OFF, player.shuffleModeEnabled) ?: return false
        return index != C.INDEX_UNSET && isTalkId(player.getMediaItemAt(index).mediaId)
    }

    /**
     * Inserts a verified clip right after [afterId] (the current Radio song) and before [nextId]. Refused unless the
     * current song is [afterId] with at least the insertion margin left, [nextId] plays next, shuffle and repeat-one are
     * off and no other clip is queued. Never seeks, prepares or starts anything.
     */
    internal fun insertTalk(clip: TalkClip, afterId: String, nextId: String, marginMs: Long = TalkPolicy.INSERTION_MARGIN_MS): Boolean {
        if (closed || talk != null || !radioActive || accessLost || error != null || fadeJob != null) return false
        if (!talkFileAllowed(clip.file) || !clip.file.isFile) return false
        if (player.shuffleModeEnabled || player.repeatMode == Player.REPEAT_MODE_ONE) return false
        if (player.currentMediaItem?.mediaId != afterId || queueById[afterId] == null) return false
        val next = adjacent(true)
        if (next == C.INDEX_UNSET || player.getMediaItemAt(next).mediaId != nextId || next != player.currentMediaItemIndex + 1) return false
        val duration = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: queueById[afterId]?.durationMs
        if (!TalkPolicy.canInsert(player.currentPosition, duration, marginMs)) return false
        player.addMediaItem(player.currentMediaItemIndex + 1, talkItem(clip))
        talk = clip; talkAfter = afterId; talkNext = nextId
        talkStarted = false; talkCompleted = false; talkHeardMs = 0; talkWasCurrent = false
        publish()
        return true
    }

    /** Removes the queued or playing clip (queue row removal, talk turned off, the next song changed). */
    internal fun removeTalk() { if (talk != null) dropTalk(skipToNext = talkCurrent()) }

    /** Watches the clip: its first audible moment, how far it got, and when it left the timeline. */
    private fun trackTalk() {
        val clip = talk ?: return
        val index = nativeIndex(clip.mediaId)
        if (index == null) { finishTalk(); return }  // A queue replacement or edit removed it.
        val current = talkCurrent()
        if (current) {
            talkWasCurrent = true
            talkHeardMs = maxOf(talkHeardMs, player.currentPosition.coerceAtLeast(0))
            if (!talkStarted && player.isPlaying && player.playbackState == Player.STATE_READY) {
                talkStarted = true
                onTalk(TalkEvent.Started(clip))
            }
            if (sleepEndOfTrack) { sleepGain = TalkPolicy.stopFadeGain(player.currentPosition, player.duration.takeIf { it != C.TIME_UNSET }); applyVolume() }
        } else {
            val id = player.currentMediaItem?.mediaId
            // Moved past it (played, skipped or skipped over), or the song before it is no longer playing.
            if (talkWasCurrent || id != talkAfter) dropTalk(skipToNext = false)
        }
    }

    private fun dropTalk(skipToNext: Boolean) {
        val clip = talk ?: return
        if (skipToNext && talkCurrent()) {
            val next = adjacent(true)
            val continuePlaying = player.playWhenReady
            if (next != C.INDEX_UNSET) {
                beginAttempt(player.getMediaItemAt(next).mediaId, PlayTrigger.NEXT)
                player.seekTo(next, 0)
                player.playWhenReady = continuePlaying
            }
        }
        nativeIndex(clip.mediaId)?.let { if (player.currentMediaItem?.mediaId != clip.mediaId) player.removeMediaItem(it) }
        finishTalk()
    }

    private fun finishTalk() {
        val clip = talk ?: return
        if (talkCurrent()) return
        talk = null; talkAfter = null; talkNext = null
        nativeIndex(clip.mediaId)?.let { player.removeMediaItem(it) }
        if (sleepGain != 1f && sleepDeadline == null) { sleepGain = 1f; applyVolume() }
        onTalk(TalkEvent.Finished(clip, TalkPolicy.outcome(talkStarted, talkCompleted), talkHeardMs))
        publish()
    }

    fun close() {
        if (closed) return
        publish(checkpoint = true)
        closed = true; scope.cancel(); tail?.release(); tail = null; effects.release(); player.release()
    }
}

/** How far ahead of the outgoing song's position the tail is prepared; covers loading it. */
private const val CROSSFADE_LEAD_MS = 1200L
/** Roughly how long a prepared player takes to make sound after play(). */
private const val TAIL_START_LATENCY_MS = 60L

private fun Int.toRepeatMode(): RepeatMode = when (this) {
    Player.REPEAT_MODE_ALL -> RepeatMode.ALL
    Player.REPEAT_MODE_ONE -> RepeatMode.ONE
    else -> RepeatMode.OFF
}
