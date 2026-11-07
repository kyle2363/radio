package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import dev.radio.mobile.ui.theme.StationColors
import org.junit.Assert.*
import org.junit.Test

class StationPolicyTest {
    private fun pick(slot: String = "exploit", reason: String = "Similar to current song") =
        RadioPick("s", "l", 1.0, .5, null, slot = slot, reason = reason)

    @Test fun presetsUseFixedValuesAndDefaultIsBalanced() {
        assertEquals(.10, ExplorationPreset.Familiar.value, 0.0)
        assertEquals(.30, ExplorationPreset.Balanced.value, 0.0)
        assertEquals(.70, ExplorationPreset.Adventurous.value, 0.0)
        assertEquals(ExplorationPreset.Balanced, ExplorationPreset.nearest(.30))
    }

    @Test fun nearestPresetBoundaries() {
        assertEquals(ExplorationPreset.Familiar, ExplorationPreset.nearest(0.0))
        assertEquals(ExplorationPreset.Familiar, ExplorationPreset.nearest(.19))
        assertEquals(ExplorationPreset.Balanced, ExplorationPreset.nearest(.21))
        assertEquals(ExplorationPreset.Balanced, ExplorationPreset.nearest(.49))
        assertEquals(ExplorationPreset.Adventurous, ExplorationPreset.nearest(.51))
        assertEquals(ExplorationPreset.Adventurous, ExplorationPreset.nearest(1.0))
        assertEquals(ExplorationPreset.Balanced, ExplorationPreset.nearest(Double.NaN))
    }

    @Test fun summaryShowsNameAndPercent() {
        assertEquals("Balanced · 30%", ExplorationPreset.summary(.30))
        assertEquals("Balanced · 42%", ExplorationPreset.summary(.42))
        assertEquals("Familiar · 0%", ExplorationPreset.summary(0.0))
        assertEquals("Adventurous · 100%", ExplorationPreset.summary(1.0))
    }

    @Test fun originsFollowSlotsAndReasons() {
        assertEquals(StationOrigin.Desktop, StationOrigin.of(pick(reason = ConnectedPolicy.SERVER_AI_PREFIX + " · close match")))
        assertEquals(StationOrigin.Start, StationOrigin.of(pick(reason = "Chosen starting song")))
        assertEquals(StationOrigin.Vibe, StationOrigin.of(pick(reason = "Matches locked vibe")))
        assertEquals(StationOrigin.Explore, StationOrigin.of(pick(slot = "explore")))
        assertEquals(StationOrigin.Wildcard, StationOrigin.of(pick(slot = "wildcard")))
        assertEquals(StationOrigin.Context, StationOrigin.of(pick(slot = "context")))
        assertEquals(StationOrigin.Similar, StationOrigin.of(pick(reason = "Similar to previous suggestion")))
        assertEquals(StationOrigin.Taste, StationOrigin.of(pick(reason = "Matches listening taste")))
        assertEquals(StationOrigin.Taste, StationOrigin.of(pick(reason = "Previous suggestion + listening taste")))
        assertEquals(StationOrigin.Taste, StationOrigin.of(pick(reason = "Liked song")))
        assertEquals(StationOrigin.Mood, StationOrigin.of(pick(reason = "Mood and energy ranking")))
        assertEquals(StationOrigin.Mood, StationOrigin.of(pick(reason = "Local choice")))
    }

    @Test fun everyOriginHasALabelAndRoundTripsByName() {
        StationOrigin.entries.forEach {
            assertTrue(it.label.isNotBlank())
            assertEquals(it, StationOrigin.parse(it.name))
        }
        assertNull(StationOrigin.parse("nonsense"))
    }

    @Test fun stationRoundTripsAndPrunesToPlayOrder() {
        val station = StationRecord("seed", "Sunlight", mapOf("a" to StationOrigin.Similar, "b" to StationOrigin.User, "gone" to StationOrigin.Desktop), listOf("a", "b"), "a")
        val decoded = StationRecord.decode(station.encode())
        assertEquals(station, decoded)
        assertEquals(setOf("a", "b"), station.pruned(listOf("a", "b")).origins.keys)
        assertNull(StationRecord.decode("not json"))
        assertNull(StationRecord.decode(null))
    }

    @Test fun stationOnlyRestoresForTheSameQueue() {
        val station = StationRecord(null, null, mapOf("a" to StationOrigin.Taste), listOf("a", "b"), "a")
        assertTrue(station.matches(listOf("a", "b"), "a"))
        assertFalse(station.matches(listOf("b", "a"), "a"))
        assertFalse(station.matches(listOf("a", "b"), "b"))
        assertFalse(station.matches(emptyList(), null))
    }

    @Test fun originMapIsBounded() {
        val many = (0 until 150).associate { "s$it" to StationOrigin.Similar }
        val station = StationRecord(null, null, many, many.keys.toList(), "s0")
        assertEquals(StationRecord.MAX_ORIGINS, StationRecord.decode(station.encode())!!.origins.size)
    }

    @Test fun stationColorsKeepTextContrast() {
        StationColors.all.forEach { colors ->
            assertTrue("muted ${colors.name}", contrast(colors.muted, colors.base) >= 4.5)
            assertTrue("muted on panel ${colors.name}", contrast(colors.muted, colors.panel) >= 4.5)
            assertTrue("progress ${colors.name}", contrast(colors.progress, colors.base) >= 4.5)
            assertTrue("on air ${colors.name}", contrast(colors.onAirText, colors.base) >= 4.5)
            assertTrue("white ${colors.name}", contrast(0xFFFFFFFF, colors.panel) >= 4.5)
        }
    }

    private fun contrast(a: Long, b: Long): Double {
        fun lum(c: Long): Double {
            fun ch(v: Long): Double { val s = v / 255.0; return if (s <= .03928) s / 12.92 else Math.pow((s + .055) / 1.055, 2.4) }
            return .2126 * ch((c shr 16) and 0xFF) + .7152 * ch((c shr 8) and 0xFF) + .0722 * ch(c and 0xFF)
        }
        val (x, y) = lum(a) to lum(b)
        return (maxOf(x, y) + .05) / (minOf(x, y) + .05)
    }
}
