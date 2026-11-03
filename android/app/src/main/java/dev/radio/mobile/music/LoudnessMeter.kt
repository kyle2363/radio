package dev.radio.mobile.music

import kotlin.math.*

const val LOUDNESS_ALGORITHM = "bs1770-stereo-v1"

data class LoudnessResult(val integratedLufs: Double?, val samplePeak: Double, val frames: Long)

/** Streaming mono/stereo integrated meter. BS.1770 K weighting, 400ms/100ms blocks,
 * absolute -70 LUFS gate and relative -10 LU gate. No retained PCM or true-peak claim.
 * Reference 48kHz coefficients: ITU-R BS.1770-5 Annex 1, tables 1 and 2.
 */
class LoudnessMeter(private val rate: Int, private val channels: Int) {
    init { require(rate in 8000..192000 && channels in 1..2) }
    private class Filter(coefficients: DoubleArray) {
        private val b = coefficients
        private var x1 = 0.0; private var x2 = 0.0; private var y1 = 0.0; private var y2 = 0.0
        fun step(x: Double): Double {
            val y = b[0] * x + b[1] * x1 + b[2] * x2 - b[3] * y1 - b[4] * y2
            x2 = x1; x1 = x; y2 = y1; y1 = y
            return y
        }
    }
    // Bilinear sample-rate conversion of the reference digital transfer function.
    // Substitute q_old=(a+q_new)/(1+a*q_new), then normalize the denominator.
    private fun coefficients(b0: Double, b1: Double, b2: Double, a1: Double, a2: Double): DoubleArray {
        val a = (48000.0 - rate) / (48000.0 + rate)
        fun transform(c0: Double, c1: Double, c2: Double) = doubleArrayOf(
            c0 + a * c1 + a * a * c2, 2 * a * (c0 + c2) + (1 + a * a) * c1,
            a * a * c0 + a * c1 + c2)
        val numerator = transform(b0, b1, b2); val denominator = transform(1.0, a1, a2)
        return doubleArrayOf(numerator[0], numerator[1], numerator[2], denominator[1], denominator[2]).map { it / denominator[0] }.toDoubleArray()
    }
    private val shelf = Array(channels) { Filter(coefficients(1.53512485958697, -2.69169618940638, 1.19839281085285, -1.69065929318241, 0.73248077421585)) }
    private val highPass = Array(channels) { Filter(coefficients(1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621)) }
    private val window = DoubleArray((rate * 0.4).roundToInt())
    private val hop = (rate * 0.1).roundToInt()
    private val blocks = ArrayList<Double>()
    private var index = 0; private var channel = 0; private var energy = 0.0; private var sum = 0.0
    private var frames = 0L; private var peak = 0.0

    fun sample(value: Double) {
        require(value.isFinite()) { "Invalid PCM sample" }
        peak = max(peak, abs(value))
        val weighted = highPass[channel].step(shelf[channel].step(value))
        energy += weighted * weighted
        if (++channel != channels) return
        channel = 0
        sum += energy - window[index]; window[index] = energy; energy = 0.0
        index = (index + 1) % window.size
        frames++
        if (frames >= window.size && (frames - window.size) % hop == 0L) blocks += max(0.0, sum / window.size)
    }
    fun result(): LoudnessResult {
        require(channel == 0 && frames >= window.size) { "Audio is too short for integrated loudness" }
        val absolute = 10.0.pow((-70.0 + 0.691) / 10.0)
        val eligible = blocks.filter { it > absolute }
        if (eligible.isEmpty()) return LoudnessResult(null, peak, frames)
        val relative = eligible.average() * 0.1
        val gated = eligible.filter { it > relative }
        val lufs = -0.691 + 10 * log10(gated.average())
        return LoudnessResult(lufs.takeIf { it.isFinite() }, peak, frames)
    }
}

/** Attenuation only: never boost quiet/silent/unknown tracks or use invalid measurements. */
fun normalizationGain(lufs: Double?, targetLufs: Double = -18.0): Float {
    if (lufs == null || !lufs.isFinite() || lufs !in -70.0..10.0 || !targetLufs.isFinite() || targetLufs !in -30.0..-10.0) return 1f
    return 10.0.pow((targetLufs - lufs).coerceIn(-60.0, 0.0) / 20.0).toFloat()
}
