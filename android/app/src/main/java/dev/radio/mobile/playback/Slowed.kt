package dev.radio.mobile.playback

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.roundToInt
import kotlin.random.Random

/**
 * Slowed Radio presets (checkpoint 14). Tape style: speed and pitch move together, so Media3's Sonic only resamples.
 * The same names and numbers are seeded on the desktop (`slowed_presets`) so both sides agree.
 */
enum class SlowedPreset(val id: String, val label: String, val rate: Float, val room: Float, val damping: Float, val wet: Float, val weight: Int,
    /** How the effect is described to the desktop's writer (plan §13.2). */
    val description: String) {
    REALLY_SLOW("really_slow", "Really slow", 0.75f, 0.60f, 0.45f, 0.22f, 20, "way down, deep and heavy, with lots of space and echo"),
    SLOWED("slowed", "Slowed", 0.85f, 0.40f, 0.50f, 0.12f, 50, "a little slower and lower, with a soft echo"),
    LOFI("lofi", "Lo-fi", 0.90f, 0.55f, 0.60f, 0.20f, 30, "just a touch slower, warm and hazy");

    val semitones: Double get() = 12 * ln(rate.toDouble()) / ln(2.0)

    data class Parameters(val speed: Float, val pitch: Float)

    companion object {
        fun fromId(id: String?): SlowedPreset? = entries.firstOrNull { it.id == id }

        /** The listener's own speed keeps pitch; the effect is tape style on top: speed = user × rate, pitch = rate. */
        fun parameters(userSpeed: Float, preset: SlowedPreset?): Parameters =
            if (preset == null) Parameters(userSpeed, 1f) else Parameters(userSpeed * preset.rate, preset.rate)

        /** Weighted Slowed 50 / Lo-fi 30 / Really slow 20, renormalized over [allowed]; null when none are allowed. */
        fun pick(allowed: Set<SlowedPreset>, random: Random = Random.Default): SlowedPreset? {
            val choices = entries.filter { it in allowed }
            if (choices.isEmpty()) return null
            var roll = random.nextInt(choices.sumOf { it.weight })
            for (preset in choices) { roll -= preset.weight; if (roll < 0) return preset }
            return choices.last()
        }
    }
}

/**
 * Reverb and limiter for Slowed songs, on interleaved float samples. The reverb is JUCE's Freeverb (what the desktop's
 * pedalboard `Reverb` runs) with the same room/damping/wet settings; dry stays at unity instead of JUCE's ×2 and the wet
 * level keeps the desktop's wet-to-dry ratio, so the phone never needs make-up gain. A −1 dBFS peak limiter follows.
 *
 * The reverb runs before the player's tape slow-down, so with [scaleDelays] its delay lines are shortened by the rate:
 * once slowed, the room has the same size and decay as the desktop's, which adds reverb after slowing.
 *
 * With no preset and no tail left it touches nothing (bit-exact bypass). Turning the effect off lets the tail ring out
 * over at most [RING_OUT_SECONDS], fading, instead of cutting.
 */
class SlowedDsp(private val sampleRate: Int, private val channels: Int, private val scaleDelays: Boolean = true) {
    private inner class Comb(val buffer: FloatArray) {
        var size = buffer.size; var index = 0; var last = 0f
        fun process(input: Float, feedback: Float, damp1: Float, damp2: Float): Float {
            val output = buffer[index]
            last = output * damp2 + last * damp1
            buffer[index] = input + last * feedback
            if (++index >= size) index = 0
            return output
        }
        fun clear() { buffer.fill(0f); index = 0; last = 0f }
    }
    private inner class AllPass(val buffer: FloatArray) {
        var size = buffer.size; var index = 0
        fun process(input: Float): Float {
            val buffered = buffer[index]
            buffer[index] = input + buffered * 0.5f
            if (++index >= size) index = 0
            return buffered - input
        }
        fun clear() { buffer.fill(0f); index = 0 }
    }

