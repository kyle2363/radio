package dev.radio.mobile

import dev.radio.mobile.playback.ListeningAttempt
import org.junit.Assert.*
import org.junit.Test

class ListeningAttemptTest {
    @Test fun onlyAudibleTimeCountsAndPauseSeekCannotCreateStarts() {
        val attempt = ListeningAttempt("attempt", "song")
        assertTrue(attempt.observe(100, true))
        assertFalse(attempt.observe(1100, true))
        attempt.observe(1500, false)
        attempt.observe(9500, false)
        assertFalse(attempt.observe(10000, true))
        assertEquals(1900L, attempt.finish(10500))
        assertNull(attempt.finish(11000))
    }
    @Test fun fasterPlaybackCountsMoreOfTheSong() {
        // Ten seconds at 1.5x covers fifteen seconds of the song, so a finished song still reads as finished.
        val attempt = ListeningAttempt("attempt", "song")
        attempt.observe(0, true, rate = 1.5f)
        attempt.observe(10_000, true, rate = 1f)
        assertEquals(19_000L, attempt.finish(14_000))
    }
    @Test fun unstartedAttemptHasNoTerminalEvent() {
        assertNull(ListeningAttempt("a", "s").finish(5000))
    }
}
