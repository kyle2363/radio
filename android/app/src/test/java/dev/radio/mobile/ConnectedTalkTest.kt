package dev.radio.mobile

import dev.radio.mobile.connection.RadioApiClient
import dev.radio.mobile.connection.RadioBinaryResponse
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.NormalizationProfile
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.playback.TalkNow
import dev.radio.mobile.playback.snapshot
import dev.radio.mobile.recommendation.*
import java.nio.file.Files
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class ConnectedTalkTest {
    private val sha = "a".repeat(64)
    private val rid = "resource-aaaaaaaaaaaaaaaa"
    private val job = "job-aaaaaaaaaaaaaaaaaaaa"

    private fun readyJson(bytes: ByteArray = ByteArray(10) { 1 }, digest: String = TalkCache.sha256(bytes)) = """
        {"job_id":"$job","turn_id":"t1","state":"ready","kind":"talk","segment_type":"caller","title":"Mike on the line",
         "speakers":[{"id":"dj","name":"DJ"},{"id":"caller_mike","name":"Mike"}],
         "transcript":[{"speaker":"dj","text":"Hello"},{"speaker":"caller_mike","text":"Hi there"},{"speaker":"dj","text":""}],
         "duration_ms":41230,"shift":{"mood":"energetic","feel":null,"restore_at_end":false},"guest_set":null,
         "future_field":{"x":1},
         "resource":{"id":"$rid","sha256":"$digest","byte_length":${bytes.size},"mime":"audio/mp4","expires_at":1795000000}}
    """.trimIndent()

    private fun info(bytes: ByteArray = ByteArray(10) { 1 }) = TalkClipInfo.parse(job, ConnectedJson.parse(readyJson(bytes)) as Map<*, *>)!!

    @Test fun volumeTableAttenuatesOnly() {
        assertEquals(1f, TalkVolume.LOUDER.linear, 1e-6f)
        assertEquals(0.631f, TalkVolume.NORMAL.linear, 1e-3f)
        assertEquals(0.398f, TalkVolume.QUIETER.linear, 1e-3f)
        TalkVolume.entries.forEach { assertTrue(it.linear <= 1f) }
    }

    @Test fun talkGainBypassesSongNormalization() {
        val profile = NormalizationProfile(enabled = false, gains = mapOf("song" to .5f), talkGain = TalkVolume.QUIETER.linear)
        assertEquals(TalkVolume.QUIETER.linear, profile.gain("talk:$job"), 1e-6f)
        assertEquals(1f, profile.gain("song"), 1e-6f)
        val on = profile.copy(enabled = true, gains = mapOf("song" to .5f, "talk:$job" to .1f))
        assertEquals(.5f, on.gain("song"), 1e-6f)
        assertEquals(TalkVolume.QUIETER.linear, on.gain("talk:$job"), 1e-6f)
    }

    @Test fun settingsWireOmitsDesktopDefaults() {
        assertNull(TalkSettings(enabled = true).toWire())
        val custom = TalkSettings(enabled = true, frequency = 20, callers = false, guestSongs = 1, volume = TalkVolume.LOUDER)
        assertEquals(mapOf("frequency" to 10, "callers" to false, "guest_songs" to 2), custom.toWire())
        val decoded = TalkSettings.decode(custom.encode())
        assertEquals(custom.normalized(), decoded)
        assertEquals(TalkSettings(enabled = true), custom.desktopDefaults())
        assertFalse(TalkSettings.decode(null).enabled)
        assertEquals(TalkSettings(), TalkSettings.decode("not json"))
    }

    @Test fun capabilityParsesAndExplains() {
        val cap = TalkCapability.parse(ConnectedJson.parse("""{"available":false,"reason":"tts_disabled","max_clip_seconds":180}"""))!!
        assertFalse(cap.available)
        assertEquals("Speech is turned off on the desktop", cap.message)
        assertNull(TalkCapability.parse(null))
        val access = ConnectedAccess.parse("k", """{"connected_recommendations":true,"scopes":["recommend:read"],"connected_talk":{"available":true,"reason":null}}""")
        assertEquals(true, access.talk?.available)
        assertNull(ConnectedAccess.parse("k", """{"connected_recommendations":true,"scopes":[]}""").talk)
    }

    @Test fun readyMetadataParsesAndIgnoresUnknownFields() {
        val parsed = info()
        assertEquals("caller", parsed.segmentType)
        assertEquals("Mike on the line", parsed.title)
        assertEquals("DJ · Talk", parsed.artist)
        assertEquals(listOf(TalkLine("dj", "Hello"), TalkLine("caller_mike", "Hi there")), parsed.transcript)
        assertEquals("Mike", parsed.speakerName("caller_mike"))
        assertEquals(TalkShift("energetic", null, false), parsed.shift)
        assertEquals(41230L, parsed.durationMs)
    }

    @Test fun malformedMetadataIsRejected() {
        fun parse(text: String) = TalkClipInfo.parse(job, ConnectedJson.parse(text) as Map<*, *>)
        assertNull(parse(readyJson().replace("\"state\":\"ready\"", "\"state\":\"pending\"")))
        assertNull(parse(readyJson().replace("audio/mp4", "audio/mpeg")))
        assertNull(parse(readyJson(digest = "XYZ")))
        assertNull(parse(readyJson().replace(job, "other-job-aaaaaaaaaaaaaaa")))
        assertNull(parse(readyJson().replace("\"byte_length\":10", "\"byte_length\":${3 * 1024 * 1024}")))
    }

    @Test fun insertionMarginAndPolling() {
        assertTrue(TalkPolicy.canInsert(57_000, 60_000))
        assertFalse(TalkPolicy.canInsert(57_001, 60_000))
        assertFalse(TalkPolicy.canInsert(0, null))
        assertEquals(1_000L, TalkPolicy.pollDelayMs(0))
        assertEquals(5_000L, TalkPolicy.pollDelayMs(40))
        assertTrue(TalkPolicy.isTalk("talk:x")); assertFalse(TalkPolicy.isTalk("song"))
    }

    @Test fun stopAtEndFadeCoversLastTenSecondsOrWholeClip() {
        assertEquals(1f, TalkPolicy.stopFadeGain(20_000, 40_000), 1e-6f)
        assertEquals(.5f, TalkPolicy.stopFadeGain(35_000, 40_000), 1e-6f)
        assertEquals(0f, TalkPolicy.stopFadeGain(40_000, 40_000), 1e-6f)
        assertEquals(.5f, TalkPolicy.stopFadeGain(2_000, 4_000), 1e-6f)
    }

    @Test fun outcomesFollowWhatWasHeard() {
        assertEquals("played", TalkPolicy.outcome(started = true, completed = true))
        assertEquals("skipped", TalkPolicy.outcome(started = true, completed = false))
        assertEquals("discarded", TalkPolicy.outcome(started = false, completed = false))
    }

    @Test fun turnBodyUsesCatalogIdsOrFilenameTitlesAndBoundsExtras() {
        val body = ConnectedJson.parse(TalkWire.turn(TalkWire.Turn("t1", 5, TalkWire.song("Song A", "12"), TalkWire.song("my_file", null, "wildcard"), "chill",
            feedback = List(14) { TalkWire.Feedback("S$it", null, it % 2 == 0) }, milestones = listOf("m" to "1,000 songs"),
            history = TalkWire.History(3, 40), localIso = "2026-11-19T21:30:00", weekday = "thursday", talk = mapOf("frequency" to 3)))) as Map<*, *>
        assertEquals(mapOf("song_id" to "12"), body["after_song"])
        assertEquals(mapOf("title" to "my_file", "source" to "filename"), body["next_song"])  // slot only travels with catalog songs
        assertEquals(10, (body["session_feedback"] as List<*>).size)
        assertEquals(mapOf("iso" to "2026-11-19T21:30:00", "weekday" to "thursday"), body["local_time"])
        assertEquals(mapOf("frequency" to 3L), body["talk"])
        assertEquals(5L, body["turn_seq"])
        assertEquals(mapOf("song_id" to "9", "slot" to "wildcard"), TalkWire.song("x", "9", "wildcard"))
    }

    @Test fun milestonesAreSentOnce() {
        val outbox = MilestoneOutbox()
        assertEquals(listOf("a" to "A"), outbox.take(listOf("a" to "A")))
        assertEquals(listOf("b" to "B"), outbox.take(listOf("a" to "A", "b" to "B")))
        assertTrue(outbox.take(listOf("a" to "A")).isEmpty())
    }

    @Test fun cacheVerifiesBeforeWritingAndStaysBounded() {
        val dir = Files.createTempDirectory("talk").toFile()
        val cache = TalkCache(dir, maxFiles = 2)
        val bytes = ByteArray(10) { 1 }
        val good = info(bytes)
        assertNull(cache.store(good, ByteArray(10) { 2 }))       // wrong hash
        assertNull(cache.store(good, ByteArray(9) { 1 }))        // wrong length
        assertTrue(dir.listFiles().orEmpty().isEmpty())
        val clip = cache.store(good, bytes)!!
        assertTrue(cache.owns(clip.file))
        assertFalse(cache.owns(java.io.File(dir.parentFile, "elsewhere.m4a")))
        assertEquals("talk:$job", clip.mediaId)
        repeat(3) { i -> cache.store(good.copy(jobId = "job-$i-aaaaaaaaaaaaaaaaa"), bytes) }
        assertEquals(2, dir.listFiles()!!.count { it.name.endsWith(".m4a") })
        cache.clear()
        assertTrue(dir.listFiles().orEmpty().isEmpty())
    }

    private class FakeLink(val replies: MutableList<RadioResponse>, val clip: ByteArray = ByteArray(10) { 1 }) : ConnectedServerLink {
        val calls = mutableListOf<String>()
        override suspend fun access() = ConnectedAccess("k", true, true)
        override suspend fun post(path: String, body: String): RadioResponse { calls += "POST $path $body"; return replies.removeAt(0) }
        override suspend fun get(path: String): RadioResponse { calls += "GET $path"; return replies.removeAt(0) }
        override suspend fun download(path: String, maxBytes: Int): RadioBinaryResponse { calls += "DOWNLOAD $path $maxBytes"; return RadioBinaryResponse(200, clip) }
    }

    @Test fun sourceRunsTurnPollDownloadAndReport() = runBlocking {
        val link = FakeLink(mutableListOf(RadioResponse(200, """{"job_id":"$job","state":"pending"}"""),
            RadioResponse(200, """{"job_id":"$job","state":"pending"}"""), RadioResponse(200, readyJson()), RadioResponse(200, "{}")))
        val source = ConnectedTalkSource(link, TalkCache(Files.createTempDirectory("talk").toFile()))
        assertEquals(TalkTurnResult.Pending(job), source.start("context-aaaaaaaaaaaaaaaa", "{}"))
        val ready = source.await(job) { true }!!
        val clip = source.download(ready)!!
        assertTrue(clip.file.isFile)
        assertTrue(source.report(job, "skipped", 4.25))
        assertTrue(link.calls.last().endsWith("""{"protocol_version":1,"outcome":"skipped","heard_s":4.2}"""))
        assertTrue(link.calls.any { it == "DOWNLOAD /talk/resources/$rid ${TalkClipInfo.MAX_BYTES}" })
    }

    @Test fun sourceStopsPollingWhenTheGapHasPassedAndHandlesNone() = runBlocking {
        val link = FakeLink(mutableListOf(RadioResponse(200, """{"state":"none"}""")))
        val source = ConnectedTalkSource(link, TalkCache(Files.createTempDirectory("talk").toFile()))
        assertEquals(TalkTurnResult.None, source.start("context-aaaaaaaaaaaaaaaa", "{}"))
        assertNull(source.await(job) { false })
        assertEquals(1, link.calls.size)
        val tampered = FakeLink(mutableListOf(), clip = ByteArray(10) { 9 })
        assertNull(ConnectedTalkSource(tampered, TalkCache(Files.createTempDirectory("talk").toFile())).download(info()))
    }

    @Test fun apiClientAllowsOnlyTheTalkRoutes() {
        val ctx = "context-aaaaaaaaaaaaaaaa"
        assertEquals("TALK_TURN", RadioApiClient.connectedRoute("/contexts/$ctx/talk")?.name)
        assertEquals("TALK_JOB", RadioApiClient.connectedRoute("/talk/jobs/$job")?.name)
        assertEquals("TALK_OUTCOME", RadioApiClient.connectedRoute("/talk/jobs/$job/outcome")?.name)
        assertNull(RadioApiClient.connectedRoute("/talk/jobs/short"))
        assertTrue(RadioApiClient.isTalkResource("/talk/resources/$rid"))
        assertFalse(RadioApiClient.isTalkResource("/talk/resources/../x"))
    }

    @Test fun talkIsNeverBookmarkedTheNextSongIsFromItsStart() {
        fun song(id: String) = LocalSong(id, "tree", "folder", "content://fixture/$id", "$id.mp3", "Folder", 1, 1, 1, 10000, true)
        val state = PlaybackState(queue = listOf(song("a"), song("b")), playOrder = listOf("a", "b"), currentId = "talk:$job", positionMs = 12_000,
            talk = TalkNow(info(), "talk:$job", current = true, afterId = "a", nextId = "b"))
        val saved = state.snapshot()!!
        assertEquals("b", saved.currentId)
        assertEquals(0L, saved.positionMs)
        assertEquals("a", state.copy(currentId = "a", positionMs = 5, talk = state.talk!!.copy(current = false)).snapshot()!!.currentId)
    }

    @Test fun serverChipShowsOnlyWhenSomethingUsesTheDesktop() {
        assertNull(ServerChip.of(paired = false, desktopPicks = true, talk = true, reachable = true))
        assertNull(ServerChip.of(paired = true, desktopPicks = false, talk = false, reachable = true))
        assertEquals(ServerChip.Connected, ServerChip.of(paired = true, desktopPicks = false, talk = true, reachable = true))
        assertEquals(ServerChip.Connected, ServerChip.of(paired = true, desktopPicks = true, talk = false, reachable = true))
        assertEquals(ServerChip.Offline, ServerChip.of(paired = true, desktopPicks = false, talk = true, reachable = false))
        assertEquals(ServerChip.Checking, ServerChip.of(paired = true, desktopPicks = false, talk = true, reachable = null))
    }
}
