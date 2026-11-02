package dev.radio.mobile

import dev.radio.mobile.recommendation.*
import org.junit.Assert.*
import org.junit.Test

class RadioSelectionPolicyTest {
    private fun vec(id: String, x: Float, y: Float) = RadioVector(id, "text", "model", "1", floatArrayOf(x, y))

    @Test fun `sequence balances predecessor with original anchor to limit drift`() {
        fun angle(id: String, degrees: Double) = vec(id,
            kotlin.math.cos(Math.toRadians(degrees)).toFloat(), kotlin.math.sin(Math.toRadians(degrees)).toFloat())
        val ids = listOf("anchor", "first", "drift", "return")
        val engine = OfflineRecommender(ids.map { RadioTrack(it, it) }, listOf(
            angle("anchor", 0.0), angle("first", -10.0), angle("drift", -35.0), angle("return", 20.0)))
        val picks = engine.recommendSequence(RadioProfile(current="anchor"), RadioSettings(exploration=0.0), 100.0, count=2)
        assertEquals(listOf("first", "return"), picks.map { it.songId })
    }

    @Test fun `zero exploration does not let taste displace a meaningfully closer match`() {
        val ids = listOf("anchor", "nearest", "preferred", "distant")
        val engine = OfflineRecommender(ids.map { RadioTrack(it, it) }, listOf(
            vec("anchor", 1f, 0f), vec("nearest", .999f, -.045f),
            vec("preferred", .99f, .141f), vec("distant", 0f, 1f)))
        val pick = engine.recommendSequence(RadioProfile(current="anchor", hearts=setOf("preferred", "distant")),
            RadioSettings(exploration=0.0), 100.0, count=1).single()
        assertEquals("nearest", pick.songId)
        assertFalse(pick.evidence.orEmpty().contains("Listening taste"))
    }

    @Test fun `zero exploration uses taste for near ties only`() {
        val ids=listOf("anchor","nearest","preferred")
        val engine=OfflineRecommender(ids.map{RadioTrack(it,it)},listOf(
            vec("anchor",1f,0f),vec("nearest",1f,0f),vec("preferred",.99995f,.01f)))
        val pick=engine.recommendSequence(RadioProfile(current="anchor",hearts=setOf("preferred")),
            RadioSettings(exploration=0.0),100.0,1).single()
        assertEquals("preferred",pick.songId)
        assertTrue(pick.evidence.orEmpty().contains("Listening taste"))
    }

    @Test fun `zero exploration rejects a smooth transition that leaves closer seed alternatives`() {
        fun angle(id:String,deg:Double)=vec(id,kotlin.math.cos(Math.toRadians(deg)).toFloat(),kotlin.math.sin(Math.toRadians(deg)).toFloat())
        val ids=listOf("anchor","first","drift","return")
        val engine=OfflineRecommender(ids.map{RadioTrack(it,it)},listOf(
            angle("anchor",0.0),angle("first",20.0),angle("drift",45.0),angle("return",-30.0)))
        val picks=engine.recommendSequence(RadioProfile(current="anchor"),RadioSettings(exploration=0.0),100.0,2)
        assertEquals(listOf("first","return"),picks.map{it.songId})
    }

    @Test fun `explanations identify the contributing similarity modality`() {
        val ids = listOf("a", "b")
        val vectors = ids.map { vec(it, 1f, 0f) }
        val engine = OfflineRecommender(ids.map { RadioTrack(it, it) }, vectors)
        val pick = engine.recommendSequence(RadioProfile(current="a"), RadioSettings(exploration=0.0), 10.0, 1).single()
        assertEquals("Musical description similarity", pick.evidence)
        val audio = OfflineRecommender(ids.map { RadioTrack(it, it) }, vectors.map{it.copy(space="audio")})
        assertEquals("Audio similarity",audio.recommendSequence(RadioProfile(current="a"),RadioSettings(exploration=0.0),10.0,1).single().evidence)
        assertNull(OfflineRecommender(ids.map { RadioTrack(it, it) },emptyList())
            .recommendSequence(RadioProfile(),RadioSettings(),10.0,1).single().evidence)
    }

