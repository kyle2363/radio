package dev.radio.mobile.benchmark

import androidx.benchmark.macro.junit4.BaselineProfileRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Generates the baseline profile from the same journeys. Run on a phone, then copy the generated
 * `*-baseline-prof.txt` from benchmark/build/outputs to app/src/main/baseline-prof.txt (see docs/TESTING.md).
 */
@RunWith(AndroidJUnit4::class)
class BaselineProfileGenerator {
    @get:Rule val rule = BaselineProfileRule()

    @Test fun generate() = rule.collect(PACKAGE, includeInStartupProfile = true) {
        pressHome(); startActivityAndWait()
        openLibrary(); scrollAllSongs(); openNowPlaying()
    }
}
