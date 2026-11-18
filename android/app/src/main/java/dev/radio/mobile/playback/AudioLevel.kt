package dev.radio.mobile.playback

import kotlin.math.sqrt

/**
 * Checkpoint 10: how loud the music is right now (0..1), for the Moving backdrop. Measured on the PCM the app
 * already processes for normalization, so there is no microphone or Visualizer permission. The service and the
 * activity share one process, so the UI reads it directly. Nothing is measured unless the backdrop is listening.
 */
object AudioLevel {
    /** Set by the backdrop while it animates; the audio thread skips the maths otherwise. */
    @Volatile var listening = false
    /** Smoothed level 0..1; read during drawing only. */
    @Volatile var level = 0f
        private set

    private const val ATTACK = 0.5f   // rises quickly with a beat
    private const val RELEASE = 0.08f // falls slowly so the glow doesn't flicker
    /** RMS of ~-6 dBFS reads as full level; quiet passages still move a little. */
    private const val FULL_SCALE_RMS = 0.5f

    fun smooth(previous: Float, target: Float): Float =
        previous + (target - previous) * if (target > previous) ATTACK else RELEASE

    fun normalized(rms: Float): Float = (rms / FULL_SCALE_RMS).coerceIn(0f, 1f)

    internal fun report(sumSquares: Double, samples: Int) {
        if (samples <= 0) return
        level = smooth(level, normalized(sqrt(sumSquares / samples).toFloat()))
    }

    /** Let the glow settle when playback stops or the backdrop stops listening. */
    fun decay() { level = smooth(level, 0f) }
    internal fun resetForTest() { level = 0f }
}