    @Test fun `zero exploration follows closest similarity despite mood favorites artist and slot offset`() {
        val tracks = listOf(
            RadioTrack("anchor", "anchor", artist = "same", energy = .9, cluster = 1),
            RadioTrack("nearest", "nearest", artist = "same", energy = .9, valence = .9, cluster = 1),
            RadioTrack("detour", "detour", energy = .3, valence = .25, cluster = 2, favorite = true))
        val vectors = listOf(vec("anchor", 1f, 0f), vec("nearest", .99f, .141f), vec("detour", .9f, .436f))
        for (seed in 0..9) for (offset in 0..4) {
            val pick = OfflineRecommender(tracks, vectors, seed).recommendSequence(
                RadioProfile(current = "anchor"), RadioSettings(mood = "melancholy", exploration = 0.0),
                100.0, count = 1, sequenceOffset = offset).single()
            assertEquals("seed=$seed offset=$offset", "nearest", pick.songId)
            assertEquals("exploit", pick.slot)
        }
    }

    @Test fun `zero exploration lets taste reorder close neighbors without a distant detour`() {
        val tracks = listOf("anchor", "nearest", "taste", "taste-neighbor").map { RadioTrack(it, it) }
        val vectors = listOf(vec("anchor", 1f, 0f), vec("nearest", .999f, -.045f),
            vec("taste", 0f, 1f), vec("taste-neighbor", .98f, .2f))
        val profile = RadioProfile(current = "anchor", events = listOf(RadioEvent("taste", "completed", "taste", 1.0)))
        val picks = OfflineRecommender(tracks, vectors).recommendSequence(profile, RadioSettings(exploration = 0.0),
            100.0, count = 2, excluded = setOf("taste"))
        assertEquals(listOf("nearest", "taste-neighbor"), picks.map { it.songId })
        assertEquals("Similar to starting song", picks.first().reason)
        assertEquals("Similar to previous suggestion", picks.last().reason)
    }

    @Test fun `zero exploration obeys modality blend and cooldown across refill boundaries`() {
        val tracks = listOf("anchor", "text-near", "audio-near", "fallback").map { RadioTrack(it, it) }
        val vectors = listOf(vec("anchor", 1f, 0f), vec("text-near", 1f, 0f), vec("audio-near", .7f, .714f),
            vec("anchor", 1f, 0f).copy(space = "audio"), vec("text-near", .5f, .866f).copy(space = "audio"),
            vec("audio-near", 1f, 0f).copy(space = "audio"))
        val engine = OfflineRecommender(tracks, vectors)
        val profile = RadioProfile(current = "anchor")
        fun pick(blend: Double, recent: List<String> = emptyList()) = engine.recommendSequence(profile,
            RadioSettings(exploration = 0.0, audioBlend = blend), 100.0, count = 1,
            recentRecommendations = recent, sequenceOffset = 4).single().songId
        assertEquals("text-near", pick(0.0))
        assertEquals("audio-near", pick(1.0))
        assertEquals("text-near", pick(1.0, listOf("audio-near")))
    }

