package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class OfflineRecommenderTest {
    @Test fun `metadata fallback stays eligible but dislike and filters are hard`() {
        val rows = listOf(RadioTrack("a", "a", artist="A"), RadioTrack("b", "b", artist="B", folder="other"))
        val engine = OfflineRecommender(rows, emptyList(), seed=17)
        assertEquals(2, engine.rank(RadioProfile(), RadioSettings(), 2000.0).size)
        assertEquals(listOf("b"), engine.rank(RadioProfile(dislikes=setOf("a")), RadioSettings(), 2000.0).map { it.songId })
        assertTrue(engine.rank(RadioProfile(), RadioSettings(folders=setOf("missing")), 2000.0).isEmpty())
    }
    @Test fun `model identity prevents incompatible vector mixing`() {
        val rows = listOf(RadioTrack("a", "a"), RadioTrack("b", "b"))
        val vectors = listOf(RadioVector("a", "text", "old", "1", floatArrayOf(1f,0f)), RadioVector("b", "text", "new", "1", floatArrayOf(1f,0f)))
        val ranked = OfflineRecommender(rows,vectors).rank(RadioProfile(events=listOf(RadioEvent("e","completed","a",0.0))),RadioSettings(),100.0)
        assertFalse(ranked.first { it.songId=="a" }.vectorFallback)
        assertTrue(ranked.first { it.songId=="b" }.vectorFallback)
    }
    @Test fun `one song and negative scores remain playable without duplicate suggestions`() {
        val track=RadioTrack("a","a",artist="A")
        val profile=RadioProfile(current="a",events=listOf(RadioEvent("e","started","a",10.0),RadioEvent("s","skipped","a",10.0)))
        val picks=OfflineRecommender(listOf(track),emptyList()).recommend(profile,RadioSettings(),10.0)
        assertEquals(1,picks.size); assertTrue(picks.single().score<0)
    }
    @Test fun `skip trajectory and vibe change future scores without modifying inputs`() {
        val rows=listOf(RadioTrack("a","a",energy=.2),RadioTrack("b","b",energy=.8))
        val vectors=listOf(RadioVector("a","text","model","v1",floatArrayOf(1f,0f)),RadioVector("b","text","model","v1",floatArrayOf(0f,1f)))
        val engine=OfflineRecommender(rows,vectors)
        val profile=RadioProfile(events=listOf(RadioEvent("e","completed","a",0.0)))
        val normal=engine.rank(profile,RadioSettings(),2000.0)
        val locked=engine.rank(profile,RadioSettings(vibeLock="b"),2000.0)
        assertEquals("a",normal.first().songId);assertEquals("b",locked.first().songId)
        assertEquals(1,profile.events.size);assertEquals(1f,vectors.first().values[0],0f)
        val rated=engine.rank(profile.copy(userEnergy=mapOf("a" to 1.0)),RadioSettings(),2000.0)
        assertEquals(.44,rated.first{it.songId=="a"}.energy,1e-9)
    }
    @Test fun `refill exclusions retain current artist and cluster context`() {
        val rows=listOf(RadioTrack("current","current",artist="A",energy=.7,cluster=1),RadioTrack("same","same",artist="A",energy=.7,cluster=1),RadioTrack("other","other",artist="B",energy=.7,cluster=2))
        val engine=OfflineRecommender(rows,emptyList());val profile=RadioProfile(current="current")
        val picks=engine.recommend(profile,RadioSettings(),0.0,excluded=setOf("current"))
        assertEquals("other",picks.first().songId)
        assertEquals(.12,picks.single{it.songId=="same"}.artistPenalty,0.0)
    }
}
