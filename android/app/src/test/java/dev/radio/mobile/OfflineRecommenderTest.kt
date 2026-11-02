package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class OfflineRecommenderTest {
    private val continuityTracks = listOf("taste", "current", "neighbor", "locked").map { RadioTrack(it,it) }
    private val continuityVectors = listOf(
        RadioVector("taste","text","model","1",floatArrayOf(1f,0f)),
        RadioVector("current","text","model","1",floatArrayOf(0f,1f)),
        RadioVector("neighbor","text","model","1",floatArrayOf(0f,1f)),
        RadioVector("locked","text","model","1",floatArrayOf(-1f,0f)))

    @Test fun `current song guides cold start without creating positive feedback`() {
        val profile=RadioProfile(current="current")
        val engine=OfflineRecommender(continuityTracks,continuityVectors)
        val picks=engine.recommend(profile,RadioSettings(),100.0,excluded=setOf("current"))
        assertEquals("neighbor",picks.first().songId)
        assertEquals(1.0,picks.first().textSimilarity!!,1e-6)
        assertEquals("Similar to current song",picks.first().reason)
        assertTrue(profile.events.isEmpty())
        assertArrayEquals(floatArrayOf(0f,1f),continuityVectors[1].values,0f)
    }
    @Test fun `exploration reduces ordinary continuity and vibe overrides current song`() {
        val profile=RadioProfile(current="current",events=listOf(RadioEvent("e","completed","taste",0.0)))
        val engine=OfflineRecommender(continuityTracks,continuityVectors)
        fun neighbor(settings:RadioSettings)=engine.rank(profile,settings,100.0).single{it.songId=="neighbor"}
        assertTrue(neighbor(RadioSettings(exploration=.2)).textSimilarity!! > neighbor(RadioSettings(exploration=.9)).textSimilarity!!)
        // Same orthogonal-vector example as desktop context_query and the Python reference.
        assertEquals(.8970890336601902,neighbor(RadioSettings(exploration=.2)).textSimilarity!!,2e-6)
        val locked=engine.rank(profile,RadioSettings(vibeLock="locked"),100.0)
        assertEquals("locked",locked.first().songId)
        assertEquals("Matches locked vibe",locked.first().reason)
        assertEquals("Current song + listening taste",neighbor(RadioSettings()).reason)
    }
    @Test fun `missing or incompatible current vectors retain taste and honest fallback reasons`() {
        val engine=OfflineRecommender(continuityTracks,continuityVectors.filter{it.songId!="current"})
        val profile=RadioProfile(current="current",events=listOf(RadioEvent("e","completed","taste",0.0)))
        val ranked=engine.rank(profile,RadioSettings(),100.0)
        assertEquals(1.0,ranked.single{it.songId=="taste"}.textSimilarity!!,1e-6)
        assertEquals("Matches listening taste",ranked.single{it.songId=="taste"}.reason)
        val fallback=OfflineRecommender(continuityTracks,emptyList()).rank(profile,RadioSettings(),100.0)
        assertTrue(fallback.all{it.vectorFallback && it.reason=="Local choice"})
        val incompatible=OfflineRecommender(continuityTracks,continuityVectors.map{if(it.songId=="current")it.copy(model="other") else it})
        assertTrue(incompatible.rank(RadioProfile(current="current"),RadioSettings(),100.0).single{it.songId=="neighbor"}.vectorFallback)
    }
    @Test fun `audio continuity and explanations respect the selected modality`() {
        val vectors=continuityVectors.map{it.copy(space="audio")} + listOf(
            RadioVector("taste","text","text-model","1",floatArrayOf(1f,0f)),
            RadioVector("neighbor","text","text-model","1",floatArrayOf(1f,0f)))
        val profile=RadioProfile(current="current",events=listOf(RadioEvent("e","completed","taste",0.0)))
        val engine=OfflineRecommender(continuityTracks,vectors)
        val audio=engine.rank(profile,RadioSettings(audioBlend=1.0),100.0).single{it.songId=="neighbor"}
        val text=engine.rank(profile,RadioSettings(audioBlend=0.0),100.0).single{it.songId=="neighbor"}
        assertEquals("Current song + listening taste",audio.reason)
        assertEquals("Matches listening taste",text.reason)
        assertTrue(audio.audioSimilarity!!>.3)
        assertEquals(1.0,text.textSimilarity!!,1e-6)
    }
    @Test fun `conflicting modalities do not claim a positive similarity match`() {
        val vectors=listOf(
            RadioVector("taste","text","text","1",floatArrayOf(1f,0f)),
            RadioVector("neighbor","text","text","1",floatArrayOf(1f,0f)),
            RadioVector("taste","audio","audio","1",floatArrayOf(1f,0f)),
            RadioVector("neighbor","audio","audio","1",floatArrayOf(-1f,0f)))
        val profile=RadioProfile(events=listOf(RadioEvent("e","completed","taste",0.0)))
        val pick=OfflineRecommender(continuityTracks,vectors).rank(profile,RadioSettings(),100.0).single{it.songId=="neighbor"}
        assertEquals("Mood and energy ranking",pick.reason)
    }
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