    @Test fun `zero exploration chooses nearest available neighbor for twenty songs across refills`() {
        val tracks = (0 until 400).map { RadioTrack("s$it", "s$it", artist = "artist${it % 6}",
            energy = (it % 100) / 100.0, valence = (it % 50) / 50.0, cluster = it % 20, favorite = it % 7 == 0) }
        val vectors = tracks.mapIndexed { i, row -> vec(row.songId,
            kotlin.math.cos(i * .0055).toFloat(), kotlin.math.sin(i * .0055).toFloat()) }
        val byId = vectors.associate { it.songId to it.values }
        fun cosine(a: String, b: String): Double {
            val av = byId.getValue(a); val bv = byId.getValue(b)
            val dot = av.indices.sumOf { av[it].toDouble() * bv[it] }
            val an = kotlin.math.sqrt(av.sumOf { it.toDouble() * it })
            val bn = kotlin.math.sqrt(bv.sumOf { it.toDouble() * it })
            return dot / an / bn
        }
        val selected = mutableListOf("s0")
        val engine = OfflineRecommender(tracks, vectors, seed = 71)
        repeat(4) {
            val anchor = selected.last()
            val batch = engine.recommendSequence(RadioProfile(current = anchor, dislikes = setOf("s2")),
                RadioSettings(exploration = 0.0, mood = "melancholy"), 100_000.0, count = 5,
                excluded = setOf("s3"), recentRecommendations = selected, sequenceOffset = selected.size - 1)
            assertEquals(5, batch.size)
            for (pick in batch) {
                val previous = selected.last()
                val available = tracks.filter { it.songId !in selected && it.songId !in setOf("s2", "s3") }
                val best = available.maxOf { .65*cosine(previous, it.songId)+.35*cosine(anchor, it.songId) }
                assertEquals("Not the nearest available transition from $previous to ${pick.songId}",
                    best, .65*cosine(previous, pick.songId)+.35*cosine(anchor, pick.songId), .000001)
                assertEquals("exploit", pick.slot)
                selected += pick.songId
            }
        }
        assertEquals(21, selected.toSet().size)
    }

    @Test fun `sequence keeps learned taste while each next pick follows its predecessor`() {
        val tracks = listOf("taste", "first", "near-first", "near-near", "far").map { RadioTrack(it, it) }
        val vectors = listOf(vec("taste", 1f, 0f), vec("first", .6f, .8f), vec("near-first", .1f, .995f), vec("near-near", 0f, 1f), vec("far", -1f, 0f))
        val engine = OfflineRecommender(tracks, vectors, seed = 9)
        val profile = RadioProfile(events = listOf(RadioEvent("learned", "completed", "taste", 1.0)))

        val picks = engine.recommendSequence(profile, RadioSettings(exploration = .3), 100.0, count = 3, excluded = setOf("taste"))

        assertEquals(3, picks.size)
        assertEquals(3, picks.map { it.songId }.toSet().size)
        assertEquals("first", picks[0].songId)
        assertEquals("Previous suggestion + listening taste", picks[1].reason)
        assertTrue(picks[1].songId in setOf("near-first", "near-near"))
        assertEquals(setOf("near-first", "near-near"), picks.drop(1).map { it.songId }.toSet())
        assertEquals(1, profile.events.size)
    }

    @Test fun `cooldown excludes recent choices while alternatives remain and then relaxes oldest`() {
        val tracks = listOf("a", "b", "c", "d").map { RadioTrack(it, it) }
        val engine = OfflineRecommender(tracks, tracks.map { vec(it.songId, 1f, 0f) }, seed = 4)
        val picks = engine.recommendSequence(RadioProfile(), RadioSettings(), 10_000.0, count = 4,
            recentRecommendations = listOf("a", "b", "c"))
        assertEquals(4, picks.size)
        assertEquals(4, picks.map { it.songId }.toSet().size)
        assertEquals("d", picks.first().songId)
        assertTrue(picks.indexOfFirst { it.songId == "a" } < picks.indexOfFirst { it.songId == "b" })
        assertTrue(picks.indexOfFirst { it.songId == "b" } < picks.indexOfFirst { it.songId == "c" })
    }

    @Test fun `duplicate recommendation history relaxes by each song latest occurrence`() {
        val tracks = listOf("a", "b", "c").map { RadioTrack(it, it) }
        val picks = OfflineRecommender(tracks, emptyList()).recommendSequence(RadioProfile(), RadioSettings(), 10.0, count = 3,
            recentRecommendations = listOf("a", "b", "a"))
        assertEquals(listOf("c", "b", "a"), picks.map { it.songId })
    }

