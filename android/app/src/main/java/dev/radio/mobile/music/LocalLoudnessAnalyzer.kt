package dev.radio.mobile.music

import android.content.ContentResolver
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import java.nio.ByteOrder

/** Decoder-only, read-only measurement: no AudioTrack, playback session, or file output. */
class LocalLoudnessAnalyzer(private val resolver: ContentResolver) {
    suspend fun analyze(uri: Uri, progress: (Long) -> Unit = {}): LoudnessResult {
        require(uri.scheme == "content")
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null
        try {
            resolver.openAssetFileDescriptor(uri, "r")?.use { descriptor ->
                if (descriptor.declaredLength >= 0) extractor.setDataSource(descriptor.fileDescriptor, descriptor.startOffset, descriptor.declaredLength)
                else extractor.setDataSource(descriptor.fileDescriptor)
            } ?: error("File is unavailable")
            val track = (0 until extractor.trackCount).firstOrNull { extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true }
                ?: error("No audio track")
            extractor.selectTrack(track)
            val input = extractor.getTrackFormat(track)
            val decoder = MediaCodec.createDecoderByType(input.getString(MediaFormat.KEY_MIME)!!)
            codec = decoder
            decoder.configure(input, null, null, 0); decoder.start()
            var meter: LoudnessMeter? = null
            var encoding = AudioFormat.ENCODING_PCM_16BIT
            var rate = 0; var channels = 0
            var inputEnded = false; var outputEnded = false
            val info = MediaCodec.BufferInfo()
            var activityAt = android.os.SystemClock.elapsedRealtime()
            var lastProgress = 0L
            while (!outputEnded) {
                currentCoroutineContext().ensureActive()
                if (android.os.SystemClock.elapsedRealtime() - activityAt > 10000) error("Decoder stalled")
                if (!inputEnded) {
                    val index = decoder.dequeueInputBuffer(1000)
                    if (index >= 0) {
                        val buffer = checkNotNull(decoder.getInputBuffer(index)); buffer.clear()
                        val count = extractor.readSampleData(buffer, 0)
                        if (count < 0) {
                            decoder.queueInputBuffer(index, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputEnded = true
                        } else {
                            decoder.queueInputBuffer(index, 0, count, extractor.sampleTime, 0); extractor.advance()
                        }
                        activityAt = android.os.SystemClock.elapsedRealtime()
                    }
                }
                val index = decoder.dequeueOutputBuffer(info, 1000)
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    val format = decoder.outputFormat
                    val newRate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE); val newChannels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                    check(meter == null || rate == newRate && channels == newChannels) { "Audio format changed" }
                    rate = newRate; channels = newChannels
                    encoding = if (format.containsKey(MediaFormat.KEY_PCM_ENCODING)) format.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                    check(encoding == AudioFormat.ENCODING_PCM_16BIT || encoding == AudioFormat.ENCODING_PCM_FLOAT) { "Unsupported PCM format" }
                    if (meter == null) meter = LoudnessMeter(rate, channels)
                } else if (index >= 0) {
                    try {
                        if (info.size > 0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                            val output = checkNotNull(decoder.getOutputBuffer(index)).order(ByteOrder.nativeOrder())
                            output.position(info.offset); output.limit(info.offset + info.size)
                            val measurement = checkNotNull(meter)
                            val bytes = if (encoding == AudioFormat.ENCODING_PCM_FLOAT) 4 else 2
                            check(output.remaining() % bytes == 0)
                            while (output.hasRemaining()) measurement.sample(if (bytes == 4) output.float.toDouble() else output.short / 32768.0)
                            check(info.presentationTimeUs <= 6L * 60 * 60 * 1000000) { "Audio exceeds the analysis limit" }
                            if (info.presentationTimeUs - lastProgress >= 1000000) { lastProgress = info.presentationTimeUs; progress(lastProgress / 1000) }
                        }
                        outputEnded = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                        activityAt = android.os.SystemClock.elapsedRealtime()
                    } finally { decoder.releaseOutputBuffer(index, false) }
                }
            }
            return checkNotNull(meter).result()
        } finally {
            codec?.let { runCatching { it.stop() }; it.release() }
            extractor.release()
        }
    }
}
