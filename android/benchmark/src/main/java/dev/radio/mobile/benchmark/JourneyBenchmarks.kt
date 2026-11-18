package dev.radio.mobile.benchmark

import androidx.benchmark.macro.CompilationMode
import androidx.benchmark.macro.FrameTimingMetric
import androidx.benchmark.macro.StartupMode
import androidx.benchmark.macro.StartupTimingMetric
import androidx.benchmark.macro.junit4.MacrobenchmarkRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Each journey runs twice: without ahead-of-time compilation (what a fresh install feels like today) and with the
 * baseline profile (what users get once it ships). The difference is the profile's benefit.
 */
@RunWith(AndroidJUnit4::class)
class JourneyBenchmarks {
    @get:Rule val rule = MacrobenchmarkRule()

    @Test fun coldStartNone() = coldStart(CompilationMode.None())
    @Test fun coldStartProfile() = coldStart(CompilationMode.Partial())
    @Test fun libraryScrollNone() = libraryScroll(CompilationMode.None())
    @Test fun libraryScrollProfile() = libraryScroll(CompilationMode.Partial())
    @Test fun nowPlayingNone() = nowPlaying(CompilationMode.None())
    @Test fun nowPlayingProfile() = nowPlaying(CompilationMode.Partial())

    private fun coldStart(mode: CompilationMode) = rule.measureRepeated(PACKAGE, listOf(StartupTimingMetric()), mode,
        iterations = 8, startupMode = StartupMode.COLD) { pressHome(); startActivityAndWait() }

    private fun libraryScroll(mode: CompilationMode) = rule.measureRepeated(PACKAGE, listOf(FrameTimingMetric()), mode,
        iterations = 5, startupMode = StartupMode.WARM, setupBlock = { pressHome(); startActivityAndWait() }) { openLibrary(); scrollAllSongs() }

    private fun nowPlaying(mode: CompilationMode) = rule.measureRepeated(PACKAGE, listOf(FrameTimingMetric()), mode,
        iterations = 5, startupMode = StartupMode.WARM, setupBlock = { pressHome(); startActivityAndWait() }) { openNowPlaying() }
}