    @Test fun `recent starts cool down while future starts do not`() {
        val tracks = listOf(
            RadioTrack("recent", "recent", energy = .5),
            RadioTrack("future", "future", energy = .5),
            RadioTrack("best", "best", energy = .5),
            RadioTrack("lower", "lower", energy = .1),
            RadioTrack("current", "current", energy = .5)
        )
        val vectors = listOf(vec("recent", 1f, 0f), vec("future", 1f, 0f), vec("best", -1f, 0f), vec("lower", -1f, 0f), vec("current", 1f, 0f))
        val profile = RadioProfile(events = listOf(
            RadioEvent("old-start", "started", "recent", 9.0),
            RadioEvent("future-start", "started", "future", 11.0)
        ), hearts = setOf("recent"), current = "current")
        val engine = OfflineRecommender(tracks, vectors)
        val picks = engine.recommendSequence(profile, RadioSettings(recentWindow = 100.0), 10.0, count = 1, excluded = setOf("current"))
        assertNotEquals("recent", picks.single().songId)
        assertEquals("future", picks.single().songId)
    }

    @Test fun `hard exclusions dislikes and skip cooldown never relax`() {
        val tracks = listOf("a", "b", "c", "d").map { RadioTrack(it, it) }
        val events = listOf(RadioEvent("skip", "skipped", "b", 999.0))
        val picks = OfflineRecommender(tracks, emptyList()).recommendSequence(
            RadioProfile(events = events, dislikes = setOf("c")), RadioSettings(skipWindow = 1000.0), 1000.0,
            count = 1, excluded = setOf("a"), recentRecommendations = listOf("b"))
        assertEquals(listOf("d"), picks.map { it.songId })
    }

    @Test fun `diagnostics distinguish eligible vectors and real usable learned taste`() {
        val tracks = listOf(RadioTrack("a", "a"), RadioTrack("b", "b"), RadioTrack("c", "c"))
        val vectors = listOf(vec("a", 1f, 0f), vec("b", 0f, 1f), RadioVector("c", "audio", "m", "1", floatArrayOf(0f, 0f)))
        val engine = OfflineRecommender(tracks, vectors)
        val empty = engine.diagnostics(RadioProfile(current = "a"), RadioSettings(), 100.0)
        assertEquals(3, empty.eligibleTracks)
        assertEquals(2, empty.textVectorTracks)
        assertEquals(0, empty.audioVectorTracks)
        assertFalse(empty.hasUsableTaste)
        assertEquals(setOf("a", "b"), empty.bootstrapCandidates)

        val learned = engine.diagnostics(RadioProfile(events = listOf(
            RadioEvent("start", "started", "a", 1.0),
            RadioEvent("complete", "completed", "a", 2.0),
            RadioEvent("unknown", "started", "missing", 3.0))), RadioSettings(), 100.0)
        assertEquals(1, learned.tasteEvents)
        assertEquals(1, learned.contributingTasteEvents)
        assertTrue(learned.hasUsableTaste)
        val oppositeVectors = vectors + vec("opposite", -1f, 0f)
        val canceled = OfflineRecommender(tracks + RadioTrack("opposite", "opposite"), oppositeVectors)
            .diagnostics(RadioProfile(hearts = setOf("a", "opposite")), RadioSettings(), 100.0)
        assertFalse(canceled.hasUsableTaste)
        val audioOnly = OfflineRecommender(tracks, vectors.map { it.copy(space = "audio") })
        assertFalse(audioOnly.diagnostics(RadioProfile(events = listOf(RadioEvent("a", "completed", "a", 1.0))), RadioSettings(audioBlend = 0.0), 100.0).hasUsableTaste)
    }

