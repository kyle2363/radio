package dev.radio.mobile

import dev.radio.mobile.playback.SlowedPreset
import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test
import kotlin.random.Random

class SlowedPolicyTest {
    private val on = SlowedSettings(enabled = true)

    @Test fun settingsRoundTripAndDefaults() {
        val defaults = SlowedSettings()
        assertFalse(defaults.enabled); assertEquals(SlowedFrequency.NORMAL, defaults.frequency); assertEquals(8, defaults.maxSongs)
        assertEquals(SlowedPreset.entries.toSet(), defaults.presets)
        val custom = SlowedSettings(true, SlowedFrequency.OFTEN, 10, setOf(SlowedPreset.LOFI))
        assertEquals(custom, SlowedSettings.decode(custom.encode()))
        assertEquals(10, SlowedSettings.decode(SlowedSettings(maxSongs = 40).encode()).maxSongs)
        assertEquals(4, SlowedSettings(maxSongs = 1).normalized().maxSongs)
        assertEquals(setOf(SlowedPreset.SLOWED), SlowedSettings(presets = emptySet()).normalized().presets)
        assertEquals(SlowedSettings(), SlowedSettings.decode("not json"))
    }

    @Test fun slowedIsAlwaysRarerThanAGuestTakeover() {
        val guestRate = 1.0 / 4 / 3
        assertEquals(guestRate * 0.25, SlowedPolicy.chance(on.copy(frequency = SlowedFrequency.RARELY), 4), 1e-9)
        assertEquals(guestRate * 0.5, SlowedPolicy.chance(on, null), 1e-9)
        assertEquals(guestRate * 0.75, SlowedPolicy.chance(on.copy(frequency = SlowedFrequency.OFTEN), 4), 1e-9)
        assertEquals(0.0, SlowedPolicy.chance(on.copy(frequency = SlowedFrequency.OFF), 4), 0.0)
        assertEquals(0.0, SlowedPolicy.chance(SlowedSettings(), 4), 0.0)
    }

    @Test fun whereSlowedMayRun() {
        assertTrue(SlowedPolicy.allowed(on, talkOn = true, radioActive = true, feel = null, allowedFeels = emptySet()))
        assertFalse("talk off", SlowedPolicy.allowed(on, false, true, null, emptySet()))
        assertFalse("master off", SlowedPolicy.allowed(SlowedSettings(), true, true, null, emptySet()))
        assertFalse("not Radio", SlowedPolicy.allowed(on, true, false, null, emptySet()))
        assertFalse("feel not allowed", SlowedPolicy.allowed(on, true, true, "rainy", emptySet()))
        assertTrue("feel allowed", SlowedPolicy.allowed(on, true, true, "rainy", setOf("rainy")))
    }

    @Test fun autoSetsRespectCooldownOtherGuestsAndRunningSets() {
        val always = on.copy(frequency = SlowedFrequency.OFTEN)
        val lucky = object : Random() { override fun nextBits(bitCount: Int) = 0 }
        assertTrue(SlowedPolicy.shouldStartAuto(always, 2, 15, otherGuestSet = false, running = false, random = lucky))
        assertFalse(SlowedPolicy.shouldStartAuto(always, 2, 14, false, false, lucky))
        assertFalse(SlowedPolicy.shouldStartAuto(always, 2, 40, true, false, lucky))
        assertFalse(SlowedPolicy.shouldStartAuto(always, 2, 40, false, true, lucky))
        var starts = 0
        val random = Random(3)
        repeat(100_000) { if (SlowedPolicy.shouldStartAuto(on, 4, 99, false, false, random)) starts++ }
        assertEquals(SlowedPolicy.chance(on, 4), starts / 100_000.0, 0.003)
    }

    @Test fun autoLengthsBiasLowAndManualRunsSitAtTheTop() {
        val random = Random(11)
        val auto = List(10_000) { SlowedPolicy.autoLength(on, random) }
        assertTrue(auto.all { it in 4..8 })
        assertEquals(0.7, auto.count { it in 4..6 } / 10_000.0, 0.03)
        val manual = List(10_000) { SlowedPolicy.manualLength(on, random) }
        assertTrue(manual.all { it in 4..8 })
        assertEquals(0.7, manual.count { it in 6..8 } / 10_000.0, 0.03)
        val wide = on.copy(maxSongs = 10)
        assertTrue(List(2000) { SlowedPolicy.manualLength(wide, random) }.count { it in 8..10 } > 1200)
        val tight = on.copy(maxSongs = 4)
        assertEquals(setOf(4), List(200) { SlowedPolicy.autoLength(tight, random) }.toSet())
        assertEquals(setOf(4), List(200) { SlowedPolicy.manualLength(tight, random) }.toSet())
    }

