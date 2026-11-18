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

    /** Equal-power curves keep the overall loudness steady through the overlap. */
    fun outgoing(progress: Float): Float = cos(progress.coerceIn(0f, 1f) * PI / 2).toFloat().coerceIn(0f, 1f)
    fun incoming(progress: Float): Float = sin(progress.coerceIn(0f, 1f) * PI / 2).toFloat().coerceIn(0f, 1f)
}