    @Test fun `bootstrap steers cold start without inventing history and yields to learned taste`() {
        val tracks = listOf("a", "b", "c").map { RadioTrack(it, it) }
        val vectors = listOf(vec("a", 1f, 0f), vec("b", .8f, .6f), vec("c", 0f, 1f))
        val engine = OfflineRecommender(tracks, vectors)
        val cold = RadioProfile(current = "c", bootstrap = setOf("a"))
        val coldPicks = engine.recommendSequence(cold, RadioSettings(exploration = .9), 100.0, count = 1, excluded = setOf("a"))
        assertEquals("b", coldPicks.single().songId)
        assertTrue(coldPicks.single().reason.contains("temporary starting songs", ignoreCase = true))
        assertTrue(engine.diagnostics(cold, RadioSettings(), 100.0).hasUsableTaste.not())
        assertTrue(cold.events.isEmpty())

        val warm = cold.copy(events = listOf(RadioEvent("learn", "completed", "c", 1.0)))
        val warmPick = engine.recommendSequence(warm, RadioSettings(exploration = 0.0), 100.0, count = 1, excluded = setOf("c")).single()
        assertEquals("b", warmPick.songId)
        assertEquals(1, warm.events.size)
    }

    @Test fun `vibe sequence keeps four to one slot schedule and anchors subsequent choices`() {
        val tracks = listOf(
            RadioTrack("current", "current", cluster = 1),
            RadioTrack("lock", "lock", cluster = 2),
            RadioTrack("lock-2", "lock-2", cluster = 1),
            RadioTrack("lock-3", "lock-3", cluster = 2),
            RadioTrack("lock-4", "lock-4", cluster = 2),
            RadioTrack("other", "other", cluster = 1)
        )
        val vectors = listOf(vec("current", -1f, 0f), vec("lock", 1f, 0f), vec("lock-2", 1f, 0f),
            vec("lock-3", 1f, 0f), vec("lock-4", 1f, 0f), vec("other", 1f, 0f))
        val picks = OfflineRecommender(tracks, vectors, seed = 31).recommendSequence(
            RadioProfile(current = "current", hearts = setOf("lock")), RadioSettings(vibeLock = "lock", exploration = 1.0), 20.0, count = 5)
        assertEquals(listOf("exploit", "exploit", "exploit", "exploit", "explore"), picks.map { it.slot })
        assertEquals("lock", picks.first().songId)
        assertEquals(2, picks[1].cluster)
        assertEquals("Matches locked vibe", picks[1].reason)
    }

