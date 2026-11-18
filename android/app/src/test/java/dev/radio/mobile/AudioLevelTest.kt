package dev.radio.mobile

import dev.radio.mobile.playback.AudioLevel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioLevelTest {
    @Test fun levelRisesFastAndFallsSlowly() {
        val up = AudioLevel.smooth(0f, 1f)
        val down = AudioLevel.smooth(1f, 0f)
        assertTrue(up >= 0.5f)
        assertTrue(1f - down < 0.1f)
    }

    @Test fun normalizedIsClampedToUnitRange() {
        assertEquals(0f, AudioLevel.normalized(0f))
        assertEquals(1f, AudioLevel.normalized(2f))
        assertEquals(0.5f, AudioLevel.normalized(0.25f), 1e-6f)
    }

    @Test fun silenceSettlesTowardZero() {
        var level = 1f
        repeat(100) { level = AudioLevel.smooth(level, 0f) }
        assertTrue(level < 0.01f)
    }
}
