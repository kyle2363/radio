package dev.radio.mobile

import dev.radio.mobile.data.UntrackedListening
import org.junit.Assert.*
import org.junit.Test

class UntrackedListeningTest {
    private var now = 1_000_000L
    private val minute = 60_000L
    private fun session() = UntrackedListening(store = null, now = { now })

    @Test fun offByDefault() = assertFalse(session().active())

    @Test fun staysOnWhilePlaying() {
        val s = session(); s.start()
        repeat(100) { now += minute; s.touch(playing = true) }
        assertTrue(s.active())
    }

    @Test fun endsAfterTwoHours() {
        val s = session(); s.start(); s.touch(true)
        now += UntrackedListening.MAX_MS - 1; s.touch(true); assertTrue(s.active())
        now += 2; assertFalse(s.active()); assertNull(s.state.value)
    }

    @Test fun endsAfterFifteenMinutesStopped() {
        val s = session(); s.start(); s.touch(true)
        now += 30 * minute; s.touch(false)
        now += UntrackedListening.IDLE_MS - 1; s.touch(false); assertTrue(s.active())
        now += 2; s.touch(false); assertFalse(s.active())
    }

    @Test fun pauseThenResumeInsideIdleKeepsItOn() {
        val s = session(); s.start()
        now += 10 * minute; s.touch(false)
        now += 4 * minute; s.touch(true)
        now += 10 * minute; s.touch(true)
        assertTrue(s.active())
    }

    @Test fun stopEndsImmediately() {
        val s = session(); s.start(); s.stop(); assertFalse(s.active())
    }
}
