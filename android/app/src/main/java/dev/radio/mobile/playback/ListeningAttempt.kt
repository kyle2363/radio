package dev.radio.mobile.playback

/** Monotonic audible wall time, independent of media position and seeking. */
class ListeningAttempt(val id: String, val songId: String) {
    private var last: Long? = null
    private var audible = false
    private var listened = 0L
    private var started = false
    private var finished = false
    val isFinished: Boolean get() = finished
    fun observe(now: Long, isPlaying: Boolean): Boolean {
        if (finished) return false
        last?.let { if (audible) listened += (now - it).coerceAtLeast(0) }
        last = now; audible = isPlaying
        return if (isPlaying && !started) { started = true; true } else false
    }
    fun finish(now: Long): Long? {
        if (finished) return null
        observe(now, false); finished = true
        return if (started) listened else null
    }
}
