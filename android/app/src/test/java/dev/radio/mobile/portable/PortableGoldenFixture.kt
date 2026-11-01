package dev.radio.mobile.portable

/** Typed JVM view of backend/tests/fixtures/portable_golden.json; generated from the checked-in wire fixture. */
internal object PortableGoldenFixture {
    const val seed = 17
    const val now = 2000.0
    const val audioBlend = 0.65
    const val mood = "neutral"
    const val exploration = 0.3
    val tracks = listOf(
        GoldenTrack("1", "North", 0.5, 0.5, 0, "Morning", setOf("ambient"), setOf("calm")),
        GoldenTrack("2", "East", 0.6, 0.6, 1, "Day", setOf("pop"), setOf("bright")),
        GoldenTrack("3", "West", 0.4, 0.4, 2, "Morning", setOf("ambient"), setOf("calm")),
        GoldenTrack("4", "South", 0.8, 0.6, 3, "Day", setOf("rock"), setOf()),
        GoldenTrack("5", "Ocean", 0.2, 0.3, 4, "Night", setOf("ambient"), setOf()),
        GoldenTrack("6", "Sky", 0.5, 0.5, 5, "Day", setOf("pop"), setOf())
    )
    val vectors = listOf(
        GoldenVector("1", "text", "fixture-text", "1", floatArrayOf(1f, 0f, 0f)),
        GoldenVector("2", "text", "fixture-text", "1", floatArrayOf(0.8f, 0.6f, 0f)),
        GoldenVector("3", "text", "fixture-text", "1", floatArrayOf(0.6f, 0.8f, 0f)),
        GoldenVector("4", "text", "fixture-text", "1", floatArrayOf(0f, 1f, 0f)),
        GoldenVector("5", "text", "fixture-text", "1", floatArrayOf(-1f, 0f, 0f)),
        GoldenVector("1", "audio", "fixture-audio", "1", floatArrayOf(1f, 0f)),
        GoldenVector("2", "audio", "fixture-audio", "1", floatArrayOf(0.6f, 0.8f)),
        GoldenVector("3", "audio", "fixture-audio", "1", floatArrayOf(0.8f, 0.6f)),
        GoldenVector("4", "audio", "fixture-audio", "1", floatArrayOf(0f, 1f)),
        GoldenVector("5", "audio", "fixture-audio", "1", floatArrayOf(-1f, 0f))
    )
    val localTracks = listOf(
        GoldenLocalTrack("phone-1", "1", "", "", 0.5),
        GoldenLocalTrack("phone-2", "2", "", "", 0.5),
        GoldenLocalTrack("phone-3", "3", "", "", 0.5),
        GoldenLocalTrack("phone-4", "4", "", "", 0.5),
        GoldenLocalTrack("phone-5", "5", "", "", 0.5),
        GoldenLocalTrack("phone-6", "6", "", "", 0.5),
        GoldenLocalTrack("extra", "", "Phone only", "Phone", 0.5)
    )
    val history = listOf(
        GoldenEvent("start1", "started", "1", 0.0),
        GoldenEvent("end1", "completed", "1", 180.0)
    )
    val expectedRankIds = listOf("3", "2", "6", "local:extra", "1", "4", "5")
    val expectedScores = listOf(0.70800001, 0.67200001, 0.3, 0.3, 0.28, 0.23, -0.38)
    val expectedSelectionIds = listOf("3", "2", "6", "local:extra", "1")
    val expectedSlots = listOf("exploit", "explore", "wildcard", "context", "fallback")
    const val replayCurrent = "1"
    val upcomingAfterReplay = listOf("3", "2", "6", "local:extra", "1")
}

internal data class GoldenTrack(val songId:String,val artist:String,val energy:Double,val valence:Double,val cluster:Int,val folder:String,val genres:Set<String>,val collections:Set<String>)
internal data class GoldenVector(val songId:String,val space:String,val model:String,val version:String,val values:FloatArray)
internal data class GoldenLocalTrack(val localId:String,val songId:String,val title:String,val artist:String,val energy:Double)
internal data class GoldenEvent(val id:String,val kind:String,val songId:String,val at:Double)
