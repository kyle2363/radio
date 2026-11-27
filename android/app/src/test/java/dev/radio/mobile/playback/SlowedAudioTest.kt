package dev.radio.mobile.playback

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.abs
import kotlin.math.log2
import kotlin.math.sin
import kotlin.random.Random

class SlowedAudioTest {
    @Test fun presetTableMatchesThePlan() {
        assertEquals(listOf("really_slow", "slowed", "lofi"), SlowedPreset.entries.map { it.id })
        with(SlowedPreset.REALLY_SLOW) { assertEquals(0.75f, rate); assertEquals(0.60f, room); assertEquals(0.45f, damping); assertEquals(0.22f, wet) }
        with(SlowedPreset.SLOWED) { assertEquals(0.85f, rate); assertEquals(0.40f, room); assertEquals(0.50f, damping); assertEquals(0.12f, wet) }
        with(SlowedPreset.LOFI) { assertEquals(0.90f, rate); assertEquals(0.55f, room); assertEquals(0.60f, damping); assertEquals(0.20f, wet) }
        assertEquals(-2.8, SlowedPreset.SLOWED.semitones, 0.05)
        assertEquals(-5.0, SlowedPreset.REALLY_SLOW.semitones, 0.05)
        assertEquals(SlowedPreset.SLOWED, SlowedPreset.fromId("slowed"))
        assertNull(SlowedPreset.fromId("chipmunk"))
    }

    @Test fun effectMultipliesTheListenersSpeedAndSetsPitch() {
        val plain = SlowedPreset.parameters(1.10f, null)
        assertEquals(1.10f, plain.speed, 1e-6f); assertEquals(1f, plain.pitch, 1e-6f)
        val slowed = SlowedPreset.parameters(1.10f, SlowedPreset.SLOWED)
        assertEquals(0.935f, slowed.speed, 1e-4f); assertEquals(0.85f, slowed.pitch, 1e-6f)
        val tape = SlowedPreset.parameters(1f, SlowedPreset.REALLY_SLOW)
        assertEquals(tape.speed, tape.pitch, 0f)
    }

    @Test fun weightedPickRenormalizesOverAllowedPresets() {
        val counts = HashMap<SlowedPreset, Int>()
        val random = Random(7)
        repeat(20_000) { counts.merge(SlowedPreset.pick(SlowedPreset.entries.toSet(), random)!!, 1, Int::plus) }
        assertEquals(0.5, counts.getValue(SlowedPreset.SLOWED) / 20_000.0, 0.02)
        assertEquals(0.3, counts.getValue(SlowedPreset.LOFI) / 20_000.0, 0.02)
        assertEquals(0.2, counts.getValue(SlowedPreset.REALLY_SLOW) / 20_000.0, 0.02)
        val two = HashMap<SlowedPreset, Int>()
        repeat(10_000) { two.merge(SlowedPreset.pick(setOf(SlowedPreset.LOFI, SlowedPreset.REALLY_SLOW), random)!!, 1, Int::plus) }
        assertEquals(0.6, two.getValue(SlowedPreset.LOFI) / 10_000.0, 0.03)
        assertFalse(SlowedPreset.SLOWED in two)
        assertNull(SlowedPreset.pick(emptySet(), random))
    }

    @Test fun bypassedReverbIsBitExact() {
        val dsp = SlowedDsp(44_100, 2)
        val input = FloatArray(2048) { i -> sin(i * 0.05f) * 0.5f }
        val output = input.copyOf()
        dsp.process(output, output.size / 2, null)
        assertArrayEquals(input, output, 0f)
        assertFalse(dsp.ringing)
    }

    @Test fun reverbAddsADecayingTailWhoseLengthGrowsWithRoomSize() {
        fun rt60(preset: SlowedPreset): Double = rt60(impulse(preset, 44_100), 44_100)
        val slowed = rt60(SlowedPreset.SLOWED); val heavy = rt60(SlowedPreset.REALLY_SLOW)
        assertTrue("tail exists ($slowed)", slowed > 0.3)
        assertTrue("bigger room rings longer ($heavy vs $slowed)", heavy > slowed)
    }

    @Test fun reverbDelaysScaleWithTheRateSoTheSlowedTailMatchesTheDesktop() {
        // Reverb runs before the tape slow-down, so its delays are shortened by the rate: once the player slows the
        // audio, the room sounds the same size as the desktop's (which adds reverb after slowing).
        val plain = rt60(impulse(SlowedPreset.SLOWED, 44_100, rateScaled = false), 44_100)
        val scaled = rt60(impulse(SlowedPreset.SLOWED, 44_100), 44_100)
        assertEquals(plain * SlowedPreset.SLOWED.rate, scaled, plain * 0.08)
    }

    @Test fun limiterKeepsLoudInputUnderMinusOneDbfs() {
        val dsp = SlowedDsp(48_000, 2)
        val frames = 48_000
        val data = FloatArray(frames * 2) { i -> if ((i / 2) % 100 < 50) 1f else -1f }
        dsp.process(data, frames, SlowedPreset.REALLY_SLOW)
        val ceiling = SlowedDsp.LIMIT
        assertTrue(data.all { abs(it) <= ceiling + 1e-4f })
        assertEquals(0.891f, ceiling, 0.001f)
    }

    @Test fun quietMusicPassesTheLimiterUntouchedApartFromTheWetSignal() {
        val dsp = SlowedDsp(44_100, 1)
        val data = FloatArray(4410) { i -> sin(i * 0.1f) * 0.1f }
        val copy = data.copyOf()
        dsp.process(data, data.size, SlowedPreset.SLOWED)
        // Dry stays at unity and the reverb input is tiny, so the first samples (before any echo) are unchanged.
        for (i in 0 until 200) assertEquals(copy[i], data[i], 0.01f)
    }

    @Test fun turningTheEffectOffLetsTheTailRingOutForAtMostASecond() {
        val rate = 44_100
        val dsp = SlowedDsp(rate, 1)
        val loud = FloatArray(rate) { i -> sin(i * 0.2f) * 0.5f }
        dsp.process(loud, rate, SlowedPreset.REALLY_SLOW)
        val silence = FloatArray(rate * 2)
        dsp.process(silence, silence.size, null)
        val firstHalf = silence.copyOfRange(0, rate / 4).maxOf { abs(it) }
        assertTrue("tail audible at first ($firstHalf)", firstHalf > 1e-3f)
        assertTrue("silent after a second", silence.copyOfRange(rate, rate * 2).all { it == 0f })
        assertFalse(dsp.ringing)
    }

    private fun impulse(preset: SlowedPreset, rate: Int, rateScaled: Boolean = true): FloatArray {
        val dsp = SlowedDsp(rate, 1, scaleDelays = rateScaled)
        val data = FloatArray(rate * 4); data[0] = 0.5f
        // Long enough for the limiter release not to matter and with ring-out disabled by keeping the effect on.
        dsp.process(data, data.size, preset)
        data[0] = 0f
        return data
    }

    /** Schroeder backward integration; −5 to −35 dB fitted and extrapolated to 60 dB. */
    private fun rt60(signal: FloatArray, rate: Int): Double {
        val energy = DoubleArray(signal.size)
        var sum = 0.0
        for (i in signal.indices.reversed()) { sum += signal[i].toDouble() * signal[i]; energy[i] = sum }
        val total = energy[0]
        fun db(i: Int) = 10 * log2(energy[i] / total) / log2(10.0)
        val start = (signal.indices).first { db(it) <= -5 }
        val end = (signal.indices).first { db(it) <= -35 }
        return (end - start).toDouble() / rate * 2.0
    }

}
