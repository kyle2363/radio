package dev.radio.mobile.playback

import org.junit.Assert.*
import org.junit.Test

class PlaybackErrorPolicyTest {
    @Test fun skipsAnUnreadableFileWhileMusicIsPlaying() {
        assertTrue(PlaybackErrorPolicy.shouldSkip(consecutiveErrors = 1, playing = true, hasNext = true))
        assertTrue(PlaybackErrorPolicy.shouldSkip(consecutiveErrors = PlaybackErrorPolicy.MAX_SKIPS, playing = true, hasNext = true))
    }
    @Test fun stopsAfterTooManyFailuresInARow() =
        assertFalse(PlaybackErrorPolicy.shouldSkip(consecutiveErrors = PlaybackErrorPolicy.MAX_SKIPS + 1, playing = true, hasNext = true))
    @Test fun neverStartsMusicOrSkipsPastTheEnd() {
        assertFalse("paused playback stays paused", PlaybackErrorPolicy.shouldSkip(consecutiveErrors = 1, playing = false, hasNext = true))
        assertFalse(PlaybackErrorPolicy.shouldSkip(consecutiveErrors = 1, playing = true, hasNext = false))
    }
}
