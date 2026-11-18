package dev.radio.mobile

import dev.radio.mobile.playback.PlaySource
import dev.radio.mobile.playback.PlayTrigger
import org.junit.Assert.assertEquals
import org.junit.Test

class PlayTriggerTest {
    @Test fun tapInTheAppIsChosen() = assertEquals(PlayTrigger.TAP, PlayTrigger.resolve(PlayTrigger.TAP, null, null))
    @Test fun naturalAdvanceStaysAutomaticEvenAfterAnOutsideCommand() {
        assertEquals(PlayTrigger.AUTO_ADVANCE, PlayTrigger.resolve(PlayTrigger.AUTO_ADVANCE, PlayTrigger.MEDIA_BUTTON, PlayTrigger.TAP))
        assertEquals(PlayTrigger.CROSSFADE, PlayTrigger.resolve(PlayTrigger.CROSSFADE, null, PlayTrigger.TAP))
    }
    @Test fun notificationNextIsAMediaButtonNotAnInAppNext() =
        assertEquals(PlayTrigger.MEDIA_BUTTON, PlayTrigger.resolve(PlayTrigger.NEXT, PlayTrigger.MEDIA_BUTTON, null))
    @Test fun widgetIsDistinct() = assertEquals(PlayTrigger.WIDGET, PlayTrigger.resolve(PlayTrigger.PREVIOUS, PlayTrigger.WIDGET, null))
    @Test fun restoredSongTakesTheCommandThatMadeItAudible() {
        assertEquals(PlayTrigger.MEDIA_BUTTON, PlayTrigger.resolve(null, null, PlayTrigger.MEDIA_BUTTON))
        assertEquals(PlayTrigger.TAP, PlayTrigger.resolve(null, null, PlayTrigger.TAP))
        assertEquals(PlayTrigger.AUTO_ADVANCE, PlayTrigger.resolve(null, null, null))
    }
    @Test fun labelsMapToKinds() {
        assertEquals(PlaySource.RADIO, PlaySource.of("Radio").kind)
        assertEquals(PlaySource.SEARCH, PlaySource.of("Search results").kind)
        assertEquals(PlaySource.QUEUE_EDIT, PlaySource.of("Custom queue").kind)
    }
}
