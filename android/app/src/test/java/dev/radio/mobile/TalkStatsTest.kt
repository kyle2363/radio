package dev.radio.mobile

import dev.radio.mobile.data.LocalTalkEvent
import dev.radio.mobile.data.StatsPeriod
import dev.radio.mobile.data.TalkStats
import dev.radio.mobile.recommendation.TalkClipInfo
import dev.radio.mobile.recommendation.TalkGuestSet
import dev.radio.mobile.recommendation.TalkResource
import dev.radio.mobile.recommendation.TalkShift
import dev.radio.mobile.recommendation.TalkSpeaker
import java.time.LocalDateTime
import java.time.ZoneOffset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TalkStatsTest {
    private val zone = ZoneOffset.UTC
    private val now = LocalDateTime.of(2026, 11, 26, 12, 0).toInstant(zone).toEpochMilli()
    private val day = 86_400_000L

    private fun event(id: String, type: String, persona: String? = null, outcome: String = "played", heardMs: Long = 30_000,
                      at: Long = now - day, shift: String? = null) =
        LocalTalkEvent(id, type, persona?.lowercase(), persona, at - heardMs, at, heardMs, 30_000, outcome, shift)

    private fun info(type: String, speakers: List<TalkSpeaker>, shift: TalkShift? = null, guest: TalkGuestSet? = null) =
        TalkClipInfo("job-aaaaaaaaaaaaaaaa", "talk", type, "Title", speakers, emptyList(), 42_000, shift, guest,
            TalkResource("r", "0".repeat(64), 10, 0))

    @Test fun summaryCountsHeardTalkTopPeopleSkipsAndGuestChanges() {
        val events = listOf(
            event("1", "caller", "Mike"), event("2", "caller", "Mike", outcome = "skipped", heardMs = 5_000),
            event("3", "caller", "Ana"), event("4", "dj_monologue"),
            event("5", "guest_takeover_intro", "Nova", shift = "feel:Calm"), event("6", "guest_takeover_outro", "Nova"),
            event("7", "announcement"), event("8", "caller", "Ana", outcome = "discarded", heardMs = 0),
            event("old", "caller", "Old", at = now - 400 * day),
        )
        val s = TalkStats.summarize(events, StatsPeriod.Week, now, zone)
        assertEquals(5_000L + 6 * 30_000L, s.heardMs)
        assertEquals(7, s.clips)
        assertEquals(mapOf("Callers" to 3, "DJ" to 2, "Guest DJs" to 2), s.byType)
        assertEquals("Mike" to 2, s.topCaller)
        assertEquals("Nova" to 1, s.topGuest)
        assertEquals(1.0 / 7, s.skipRate!!, 1e-9)
        assertEquals(listOf("Calm"), s.guestChanges)
        assertEquals(8, TalkStats.summarize(events, StatsPeriod.All, now, zone).clips)
    }

    @Test fun emptyPeriodHasNoRates() {
        val s = TalkStats.summarize(emptyList(), StatsPeriod.Month, now, zone)
        assertEquals(0, s.clips); assertNull(s.skipRate); assertNull(s.topCaller); assertTrue(s.empty)
    }

    @Test fun eventFromAClipNamesTheOtherVoiceAndTheAppliedChange() {
        val caller = TalkStats.event(info("caller", listOf(TalkSpeaker("dj", "Sam"), TalkSpeaker("p1", "Mike"))), "played", 41_000, 100, 200, null)
        assertEquals("p1", caller.personaId); assertEquals("Mike", caller.personaName); assertEquals(42_000L, caller.clipMs)
        val guest = TalkStats.event(info("guest_takeover_intro", listOf(TalkSpeaker("g", "Nova")), guest = TalkGuestSet("g", "Nova", 4, false)),
            "skipped", 3_000, 100, 200, "mood:chill")
        assertEquals("Nova", guest.personaName); assertEquals("mood:chill", guest.shift)
        val dj = TalkStats.event(info("dj_monologue", listOf(TalkSpeaker("dj", "Sam"))), "played", 1, null, 200, null)
        assertNull(dj.personaName)
    }
}
