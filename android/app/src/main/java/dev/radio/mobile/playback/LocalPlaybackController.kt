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
    private val onAudibleStart: (LocalSong, String, Long) -> Unit = { _, _, _ -> }
) : PlaybackController {
    private val player = ExoPlayer.Builder(context.applicationContext).build().apply {
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
    private var error: String? = null
    private var accessLost = false
    private var attemptSong: String? = null
    private var attemptId = ""
    private var started = false
    private var ticker: Job? = null
    private var closed = false

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
        override fun stop() { player.pause(); player.stop(); publish() }
    }

    init {
        player.addListener(object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) {
                val id = player.currentMediaItem?.mediaId
                if (id != null && id != attemptSong) beginAttempt(id)
                // Preparing/prefetching, buffering, and pause/resume cannot manufacture starts.
                if (player.isPlaying && player.playbackState == Player.STATE_READY && !started) {
                    queueById[id]?.let { song ->
                        started = true
                        onAudibleStart(song, attemptId, player.currentPosition.coerceAtLeast(0))
                    }
                }
                publish()
                if (player.isPlaying && ticker?.isActive != true) ticker = scope.launch {
                    while (isActive && player.isPlaying) { publish(); delay(250) }
                } else if (!player.isPlaying) { ticker?.cancel(); ticker = null }
            }
            override fun onPlayerError(exception: PlaybackException) {
                error = "Couldn't play this file. It may be missing, unreadable or unsupported. Try Next, or check the Music folder and rescan."
                player.pause()
                publish()
            }
        })
    }

    private fun beginAttempt(id: String) { attemptSong = id; attemptId = UUID.randomUUID().toString(); started = false }
    private fun canReadCurrent() = !accessLost && queueById[player.currentMediaItem?.mediaId]?.available == true
    private fun validUri(song: LocalSong) = Uri.parse(song.uri).scheme == "content"
    private fun publish() {
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
            queue = queue, playOrder = cachedOrder, source = source, currentId = id,
            positionMs = player.currentPosition.coerceAtLeast(0),
            durationMs = duration,
            isPlaying = player.isPlaying, playWhenReady = player.playWhenReady && player.playbackState != Player.STATE_ENDED,
            buffering = player.playbackState == Player.STATE_BUFFERING, ended = player.playbackState == Player.STATE_ENDED,
            seekable = player.isCurrentMediaItemSeekable && duration != null && duration > 0 && error == null && canReadCurrent(),
            canNext = !accessLost && next != C.INDEX_UNSET && queue.any { it.available && it.id != id },
            canPrevious = canReadCurrent(), shuffled = player.shuffleModeEnabled, error = error
        )
    }

    override fun playFrom(items: List<LocalSong>, selectedId: String, source: String) {
        val snapshot = playbackQueue(items, selectedId).filter(::validUri)
        val index = snapshot.indexOfFirst { it.id == selectedId }
        if (accessLost || index < 0) { player.pause(); error = "This file is unavailable. Check folder access and rescan."; publish(); return }
        queue = snapshot; queueById = snapshot.associateBy { it.id }; this.source = source; error = null; beginAttempt(selectedId)
        player.setMediaItems(snapshot.map { song ->
            MediaItem.Builder().setMediaId(song.id).setUri(song.uri)
                .setMediaMetadata(metadata(song)).build()
        }, index, 0)
        if (player.shuffleModeEnabled) reshuffle()
        player.prepare()
        player.play()
        publish()
    }
    override fun toggle() {
        if (player.playWhenReady && player.playbackState != Player.STATE_ENDED) { pause(); return }
        resume()
    }
    private fun resume() {
        if (!canReadCurrent()) { error = "This file is unavailable. Check folder access and rescan."; publish(); return }
        if (player.playbackState == Player.STATE_ENDED) { beginAttempt(player.currentMediaItem!!.mediaId); player.seekTo(0) }
        error = null
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish()
    }
    private fun metadata(song: LocalSong) = MediaMetadata.Builder().setTitle(song.title).setArtist(song.artist)
        .setAlbumTitle(song.folderPath.ifEmpty { source }).build()
    override fun pause() { if (!closed) { player.pause(); publish() } }
    override fun seek(positionMs: Long) {
        if (!state.value.seekable) return
        player.seekTo(positionMs.coerceIn(0, state.value.durationMs ?: Long.MAX_VALUE)); publish()
    }
    private fun adjacent(forward: Boolean): Int {
        val timeline = player.currentTimeline
        var index = player.currentMediaItemIndex
        repeat(queue.size) {
            index = if (forward) timeline.getNextWindowIndex(index, Player.REPEAT_MODE_OFF, player.shuffleModeEnabled)
                else timeline.getPreviousWindowIndex(index, Player.REPEAT_MODE_OFF, player.shuffleModeEnabled)
            if (index == C.INDEX_UNSET) return C.INDEX_UNSET
            if (queueById[player.getMediaItemAt(index).mediaId]?.available == true) return index
        }
        return C.INDEX_UNSET
    }
    private fun move(index: Int) {
        if (accessLost || index == C.INDEX_UNSET) return
        val continuePlaying = player.playWhenReady || error != null
        error = null
        beginAttempt(player.getMediaItemAt(index).mediaId)
        player.seekTo(index, 0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.playWhenReady = continuePlaying
        publish()
    }
    override fun next() { if (queue.isNotEmpty()) move(adjacent(true)) }
    override fun previous() {
        if (!canReadCurrent()) return
        val previous = adjacent(false)
        if (player.currentPosition > 3000 || previous == C.INDEX_UNSET) seek(0) else move(previous)
    }
    override fun jumpTo(songId: String) {
        val index = (0 until player.mediaItemCount).firstOrNull { player.getMediaItemAt(it).mediaId == songId } ?: return
        if (accessLost || queue.none { it.id == songId && it.available }) return
        error = null; beginAttempt(songId); player.seekTo(index, 0)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play(); publish()
    }
    private fun seekToItem(index: Int, positionMs: Long) {
        if (index !in 0 until player.mediaItemCount || accessLost) return
        val id = player.getMediaItemAt(index).mediaId
        if (queueById[id]?.available != true) return
        if (id != player.currentMediaItem?.mediaId) beginAttempt(id)
        error = null
        player.seekTo(index, positionMs.coerceAtLeast(0))
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        publish()
    }
    override fun shuffle(enabled: Boolean) {
        if (enabled && !player.shuffleModeEnabled) reshuffle()
        player.shuffleModeEnabled = enabled
        publish()
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
    override fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean) {
        this.accessLost = accessLost
        if (queue.isEmpty()) return
        val byId = songs.associateBy { it.id }
        val updated = queue.map { byId[it.id] ?: it.copy(available = false) }
        val uriChanged = queue.zip(updated).any { (before, after) -> before.uri != after.uri }
        queue = updated
        queueById = updated.associateBy { it.id }
        if (!canReadCurrent()) {
            player.stop()
            error = "Folder access or the current file is unavailable. Choose the Music folder again or rescan."
        } else if (uriChanged) {
            val id = player.currentMediaItem!!.mediaId
            val position = player.currentPosition
            player.pause()
            val readable = queue.filter { it.available }
            player.setMediaItems(readable.map { MediaItem.Builder().setMediaId(it.id).setUri(it.uri).setMediaMetadata(metadata(it)).build() }, readable.indexOfFirst { it.id == id }, position)
        } else {
            for (index in player.mediaItemCount - 1 downTo 0) {
                if (queue.none { it.id == player.getMediaItemAt(index).mediaId && it.available }) player.removeMediaItem(index)
            }
        }
        publish()
    }
    fun close() {
        if (closed) return
        closed = true; scope.cancel(); player.release()
    }
}
