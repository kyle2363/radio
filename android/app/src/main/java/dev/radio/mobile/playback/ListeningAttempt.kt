package dev.radio.mobile.playback

/** Monotonic audible wall time, independent of media position and seeking. */
class ListeningAttempt(val id: String, val songId: String) {
    private var last: Long? = null
    private var audible = false
    private var listened = 0L
    private var started = false
    private var finished = false
    val isFinished: Boolean get() = finished
    private var rate = 1f
    /** [rate] is the playback speed from now on: at 1.5x, ten seconds of listening covers fifteen seconds of the song. */
    fun observe(now: Long, isPlaying: Boolean, rate: Float = 1f): Boolean {
        if (finished) return false
        last?.let { if (audible) listened += ((now - it).coerceAtLeast(0) * this.rate).toLong() }
        last = now; audible = isPlaying; this.rate = rate
        return if (isPlaying && !started) { started = true; true } else false
    }
    fun finish(now: Long): Long? {
        if (finished) return null
        observe(now, false); finished = true
        return if (started) listened else null
    }
}
