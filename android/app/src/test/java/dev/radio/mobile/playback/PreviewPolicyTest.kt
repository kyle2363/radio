package dev.radio.mobile.playback

import org.junit.Assert.*
import org.junit.Test

class PreviewPolicyTest {
    @Test fun clipStartsAt35PercentAndLasts15Seconds() {
        assertEquals(PreviewWindow(70_000, 85_000), PreviewPolicy.window(200_000))
    }
    @Test fun callerStartFractionIsHonouredAndClamped() {
        assertEquals(PreviewWindow(100_000, 115_000), PreviewPolicy.window(200_000, 0.5f))
        assertEquals(PreviewWindow(0, 15_000), PreviewPolicy.window(200_000, -1f))
        assertEquals(PreviewWindow(185_000, 200_000), PreviewPolicy.window(200_000, 1f))
    }
    @Test fun shortSongPlaysWholeAndUnknownDurationStartsAtZero() {
        assertEquals(PreviewWindow(0, 10_000), PreviewPolicy.window(10_000))
        assertEquals(PreviewWindow(0, 15_000), PreviewPolicy.window(null))
        assertEquals(PreviewWindow(0, 15_000), PreviewPolicy.window(0))
    }
    @Test fun exitResumesOnlyWhenMainWasPlaying() {
        val playing = PreviewSession(); playing.enter(mainPlaying = true)
        assertTrue(playing.exit())
        val paused = PreviewSession(); paused.enter(mainPlaying = false)
        assertFalse(paused.exit())
    }
    @Test fun secondEnterKeepsTheOriginalDecision() {
        val s = PreviewSession()
        assertTrue(s.enter(mainPlaying = true))   // caller must pause main
        assertFalse(s.enter(mainPlaying = false)) // already in preview: nothing to pause
        assertTrue(s.exit())
    }
    @Test fun exitWithoutEnterOrTwiceNeverResumes() {
        val s = PreviewSession()
        assertFalse(s.exit())
        s.enter(true); s.exit()
        assertFalse(s.exit())
    }
    @Test fun processLossForgetsTheDecision() {
        // A fresh session (new process) never resumes.
        assertFalse(PreviewSession().exit())
    }
}
