package dev.radio.mobile.playback

import androidx.media3.common.C
import androidx.media3.common.Timeline
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import java.nio.ByteBuffer
import kotlin.math.roundToInt

data class NormalizationProfile(val enabled: Boolean = false, val gains: Map<String, Float> = emptyMap()) {
    fun gain(id: String) = if (enabled) gains[id]?.takeIf { it.isFinite() }?.coerceIn(0f, 1f) ?: 1f else 1f
}

/** Gain is selected from the PCM stream's timeline identity, not the UI's current track.
 * This also covers prebuffered/gapless streams. Sleep fading remains downstream volume.
 */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
internal class NormalizationAudioProcessor : BaseAudioProcessor() {
    @Volatile var profile = NormalizationProfile()
    @Volatile var processedMediaId: String = ""
        private set
    @Volatile var appliedGain = 1f
        private set
    private var mediaId = ""
    override fun onConfigure(input: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat =
        if (input.encoding == C.ENCODING_PCM_16BIT || input.encoding == C.ENCODING_PCM_FLOAT) input else AudioProcessor.AudioFormat.NOT_SET

    override fun onFlush(metadata: AudioProcessor.StreamMetadata) {
        mediaId = ""
        val uid = metadata.periodUid
        if (uid != null && metadata.timeline.getIndexOfPeriod(uid) != C.INDEX_UNSET) {
            val period = metadata.timeline.getPeriodByUid(uid, Timeline.Period())
            mediaId = metadata.timeline.getWindow(period.windowIndex, Timeline.Window()).mediaItem.mediaId
        }
    }
    override fun queueInput(input: ByteBuffer) {
        // The pipeline may drain us with its shared EMPTY_BUFFER. Replacing a zero-size
        // output buffer would alias that same object, so it must never be copied to itself.
        if (!input.hasRemaining()) return
        val gain = profile.gain(mediaId)
        processedMediaId = mediaId; appliedGain = gain
        val output = replaceOutputBuffer(input.remaining())
        if (gain == 1f) output.put(input)
        else if (inputAudioFormat.encoding == C.ENCODING_PCM_FLOAT) {
            while (input.remaining() >= 4) output.putFloat(input.float * gain)
        } else {
            while (input.remaining() >= 2) output.putShort((input.short * gain).roundToInt().coerceIn(-32768, 32767).toShort())
        }
        output.flip()
    }
    override fun onReset() { mediaId = ""; processedMediaId = ""; appliedGain = 1f }
}
