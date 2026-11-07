package dev.radio.mobile

import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.connection.RadioTransport
import dev.radio.mobile.data.JournalIdentityProvider.Entry
import dev.radio.mobile.data.JournalIdentityProvider.MappingSnapshot
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.*
import java.security.MessageDigest
import kotlinx.coroutines.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Test

/** Fake desktop reached through the real [RadioTransport] seam. Replies are scripted per path. */
private class FakeServer : RadioTransport {
    val calls = mutableListOf<Triple<String, String?, String?>>() // path, body, bearer
    var capabilities = """{"protocol_version":1,"connected_recommendations":true,"scopes":["catalog:read","recommend:read"]}"""
    var availability: (Map<*, *>) -> RadioResponse = { RadioResponse(200, """{"digest":"${it["digest"]}","count":${(it["song_ids"] as List<*>).size},"catalog_id":"c","catalog_revision":${it["catalog_revision"]}}""") }
    var epoch = 0L
    var contexts = 0
    var contextReply: (String, Map<*, *>) -> RadioResponse = { _, _ -> RadioResponse(200, """{"context_id":"ctx-aaaaaaaaaaaaaaaa","epoch":${++epoch},"expires_in":1800}""") }
    var picks: List<Triple<String, String, String>> = emptyList()
    var status = "ok"
    var vibeLocked = false
    var recommendation: suspend (Map<*, *>) -> RadioResponse = { req ->
        RadioResponse(200, ConnectedJson.write(linkedMapOf("request_id" to req["request_id"], "result_id" to "r1", "context_id" to "ctx-aaaaaaaaaaaaaaaa",
            "epoch" to req["epoch"], "availability_digest" to req["availability_digest"], "catalog_revision" to req["catalog_revision"],
            "anchor_song_id" to req["anchor_song_id"], "status" to status, "ranked_by" to "llm", "vibe_locked" to vibeLocked,
            "picks" to picks.map { linkedMapOf("song_id" to it.first, "slot" to it.second, "reason" to it.third) })))
    }
    fun count(prefix: String) = calls.count { it.first == prefix }
    override suspend fun request(endpoint: String, path: String, body: String?, bearer: String?): RadioResponse {
        calls += Triple(path, body, bearer)
        val parsed = body?.let { ConnectedJson.parse(it) as Map<*, *> }
        return when {
            path == "/capabilities" -> RadioResponse(200, capabilities)
            path == "/availability" -> availability(parsed!!)
            path == "/contexts" -> { contexts++; contextReply("", parsed!!) }
            path.endsWith("/recommendations") -> recommendation(parsed!!)
            path.startsWith("/contexts/") -> contextReply(path, parsed!!)
            else -> RadioResponse(404, "{}")
        }
    }
}

private class TransportLink(val transport: RadioTransport, val key: String = "server:device") : ConnectedServerLink {
    override suspend fun access() = ConnectedAccess.parse(key, transport.request("https://x", "/capabilities", null, "bearer").body)
    override suspend fun post(path: String, body: String) = transport.request("https://x", path, body, "bearer")
}

class ConnectedRadioSourceTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    @After fun stop() { scope.cancel() }

    private fun song(id: String, available: Boolean = true) = LocalSong(id, "tree", "folder", "content://fixture/$id", "$id.mp3", "Folder", 1, 1, 1, 1000, available)
    private val songs = (1..8).associate { "l$it" to song("l$it") }
    private fun entry(local: String, server: String, revision: Long = 12) = Entry(songs.getValue(local), "srv", "cat", revision, server, "prov:$revision")
    private fun mappings(vararg pairs: Pair<String, String>, generation: Long = 1) =
        MappingSnapshot(generation, pairs.associate { it.first to entry(it.first, it.second) }, loaded = true)
    private val full = arrayOf("l1" to "s1", "l2" to "s2", "l3" to "s3", "l4" to "s4", "l5" to "s5", "l6" to "s6")

    private fun request(map: MappingSnapshot = mappings(*full), anchor: String = "l1", exclude: Set<String> = setOf("l1"), disliked: Set<String> = emptySet(),
        available: Map<String, LocalSong> = songs, vibe: String? = null, recent: List<ConnectedRecent> = emptyList()) =
        ConnectedRequest(songs.getValue(anchor), "neutral", .3, vibe, map, available, recent, exclude, { it in disliked},
            ConnectedFence(anchor, "neutral", .3, vibe, map.generation, 0))

    private fun source(server: FakeServer, timeout: Long = 5_000, link: ConnectedServerLink = TransportLink(server)) =
        ConnectedRadioSource(link, scope, timeout, newId = { "req-${java.util.UUID.randomUUID()}" }).also { it.setEnabled(true) }

    private fun run(source: ConnectedRadioSource, request: ConnectedRequest) = runBlocking { source.begin(request)!!.join() }

    @Test fun digestUsesUtf8ByteOrderNotUtf16Order() {
        val ids = listOf("🎵", "～", "b", "a", "a")
        val expected = listOf("a", "b", "～", "🎵").joinToString("\n")
        val hex = MessageDigest.getInstance("SHA-256").digest(expected.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        assertEquals(hex, ConnectedDigest.of(ids))
        assertEquals(64, hex.length)
    }

    @Test fun timeoutConstantsMatchTheFrozenContract() {
        assertEquals(75_000L, ConnectedRadioSource.RECOMMEND_TIMEOUT_MS)
    }

    @Test fun uploadsAvailabilityCreatesContextAndSplitsPicksBySlot() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "close"), Triple("s3", "exploit", "also close"), Triple("s4", "explore", "new"), Triple("s5", "wildcard", "odd"), Triple("s6", "context", "evening")) }
        val source = source(server)
        run(source, request(recent = listOf(ConnectedRecent("l2", "completed"), ConnectedRecent("l3", "skipped"), ConnectedRecent("zzz", "completed"))))
        val set = source.set.value!!
        assertEquals(listOf("l2", "l3"), set.continuation.map { it.song.id })
        assertEquals(listOf("explore", "wildcard", "context"), set.options.map { it.slot })
        assertEquals("llm", set.rankedBy)
        assertEquals(1, server.count("/availability")); assertEquals(1, server.contexts)
        val availability = ConnectedJson.parse(server.calls.first { it.first == "/availability" }.second!!) as Map<*, *>
        assertEquals(listOf("s1", "s2", "s3", "s4", "s5", "s6"), availability["song_ids"])
        assertEquals(ConnectedDigest.of(listOf("s1", "s2", "s3", "s4", "s5", "s6")), availability["digest"])
        assertEquals(12L, availability["catalog_revision"]); assertEquals(1L, availability["protocol_version"])
        val context = ConnectedJson.parse(server.calls.first { it.first == "/contexts" }.second!!) as Map<*, *>
        assertEquals(listOf(mapOf("song_id" to "s2", "outcome" to "completed"), mapOf("song_id" to "s3", "outcome" to "skipped")), context["recent"])
        assertEquals("neutral", context["mood"]); assertNull(context["vibe_lock_song_id"])
        val rec = ConnectedJson.parse(server.calls.last().second!!) as Map<*, *>
        assertEquals("s1", rec["anchor_song_id"]); assertEquals(listOf("s1"), rec["exclude_song_ids"]); assertEquals(1L, rec["epoch"])
        assertTrue(source.status.value.lastOk == true && source.status.value.pendingAnchor == null)
    }

    @Test fun dropsUnmappedUnavailableDislikedQueuedCurrentAndDuplicatePicks() {
        val server = FakeServer().apply { picks = listOf(Triple("ghost", "exploit", ""), Triple("s2", "exploit", ""), Triple("s3", "exploit", ""), Triple("s4", "explore", ""),
            Triple("s5", "wildcard", ""), Triple("s1", "context", ""), Triple("s6", "context", ""), Triple("s6", "context", "dup")) }
        val unavailable = songs + ("l5" to song("l5", available = false))
        val source = source(server)
        // s2 queued, s3 disliked, s4 fine, s5 unavailable (library says gone), s1 is current.
        run(source, request(map = mappings(*full), exclude = setOf("l1", "l2"), disliked = setOf("l3"), available = unavailable))
        val set = source.set.value!!
        assertEquals(emptyList<String>(), set.continuation.map { it.song.id })
        assertEquals(listOf("l4", "l6"), set.options.map { it.song.id })
        assertEquals(6, set.dropped)
        val rec = ConnectedJson.parse(server.calls.last().second!!) as Map<*, *>
        assertEquals(listOf("s1", "s2"), (rec["exclude_song_ids"] as List<*>).sortedBy { it.toString() })
    }

    @Test fun allPicksDroppedBecomesInsufficientWithAReason() {
        val server = FakeServer().apply { picks = listOf(Triple("ghost", "exploit", "")) }
        val source = source(server)
        run(source, request())
        assertNull(source.set.value)
        assertEquals(ConnectedFailureKind.INSUFFICIENT, source.status.value.lastFailure!!.kind)
        assertNull(source.status.value.pendingAnchor)
    }

    @Test fun serverInsufficientStatusIsRecorded() {
        val server = FakeServer().apply { status = "insufficient"; picks = emptyList() }
        val source = source(server)
        run(source, request())
        assertEquals(ConnectedFailureKind.INSUFFICIENT, source.status.value.lastFailure!!.kind)
    }

    @Test fun uploadsAvailabilityOnlyWhenTheDigestChanges() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val source = source(server)
        run(source, request()); run(source, request(anchor = "l2", exclude = setOf("l2"), recent = listOf(ConnectedRecent("l1", "completed"))))
        assertEquals(1, server.count("/availability"))
        assertEquals(1, server.contexts)
        assertEquals(1, server.calls.count { it.first == "/contexts/ctx-aaaaaaaaaaaaaaaa" })
        run(source, request(map = mappings(*full, "l7" to "s7", generation = 2)))
        assertEquals(2, server.count("/availability"))
        // An unavailable song leaves the shared set too.
        run(source, request(map = mappings(*full, "l7" to "s7", generation = 2), available = songs + ("l7" to song("l7", false))))
        assertEquals(3, server.count("/availability"))
        // Same digest, new catalog revision: upload again.
        val bumped = MappingSnapshot(3, full.associate { it.first to entry(it.first, it.second, revision = 13) }, loaded = true)
        run(source, request(map = bumped)); run(source, request(map = mappings(*full)))
        assertEquals(5, server.count("/availability"))
    }

    @Test fun contextUpdatesCarryTheNewEpochIntoTheNextRequest() {
        val server = FakeServer().apply { picks = listOf(Triple("s3", "exploit", "")) }
        val source = source(server)
        run(source, request()); run(source, request(anchor = "l2", exclude = setOf("l2"), recent = listOf(ConnectedRecent("l1", "completed"))))
        val epochs = server.calls.filter { it.first.endsWith("/recommendations") }.map { (ConnectedJson.parse(it.second!!) as Map<*, *>)["epoch"] }
        assertEquals(listOf(1L, 2L), epochs)
        assertEquals(2L, source.latestEpoch)
    }

    @Test fun capabilityAndScopeGateEverything() {
        val noScope = FakeServer().apply { capabilities = """{"connected_recommendations":true,"scopes":["catalog:read"]}""" }
        source(noScope).also { run(it, request()) }.let {
            assertEquals(ConnectedFailureKind.NOT_PERMITTED, it.status.value.lastFailure!!.kind)
            assertEquals("Not permitted on desktop", it.status.value.lastMessage); assertEquals(false, it.status.value.permitted)
        }
        assertEquals(0, noScope.count("/availability"))
        val noCap = FakeServer().apply { capabilities = """{"scopes":["recommend:read"]}""" }
        source(noCap).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.NOT_CAPABLE, it.status.value.lastFailure!!.kind) }
        assertEquals(0, noCap.count("/availability"))
    }

    @Test fun http403OnAnyCallIsNotPermitted() {
        val server = FakeServer().apply { recommendation = { RadioResponse(403, """{"detail":"Device token lacks recommend:read scope"}""") } }
        val source = source(server)
        run(source, request())
        assertEquals(ConnectedFailureKind.NOT_PERMITTED, source.status.value.lastFailure!!.kind)
        val upload = FakeServer().apply { availability = { RadioResponse(403, "{}") } }
        source(upload).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.NOT_PERMITTED, it.status.value.lastFailure!!.kind) }
    }

    @Test fun epochStaleResyncsContextAndRetriesOnce() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val good = server.recommendation
        var stale = 1
        server.recommendation = { if (stale-- > 0) RadioResponse(409, """{"detail":{"reason":"epoch_stale"}}""") else good(it) }
        val source = source(server)
        run(source, request())
        assertNotNull(source.set.value)
        assertEquals(2, server.calls.count { it.first.endsWith("/recommendations") })
        assertEquals(1, server.calls.count { it.first == "/contexts/ctx-aaaaaaaaaaaaaaaa" }) // forced re-sync
        val epochs = server.calls.filter { it.first.endsWith("/recommendations") }.map { (ConnectedJson.parse(it.second!!) as Map<*, *>)["epoch"] }
        assertEquals(listOf(1L, 2L), epochs)
    }

    @Test fun availabilityStaleReuploadsAndRetriesOnceThenReportsStale() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val good = server.recommendation
        var stale = 1
        server.recommendation = { if (stale-- > 0) RadioResponse(409, """{"detail":{"reason":"availability_stale"}}""") else good(it) }
        val source = source(server)
        run(source, request())
        assertNotNull(source.set.value); assertEquals(2, server.count("/availability"))

        val stuck = FakeServer().apply { recommendation = { RadioResponse(409, """{"detail":{"reason":"no_availability"}}""") } }
        val failing = source(stuck)
        run(failing, request())
        assertEquals(ConnectedFailureKind.STALE, failing.status.value.lastFailure!!.kind)
        assertEquals(2, stuck.calls.count { it.first.endsWith("/recommendations") }) // exactly one retry
    }

    @Test fun supersededAndCatalogMismatchAreRecordedWithoutRetry() {
        val a = FakeServer().apply { recommendation = { RadioResponse(409, """{"detail":{"reason":"superseded"}}""") } }
        source(a).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.SUPERSEDED, it.status.value.lastFailure!!.kind) }
        assertEquals(1, a.calls.count { it.first.endsWith("/recommendations") })
        val b = FakeServer().apply { availability = { RadioResponse(409, """{"detail":{"reason":"catalog_mismatch"}}""") } }
        source(b).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.CATALOG_MISMATCH, it.status.value.lastFailure!!.kind) }
    }

    @Test fun lostContextIsRecreatedOnce() {
        val server = FakeServer().apply { picks = listOf(Triple("s3", "exploit", "")) }
        val source = source(server)
        run(source, request())
        val inner = server.contextReply
        server.contextReply = { path, body -> if (path.isNotEmpty()) RadioResponse(404, """{"detail":"context_not_found"}""") else inner(path, body) }
        run(source, request(anchor = "l2", exclude = setOf("l2"), recent = listOf(ConnectedRecent("l1", "completed"))))
        assertNotNull(source.set.value)
        assertEquals(2, server.contexts)
    }

    @Test fun networkFailureTimeoutAndServerErrorsEachRecordAReason() {
        val network = FakeServer().apply { recommendation = { throw ConnectionFailure("Cannot reach the server.") } }
        source(network).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.UNREACHABLE, it.status.value.lastFailure!!.kind); assertNull(it.status.value.pendingAnchor) }
        val hung = FakeServer().apply { recommendation = { awaitCancellation() } }
        source(hung, timeout = 150).also { run(it, request()) }.let {
            assertEquals(ConnectedFailureKind.TIMEOUT, it.status.value.lastFailure!!.kind); assertNull(it.status.value.pendingAnchor); assertNull(it.set.value)
        }
        val broken = FakeServer().apply { recommendation = { RadioResponse(500, "oops") } }
        source(broken).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.SERVER_ERROR, it.status.value.lastFailure!!.kind) }
        val unpaired = object : ConnectedServerLink {
            override suspend fun access(): ConnectedAccess = throw ConnectionFailure("Pair this phone first.")
            override suspend fun post(path: String, body: String) = throw IllegalStateException()
        }
        source(FakeServer(), link = unpaired).also { run(it, request()) }.let { assertEquals(ConnectedFailureKind.UNREACHABLE, it.status.value.lastFailure!!.kind); assertTrue(it.status.value.lastMessage.contains("Pair this phone")) }
    }

    @Test fun replyThatDoesNotEchoEveryFenceIsRejected() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val good = server.recommendation
        server.recommendation = { req -> val reply = ConnectedJson.parse(good(req).body) as Map<*, *>; RadioResponse(200, ConnectedJson.write(reply + ("availability_digest" to "0".repeat(64)))) }
        val source = source(server)
        run(source, request())
        assertNull(source.set.value)
        assertEquals(ConnectedFailureKind.INVALID_REPLY, source.status.value.lastFailure!!.kind)
    }

    @Test fun aNewerSongStartMakesAnOlderResultStale() {
        val release = CompletableDeferred<Unit>()
        val server = FakeServer().apply { picks = listOf(Triple("s3", "exploit", "")) }
        val fast = server.recommendation
        var first = true
        server.recommendation = { req -> if (first) { first = false; release.await() }; fast(req) }
        val source = source(server)
        runBlocking {
            val old = source.begin(request())!!
            delay(100)
            assertEquals("l1", source.status.value.pendingAnchor)
            val newer = source.begin(request(anchor = "l2", exclude = setOf("l2"), recent = listOf(ConnectedRecent("l1", "completed"))))!!
            assertEquals("l2", source.status.value.pendingAnchor)
            newer.join()
            release.complete(Unit)
            old.join()
            assertEquals("l2", source.set.value!!.anchorLocalId)
        }
    }

    @Test fun oneRequestInFlightAtATime() {
        val server = FakeServer().apply { picks = listOf(Triple("s3", "exploit", "")) }
        var running = 0; var max = 0
        val fast = server.recommendation
        server.recommendation = { req -> running++; max = maxOf(max, running); try { delay(50); fast(req) } finally { running-- } }
        val source = source(server)
        runBlocking {
            source.begin(request()); delay(10)
            source.begin(request(anchor = "l2", exclude = setOf("l2")))!!.join()
        }
        assertEquals(1, max)
    }

    @Test fun vibeLockSendsServerIdAndKeepsOnlyExploreOptions() {
        val server = FakeServer().apply { vibeLocked = true; picks = listOf(Triple("s2", "exploit", ""), Triple("s3", "exploit", ""), Triple("s4", "explore", "x"), Triple("s5", "wildcard", "y")) }
        val source = source(server)
        run(source, request(vibe = "l1"))
        val context = ConnectedJson.parse(server.calls.first { it.first == "/contexts" }.second!!) as Map<*, *>
        assertEquals("s1", context["vibe_lock_song_id"])
        val set = source.set.value!!
        assertEquals(2, set.continuation.size); assertEquals(listOf("l4"), set.options.map { it.song.id }); assertTrue(set.vibeLocked)
    }

    @Test fun exclusionsAreCappedAtTwoHundredServerIds() {
        val many = (1..300).associate { "m$it" to song("m$it") }
        val map = MappingSnapshot(1, many.mapValues { (k, v) -> Entry(v, "srv", "cat", 12, "x$k", "p") } + mappings(*full).entries, loaded = true)
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val source = source(server)
        run(source, request(map = map, available = songs + many, exclude = many.keys + "l1"))
        val rec = ConnectedJson.parse(server.calls.last().second!!) as Map<*, *>
        assertEquals(200, (rec["exclude_song_ids"] as List<*>).size)
    }

    @Test fun disabledSourceMakesNoCallsAndFencesExpireTheHeldSet() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val off = ConnectedRadioSource(TransportLink(server), scope)
        assertNull(off.begin(request()))
        assertTrue(server.calls.isEmpty())
        val source = source(server)
        run(source, request())
        val fence = source.set.value!!.fence
        source.revalidate(fence)
        assertNotNull(source.set.value)
        source.revalidate(fence.copy(mood = "chill")); assertNull(source.set.value)
        run(source, request()); source.revalidate(fence.copy(vibe = "l2")); assertNull(source.set.value)
        run(source, request()); source.revalidate(fence.copy(mappingGeneration = 9)); assertNull(source.set.value)
        run(source, request()); source.revalidate(fence.copy(anchorLocalId = "l2")); assertNull(source.set.value)
        run(source, request()); source.revalidate(fence.copy(syncEpoch = 4)); assertNull(source.set.value)
        run(source, request()); source.revalidate(null); assertNull(source.set.value)
    }

    @Test fun permissionCheckRecordsGrantedAndDeniedWithoutTouchingTheHeldSet() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val source = source(server)
        run(source, request())
        val held = source.set.value
        server.capabilities = """{"connected_recommendations":true,"scopes":["catalog:read"]}"""
        runBlocking { source.checkAccess() }
        assertEquals(false, source.status.value.permitted)
        assertEquals("Not permitted on desktop", source.status.value.lastMessage)
        server.capabilities = """{"connected_recommendations":true,"scopes":["recommend:read"]}"""
        runBlocking { source.checkAccess() }
        assertEquals(true, source.status.value.permitted)
        assertSame(held, source.set.value)
    }

    @Test fun turningItOffClearsTheSetAndPendingState() {
        val server = FakeServer().apply { picks = listOf(Triple("s2", "exploit", "")) }
        val source = source(server)
        run(source, request())
        source.setEnabled(false)
        assertNull(source.set.value); assertFalse(source.status.value.enabled); assertNull(source.status.value.pendingAnchor)
    }

    @Test fun jsonCodecRoundTripsAndRejectsDuplicateKeys() {
        val text = ConnectedJson.write(linkedMapOf("a" to "x\"y\n\u0001", "n" to null, "l" to listOf(1, 2.5, true)))
        assertEquals(mapOf("a" to "x\"y\n\u0001", "n" to null, "l" to listOf(1L, 2.5, true)), ConnectedJson.parse(text))
        assertThrows(IllegalArgumentException::class.java) { ConnectedJson.parse("""{"a":1,"a":2}""") }
        assertThrows(IllegalArgumentException::class.java) { ConnectedJson.parse("""{"a":1} x""") }
    }
}
