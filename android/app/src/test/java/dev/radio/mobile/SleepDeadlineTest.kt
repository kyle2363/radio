package dev.radio.mobile

import dev.radio.mobile.playback.SleepDeadline
import org.junit.Assert.*
import org.junit.Test

class SleepDeadlineTest {
    @Test fun monotonicDeadlineFadesOnlyInFinalTenSecondsAndNeverProducesNegativeGain() {
        val timer = SleepDeadline(30000, true)
        assertEquals(30000L, timer.remaining(0))
        assertEquals(1f, timer.gain(19000))
        assertEquals(0.5f, timer.gain(25000))
        assertEquals(0f, timer.gain(30001))
        assertEquals(0L, timer.remaining(90000))
        assertEquals(1f, timer.copy(fade = false).gain(29999))
    }
}
