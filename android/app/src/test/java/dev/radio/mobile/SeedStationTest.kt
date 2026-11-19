package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

/** "Start radio from this": a song or playlist keeps steering the whole station, not just the first batch. */
class SeedStationTest {
    private val raw = mapOf(
        "seed" to floatArrayOf(1f, 0f), "near" to floatArrayOf(.95f, .05f), "mid" to floatArrayOf(.6f, .4f),
        "current" to floatArrayOf(0f, 1f), "currentTwin" to floatArrayOf(0f, 1f), "far" to floatArrayOf(-1f, 0f))
    private val vectors = raw.map { (id, v) -> RadioVector(id, "audio", "m", "1", v) }
    private val tracks = raw.keys.map { RadioTrack(it, it) }

    @Test fun `closer songs score higher and opposite songs get nothing`() {
        val scores = SeedStation.scores(setOf("seed"), vectors, audioBlend = .65, includeSeeds = false)
        assertTrue(scores.getValue("near") > scores.getValue("mid"))
        assertTrue(scores.getValue("mid") > 0.0)
        assertNull(scores["far"])
        assertNull(scores["current"]) // orthogonal: no pull either way
        assertNull("the seed itself already played first", scores["seed"])
    }

    @Test fun `a playlist steers toward the middle of its songs and keeps its own songs in reach`() {
        val scores = SeedStation.scores(setOf("seed", "current"), vectors, audioBlend = .65, includeSeeds = true)
        assertTrue(scores.getValue("mid") > scores.getValue("near"))
        assertTrue(scores.getValue("seed") > 0.0)
        assertTrue(scores.getValue("currentTwin") > 0.0)
        assertNull(scores["far"])
    }

    @Test fun `seeds without similarity data give no scores, so Radio falls back to the mood`() {
        assertTrue(SeedStation.scores(setOf("unknown"), vectors, .65, includeSeeds = false).isEmpty())
        assertTrue(SeedStation.scores(emptySet(), vectors, .65, includeSeeds = true).isEmpty())
    }

    @Test fun `text and audio similarity blend like the rest of Radio`() {
        val mixed = listOf(
            RadioVector("seed", "text", "t", "1", floatArrayOf(1f, 0f)), RadioVector("a", "text", "t", "1", floatArrayOf(1f, 0f)),
            RadioVector("seed", "audio", "m", "1", floatArrayOf(1f, 0f)), RadioVector("a", "audio", "m", "1", floatArrayOf(0f, 1f)))
        val audioHeavy = SeedStation.scores(setOf("seed"), mixed, audioBlend = .9, includeSeeds = false)["a"] ?: 0.0
        val textHeavy = SeedStation.scores(setOf("seed"), mixed, audioBlend = .1, includeSeeds = false)["a"] ?: 0.0
        assertTrue(textHeavy > audioHeavy)
    }

    @Test fun `the station keeps following the seed after the queue moves on`() {
        val seed = SeedStation.scores(setOf("seed"), vectors, .65, includeSeeds = false)
        val picks = OfflineRecommender(tracks, vectors).recommendSequence(RadioProfile(current = "current"),
            RadioSettings(seed = seed), 1000.0, count = 2, excluded = setOf("seed", "current"))
        assertEquals("near", picks.first().songId)
        assertEquals(StationOrigin.Seed, StationOrigin.of(picks.first()))
    }

    @Test fun `zero exploration still follows the seed`() {
        val seed = SeedStation.scores(setOf("seed"), vectors, .65, includeSeeds = false)
        val picks = OfflineRecommender(tracks, vectors).recommendSequence(RadioProfile(current = "current"),
            RadioSettings(exploration = 0.0, seed = seed), 1000.0, count = 1, excluded = setOf("seed", "current"))
        assertEquals("near", picks.single().songId)
    }

    @Test fun `without a seed nothing changes`() {
        val picks = OfflineRecommender(tracks, vectors).recommendSequence(RadioProfile(current = "current"),
            RadioSettings(exploration = 0.0), 1000.0, count = 1, excluded = setOf("current"))
        assertEquals("currentTwin", picks.single().songId)
    }

    @Test fun `station spec survives being saved`() {
        val spec = SeedSpec(SeedSpec.Kind.Playlist, "Road | trip", listOf("a:1", "b|2"))
        assertEquals(spec, SeedSpec.decode(spec.encode()))
        assertEquals(SeedSpec(SeedSpec.Kind.Song, "Song", listOf("x")), SeedSpec.decode(SeedSpec(SeedSpec.Kind.Song, "Song", listOf("x")).encode()))
        assertNull(SeedSpec.decode(null))
        assertNull(SeedSpec.decode("garbage"))
        assertNull(SeedSpec.decode(SeedSpec(SeedSpec.Kind.Song, "Song", emptyList()).encode()))
    }
}
