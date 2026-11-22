package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.LocalPlaybackController
import dev.radio.mobile.playback.snapshot
import dev.radio.mobile.recommendation.*
import java.io.File
import java.io.RandomAccessFile
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Checkpoint 13B: a connected-talk clip in the real Media3 timeline. */
class TalkPlaybackTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var scenario: ActivityScenario<MainActivity>
    private lateinit var controller: LocalPlaybackController
    private val starts = mutableListOf<String>()
    private val finished = mutableListOf<String>()
    private val events = mutableListOf<LocalPlaybackController.TalkEvent>()
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
        fail(message + ": " + controller.state.value)
    }

    /** A 3 s 16-bit mono WAV standing in for a downloaded clip. */
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
            val body = ByteArray(samples * 2)
            for (i in 0 until samples) { val v = (Math.sin(i * 2 * Math.PI * 220 / rate) * 6000).toInt(); body[2 * i] = v.toByte(); body[2 * i + 1] = (v shr 8).toByte() }
            out.write(body)
        }
        val info = TalkClipInfo(job, "talk", "caller", "Mike on the line", listOf(TalkSpeaker("dj", "DJ")), listOf(TalkLine("dj", "Hello")),
            seconds * 1000L, null, null, TalkResource("resource-aaaaaaaaaaaaaaaa", "0".repeat(64), file.length().toInt(), 0))
        return TalkClip(info, file)
    }

    @Before fun setup() {
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "audio", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        dir = File(context.cacheDir, "talk-test").apply { deleteRecursively(); mkdirs() }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        main {
            controller = LocalPlaybackController(context, onAudibleStart = { track, _, _ -> starts += track.id },
                onAttemptFinished = { songId, _, _, _, _, _ -> finished += songId })
            controller.talkFileAllowed = { it.parentFile?.canonicalFile == dir.canonicalFile }
            controller.onTalk = { events += it }
        }
    }
    @After fun cleanup() {
        main { controller.close() }; scenario.close()
        dir.deleteRecursively()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    private fun startRadioWithTalk(talk: TalkClip = clip()): TalkClip {
        main { controller.beginRadio(listOf(song("long1"), song("long2"))) }
        await("Radio didn't start") { controller.state.value.isPlaying && controller.state.value.currentId == "long1" && controller.state.value.durationMs != null }
        main { assertTrue(controller.insertTalk(talk, "long1", "long2")) }
        val state = controller.state.value
        assertEquals(listOf("long1", "long2"), state.playOrder)   // talk is not a queue song
        assertEquals("long2", state.talk!!.nextId)
        assertFalse(state.talk!!.current)
        return talk
    }
    private fun playIntoTalk() {
        main { controller.seek(controller.state.value.durationMs!! - 400) }
        await("Talk never played") { controller.state.value.talkPlaying && controller.state.value.isPlaying }
        await("Talk start not reported") { events.any { it is LocalPlaybackController.TalkEvent.Started } }
    }
    private fun outcome() = (events.last() as LocalPlaybackController.TalkEvent.Finished).outcome

    @Test fun clipPlaysBetweenSongsAndIsNeverJournaled() {
        startRadioWithTalk()
        playIntoTalk()
        assertNull(controller.state.value.current)
        main { controller.seek(controller.state.value.durationMs!! - 300) }
        await("Next song didn't follow the clip") { controller.state.value.currentId == "long2" && controller.state.value.isPlaying }
        await("Clip outcome missing") { events.lastOrNull() is LocalPlaybackController.TalkEvent.Finished }
        assertEquals("played", outcome())
        assertNull(controller.state.value.talk)
        await("Second song start") { starts.contains("long2") }
        assertEquals(listOf("long1", "long2"), starts)
        assertTrue(finished.none { TalkPolicy.isTalk(it) })
    }

    @Test fun skipEndsTheClipAndGoesToTheNextSong() {
        startRadioWithTalk(); playIntoTalk()
        main { controller.next() }
        await("Skip didn't reach the next song") { controller.state.value.currentId == "long2" }
        await("Skip not reported") { events.lastOrNull() is LocalPlaybackController.TalkEvent.Finished }
        assertEquals("skipped", outcome())
        assertTrue((events.last() as LocalPlaybackController.TalkEvent.Finished).heardMs >= 0)
    }

    @Test fun previousReturnsToTheSongBeforeAndNeverReplaysTheClip() {
        startRadioWithTalk(); playIntoTalk()
        main { controller.previous() }
        await("Previous didn't return to the first song") { controller.state.value.currentId == "long1" }
        await("Clip should have left the timeline") { controller.state.value.talk == null }
        main { controller.seek(controller.state.value.durationMs!! - 300) }
        await("Should go straight to the next song") { controller.state.value.currentId == "long2" }
        assertEquals(1, events.count { it is LocalPlaybackController.TalkEvent.Started })
    }

    @Test fun skippingOverAQueuedClipDiscardsIt() {
        startRadioWithTalk()
        main { controller.next() }
        await("Next song") { controller.state.value.currentId == "long2" }
        await("Discard not reported") { events.lastOrNull() is LocalPlaybackController.TalkEvent.Finished }
        assertEquals("discarded", outcome())
        assertTrue(events.none { it is LocalPlaybackController.TalkEvent.Started })
    }

    @Test fun removingTheQueueRowDiscardsIt() {
        val talk = startRadioWithTalk()
        main { controller.removeFromQueue(talk.mediaId) }
        await("Removed") { controller.state.value.talk == null }
        assertEquals("discarded", outcome())
        assertEquals("long1", controller.state.value.currentId)
    }

    @Test fun bookmarkDuringTalkIsTheNextSongFromItsStart() {
        startRadioWithTalk(); playIntoTalk()
        val saved = controller.state.value.snapshot()!!
        assertEquals("long2", saved.currentId)
        assertEquals(0L, saved.positionMs)
    }

    @Test fun talkGetsTalkVolumeNotSongNormalization() {
        main { controller.setTalkGain(TalkVolume.QUIETER.linear) }
        val talk = startRadioWithTalk(); playIntoTalk()
        await("Talk audio not processed") { controller.normalizationProcessor.processedMediaId == talk.mediaId }
        assertEquals(TalkVolume.QUIETER.linear, controller.normalizationProcessor.appliedGain, 1e-4f)
    }

    @Test fun insertionIsRefusedWhenUnsafe() {
        main { controller.beginRadio(listOf(song("long1"), song("long2"))) }
        await("Radio didn't start") { controller.state.value.isPlaying && controller.state.value.durationMs != null }
        main {
            assertFalse(controller.insertTalk(clip(), "long2", "long1"))               // not the current song
            assertFalse(controller.insertTalk(clip(), "long1", "short1"))              // not the planned next song
            val outside = clip().copy(file = File(context.cacheDir, "outside.wav").also { clip().file.copyTo(it, true) })
            assertFalse(controller.insertTalk(outside, "long1", "long2"))              // not from the private cache
            controller.shuffle(true)
            assertFalse(controller.insertTalk(clip(), "long1", "long2"))               // shuffle on
            controller.shuffle(false)
            controller.seek(controller.state.value.durationMs!! - 1000)
            assertFalse(controller.insertTalk(clip(), "long1", "long2"))               // inside the insertion margin
        }
        assertNull(controller.state.value.talk)
    }

    @Test fun stopAtEndOfSongPlaysTheTrailingClipThenRestsOnTheNextSong() {
        startRadioWithTalk()
        main { controller.sleepAtTrackEnd() }
        playIntoTalk()
        main { controller.seek(controller.state.value.durationMs!! - 300) }
        await("Should pause on the next song") { controller.state.value.currentId == "long2" && !controller.state.value.playWhenReady }
        await("Clip outcome missing") { events.lastOrNull() is LocalPlaybackController.TalkEvent.Finished }
        assertEquals("played", outcome())
        assertFalse(controller.state.value.sleepTimer.active)
        assertFalse(starts.contains("long2"))
    }
}