    private val scale = sampleRate / 44_100.0
    private val combs = Array(2) { side -> Array(COMBS.size) { Comb(FloatArray(((COMBS[it] + side * SPREAD) * scale).roundToInt().coerceAtLeast(1))) } }
    private val allPasses = Array(2) { side -> Array(ALL_PASSES.size) { AllPass(FloatArray(((ALL_PASSES[it] + side * SPREAD) * scale).roundToInt().coerceAtLeast(1))) } }
    private var sizedFor = -1f
    private var current: SlowedPreset? = null
    private var ringFrames = 0
    private var limiterGain = 1f
    private val release = exp(-1.0 / (0.05 * sampleRate)).toFloat()
    /** True while a tail is still fading out after the effect was turned off. */
    val ringing: Boolean get() = ringFrames > 0

    /** Processes [frames] interleaved frames of [data] in place. */
    fun process(data: FloatArray, frames: Int, preset: SlowedPreset?) {
        if (preset != null) {
            if (current == null && ringFrames == 0) clear()
            current = preset; ringFrames = 0
            resize(preset.rate)
        } else if (current != null) {
            ringFrames = (RING_OUT_SECONDS * sampleRate).toInt()
        }
        val active = current ?: return
        val feedback = active.room * 0.28f + 0.7f
        val damp1 = active.damping * 0.4f; val damp2 = 1 - damp1
        val wet = active.wet * WET_SCALE
        val totalRing = (RING_OUT_SECONDS * sampleRate).toInt()
        for (frame in 0 until frames) {
            val base = frame * channels
            val ringGain = if (preset == null) {
                if (ringFrames <= 0) { finishRing(); return }
                (ringFrames--).toFloat() / totalRing
            } else 1f
            val left = data[base]; val right = if (channels > 1) data[base + 1] else left
            val input = if (preset == null) 0f else if (channels > 1) (left + right) * GAIN else left * GAIN
            var outL = 0f; var outR = 0f
            for (comb in combs[0]) outL += comb.process(input, feedback, damp1, damp2)
            for (pass in allPasses[0]) outL = pass.process(outL)
            if (channels > 1) {
                for (comb in combs[1]) outR += comb.process(input, feedback, damp1, damp2)
                for (pass in allPasses[1]) outR = pass.process(outR)
            }
            // JUCE: width 1 gives wet1 = wet, wet2 = 0, so each side keeps its own decorrelated tail.
            data[base] = left + outL * wet * ringGain
            if (channels > 1) data[base + 1] = right + outR * wet * ringGain
            limitFrame(data, base)
        }
        if (preset == null && ringFrames <= 0) finishRing()
    }

    /** Instant attack (so the ceiling always holds), 50 ms release, linked across channels. */
    private fun limitFrame(data: FloatArray, base: Int) {
        var peak = 0f
        for (c in 0 until channels) peak = maxOf(peak, abs(data[base + c]))
        val needed = if (peak * limiterGain > LIMIT) LIMIT / peak else 1f
        limiterGain = if (needed < limiterGain) needed else minOf(1f, needed, 1f - (1f - limiterGain) * release)
        if (limiterGain < 1f) for (c in 0 until channels) data[base + c] *= limiterGain
    }

    private fun finishRing() { ringFrames = 0; current = null; limiterGain = 1f; clear() }

    private fun resize(rate: Float) {
        val factor = if (scaleDelays) rate else 1f
        if (factor == sizedFor) return
        sizedFor = factor
        for (side in 0..1) {
            combs[side].forEachIndexed { i, comb -> comb.size = ((COMBS[i] + side * SPREAD) * scale * factor).roundToInt().coerceIn(1, comb.buffer.size); comb.index %= comb.size }
            allPasses[side].forEachIndexed { i, pass -> pass.size = ((ALL_PASSES[i] + side * SPREAD) * scale * factor).roundToInt().coerceIn(1, pass.buffer.size); pass.index %= pass.size }
        }
    }

    fun clear() { combs.forEach { side -> side.forEach { it.clear() } }; allPasses.forEach { side -> side.forEach { it.clear() } }; limiterGain = 1f }

    companion object {
        /** −1 dBFS. */
        const val LIMIT = 0.8912509f
        const val RING_OUT_SECONDS = 1.0
        private val COMBS = intArrayOf(1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
        private val ALL_PASSES = intArrayOf(556, 441, 341, 225)
        private const val SPREAD = 23
        private const val GAIN = 0.015f
        /** JUCE's wet ×3 over its dry ×2, with dry at unity here. */
        private const val WET_SCALE = 1.5f
    }
}
