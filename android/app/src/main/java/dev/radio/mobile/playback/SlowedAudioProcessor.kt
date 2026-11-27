package dev.radio.mobile.playback

import androidx.media3.common.C
import androidx.media3.common.Timeline
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import dev.radio.mobile.recommendation.TalkPolicy
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.roundToInt

/**
 * Which timeline items play Slowed (checkpoint 14). Only the listed song ids do; speech items never do, whatever is
 * listed, so an opener, closer or other talk inside a set always plays at normal speed without reverb.
 */
data class SlowedPlan(val preset: SlowedPreset? = null, val ids: Set<String> = emptySet()) {
    fun presetFor(mediaId: String?): SlowedPreset? =
        if (preset != null && mediaId != null && mediaId in ids && !TalkPolicy.isTalk(mediaId)) preset else null
    val active: Boolean get() = preset != null && ids.isNotEmpty()
    companion object { val OFF = SlowedPlan() }
}

/**
 * Reverb and limiter for Slowed songs, chosen from the PCM stream's own timeline item (like normalization), so it starts
 * and stops exactly at the song even when the next one is prebuffered. Runs after normalization (attenuation only).
 *
 * The sink flushes the processors at the first buffer of each new stream; [onStream] reports that item so the
 * controller can set the matching tape speed/pitch, which the sink applies from the next buffer it writes.
 */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
internal class SlowedAudioProcessor(private val onStream: (String) -> Unit = {}) : BaseAudioProcessor() {
    @Volatile var plan: SlowedPlan = SlowedPlan.OFF
    /** The item whose audio is entering the sink now (may be ahead of the player's current item). */
    @Volatile var streamMediaId: String = ""
        private set
    private var dsp: SlowedDsp? = null
    private var scratch = FloatArray(0)

    override fun onConfigure(input: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        if (input.encoding != C.ENCODING_PCM_16BIT && input.encoding != C.ENCODING_PCM_FLOAT) return AudioProcessor.AudioFormat.NOT_SET
        if (input.channelCount !in 1..2) return AudioProcessor.AudioFormat.NOT_SET
        return input
    }

    override fun onFlush(metadata: AudioProcessor.StreamMetadata) {
        // A flush also happens for a speed change or seek within the same song; the reverb keeps its state then.
        val format = inputAudioFormat
        if (dsp == null || format.sampleRate != dspRate || format.channelCount != dspChannels) {
            dsp = SlowedDsp(format.sampleRate, format.channelCount); dspRate = format.sampleRate; dspChannels = format.channelCount
        }
        var id = ""
        val uid = metadata.periodUid
        if (uid != null && metadata.timeline.getIndexOfPeriod(uid) != C.INDEX_UNSET) {
            val period = metadata.timeline.getPeriodByUid(uid, Timeline.Period())
            id = metadata.timeline.getWindow(period.windowIndex, Timeline.Window()).mediaItem.mediaId
        }
        if (id != streamMediaId) { streamMediaId = id; if (id.isNotEmpty()) onStream(id) }
    }
    private var dspRate = 0
    private var dspChannels = 0

    override fun queueInput(input: ByteBuffer) {
        if (!input.hasRemaining()) return
        val processor = dsp
        val preset = plan.presetFor(streamMediaId)
        if (processor == null || (preset == null && !processor.ringing)) {
            val output = replaceOutputBuffer(input.remaining()); output.put(input); output.flip(); return
        }
        input.order(ByteOrder.LITTLE_ENDIAN)
        val float = inputAudioFormat.encoding == C.ENCODING_PCM_FLOAT
        val samples = input.remaining() / if (float) 4 else 2
        if (scratch.size < samples) scratch = FloatArray(samples)
        if (float) for (i in 0 until samples) scratch[i] = input.float
        else for (i in 0 until samples) scratch[i] = input.short / 32768f
        processor.process(scratch, samples / inputAudioFormat.channelCount, preset)
        val output = replaceOutputBuffer(samples * if (float) 4 else 2)
        if (float) for (i in 0 until samples) output.putFloat(scratch[i])
        else for (i in 0 until samples) output.putShort((scratch[i] * 32768f).roundToInt().coerceIn(-32768, 32767).toShort())
        output.flip()
    }

    override fun onReset() { dsp = null; dspRate = 0; dspChannels = 0; streamMediaId = ""; scratch = FloatArray(0) }
}
