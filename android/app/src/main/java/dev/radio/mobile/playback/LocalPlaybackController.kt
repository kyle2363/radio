package dev.radio.mobile.playback

import android.content.Context
import android.net.Uri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Timeline
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.source.ShuffleOrder.DefaultShuffleOrder
import dev.radio.mobile.data.LocalSong
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
    internal val normalizationProcessor = NormalizationAudioProcessor()
    private val renderers = object : DefaultRenderersFactory(context.applicationContext) {
        override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink =
            DefaultAudioSink.Builder(context).setEnableFloatOutput(false).setAudioProcessors(arrayOf(normalizationProcessor)).build()
    }
    private val player = ExoPlayer.Builder(context.applicationContext, renderers).build().apply {
        setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(), true)
        setHandleAudioBecomingNoisy(true)
        setWakeMode(C.WAKE_MODE_LOCAL)
        repeatMode = Player.REPEAT_MODE_OFF
    }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val mutable = MutableStateFlow(PlaybackState())
    override val state: StateFlow<PlaybackState> = mutable.asStateFlow()
    private var queue = emptyList<LocalSong>()
    private var queueById = emptyMap<String, LocalSong>()
    private var cachedTimeline: Timeline? = null
    private var cachedShuffle = false
    private var cachedOrder = emptyList<String>()
    private var source = ""
    private var radioActive = false
    private var queueRevision = 0L
    internal var onRadioStart: () -> Unit = {}
    override fun startRadio() = onRadioStart()
    override fun stopRadio() { radioActive=false;queueRevision++;publish(checkpoint=true) }
    private var error: String? = null
    private var accessLost = false
    private var attemptSong: String? = null
    private var attemptId = ""
    private var started = false
    private var listeningAttempt: ListeningAttempt? = null
    private var attemptDurationMs: Long? = null
    private var ticker: Job? = null
    private var closed = false
    private var lastCheckpointAt = 0L
    private var sleepDeadline: SleepDeadline? = null
    private var sleepEndOfTrack = false
    private var sleepJob: Job? = null

    // Every system transport command uses the same guards, shuffle and journal as the app.
    internal val sessionPlayer: Player = object : ForwardingPlayer(player) {
        override fun play() = resume()
        override fun pause() = this@LocalPlaybackController.pause()
        override fun setPlayWhenReady(playWhenReady: Boolean) { if (playWhenReady) resume() else pause() }
        override fun seekTo(positionMs: Long) = seek(positionMs)
        override fun seekTo(mediaItemIndex: Int, positionMs: Long) = seekToItem(mediaItemIndex, positionMs)
        override fun seekToDefaultPosition() = seek(0)
        override fun seekToDefaultPosition(mediaItemIndex: Int) = seekToItem(mediaItemIndex, 0)
        override fun seekToNext() = next()
        override fun seekToNextMediaItem() = next()
        override fun seekToPrevious() = previous()
        override fun seekToPreviousMediaItem() = previous()
        override fun setShuffleModeEnabled(shuffleModeEnabled: Boolean) = shuffle(shuffleModeEnabled)
        override fun setRepeatMode(repeatMode: Int) = setRepeat(repeatMode.toRepeatMode())
        override fun stop() { queueRevision++;finishAttempt("attempt_stopped", "system_stop"); player.pause(); player.stop(); publish(checkpoint = true) }
    }

    init {
        player.addListener(object : Player.Listener {
            override fun onPositionDiscontinuity(oldPosition: Player.PositionInfo, newPosition: Player.PositionInfo, reason: Int) {
                if (reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
                    finishAttempt("song_completed", "natural_end")
                    newPosition.mediaItem?.mediaId?.let { beginAttempt(it) }
                }
            }
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (!playWhenReady && reason == Player.PLAY_WHEN_READY_CHANGE_REASON_END_OF_MEDIA_ITEM) { finishAttempt("song_completed", "natural_end"); if (sleepEndOfTrack) cancelSleepTimer() }
            }
            override fun onEvents(player: Player, events: Player.Events) {
                val id = player.currentMediaItem?.mediaId
                if (id != null && id != attemptSong) beginAttempt(id)
                if (id != null && player.isPlaying && listeningAttempt?.isFinished == true) beginAttempt(id)
                listeningAttempt?.observe(android.os.SystemClock.elapsedRealtime(), player.isPlaying)
                if (player.playbackState == Player.STATE_ENDED) finishAttempt("song_completed", "natural_end")
                // Preparing/prefetching, buffering, and pause/resume cannot manufacture starts.
                if (player.isPlaying && player.playbackState == Player.STATE_READY && !started) {
                    queueById[id]?.let { song ->
                        started = true
                        attemptDurationMs = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: song.durationMs
                        onAudibleStart(song, attemptId, player.currentPosition.coerceAtLeast(0))
                    }
                }
                publish(checkpoint = events.containsAny(Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                    Player.EVENT_PLAY_WHEN_READY_CHANGED, Player.EVENT_PLAYBACK_STATE_CHANGED, Player.EVENT_SHUFFLE_MODE_ENABLED_CHANGED,
                    Player.EVENT_REPEAT_MODE_CHANGED, Player.EVENT_TIMELINE_CHANGED, Player.EVENT_PLAYER_ERROR))
                if (player.isPlaying && ticker?.isActive != true) ticker = scope.launch {
                    while (isActive && player.isPlaying) { publish(); delay(250) }
                } else if (!player.isPlaying) { ticker?.cancel(); ticker = null }
            }
            override fun onPlayerError(exception: PlaybackException) {
                finishAttempt("attempt_stopped", "decoder_error")
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
        onAttemptFinished(attempt.songId, attempt.id, kind, listened, duration, reason)
    }
    private fun beginAttempt(id: String) {
        val active = player.playWhenReady && started
        finishAttempt(if (active) "song_skipped" else "attempt_stopped", if (active) "explicit_navigation" else "paused_navigation")
        attemptSong = id; attemptId = UUID.randomUUID().toString(); started = false
        attemptDurationMs = null
        listeningAttempt = ListeningAttempt(attemptId, id)
    }
    override fun replay() {
        queueRevision++
        val song = queueById[player.currentMediaItem?.mediaId] ?: return
        if (!canReadCurrent()) return
        finishAttempt("attempt_stopped", "explicit_replay")
        beginAttempt(song.id)
        onExplicitReplay(song, attemptId)
        player.seekTo(0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish(checkpoint = true)
    }
    private fun canReadCurrent() = !accessLost && queueById[player.currentMediaItem?.mediaId]?.available == true
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
            while (index != C.INDEX_UNSET && order.size < queue.size) {
                order += player.getMediaItemAt(index).mediaId
                index = timeline.getNextWindowIndex(index, Player.REPEAT_MODE_OFF, player.shuffleModeEnabled)
            }
            cachedTimeline = timeline; cachedShuffle = player.shuffleModeEnabled; cachedOrder = order
        }
        val next = if (id != null) adjacent(true) else C.INDEX_UNSET
        val duration = player.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: current?.durationMs
        mutable.value = PlaybackState(
            queue = queue, playOrder = cachedOrder, source = source, currentId = id, radioActive = radioActive, queueRevision = queueRevision,
            positionMs = player.currentPosition.coerceAtLeast(0),
            durationMs = duration,
            isPlaying = player.isPlaying, playWhenReady = player.playWhenReady && player.playbackState != Player.STATE_ENDED,
            buffering = player.playbackState == Player.STATE_BUFFERING, ended = player.playbackState == Player.STATE_ENDED,
            seekable = (player.isCurrentMediaItemSeekable || player.playbackState == Player.STATE_IDLE) && duration != null && duration > 0 && error == null && canReadCurrent(),
            canNext = !accessLost && next != C.INDEX_UNSET && queue.any { it.available && it.id != id },
            canPrevious = canReadCurrent(), shuffled = player.shuffleModeEnabled, repeat = player.repeatMode.toRepeatMode(), error = error,
            sleepTimer = SleepTimerState(sleepDeadline?.remaining(android.os.SystemClock.elapsedRealtime()) ?: 0L, sleepEndOfTrack, sleepDeadline?.fade == true)
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
        queue = restored.songs; queueById = queue.associateBy { it.id }; source = restored.source
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
    internal fun updateNormalization(profile: NormalizationProfile) { normalizationProcessor.profile = profile; publish() }

    override fun playFrom(items: List<LocalSong>, selectedId: String, source: String) {
        radioActive=false;queueRevision++
        val snapshot = playbackQueue(items, selectedId).filter(::validUri)
        val index = snapshot.indexOfFirst { it.id == selectedId }
        if (accessLost || index < 0) { player.pause(); error = "This file is unavailable. Check folder access and rescan."; publish(); return }
        finishAttempt(if (player.playWhenReady && started) "song_skipped" else "attempt_stopped", "queue_replacement")
        queue = snapshot; queueById = snapshot.associateBy { it.id }; this.source = source; error = null; beginAttempt(selectedId)
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
    private fun resume() {
        queueRevision++
        if (!canReadCurrent()) { error = "This file is unavailable. Check folder access and rescan."; publish(); return }
        if (listeningAttempt?.isFinished == true && player.playbackState != Player.STATE_ENDED) beginAttempt(player.currentMediaItem!!.mediaId)
        if (player.playbackState == Player.STATE_ENDED) { beginAttempt(player.currentMediaItem!!.mediaId); player.seekTo(0) }
        error = null
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish(checkpoint = true)
    }
    private fun metadata(song: LocalSong) = MediaMetadata.Builder().setTitle(song.title).setArtist(song.artist)
        .setAlbumTitle(song.folderPath.ifEmpty { source }).build()
    override fun pause() { if (!closed) { queueRevision++;player.pause(); publish(checkpoint = true) } }
    override fun seek(positionMs: Long) {
        if (!state.value.seekable) return
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
    private fun move(index: Int) {
        if (accessLost || index == C.INDEX_UNSET) return
        queueRevision++
        val continuePlaying = player.playWhenReady || error != null
        error = null
        beginAttempt(player.getMediaItemAt(index).mediaId)
        player.seekTo(index, 0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.playWhenReady = continuePlaying
        publish(checkpoint = true)
    }
    override fun next() { if (queue.isNotEmpty()) move(adjacent(true)) }
    override fun next(reason: String) {
        if (accessLost || adjacent(true) == C.INDEX_UNSET) return
        if (started && player.playWhenReady) finishAttempt("song_skipped", reason.takeIf {
            it in setOf("too_intense", "too_slow", "too_samey", "wrong_mood", "just_skip")
        } ?: "just_skip")
        next()
    }
    override fun previous() {
        if (!canReadCurrent()) return
        val previous = adjacent(false)
        if (player.currentPosition > 3000 || previous == C.INDEX_UNSET) seek(0) else move(previous)
    }
    override fun jumpTo(songId: String) {
        val index = (0 until player.mediaItemCount).firstOrNull { player.getMediaItemAt(it).mediaId == songId } ?: return
        if (accessLost || queue.none { it.id == songId && it.available }) return
        queueRevision++
        error = null; beginAttempt(songId); player.seekTo(index, 0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish(checkpoint = true)
    }
    private fun seekToItem(index: Int, positionMs: Long) {
        if (index !in 0 until player.mediaItemCount || accessLost) return
        val id = player.getMediaItemAt(index).mediaId
        if (queueById[id]?.available != true) return
        queueRevision++
        if (id != player.currentMediaItem?.mediaId) beginAttempt(id)
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
    internal fun beginRadio(items: List<LocalSong>) {
        val first=items.firstOrNull() ?: return
        playFrom(items.take(5),first.id,"Radio")
        radioActive=true
        publish(checkpoint=true)
    }
    /** Main-thread atomic append. Retire only played Radio prefix; never seek or prepare. */
    internal fun appendRadio(expected:PlaybackState,items:List<LocalSong>):Boolean {
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
        queueRevision++;publish(checkpoint=true)
        return additions.isNotEmpty()
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
        val incoming = items.filter { it.available && validUri(it) }.distinctBy { it.id }
        if (incoming.isEmpty()) return
        queueRevision++
        val before = state.value.playOrder
        val wasEmpty = player.mediaItemCount == 0
        if (wasEmpty) player.pause()
        val additions = incoming.filter { nativeIndex(it.id) == null }
        val incomingById = incoming.associateBy { it.id }
        queue = queue.map { incomingById[it.id] ?: it } + additions.filter { added -> queue.none { it.id == added.id } }
        queueById = queue.associateBy { it.id }
        if (additions.isNotEmpty()) player.addMediaItems(additions.map(::mediaItem))
        source = if(radioActive)"Radio" else "Custom queue"; error = null
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
        source = if(radioActive)"Radio" else "Custom queue"
        publish(checkpoint = true)
    }
    override fun removeFromQueue(songId: String) {
        val index = nativeIndex(songId) ?: return
        queueRevision++
        val current = player.currentMediaItem?.mediaId == songId
        if (current) finishAttempt(if (player.isPlaying) "song_skipped" else "attempt_stopped", "queue_removal")
        val oldOrder = state.value.playOrder
        val successor = oldOrder.drop(oldOrder.indexOf(songId) + 1).firstOrNull { queueById[it]?.available == true }
            ?: oldOrder.take(oldOrder.indexOf(songId)).lastOrNull { queueById[it]?.available == true }
        val continuePlaying = player.playWhenReady
        queue = queue.filterNot { it.id == songId }; queueById = queue.associateBy { it.id }
        player.removeMediaItem(index)
        if (current && successor != null) {
            beginAttempt(successor)
            player.seekTo(nativeIndex(successor) ?: 0, 0)
            player.playWhenReady = continuePlaying
        }
        if (queue.isEmpty() || player.mediaItemCount == 0) {
            radioActive=false
            queue = emptyList(); queueById = emptyMap()
            player.pause(); player.clearMediaItems(); cancelSleepTimer(); source = ""; error = null
            onQueueCleared()
        } else { source = if(radioActive)"Radio" else "Custom queue"; error = null }
        publish(checkpoint = true)
    }
    override fun clearUpcoming() {
        radioActive=false;queueRevision++
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
        source = "Custom queue"
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
                    finishAttempt("attempt_stopped", "sleep_timer"); player.pause(); sleepDeadline = null; player.volume = 1f
                    publish(checkpoint = true); break
                }
                player.volume = deadline.gain(now)
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
    override fun cancelSleepTimer() {
        queueRevision++
        sleepJob?.cancel(); sleepJob = null; sleepDeadline = null; sleepEndOfTrack = false
        player.pauseAtEndOfMediaItems = false; player.volume = 1f
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
            finishAttempt("attempt_stopped", "access_lost")
            player.stop()
            error = "Folder access or the current file is unavailable. Choose the Music folder again or rescan."
        } else if (uriChanged) {
            finishAttempt("attempt_stopped", "source_uri_changed")
            bookmark?.let { restorePaused(it, queue, accessLost = false) }
        } else {
            for (index in player.mediaItemCount - 1 downTo 0) {
                if (queue.none { it.id == player.getMediaItemAt(index).mediaId && it.available }) player.removeMediaItem(index)
            }
        }
        publish(checkpoint = true)
    }
    fun close() {
        if (closed) return
        publish(checkpoint = true)
        closed = true; scope.cancel(); player.release()
    }
}

private fun Int.toRepeatMode(): RepeatMode = when (this) {
    Player.REPEAT_MODE_ALL -> RepeatMode.ALL
    Player.REPEAT_MODE_ONE -> RepeatMode.ONE
    else -> RepeatMode.OFF
}
