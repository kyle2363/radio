package dev.radio.mobile

import dev.radio.mobile.ui.PlayerBackdrop
import dev.radio.mobile.ui.shouldAnimateBackdrop
import org.junit.Assert.assertEquals
import org.junit.Test

class BackdropPolicyTest {
    @Test fun motionRequiresAnimatedModePlaybackForegroundAndEnabledAnimations() {
        for (mode in PlayerBackdrop.entries) for (playing in listOf(false, true)) {
            for (foreground in listOf(false, true)) for (enabled in listOf(false, true)) {
                assertEquals(mode == PlayerBackdrop.Animated && playing && foreground && enabled,
                    shouldAnimateBackdrop(mode, playing, foreground, enabled))
            }
        }
    }
}
