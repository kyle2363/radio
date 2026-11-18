package dev.radio.mobile

import dev.radio.mobile.playback.Crossfade
import org.junit.Assert.*
import org.junit.Test

class CrossfadeTest {
    private fun start(fadeMs: Long = 6000, position: Long = 175_000, duration: Long? = 180_000, hasNext: Boolean = true,
        repeatOne: Boolean = false, pauseAtEnd: Boolean = false, fading: Boolean = false) =
        Crossfade.shouldStart(fadeMs, position, duration, hasNext, repeatOne, pauseAtEnd, fading)

    @Test fun startsInTheLastSecondsBeforeAnAutomaticChange() {
        assertTrue(start())
        assertFalse(start(position = 170_000))
    }

    @Test fun neverStartsWhenItWouldBeWrong() {
        assertFalse("off", start(fadeMs = 0))
        assertFalse("unknown length", start(duration = null))
        assertFalse("end of queue", start(hasNext = false))
        assertFalse("repeat one", start(repeatOne = true))
        assertFalse("sleep at end of song", start(pauseAtEnd = true))
        assertFalse("already fading", start(fading = true))
        assertFalse("too close to the end to hand over", start(position = 179_900))
        assertFalse("song shorter than two fades", start(position = 7_000, duration = 10_000))
    }

    @Test fun equalPowerGainsMoveFromOutgoingToIncoming() {
        assertEquals(1f, Crossfade.outgoing(0f), 1e-4f); assertEquals(0f, Crossfade.incoming(0f), 1e-4f)
        assertEquals(0f, Crossfade.outgoing(1f), 1e-4f); assertEquals(1f, Crossfade.incoming(1f), 1e-4f)
        val mid = Crossfade.outgoing(.5f)
        assertEquals(1f, mid * mid + Crossfade.incoming(.5f) * Crossfade.incoming(.5f), 1e-4f)
        assertEquals(0f, Crossfade.outgoing(2f), 1e-4f)
    }
}
