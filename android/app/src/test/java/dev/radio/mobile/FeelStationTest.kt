package dev.radio.mobile

import dev.radio.mobile.portable.FeelRules.FeelState
import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class FeelStationTest {
    private val vectors = mapOf(
        "m1" to floatArrayOf(1f, 0f), "m2" to floatArrayOf(.9f, .1f), "near" to floatArrayOf(.95f, .05f),
        "far" to floatArrayOf(-1f, 0f), "out" to floatArrayOf(0f, 1f))

    @Test fun `members rank by score above neighbours and unrated songs`() {
        val states = mapOf("m1" to FeelState(.7, .5, true), "m2" to FeelState(.9, .6, true))
        val scores = FeelStation.scores(states, vectors)
        assertTrue(scores.getValue("m2") > scores.getValue("m1"))
        assertTrue(scores.getValue("m1") > scores.getValue("near"))
        assertTrue(scores.getValue("near") > 0.0)
        assertNull(scores["far"]) // no positive neighbour
        assertEquals(FeelStation.Tier.Member, FeelStation.tier(scores.getValue("m1")))
        assertEquals(FeelStation.Tier.Neighbour, FeelStation.tier(scores.getValue("near")))
    }

    @Test fun `rated songs that do not fit are pushed down`() {
        val states = mapOf("m1" to FeelState(.8, .5, true), "out" to FeelState(.2, .5, false))
        assertTrue(FeelStation.scores(states, vectors).getValue("out") < 0.0)
    }

    @Test fun `no evidence gives no scores so the mood ranking stays in charge`() {
        assertTrue(FeelStation.scores(emptyMap(), vectors).isEmpty())
    }

    private val tracks = listOf("m1", "m2", "near", "far", "out", "disliked").map { RadioTrack(it, it) }
    private val radioVectors = (vectors + ("disliked" to floatArrayOf(1f, 0f))).map { (id, v) -> RadioVector(id, "audio", "m", "1", v) }

    @Test fun `feel station plays members first, then neighbours, then the mood fallback`() {
        val feel = mapOf("m1" to FeelStation.MEMBER + .7, "m2" to FeelStation.MEMBER + .9, "near" to .3, "disliked" to FeelStation.MEMBER + 1.0)
        val picks = OfflineRecommender(tracks, radioVectors).recommendSequence(RadioProfile(dislikes = setOf("disliked")),
            RadioSettings(feel = feel), 1000.0, count = 5)
        assertEquals(listOf("m2", "m1", "near"), picks.take(3).map { it.songId })
        assertFalse(picks.any { it.songId == "disliked" })
        assertEquals(5, picks.size) // the rest of the library fills in by mood
        assertEquals(StationOrigin.Feel, StationOrigin.of(picks[0]))
    }

    @Test fun `recently played members still cool down`() {
        val feel = mapOf("m1" to FeelStation.MEMBER + .9, "m2" to FeelStation.MEMBER + .5)
        val profile = RadioProfile(events = listOf(RadioEvent("e", "started", "m1", 990.0)))
        val picks = OfflineRecommender(tracks, radioVectors).recommendSequence(profile, RadioSettings(feel = feel), 1000.0, count = 1)
        assertEquals("m2", picks.single().songId)
    }

    @Test fun `zero exploration still follows the feel`() {
        val feel = mapOf("out" to FeelStation.MEMBER + .9)
        val picks = OfflineRecommender(tracks, radioVectors).recommendSequence(RadioProfile(current = "m1"),
            RadioSettings(exploration = 0.0, feel = feel), 1000.0, count = 1)
        assertEquals("out", picks.single().songId)
    }
}

class FeelFeaturesTest {
    @Test fun `features are keyed by local id from one vector space, preferring audio`() {
        val accepted = mapOf("a" to "s1", "b" to "s2")
        val vectors = listOf(RadioVector("s1", "text", "t", "1", floatArrayOf(1f)), RadioVector("s1", "audio", "m", "1", floatArrayOf(1f, 2f)),
            RadioVector("s2", "audio", "m", "1", floatArrayOf(3f, 4f)), RadioVector("s3", "audio", "m", "1", floatArrayOf(5f, 6f)))
        val meta = mapOf("s1" to CatalogTrackMeta(null, .8, .5, null, emptySet(), emptySet(), setOf("dreamy", "pop")))
        val features = FeelFeatures.build(accepted, vectors) { meta[it] }
        assertEquals(setOf("a", "b"), features.vectors.keys)
        assertArrayEquals(floatArrayOf(1f, 2f), features.vectors.getValue("a"), 0f)
        assertEquals(setOf("dreamy", "pop"), features.tags["a"])
        assertEquals(.8, features.energy.getValue("a"), 1e-9)
        assertNull(features.energy["b"])
    }
}
