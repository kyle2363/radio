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
    @Test fun unstartedAttemptHasNoTerminalEvent() {
        assertNull(ListeningAttempt("a", "s").finish(5000))
    }
}
