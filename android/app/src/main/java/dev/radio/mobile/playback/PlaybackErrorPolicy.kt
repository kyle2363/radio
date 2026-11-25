package dev.radio.mobile.playback

/**
 * What to do when a file can't be played. While music is playing, an unreadable file is skipped so Radio and queues
 * keep going (in a pocket or a car nobody is there to tap Next). After [MAX_SKIPS] failures in a row something is
 * wrong with the folder rather than one file, so playback stops and shows the error. Paused playback never starts.
 */
object PlaybackErrorPolicy {
    const val MAX_SKIPS = 3
    fun shouldSkip(consecutiveErrors: Int, playing: Boolean, hasNext: Boolean): Boolean =
        playing && hasNext && consecutiveErrors <= MAX_SKIPS
}
