package dev.radio.mobile

import dev.radio.mobile.ui.countUp
import org.junit.Assert.assertEquals
import org.junit.Test

class WrappedCountUpTest {
    @Test fun countsTheFirstNumberUpAndKeepsTheRest() {
        assertEquals("0 h 13 m", countUp("2 h 13 m", 0f))
        assertEquals("1 h 13 m", countUp("2 h 13 m", .5f))
        assertEquals("2 h 13 m", countUp("2 h 13 m", 1f))
        assertEquals("Variety 43", countUp("Variety 85", .5f))
        assertEquals("1,000", countUp("1,000", 1f))
        assertEquals("500", countUp("1,000", .5f).replace(",", ""))
        assertEquals("Night owl", countUp("Night owl", .3f))
    }
}
