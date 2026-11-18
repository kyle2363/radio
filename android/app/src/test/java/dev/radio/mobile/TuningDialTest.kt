package dev.radio.mobile

import dev.radio.mobile.ui.stationFrequency
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TuningDialTest {
    @Test fun eachStationHasAStableFmFrequency() {
        for (mood in listOf("neutral", "chill", "energetic", "focus", "melancholy", "discovery", "feel:abc")) {
            val f = stationFrequency(mood)
            assertTrue("$mood → $f", f in 88.1f..107.9f)
            // FM channels in the Americas end in an odd tenth.
            assertEquals(1, Math.round(f * 10) % 2)
            assertEquals(f, stationFrequency(mood))
        }
    }

    @Test fun differentStationsUsuallyDiffer() {
        val all = listOf("neutral", "chill", "energetic", "focus", "melancholy", "discovery").map(::stationFrequency).toSet()
        assertTrue(all.size >= 5)
    }
}
