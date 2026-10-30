package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/** UI wiring fixture only; actual MP3 decoding/events are exercised by MediaPlaybackTest. */
class FakePlaybackController : PlaybackController {
    private val mutable = MutableStateFlow(PlaybackState())
    override val state: StateFlow<PlaybackState> = mutable
    private fun select(id: String, playing: Boolean) {
        mutable.update { old -> old.copy(currentId = id, positionMs = 0, durationMs = old.queue.first { it.id == id }.durationMs,
            isPlaying = playing, playWhenReady = playing, seekable = true, canPrevious = true, canNext = old.queue.indexOfFirst { it.id == id } < old.queue.lastIndex) }
    }
    override fun playFrom(items: List<LocalSong>, selectedId: String, source: String) {
        val queue = playbackQueue(items, selectedId)
        if (queue.isEmpty()) return
        mutable.value = PlaybackState(queue = queue, source = source, playOrder = queue.map { it.id })
        select(selectedId, true)
    }
    override fun toggle() { mutable.update { it.copy(isPlaying = !it.isPlaying, playWhenReady = !it.playWhenReady) } }
    override fun pause() { mutable.update { it.copy(isPlaying = false, playWhenReady = false) } }
    override fun next() { val state = mutable.value; val index = state.queue.indexOfFirst { it.id == state.currentId }; if (index < state.queue.lastIndex) select(state.queue[index + 1].id, state.playWhenReady) }
    override fun previous() { val state = mutable.value; val index = state.queue.indexOfFirst { it.id == state.currentId }; if (index > 0) select(state.queue[index - 1].id, state.playWhenReady) else seek(0) }
    override fun seek(positionMs: Long) { mutable.update { it.copy(positionMs = positionMs) } }
    override fun jumpTo(songId: String) { select(songId, true) }
    override fun shuffle(enabled: Boolean) { mutable.update { old -> old.copy(shuffled = enabled,
        playOrder = if (enabled) listOfNotNull(old.currentId) + old.queue.map { it.id }.filter { it != old.currentId }.reversed() else old.queue.map { it.id }) } }
    override fun setRepeat(mode: RepeatMode) { mutable.update { it.copy(repeat = mode) } }
    override fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean) { if (accessLost) pause() }
    override fun addToQueue(items: List<LocalSong>, playNext: Boolean) { mutable.update { old ->
        val queue = (old.queue + items.filter { it.available }).distinctBy { it.id }
        val ids = items.map { it.id }.filter { it != old.currentId }.distinct()
        val order = (old.playOrder + ids).distinct().toMutableList()
        if (playNext && old.currentId != null) { order.removeAll(ids.toSet()); order.addAll(order.indexOf(old.currentId) + 1, ids) }
        old.copy(queue = queue, playOrder = order, currentId = old.currentId ?: queue.firstOrNull()?.id, source = "Custom queue")
    } }
    override fun removeFromQueue(songId: String) { mutable.update { old ->
        val queue = old.queue.filterNot { it.id == songId }; val order = old.playOrder.filterNot { it == songId }
        old.copy(queue = queue, playOrder = order, currentId = old.currentId.takeUnless { it == songId } ?: order.firstOrNull())
    } }
    override fun moveInQueue(songId: String, target: Int) { mutable.update { old ->
        val order = old.playOrder.toMutableList(); if (target in order.indices && order.remove(songId)) order.add(target, songId)
        old.copy(playOrder = order, queue = if (old.shuffled) old.queue else order.mapNotNull { id -> old.queue.firstOrNull { it.id == id } })
    } }
    override fun clearUpcoming() { mutable.update { old -> val ids = old.playOrder.take(old.playOrder.indexOf(old.currentId) + 1); old.copy(queue = old.queue.filter { it.id in ids }, playOrder = ids) } }
    override fun setSleepTimer(durationMs: Long, fade: Boolean) { mutable.update { it.copy(sleepTimer = SleepTimerState(durationMs, fade = fade)) } }
    override fun sleepAtTrackEnd() { mutable.update { it.copy(sleepTimer = SleepTimerState(endOfTrack = true)) } }
    override fun cancelSleepTimer() { mutable.update { it.copy(sleepTimer = SleepTimerState()) } }
}
