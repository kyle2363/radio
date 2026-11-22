package dev.radio.mobile.playback

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/** When an automatic song change should overlap the two songs, and how loud each is while they do. */
object Crossfade {
    /** Choices offered in Settings, in seconds; 0 is off. */
    val CHOICES = listOf(0, 2, 4, 6, 8, 10, 12)
    /** Closer to the end than this, the song is left to finish on its own. */
    private const val MIN_HANDOVER_MS = 500L

    fun shouldStart(fadeMs: Long, positionMs: Long, durationMs: Long?, hasNext: Boolean, repeatOne: Boolean, pauseAtEnd: Boolean, fading: Boolean): Boolean {
        if (fadeMs <= 0 || durationMs == null || !hasNext || repeatOne || pauseAtEnd || fading) return false
        if (durationMs < fadeMs * 2) return false
        val remaining = durationMs - positionMs
        return remaining in MIN_HANDOVER_MS..fadeMs
    }

    /** How far past the handover the player may report before the tail is moved to match it. */
    private const val ON_TIME_MS = 100L

    /**
     * Where the outgoing tail should start playing once the real player reaches the handover. Some devices report
     * position in coarse steps (~200 ms), so the player can be seen past the handover; then the tail is lined up with
     * where the song really is instead of the crossfade being abandoned. Null when too little song is left to overlap.
     */
    fun tailStart(positionMs: Long, handoverMs: Long, durationMs: Long): Long? = when {
        positionMs <= handoverMs + ON_TIME_MS -> handoverMs
        durationMs - positionMs >= MIN_HANDOVER_MS -> positionMs
        else -> null
    }

    /** Equal-power curves keep the overall loudness steady through the overlap. */
    fun outgoing(progress: Float): Float = cos(progress.coerceIn(0f, 1f) * PI / 2).toFloat().coerceIn(0f, 1f)
    fun incoming(progress: Float): Float = sin(progress.coerceIn(0f, 1f) * PI / 2).toFloat().coerceIn(0f, 1f)
}
