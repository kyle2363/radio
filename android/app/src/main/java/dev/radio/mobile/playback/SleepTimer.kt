package dev.radio.mobile.playback

data class SleepTimerState(val remainingMs: Long = 0, val endOfTrack: Boolean = false, val fade: Boolean = false) {
    val active get() = endOfTrack || remainingMs > 0
}

internal data class SleepDeadline(val elapsedMs: Long, val fade: Boolean) {
    fun remaining(now: Long) = (elapsedMs - now).coerceAtLeast(0)
    fun gain(now: Long): Float = if (fade) (remaining(now) / 10000f).coerceIn(0f, 1f) else 1f
}
