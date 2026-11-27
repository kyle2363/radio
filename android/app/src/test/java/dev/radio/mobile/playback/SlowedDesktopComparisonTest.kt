package dev.radio.mobile.playback

import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.SonicAudioProcessor
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Plan §4.4: the phone chain (reverb + limiter, then Sonic tape resample) against the desktop's pedalboard render of
 * the same source. Fixtures come from `scripts/generate_slowed_fixtures.py`; the test is skipped without them.
 */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class SlowedDesktopComparisonTest {
    private val dir = listOf(File("../../.test-runtime/android-fixtures/slowed"), File("../.test-runtime/android-fixtures/slowed")).firstOrNull { it.isDirectory }

    private class Wav(val rate: Int, val left: FloatArray)

    private fun read(file: File): Wav {
        val bytes = ByteBuffer.wrap(file.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        bytes.position(12)
        var rate = 0; var channels = 0; var bits = 0
        while (bytes.remaining() >= 8) {
            val id = ByteArray(4).also { bytes.get(it) }.toString(Charsets.US_ASCII)
            val size = bytes.int
            if (id == "fmt ") { val start = bytes.position(); bytes.short; channels = bytes.short.toInt(); rate = bytes.int; bytes.int; bytes.short; bits = bytes.short.toInt(); bytes.position(start + size) }
            else if (id == "data") {
                check(bits == 16) { "16-bit fixtures expected" }
                val frames = size / (2 * channels)
                val left = FloatArray(frames) { val v = bytes.getShort(bytes.position() + it * 2 * channels) / 32768f; v }
                return Wav(rate, left)
            } else bytes.position(bytes.position() + size + (size and 1))
        }
        error("No data chunk in $file")
    }

    /** The phone: SlowedDsp on the stereo source, then Sonic with speed = pitch = rate. Returns the left channel. */
    private fun phone(source: File, preset: SlowedPreset): Wav {
        val wav = ByteBuffer.wrap(source.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        val src = read(source)
        val frames = src.left.size
        val stereo = FloatArray(frames * 2) { src.left[it / 2] }
        // A second of silence lets the reverb tail and Sonic's buffer play out, like the next song would.
        val padded = stereo.copyOf(stereo.size + src.rate * 2)
        SlowedDsp(src.rate, 2).process(padded, padded.size / 2, preset)
        val sonic = SonicAudioProcessor().apply { setSpeed(preset.rate); setPitch(preset.rate) }
        sonic.configure(AudioProcessor.AudioFormat(src.rate, 2, C.ENCODING_PCM_16BIT))
        sonic.flush(AudioProcessor.StreamMetadata.DEFAULT)
        val input = ByteBuffer.allocateDirect(padded.size * 2).order(ByteOrder.nativeOrder())
        for (v in padded) input.putShort((v * 32767f).toInt().coerceIn(-32768, 32767).toShort())
        input.flip()
        val out = ArrayList<Float>()
        fun drain() { val o = sonic.output; while (o.remaining() >= 4) { out += o.short / 32768f; o.short } }
        while (input.hasRemaining()) {
            val chunk = input.slice().order(ByteOrder.nativeOrder()); chunk.limit(minOf(chunk.remaining(), 8192))
            val before = chunk.remaining(); sonic.queueInput(chunk); input.position(input.position() + before - chunk.remaining()); drain()
        }
        sonic.queueEndOfStream(); repeat(10) { drain() }
        check(wav.capacity() > 0)
        return Wav(src.rate, out.toFloatArray())
    }

    /** First sample after [fromS] where the signal rises above [threshold] (the click). */
    private fun onset(w: Wav, fromS: Double, threshold: Float = 0.2f) =
        (w.left.indices.drop((fromS * w.rate).toInt()).first { abs(w.left[it]) > threshold }).toDouble() / w.rate

    /** Frequency of the strongest component between 200 and 500 Hz over a steady part of the tone. */
    private fun pitch(w: Wav, startS: Double): Double {
        val n = 16384; val start = (startS * w.rate).toInt()
        val frame = DoubleArray(n) { w.left[start + it] * (0.5 - 0.5 * cos(2 * PI * it / (n - 1))) }
        var best = 0.0; var bestHz = 0.0
        var hz = 200.0
        while (hz <= 500.0) {
            var re = 0.0; var im = 0.0
            for (i in 0 until n) { val a = 2 * PI * hz * i / w.rate; re += frame[i] * cos(a); im += frame[i] * sin(a) }
            val mag = re * re + im * im
            if (mag > best) { best = mag; bestHz = hz }
            hz += 0.25
        }
        return bestHz
    }

    /** Seconds for the tail after the tone ends to fall 20 dB (from −5 to −25 dB of its RMS envelope), ×3 for RT60. */
    private fun decay(w: Wav, toneEndS: Double, clickS: Double): Double {
        val hop = w.rate / 100
        val start = (toneEndS * w.rate).toInt(); val end = (clickS * w.rate).toInt() - hop
        val env = (start until end step hop).map { i -> sqrt((i until i + hop).sumOf { w.left[it].toDouble() * w.left[it] } / hop) }
        val peak = env.take(5).max()
        val db = env.map { 20 * log10(maxOf(it, 1e-9) / peak) }
        val a = db.indexOfFirst { it <= -5 }; val b = db.indexOfFirst { it <= -25 }
        assertTrue("tail too short to measure", a >= 0 && b > a)
        return (b - a) / 100.0 * 3
    }

    @Test fun phoneMatchesTheDesktopRender() {
        assumeTrue("Run scripts/generate_slowed_fixtures.py first", dir != null)
        val source = File(dir, "source.wav")
        for (preset in SlowedPreset.entries) {
            val desktop = read(File(dir, "desktop_${preset.id}.wav"))
            val phone = phone(source, preset)
            val r = preset.rate.toDouble()
            // Duration: the click at 4 s lands at 4 / rate on both.
            val expectedClick = 4.0 / r
            assertEquals("${preset.id} desktop click", expectedClick, onset(desktop, 3.7 / r), 0.03)
            assertEquals("${preset.id} phone click", expectedClick, onset(phone, 3.7 / r), 0.03)
            // Pitch: 440 Hz × rate on both.
            assertEquals("${preset.id} desktop pitch", 440 * r, pitch(desktop, 0.3 / r), 440 * r * 0.015)
            assertEquals("${preset.id} phone pitch", 440 * r, pitch(phone, 0.3 / r), 440 * r * 0.015)
            // Reverb: the same decay after slowing, within 30%.
            val desktopDecay = decay(desktop, 2.0 / r, 4.0 / r); val phoneDecay = decay(phone, 2.0 / r, 4.0 / r)
            println("${preset.id}: decay desktop ${"%.2f".format(desktopDecay)} s, phone ${"%.2f".format(phoneDecay)} s")
            assertEquals("${preset.id} decay", desktopDecay, phoneDecay, desktopDecay * 0.3)
        }
    }
}
