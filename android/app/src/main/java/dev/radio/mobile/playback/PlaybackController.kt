package dev.radio.mobile.playback

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.StationOrigin
import kotlinx.coroutines.flow.StateFlow

/** Repeat off plays the queue once; all wraps at the end; one holds the current song. */
enum class RepeatMode { OFF, ALL, ONE }

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
    val repeat: RepeatMode = RepeatMode.OFF,
    val error: String? = null,
    val sleepTimer: SleepTimerState = SleepTimerState()
    ,val radioActive: Boolean = false, val queueRevision: Long = 0,
    /** Why each queued song is in a Radio station (checkpoint 4.2). Empty outside Radio. */
    val origins: Map<String, StationOrigin> = emptyMap(),
    /** Title of the song the station started from; null for Pick for me or outside Radio. */
    val stationSeedTitle: String? = null,
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
    fun next(reason: String) = next()
    fun replay() = Unit
    fun previous()
    fun seek(positionMs: Long)
    fun jumpTo(songId: String)
    fun shuffle(enabled: Boolean)
    fun setRepeat(mode: RepeatMode)
    fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean)
    fun addToQueue(items: List<LocalSong>, playNext: Boolean = false)
    fun removeFromQueue(songId: String)
    fun moveInQueue(songId: String, target: Int)
    fun clearUpcoming()
    fun setSleepTimer(durationMs: Long, fade: Boolean = true)
    fun sleepAtTrackEnd()
    fun cancelSleepTimer()
    fun startRadio(seedLocalId: String? = null) = Unit
    fun stopRadio() = Unit
    /** End the station: finish what's queued (radio stops adding), or stop now (pause and keep a normal queue). */
    fun endRadio(stopNow: Boolean) { if (stopNow) pause(); stopRadio() }
    /** Replace the songs Radio added after the current one with a fresh batch; songs the listener added stay. */
    fun newMix() = Unit
}
