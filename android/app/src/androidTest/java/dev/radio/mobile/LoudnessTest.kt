package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.exoplayer.source.SinglePeriodTimeline
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.music.*
import dev.radio.mobile.playback.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.junit.*
import org.junit.Assert.*
import java.nio.ByteBuffer
import java.nio.ByteOrder

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class LoudnessTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String) = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
        DocumentsContract.buildDocumentUriUsingTree(FixtureDocumentsProvider.tree(), id).toString(), "$id.mp3", "Music", 1000, 1, 32, 3000)
    private fun await(condition: () -> Boolean) {
        val end = android.os.SystemClock.elapsedRealtime() + 10000
        while (android.os.SystemClock.elapsedRealtime() < end) {
            var ready = false; main { ready = condition() }; if (ready) return; Thread.sleep(25)
        }
        fail("Loudness condition timed out")
    }
    @Before fun setup() {
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "reset", null, null)
        context.contentResolver.call(control, "loudness", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    @After fun cleanup() { runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) } }

    @Test fun decodedMp3MeasurementsMatchIndependentFfmpegReferences() = runBlocking {
        val reference = JSONArray(instrumentation.context.assets.open("loudness-reference.json").bufferedReader().use { it.readText() })
        val analyzer = LocalLoudnessAnalyzer(context.contentResolver)
        for (index in 0 until reference.length()) {
            val row = reference.getJSONObject(index)
            val measurement = analyzer.analyze(Uri.parse(song(row.getString("id")).uri))
            assertEquals(row.getString("id"), row.getDouble("lufs"), measurement.integratedLufs!!, 0.20)
            assertTrue(measurement.samplePeak in 0.0..1.0)
        }
        assertNull(analyzer.analyze(Uri.parse(song("long1").uri)).integratedLufs)
        assertTrue(runCatching { analyzer.analyze(Uri.parse(song("broken").uri)) }.isFailure)
        assertEquals(0, context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "stats", null, null)!!.getInt("writes"))
    }

    @Test fun processorUsesEachStreamIdentityAndPassesUnknownOrDisabledAudioUnchanged() {
        val processor = NormalizationAudioProcessor()
        processor.profile = NormalizationProfile(true, mapOf("one" to .25f, "two" to .5f))
        fun output(id: String, encoding: Int): Float {
            processor.configure(AudioProcessor.AudioFormat(48000, 1, encoding))
            val timeline = SinglePeriodTimeline(1000000L, true, false, false, null, MediaItem.Builder().setMediaId(id).build())
            processor.flush(AudioProcessor.StreamMetadata.Builder().setTimeline(timeline).setPeriodUid(timeline.getUidOfPeriod(0)).build())
            processor.queueInput(AudioProcessor.EMPTY_BUFFER)
            assertFalse(processor.output.hasRemaining())
            val input = ByteBuffer.allocateDirect(4).order(ByteOrder.nativeOrder())
            if (encoding == C.ENCODING_PCM_FLOAT) input.putFloat(.5f) else { input.putShort(16000); input.putShort(-16000) }
            input.flip(); processor.queueInput(input)
            val out = processor.output
            return if (encoding == C.ENCODING_PCM_FLOAT) out.float else out.short / 32000f
        }
        assertEquals(.125f, output("one", C.ENCODING_PCM_FLOAT))
        assertEquals(.25f, output("two", C.ENCODING_PCM_16BIT))
        assertEquals(.5f, output("unknown", C.ENCODING_PCM_16BIT))
        processor.profile = processor.profile.copy(enabled = false)
        assertEquals(.5f, output("one", C.ENCODING_PCM_FLOAT))
    }

    @Test fun realPlayerAppliesStreamGainIndependentlyOfSleepFadeAndKeepsActualStarts() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        lateinit var player: LocalPlaybackController
        var starts = 0
        main { player = LocalPlaybackController(context) { _, _, _ -> starts++ } }
        try {
            main {
                player.updateNormalization(NormalizationProfile(true, mapOf("level-loud" to .25f, "level-quiet" to .75f)))
                // Silent decoder fixtures exercise stream identity without audible test tones.
                player.playFrom(listOf(song("long1").copy(id = "level-loud"), song("long1").copy(id = "level-quiet")), "level-loud", "Normalization test")
            }
            await { player.state.value.isPlaying && player.normalizationProcessor.processedMediaId == "level-loud" }
            main {
                assertEquals(.25f, player.normalizationProcessor.appliedGain)
                assertEquals(1, starts)
                player.setSleepTimer(5000, true)
                assertTrue(player.sessionPlayer.volume < 1f)
                assertEquals(.25f, player.normalizationProcessor.appliedGain)
                player.cancelSleepTimer()
                assertEquals(1f, player.sessionPlayer.volume)
                player.next()
            }
            await { player.state.value.currentId == "level-quiet" && player.normalizationProcessor.processedMediaId == "level-quiet" && player.state.value.isPlaying }
            main { assertEquals(.75f, player.normalizationProcessor.appliedGain); assertEquals(2, starts); player.updateNormalization(NormalizationProfile()) }
            await { player.normalizationProcessor.appliedGain == 1f }
            main { assertEquals(2, starts) }
            // Also cover a compatible automatic transition, which can reuse the decoder.
            context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"), "audio", null, null)
            main {
                player.updateNormalization(NormalizationProfile(true, mapOf("short1" to .2f, "long2" to .6f)))
                player.playFrom(listOf(song("short1").copy(durationMs = 600), song("long2").copy(durationMs = 8000)), "short1", "Automatic normalization transition")
            }
            await { starts >= 3 }
            await { player.state.value.currentId == "long2" && player.state.value.isPlaying && player.normalizationProcessor.processedMediaId == "long2" }
            main { assertEquals(.6f, player.normalizationProcessor.appliedGain); assertEquals(4, starts) }
        } finally { main { player.close() }; scenario.close() }
    }

    @Test fun repositoryCachesReadOnlyMeasurementsAndInvalidatesChangedFiles() = runBlocking {
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val original = context.getSharedPreferences("radio_audio", 0).getBoolean("normalization", false)
        var library: LibraryRepository? = null
        try {
            val root = FixtureDocumentsProvider.tree().toString()
            db.library().saveRoot(LibraryRoot(uri = root, folderId = "root", name = "Music", scannedAt = 1000))
            val tracks = listOf(song("level-loud"), song("level-quiet"), song("long1"), song("broken"))
            db.library().saveSongs(tracks)
            val repo = LibraryRepository(context, db); library = repo
            await { repo.loudness.state.value.loaded }
            repo.loudness.setEnabled(true)
            repo.loudness.analyze()
            await { !repo.loudness.analysis.value.running && repo.loudness.analysis.value.completed == 4 }
            await { repo.loudness.state.value.measured == 3 && repo.loudness.state.value.failed == 1 }
            assertTrue(repo.loudness.state.value.profile.gain("level-loud") < .3f)
            assertEquals(1f, repo.loudness.state.value.profile.gain("level-quiet"))
            assertEquals(1f, repo.loudness.state.value.profile.gain("broken"))
            assertTrue(db.library().playbackStarts().isEmpty())
            val record = db.loudness().observe().first().first { it.songId == "level-loud" }
            assertTrue(record.matches(tracks.first(), 1000))
            assertFalse(record.matches(tracks.first().copy(modifiedAt = 2000), 2000))
            repo.loudness.analyze(setOf("level-loud"))
            assertEquals(0, repo.loudness.analysis.value.total)
        } finally { library?.loudness?.setEnabled(original); library?.close(); db.close() }
    }
    @Test fun cancellationClearsRunningStateAndAnalysisCanResumeWithoutPartialResults() = runBlocking {
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val root = FixtureDocumentsProvider.tree().toString()
            val catalog = MutableStateFlow(LibraryState(root = LibraryRoot(uri = root, folderId = "root", name = "Music", scannedAt = 1000),
                songs = List(100) { song("level-gated").copy(id = "cancel$it") }, loading = false))
            val repo = LoudnessRepository(context, db.loudness(), scope, catalog)
            await { repo.state.value.loaded }
            main { repo.analyze(); repo.pauseAnalysis() }
            await { !repo.analysis.value.running }
            assertTrue(db.loudness().observe().first().none { it.status == "failed" })
            // The cancelled decoder never commits a partial measurement as a success/failure.
            main { repo.analyze(setOf("cancel99")) }
            await { !repo.analysis.value.running && repo.analysis.value.completed == 1 }
            assertEquals("measured", db.loudness().observe().first().first { it.songId == "cancel99" }.status)
            assertTrue(db.library().playbackStarts().isEmpty())
        } finally { scope.coroutineContext[Job]?.cancelAndJoin(); db.close() }
    }
}
