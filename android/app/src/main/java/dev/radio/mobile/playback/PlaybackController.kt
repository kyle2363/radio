package dev.radio.mobile.playback

import dev.radio.mobile.data.LocalSong
import kotlinx.coroutines.flow.StateFlow

data class PlaybackState(
    val queue: List<LocalSong> = emptyList(),
    val playOrder: List<String> = emptyList(),
    val source: String = "",
    val currentId: String? = null,
    val positionMs: Long = 0,
    val durationMs: Long? = null,
    val isPlaying: Boolean = false,
    val playWhenReady: Boolean = false,
    val buffering: Boolean = false,
    val ended: Boolean = false,
    val seekable: Boolean = false,
    val canNext: Boolean = false,
    val canPrevious: Boolean = false,
    val shuffled: Boolean = false,
    val error: String? = null,
    val sleepTimer: SleepTimerState = SleepTimerState()
) {
    val current: LocalSong? get() = queue.firstOrNull { it.id == currentId }
    val status: String get() = when {
        error != null -> "Playback error"
        buffering -> "Buffering…"
        ended -> "Queue finished"
        isPlaying -> "Playing"
        currentId != null -> "Paused"
        else -> "Ready"
    }
}

fun playbackQueue(items: List<LocalSong>, selectedId: String): List<LocalSong> {
    val queue = items.filter { it.available }.distinctBy { it.id }.toList()
    return if (queue.any { it.id == selectedId }) queue else emptyList()
}

interface PlaybackController {
    val state: StateFlow<PlaybackState>
    fun playFrom(items: List<LocalSong>, selectedId: String, source: String)
    fun toggle()
    fun pause()
    fun next()
    fun previous()
    fun seek(positionMs: Long)
    fun jumpTo(songId: String)
    fun shuffle(enabled: Boolean)
    fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean)
    fun addToQueue(items: List<LocalSong>, playNext: Boolean = false)
    fun removeFromQueue(songId: String)
    fun moveInQueue(songId: String, target: Int)
    fun clearUpcoming()
    fun setSleepTimer(durationMs: Long, fade: Boolean = true)
    fun sleepAtTrackEnd()
    fun cancelSleepTimer()
}