    @Test fun setIsShortenedToItsPoolOrNotStarted() {
        assertEquals(6, SlowedPolicy.fit(6, null))
        assertEquals(5, SlowedPolicy.fit(8, 5))
        assertNull(SlowedPolicy.fit(6, 3))
    }

    @Test fun guestsMostlyAlternateAndNeedAnOpener() {
        val random = Random(5)
        val picks = List(1000) { SlowedPolicy.pickGuest(SlowedGuest.entries, SlowedGuest.UNDERTOW, random) }
        assertTrue(picks.count { it == SlowedGuest.MOONLIGHT } > 700)
        assertEquals(SlowedGuest.UNDERTOW, SlowedPolicy.pickGuest(setOf(SlowedGuest.UNDERTOW), SlowedGuest.UNDERTOW, random))
        assertNull(SlowedPolicy.pickGuest(emptySet(), null, random))
    }

    private fun set(total: Int = 4, committed: Boolean = true, played: List<String> = emptyList(), manual: Boolean = false) =
        SlowedSet(SlowedPreset.SLOWED, SlowedGuest.UNDERTOW, manual, total, afterId = "a", committed = committed, played = played)

    @Test fun noEffectBeforeTheOpenerStarts() {
        assertEquals(emptySet<String>(), SlowedPolicy.effected(set(committed = false), "a", listOf("a", "b", "c")))
        assertEquals(emptySet<String>(), SlowedPolicy.effected(null, "a", listOf("a", "b")))
    }

    @Test fun whileTheOpenerPlaysTheNextSongsAreReady() {
        // The opener is a speech item: not in the play order; it introduces "b".
        assertEquals(setOf("b", "c", "d", "e"), SlowedPolicy.effected(set(), "talk:x", listOf("a", "b", "c", "d", "e", "f"), talkNextId = "b"))
    }

    @Test fun effectFollowsTheSetAsSongsStart() {
        var s = set(total = 3)
        val order = listOf("a", "b", "c", "d", "e")
        var effected = SlowedPolicy.effected(s, "a", order)
        assertEquals(setOf("b", "c", "d"), effected)       // the song before the opener is never slowed
        s = SlowedPolicy.songStarted(s, "b", effected)
        effected = SlowedPolicy.effected(s, "b", order)
        assertEquals(setOf("b", "c", "d"), effected)
        s = SlowedPolicy.songStarted(s, "c", effected)
        s = SlowedPolicy.songStarted(s, "d", SlowedPolicy.effected(s, "c", order))
        assertTrue(s.closing)
        assertEquals(setOf("d"), SlowedPolicy.effected(s, "d", order))
        assertEquals(emptySet<String>(), SlowedPolicy.effected(s, "e", order))
        // A song that isn't part of the set never counts.
        assertEquals(s, SlowedPolicy.songStarted(s, "e", emptySet()))
    }

    @Test fun theFirstSetSongIsSlowedBeforeItsStartIsCounted() {
        // The opener ended; "b" is current but its actual start hasn't been reported yet.
        assertEquals(setOf("b", "c", "d"), SlowedPolicy.effected(set(total = 3), "b", listOf("a", "b", "c", "d", "e")))
        // The song a manual run interrupted is never part of it.
        assertEquals(setOf("b", "c", "d"), SlowedPolicy.effected(set(total = 3), "a", listOf("a", "b", "c", "d", "e")))
    }

    @Test fun newMixInsideASetKeepsTheCount() {
        val s = set(total = 4, played = listOf("b", "c"))
        assertEquals(setOf("c", "x", "y"), SlowedPolicy.effected(s, "c", listOf("b", "c", "x", "y", "z")))
    }

    @Test fun skippedSetSongsStillCount() {
        var s = set(total = 2)
        s = SlowedPolicy.songStarted(s, "b", SlowedPolicy.effected(s, "a", listOf("a", "b", "c", "d")))
        s = SlowedPolicy.songStarted(s, "c", SlowedPolicy.effected(s, "b", listOf("a", "b", "c", "d")))
        assertTrue(s.closing); assertEquals(0, s.songsLeft)
    }

    @Test fun midLinesComeAroundTheMiddleOfLongSetsAndRegularlyInRuns() {
        val random = Random(9)
        val short = set(total = 5, played = listOf("b", "c"))
        assertFalse((1..200).any { SlowedPolicy.wantsMid(short, random) })
        val long = set(total = 8, played = listOf("1", "2", "3", "4"))
        val asked = (1..1000).count { SlowedPolicy.wantsMid(long, random) }
        assertEquals(500.0, asked.toDouble(), 80.0)
        assertFalse(SlowedPolicy.wantsMid(long.copy(midsAt = setOf(2)), random))
        assertFalse(SlowedPolicy.wantsMid(set(total = 8, played = listOf("1", "2", "3")), random))
        val run = set(total = 10, manual = true, played = listOf("1", "2", "3"))
        assertTrue((1..200).any { SlowedPolicy.wantsMid(run, random) })
        assertFalse(SlowedPolicy.wantsMid(run.copy(midsAt = setOf(3)), random))
    }

