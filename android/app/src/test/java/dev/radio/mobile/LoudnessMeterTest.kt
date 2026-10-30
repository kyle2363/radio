package dev.radio.mobile

import dev.radio.mobile.music.*
import org.junit.Assert.*
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin

class LoudnessMeterTest {
    private fun tone(rate: Int, channels: Int, amplitude: Double): Double {
        val meter = LoudnessMeter(rate, channels)
        repeat(rate * 3) { frame -> repeat(channels) { meter.sample(amplitude * sin(2 * PI * 997 * frame / rate)) } }
        return meter.result().integratedLufs!!
    }
    @Test fun referenceToneAndStereoChannelEnergyMatchBs1770Calibration() {
        assertEquals(-3.01, tone(48000, 1, 1.0), 0.10)
        val mono = tone(44100, 1, .1)
        assertEquals(3.0103, tone(44100, 2, .1) - mono, 0.001)
        assertEquals(mono, tone(32000, 1, .1), 0.12)
    }
    @Test fun silenceAndShortInputDoNotInventALoudnessMeasurement() {
        val silent = LoudnessMeter(48000, 1)
        repeat(48000) { silent.sample(0.0) }
        assertNull(silent.result().integratedLufs)
        assertEquals(0.0, silent.result().samplePeak, 0.0)
        assertTrue(runCatching { LoudnessMeter(48000, 1).result() }.isFailure)
    }
    @Test fun normalizationNeverBoostsOrUsesUnmeasuredAndInvalidValues() {
        assertEquals(.501187f, normalizationGain(-12.0), .00001f)
        listOf(null, -45.0, -100.0, Double.NaN, Double.POSITIVE_INFINITY).forEach { assertEquals(1f, normalizationGain(it)) }
        assertEquals(1f, normalizationGain(-12.0, Double.NaN))
    }
}
