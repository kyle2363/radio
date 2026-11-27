package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.media3.common.PlaybackParameters
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.AudioSettings
import dev.radio.mobile.playback.LocalPlaybackController
import dev.radio.mobile.playback.SlowedPlan
import dev.radio.mobile.playback.SlowedPreset
import dev.radio.mobile.recommendation.*
import java.io.File
import java.io.RandomAccessFile
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Checkpoint 14A: the Slowed effect on the real Media3 timeline. */
class SlowedPlaybackTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var controller: LocalPlaybackController
    private lateinit var dir: File
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String) = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
        DocumentsContract.buildDocumentUriUsingTree(FixtureDocumentsProvider.tree(), id).toString(), "$id.mp3", "", 1, 1, 1, 8000)
    private fun await(message: String, condition: () -> Boolean) {
        val end = android.os.SystemClock.elapsedRealtime() + 15000
        while (android.os.SystemClock.elapsedRealtime() < end) {
            var ready = false; main { ready = condition() }
            if (ready) return
            Thread.sleep(25)
        }
        fail(message + ": " + parameters() + " " + controller.state.value)
    }
    private fun parameters(): PlaybackParameters {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) return controller.sessionPlayer.playbackParameters
        var value = PlaybackParameters.DEFAULT; main { value = controller.sessionPlayer.playbackParameters }; return value
    }
    private fun at(speed: Float, pitch: Float) = parameters().let { kotlin.math.abs(it.speed - speed) < 1e-3 && kotlin.math.abs(it.pitch - pitch) < 1e-3 }

    private fun clip(job: String = "job-aaaaaaaaaaaaaaaaaaaa", seconds: Int = 3): TalkClip {
        val rate = 16000
        val samples = rate * seconds
        val file = File(dir, "$job.wav")
        RandomAccessFile(file, "rw").use { out ->
            out.setLength(0)
            fun le32(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte())
            fun le16(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte())
            out.write("RIFF".toByteArray()); out.write(le32(36 + samples * 2)); out.write("WAVEfmt ".toByteArray())
            out.write(le32(16)); out.write(le16(1)); out.write(le16(1)); out.write(le32(rate)); out.write(le32(rate * 2)); out.write(le16(2)); out.write(le16(16))
            out.write("data".toByteArray()); out.write(le32(samples * 2))
            out.write(ByteArray(samples * 2) { i -> if (i % 2 == 0) (Math.sin(i / 2 * 2 * Math.PI * 220 / rate) * 6000).toInt().toByte() else 0 })
        }
        val info = TalkClipInfo(job, "talk", "guest_set", "Slowing down", listOf(TalkSpeaker("guest", "The Undertow")), listOf(TalkLine("guest", "Going under")),
            seconds * 1000L, null, null, TalkResource("resource-aaaaaaaaaaaaaaaa", "0".repeat(64), file.length().toInt(), 0))
        return TalkClip(info, file)
    }

    @Before fun setup() {
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "audio", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        dir = File(context.cacheDir, "slowed-test").apply { deleteRecursively(); mkdirs() }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        main {
            controller = LocalPlaybackController(context)
            controller.talkFileAllowed = { it.parentFile?.canonicalFile == dir.canonicalFile }
        }
    }
    @After fun cleanup() {
        main { controller.close() }; scenario.close()
        dir.deleteRecursively()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    private fun startRadio() {
        main { controller.beginRadio(listOf(song("long1"), song("long2"))) }
        await("Radio didn't start") { controller.state.value.isPlaying && controller.state.value.currentId == "long1" && controller.state.value.durationMs != null }
    }

    @Test fun effectAppliesToPlannedSongsAndComesOffWhenThePlanEnds() {
        startRadio()
        assertTrue(at(1f, 1f)); assertNull(controller.state.value.slowed)
        main { controller.setSlowed(SlowedPlan(SlowedPreset.SLOWED, setOf("long1"))) }
        await("Effect never applied") { at(0.85f, 0.85f) }
        assertEquals(SlowedPreset.SLOWED, controller.state.value.slowed)
        main { controller.setSlowed(SlowedPlan.OFF) }
        await("Effect never came off") { at(1f, 1f) }
        assertNull(controller.state.value.slowed)
    }

    @Test fun listenersSpeedIsMultipliedAndKeepsItsPitch() {
        startRadio()
        main { controller.applyAudio(AudioSettings(speed = 1.25f)) }
        await("User speed") { at(1.25f, 1f) }
        main { controller.setSlowed(SlowedPlan(SlowedPreset.REALLY_SLOW, setOf("long1", "long2"))) }
        await("Combined speed") { at(0.9375f, 0.75f) }
    }

    @Test fun speechInsideASetPlaysAtNormalSpeedAndTheNextSongIsSlowedAgain() {
        startRadio()
        main { controller.setSlowed(SlowedPlan(SlowedPreset.LOFI, setOf("long1", "long2"))) }
        await("Effect on the first song") { at(0.9f, 0.9f) }
        main { assertTrue(controller.insertTalk(clip(), "long1", "long2")) }
        main { controller.seek(controller.state.value.durationMs!! - 400) }
        await("Speech item at normal speed") { controller.state.value.talkPlaying && at(1f, 1f) }
        assertNull(controller.state.value.slowed)
        main { controller.seek(controller.state.value.durationMs!! - 300) }
        await("Next song slowed again") { controller.state.value.currentId == "long2" && at(0.9f, 0.9f) }
        assertEquals(SlowedPreset.LOFI, controller.state.value.slowed)
    }

    @Test fun songOutsideThePlanReturnsToNormalAtTheBoundary() {
        startRadio()
        main { controller.setSlowed(SlowedPlan(SlowedPreset.SLOWED, setOf("long1"))) }
        await("Effect on the first song") { at(0.85f, 0.85f) }
        main { controller.seek(controller.state.value.durationMs!! - 300) }
        await("Next song is normal") { controller.state.value.currentId == "long2" && at(1f, 1f) }
        assertNull(controller.state.value.slowed)
    }

    @Test fun crossfadeIntoASetSlowsTheIncomingSong() {
        startRadio()
        main { controller.applyAudio(AudioSettings(crossfadeSeconds = 2)); controller.setSlowed(SlowedPlan(SlowedPreset.SLOWED, setOf("long2"))) }
        assertTrue(at(1f, 1f))
        main { controller.seek(controller.state.value.durationMs!! - 3500) }
        await("Crossfade into the set") { controller.state.value.currentId == "long2" && at(0.85f, 0.85f) }
    }
}