    @Test fun requestWire() {
        val wire = SlowedRequest(SlowedMoment.OPENER, SlowedPreset.REALLY_SLOW, SlowedGuest.MOONLIGHT, 6, 6, manual = true).toWire()
        assertEquals(mapOf("moment" to "opener", "preset" to "really_slow", "guest" to "guest_chill", "songs" to 6, "songs_left" to 6, "trigger" to "manual"), wire)
    }

    private fun line(id: String, kind: String, preset: String? = "slowed", guest: String = "guest_undertow", lastPlayed: Long = 0, bytes: Int = 100_000) =
        SlowedLine(id, guest, kind, preset, "0".repeat(64), bytes, 5000, 0, lastPlayed)

    @Test fun bankPicksLeastRecentlyPlayedAndAvoidsTheLastFour() {
        val lines = (1..6).map { line("o$it", SlowedKinds.OPENER_AUTO, lastPlayed = it.toLong()) }
        val first = SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.UNDERTOW, SlowedMoment.OPENER, SlowedPreset.SLOWED, false)
        assertEquals("o1", first!!.id)
        val recent = mapOf(SlowedKinds.OPENER_AUTO to listOf("o1", "o2", "o3", "o4"))
        assertEquals("o5", SlowedBankPolicy.choose(lines, recent, SlowedGuest.UNDERTOW, SlowedMoment.OPENER, SlowedPreset.SLOWED, false)!!.id)
        // Other presets and guests are never used.
        assertNull(SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.MOONLIGHT, SlowedMoment.OPENER, SlowedPreset.SLOWED, false))
        assertNull(SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.UNDERTOW, SlowedMoment.OPENER, SlowedPreset.LOFI, false))
    }

    @Test fun manualRunsPreferManualOpenersAndClosersFallBackToGeneric() {
        val lines = listOf(line("auto", SlowedKinds.OPENER_AUTO), line("manual", SlowedKinds.OPENER_MANUAL), line("generic", SlowedKinds.CLOSER_GENERIC, preset = null))
        assertEquals("manual", SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.UNDERTOW, SlowedMoment.OPENER, SlowedPreset.SLOWED, true)!!.id)
        assertEquals("auto", SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.UNDERTOW, SlowedMoment.OPENER, SlowedPreset.SLOWED, false)!!.id)
        assertEquals("generic", SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.UNDERTOW, SlowedMoment.CLOSER, SlowedPreset.REALLY_SLOW, false)!!.id)
        assertNull(SlowedBankPolicy.choose(lines, emptyMap(), SlowedGuest.UNDERTOW, SlowedMoment.MID, SlowedPreset.SLOWED, false))
    }

    @Test fun offlineSetsWaitForTwoOpenersAndClosersPerAllowedPreset() {
        val half = listOf(line("o1", SlowedKinds.OPENER_AUTO), line("o2", SlowedKinds.OPENER_AUTO), line("c1", SlowedKinds.CLOSER))
        assertFalse(SlowedBankPolicy.ready(half, setOf(SlowedPreset.SLOWED)))
        val full = half + line("g1", SlowedKinds.CLOSER_GENERIC, preset = null)
        assertTrue(SlowedBankPolicy.ready(full, setOf(SlowedPreset.SLOWED)))
        assertFalse(SlowedBankPolicy.ready(full, setOf(SlowedPreset.SLOWED, SlowedPreset.LOFI)))
        assertEquals(setOf(SlowedGuest.UNDERTOW), SlowedBankPolicy.guestsWithOpeners(full, SlowedPreset.SLOWED, false))
    }

    @Test fun downloadsRespectTheCapAndTheMeteredBudget() {
        val listed = (1..10).map { line("l$it", SlowedKinds.MID, preset = null, bytes = 2 * 1024 * 1024) }
        val stored = mapOf("l1" to listed[0])
        assertEquals(9, SlowedBankPolicy.toDownload(listed, stored, 0, null).size)
        assertEquals(2, SlowedBankPolicy.toDownload(listed, stored, 0, SlowedBankPolicy.METERED_BYTES_PER_SYNC).size)
        assertEquals(1, SlowedBankPolicy.toDownload(listed, stored, SlowedBankPolicy.CAP_BYTES - 3 * 1024 * 1024, null).size)
    }
}
