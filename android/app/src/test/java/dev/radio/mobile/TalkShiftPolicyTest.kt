package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class TalkShiftPolicyTest {
    private val moods = setOf("chill", "neutral", "energetic", "focus", "melancholy", "discovery")
    private val feels = setOf("feel-rainy")
    private val neutral = StationPick("neutral", null)

    private fun clip(type: String, shift: TalkShift? = null, guest: TalkGuestSet? = null) = TalkClipInfo(
        "job", "talk", type, "t", emptyList(), emptyList(), 0, shift, guest, TalkResource("r", "s", 1, 0))

    private val guestIntro = clip("guest_takeover_intro", TalkShift("energetic", null, true), TalkGuestSet("guest_hype", "Hype", 4, false))
    private val guestOutro = clip("guest_takeover_outro", guest = TalkGuestSet(null, null, null, true))

    @Test fun callerShiftAppliesAndIsNotReverted() {
        val result = TalkShiftPolicy.onStart(clip("caller", TalkShift("chill", null, false)), neutral, null, moods, feels)
        assertEquals(StationPick("chill", null), result.apply)
        assertNull(result.pending)
    }

    @Test fun clipWithoutShiftChangesNothing() {
        val result = TalkShiftPolicy.onStart(clip("dj_monologue"), neutral, null, moods, feels)
        assertNull(result.apply); assertNull(result.pending)
    }

    @Test fun unknownMoodOrFeelIsIgnored() {
        assertNull(TalkShiftPolicy.onStart(clip("caller", TalkShift("angry", null, false)), neutral, null, moods, feels).apply)
        val feel = TalkShiftPolicy.onStart(clip("caller", TalkShift(null, "feel-rainy", false)), neutral, null, moods, feels)
        assertEquals(StationPick("neutral", "feel-rainy"), feel.apply)
        assertNull(TalkShiftPolicy.onStart(clip("caller", TalkShift(null, "feel-gone", false)), neutral, null, moods, feels).apply)
    }

    @Test fun shiftToCurrentStationIsANoOp() {
        assertNull(TalkShiftPolicy.onStart(clip("caller", TalkShift("neutral", null, false)), neutral, null, moods, feels).apply)
    }

    @Test fun guestSetRevertsWhenOutroStarts() {
        val intro = TalkShiftPolicy.onStart(guestIntro, neutral, null, moods, feels)
        assertEquals(StationPick("energetic", null), intro.apply)
        val pending = intro.pending!!
        assertEquals(GuestRestore(neutral, StationPick("energetic", null)), pending)
        val outro = TalkShiftPolicy.onStart(guestOutro, StationPick("energetic", null), pending, moods, feels)
        assertEquals(neutral, outro.apply)
        assertNull(outro.pending)
    }

    @Test fun listenerChoiceDuringGuestSetWins() {
        val pending = GuestRestore(neutral, StationPick("energetic", null))
        val outro = TalkShiftPolicy.onStart(guestOutro, StationPick("focus", null), pending, moods, feels)
        assertNull(outro.apply); assertNull(outro.pending)
    }

    @Test fun missedOutroStillEndsTheSet() {
        val pending = GuestRestore(neutral, StationPick("energetic", null))
        assertEquals(neutral, TalkShiftPolicy.onUnplayed(guestOutro, StationPick("energetic", null), pending))
        assertNull(TalkShiftPolicy.onUnplayed(guestIntro, StationPick("energetic", null), pending))
        assertNull(TalkShiftPolicy.onUnplayed(guestOutro, StationPick("energetic", null), null))
    }

    @Test fun secondGuestKeepsTheOriginalStation() {
        val pending = GuestRestore(neutral, StationPick("energetic", null))
        val next = TalkShiftPolicy.onStart(clip("guest_takeover_intro", TalkShift("chill", null, true)), StationPick("energetic", null), pending, moods, feels)
        assertEquals(GuestRestore(neutral, StationPick("chill", null)), next.pending)
    }

    @Test fun restoreEncodesAndDecodes() {
        val value = GuestRestore(StationPick("neutral", "feel-rainy"), StationPick("energetic", null))
        assertEquals(value, GuestRestore.decode(value.encode()))
        assertNull(GuestRestore.decode(null)); assertNull(GuestRestore.decode("junk"))
    }
}