    @Test fun `hundreds song library supports four distinct refill batches with coherent transitions`() {
        val tracks = (0 until 400).map { RadioTrack("s$it", "s$it", cluster = it % 20, energy = (it % 100) / 100.0) }
        val vectors = tracks.map { row -> vec(row.songId, kotlin.math.cos(row.cluster!!.toDouble()).toFloat(), kotlin.math.sin(row.cluster.toDouble()).toFloat()) }
        val engine = OfflineRecommender(tracks, vectors, seed = 77)
        fun session(): List<RadioPick> {
            val committed = mutableListOf<RadioPick>()
            repeat(4) { batch ->
                val profile = RadioProfile(current = committed.lastOrNull()?.songId)
                val refill = engine.recommendSequence(profile, RadioSettings(exploration = .3), 100_000.0 + batch,
                    count = 5, recentRecommendations = committed.map { it.songId }, sequenceOffset = committed.size)
                assertEquals(5, refill.size)
                assertTrue(committed.none { old -> refill.any { it.songId == old.songId } })
                committed += refill
            }
            return committed
        }
        fun v1RefillSession(recommender: OfflineRecommender): List<RadioPick> {
            val committed = mutableListOf<RadioPick>()
            repeat(4) { batch ->
                val profile = RadioProfile(current = committed.lastOrNull()?.songId)
                val refill = recommender.recommend(profile, RadioSettings(exploration = .3), 100_000.0 + batch,
                    count = 5, excluded = committed.lastOrNull()?.let { setOf(it.songId) } ?: emptySet())
                assertEquals(5, refill.size)
                committed += refill
            }
            return committed
        }
        val first = session()
        val sameSeed = OfflineRecommender(tracks, vectors, seed = 77).let { other ->
            val committed = mutableListOf<RadioPick>()
            repeat(4) { batch -> committed += other.recommendSequence(RadioProfile(current = committed.lastOrNull()?.songId), RadioSettings(exploration = .3), 100_000.0 + batch, 5, recentRecommendations = committed.map { it.songId }, sequenceOffset = committed.size) }
            committed
        }
        assertEquals(20, first.map { it.songId }.toSet().size)
        assertEquals(first.map { it.songId }, sameSeed.map { it.songId })
        val v1Refills = v1RefillSession(engine)
        val equalTracks = (0 until 32).map { RadioTrack("tie$it", "tie$it") }
        val equalVectors = equalTracks.map { vec(it.songId, 1f, 0f) }
        val tieSettings = RadioSettings(exploration = 0.0)
        val tieProfile = RadioProfile()
        val tied = OfflineRecommender(equalTracks, equalVectors, seed = 5).recommendSequence(tieProfile, tieSettings, 10.0, 20)
        val tiedAgain = OfflineRecommender(equalTracks, equalVectors, seed = 5).recommendSequence(tieProfile, tieSettings, 10.0, 20)
        val otherSeed = OfflineRecommender(equalTracks, equalVectors, seed = 6).recommendSequence(tieProfile, tieSettings, 10.0, 20)
        assertEquals(tied.map { it.songId }, tiedAgain.map { it.songId })
        assertNotEquals(tied.map { it.songId }, otherSeed.map { it.songId })

        fun adjacentSimilarity(rows: List<RadioPick>): Double = rows.zipWithNext().map { (a, b) ->
            val av = vectors.first { it.songId == a.songId }.values
            val bv = vectors.first { it.songId == b.songId }.values
            av.indices.sumOf { av[it].toDouble() * bv[it] }
        }.average()
        val sequenceSimilarity = adjacentSimilarity(first)
        val v1Similarity = adjacentSimilarity(v1Refills)
        val sequenceUnique = first.map { it.songId }.toSet().size
        val v1Unique = v1Refills.map { it.songId }.toSet().size
        val sequenceRepeats = first.size - sequenceUnique
        val v1Repeats = v1Refills.size - v1Unique
        println("RADIO_SEQUENCE_METRICS sequenceAdjacent=$sequenceSimilarity sequenceUnique=$sequenceUnique/20 sequenceRepeats=$sequenceRepeats; v1RefillAdjacent=$v1Similarity v1RefillUnique=$v1Unique/20 v1RefillRepeats=$v1Repeats")
        assertEquals(20, sequenceUnique)
        assertTrue("sequence coherence $sequenceSimilarity should remain close to v1 refill $v1Similarity",
            sequenceSimilarity >= v1Similarity - .05)
    }

    @Test fun `exploration does not jump to a much lower scoring cluster`() {
        val tracks = listOf(RadioTrack("context", "context", cluster = 1), RadioTrack("near", "near", cluster = 1), RadioTrack("far", "far", cluster = 9))
        val vectors = listOf(vec("context", 1f, 0f), vec("near", 1f, 0f), vec("far", -1f, 0f))
        val pick = OfflineRecommender(tracks, vectors).recommendSequence(RadioProfile(current = "context"), RadioSettings(exploration = .9), 10.0, 1, excluded = setOf("context"), sequenceOffset = 1).single()
        assertEquals("near", pick.songId)
    }

    @Test fun `single song remains available and vectorless sparse libraries do not fail`() {
        val one = OfflineRecommender(listOf(RadioTrack("solo", "solo")), emptyList())
        assertEquals(listOf("solo"), one.recommendSequence(RadioProfile(), RadioSettings(), 1.0, count = 20).map { it.songId })
        val sparse = OfflineRecommender(listOf(RadioTrack("a", "a"), RadioTrack("b", "b"), RadioTrack("c", "c")), listOf(vec("a", 1f, 0f)))
        val picks = sparse.recommendSequence(RadioProfile(dislikes = setOf("b")), RadioSettings(), 1.0, count = 10)
        assertEquals(listOf("a", "c"), picks.map { it.songId }.toSet().toList().sorted())
    }
}
