package dev.radio.mobile.portable

import dev.radio.mobile.recommendation.*
import org.junit.Assert.assertEquals
import org.junit.Test

class OfflineRecommenderGoldenTest {
    @Test fun backendGoldenScoresAndSlotsMatch() {
        val fixture = PortableGoldenFixture
        val tracks = fixture.tracks.map { row ->
            RadioTrack(row.songId, "phone-${row.songId}", row.artist, row.energy, row.valence,
                row.cluster, row.folder, row.genres, row.collections)
        } + fixture.localTracks.filter { it.songId.isEmpty() }.map { row ->
            RadioTrack("local:${row.localId}", row.localId, row.artist, row.energy)
        }
        val vectors = fixture.vectors.map { RadioVector(it.songId, it.space, it.model, it.version, it.values) }
        val events = fixture.history.map { RadioEvent(it.id, it.kind, it.songId, it.at) }
        val profile = RadioProfile(events = events, current = fixture.replayCurrent)
        val settings = RadioSettings(mood = fixture.mood, audioBlend = fixture.audioBlend,
            exploration = fixture.exploration)
        val scorer = OfflineRecommender(tracks, vectors, fixture.seed)

        val ranked = scorer.rank(profile, settings, fixture.now)
        assertEquals(fixture.expectedRankIds, ranked.map { it.songId })
        fixture.expectedScores.zip(ranked).forEach { (expected, actual) ->
            assertEquals(expected, actual.score, 0.000002)
        }
        val selected = scorer.recommend(profile, settings, fixture.now, fixture.expectedSelectionIds.size)
        assertEquals(fixture.expectedSelectionIds, selected.map { it.songId })
        assertEquals(fixture.expectedSlots, selected.map { it.slot })
    }
}
