package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class CatalogVectorPolicyTest {
    private val snapshot = "0123456789abcdef".repeat(4)

    @Test fun `only exact exporter tagged legacy collection names are recognized`() {
        assertTrue(CatalogVectorPolicy.isLegacyCollection("legacy_unknown:songs", "unknown"))
        assertTrue(CatalogVectorPolicy.isLegacyCollection("legacy_unknown:songs_audio", "unknown"))
        assertFalse(CatalogVectorPolicy.isLegacyCollection("legacy_unknown:songs", "v1"))
        assertFalse(CatalogVectorPolicy.isLegacyCollection("legacy_unknown:", "unknown"))
        assertFalse(CatalogVectorPolicy.isLegacyCollection("legacy_unknown:../songs", "unknown"))
        assertFalse(CatalogVectorPolicy.isLegacyCollection("Legacy_unknown:songs", "unknown"))
        assertFalse(CatalogVectorPolicy.isLegacyCollection("unknown:songs", "unknown"))
    }

    @Test fun `legacy identities require explicit opt in and a lowercase snapshot hash`() {
        assertNull(CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", snapshot))
        assertNull(CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", "", allowLegacy = true))
        assertNull(CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", "A" + snapshot.drop(1), allowLegacy = true))
        assertNull(CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", "0".repeat(63), allowLegacy = true))

        val accepted = CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", snapshot, allowLegacy = true)
        assertEquals(RadioVectorIdentity("legacy_unknown:songs", "unknown@snapshot:$snapshot", legacy = true), accepted)
        assertEquals("legacy_unknown:songs", accepted!!.model)
    }

    @Test fun `known descriptors remain unchanged and generic unknowns stay blocked`() {
        assertEquals(RadioVectorIdentity("text-encoder", "2026-01", false),
            CatalogVectorPolicy.identity("text-encoder", "2026-01", "", allowLegacy = true))
        assertNull(CatalogVectorPolicy.identity("generic_unknown_model", "v1", snapshot, allowLegacy = true))
        assertNull(CatalogVectorPolicy.identity("text-encoder", "unknown-v2", snapshot, allowLegacy = true))
        assertNull(CatalogVectorPolicy.identity("legacy_unknown:songs", "Unknown", snapshot, allowLegacy = true))
        assertNull(CatalogVectorPolicy.identity("legacy_unknown:bad/name", "unknown", snapshot, allowLegacy = true))
    }

    @Test fun `catalog shaped legacy text and audio vectors can drive local taste when opted in`() {
        val textIdentity = CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", snapshot, allowLegacy = true)!!
        val audioIdentity = CatalogVectorPolicy.identity("legacy_unknown:songs_audio", "unknown", snapshot, allowLegacy = true)!!
        val tracks = listOf(RadioTrack("taste", "taste"), RadioTrack("current", "current"), RadioTrack("neighbor", "neighbor"))
        fun values(size: Int, index: Int) = FloatArray(size).also { it[index] = 1f }
        val vectors = listOf(
            RadioVector("taste", "text", textIdentity.model, textIdentity.version, values(2560, 0)),
            RadioVector("current", "text", textIdentity.model, textIdentity.version, values(2560, 1)),
            RadioVector("neighbor", "text", textIdentity.model, textIdentity.version, values(2560, 1)),
            RadioVector("taste", "audio", audioIdentity.model, audioIdentity.version, values(512, 0)),
            RadioVector("current", "audio", audioIdentity.model, audioIdentity.version, values(512, 1)),
            RadioVector("neighbor", "audio", audioIdentity.model, audioIdentity.version, values(512, 1))
        )
        val profile = RadioProfile(current = "current", events = listOf(RadioEvent("learned", "completed", "taste", 1.0)))
        val engine = OfflineRecommender(tracks, vectors)
        val diagnostics = engine.diagnostics(profile, RadioSettings(audioBlend = 0.0), 10.0)
        assertEquals(3, diagnostics.textVectorTracks)
        assertEquals(3, diagnostics.audioVectorTracks)
        assertEquals(1, diagnostics.contributingTasteEvents)
        assertTrue(diagnostics.hasUsableTaste)
        val neighbor = engine.rank(profile, RadioSettings(audioBlend = 0.0, exploration = .2), 10.0).single { it.songId == "neighbor" }
        assertTrue(neighbor.textSimilarity!! > .89)
        assertEquals("Current song + listening taste", neighbor.reason)
    }

    @Test fun `legacy vectors from different snapshots never share a recommender group`() {
        val first = CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", snapshot, allowLegacy = true)!!
        val secondHash = "f".repeat(64)
        val second = CatalogVectorPolicy.identity("legacy_unknown:songs", "unknown", secondHash, allowLegacy = true)!!
        assertNotEquals(first, second)
        assertNotEquals(first.version, second.version)

        val tracks = listOf(RadioTrack("learned", "learned"), RadioTrack("other-snapshot", "other"))
        val vectors = listOf(
            RadioVector("learned", "text", first.model, first.version, floatArrayOf(1f, 0f)),
            RadioVector("other-snapshot", "text", second.model, second.version, floatArrayOf(1f, 0f))
        )
        val ranked = OfflineRecommender(tracks, vectors).rank(
            RadioProfile(events = listOf(RadioEvent("taste", "completed", "learned", 1.0))), RadioSettings(), 10.0)
        assertTrue(ranked.single { it.songId == "other-snapshot" }.vectorFallback)
    }
}
