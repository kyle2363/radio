package dev.radio.mobile

import dev.radio.mobile.data.SlowedEvents
import org.junit.Assert.*
import org.junit.Test

class SlowedEventsTest {
    private val skip = """{"protocol_version":1,"device_id":"d","event_id":"e","logical_clock":3,"kind":"song_skipped","occurred_at":1,"song_id":null,"local_id":"x","attempt_id":"a","completion":0.1,"reason":"just_skip","effect":"slowed"}"""

    @Test fun onlySkipsWithAnEffectAreIgnored() {
        assertTrue(SlowedEvents.isEffectSkip("song_skipped", skip))
        assertFalse(SlowedEvents.isEffectSkip("song_completed", skip.replace("song_skipped", "song_completed")))
        assertFalse(SlowedEvents.isEffectSkip("song_skipped", SlowedEvents.withoutEffect(skip)))
    }

    @Test fun olderDesktopsGetTheEventWithoutTheField() {
        val stripped = SlowedEvents.withoutEffect(skip)
        assertEquals(skip.replace(""","effect":"slowed"""", ""), stripped)
        val first = """{"effect":"really_slow","kind":"song_started"}"""
        assertEquals("""{"kind":"song_started"}""", SlowedEvents.withoutEffect(first))
        val plain = """{"kind":"song_started","reason":"effect"}"""
        assertSame(plain, SlowedEvents.withoutEffect(plain))
    }
}
