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
    override fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean) { if (accessLost) pause() }
}
