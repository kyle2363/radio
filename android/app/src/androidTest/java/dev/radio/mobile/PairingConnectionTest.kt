package dev.radio.mobile

import android.content.Context
import android.security.NetworkSecurityPolicy
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.*
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.cancelAndJoin
import org.json.JSONObject
import org.junit.After
import org.junit.Before
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class PairingConnectionTest {
    @Test fun binaryTransportNegotiatesCatalogBatchesAndUsesBoundedRawRequests() = runBlocking {
        val opened = mutableListOf<String>()
        val payload = byteArrayOf(1, 2, 3, 4)
        val client = RadioApiClient(EndpointPolicy(true), openConnection = { url ->
            opened += url.toString()
            object : HttpURLConnection(url) {
                override fun disconnect() {}
                override fun usingProxy() = false
                override fun connect() {}
                override fun getResponseCode() = 200
                override fun getContentLengthLong() = payload.size.toLong()
                override fun getInputStream() = java.io.ByteArrayInputStream(payload)
                override fun getHeaderField(name: String?): String? = if (name == "X-Radio-Catalog-Batches") "1" else null
            }
        })
        val endpoint = "http://127.0.0.1:18766"
        val bearer = WireBase64.encode(ByteArray(32) { 9 })
        assertTrue(client.download(endpoint, "/catalog", bearer, 16).catalogBatches)
        val digest = "a".repeat(64)
        val path = "/vector-batch?revision=1&manifest_sha256=$digest&digests=$digest"
        val response = client.download(endpoint, path, bearer, 4)
        assertArrayEquals(payload, response.body)
        assertFalse(response.catalogBatches)
        assertEquals("$endpoint/api/portable/v1$path", opened.last())
        assertThrows(ConnectionFailure::class.java) { runBlocking { client.download(endpoint, path, bearer, 3) } }
        val before = opened.size
        assertThrows(ConnectionFailure::class.java) { runBlocking { client.download(endpoint, path + "&extra=1", bearer, 4) } }
        assertEquals(before, opened.size)
    }
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val policy = EndpointPolicy(true)
    private val fixtureNamespace = "pairing_fixture"
    private fun fixtureStore() = PairedServerStore(context, policy, fixtureNamespace)
    private var defaultIdentity: Map<String, *> = emptyMap<String, Any>()
    private var defaultKeyExists = false
    @Before fun preserveProductionIdentity() {
        defaultIdentity = context.getSharedPreferences("radio_connection_identity", Context.MODE_PRIVATE).all.toMap()
        defaultKeyExists = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.containsAlias("radio.mobile.pairing.aes.v1")
    }
    private fun fixture() = JSONObject(instrumentation.context.assets.open("mobile_pairing_golden.json").bufferedReader().use { it.readText() })
    private fun bundle() = PairingBundle.parse(fixture().getJSONObject("bundle").toString(), policy)
    private fun pending(marker: String = "synthetic-marker") = PairingRecord(bundle(), WireBase64.encode(ByteArray(32) { 9 }), UUID.randomUUID().toString(), marker)
    private fun clock() = fixture().getLong("verification_time")
    private fun identity() = fixture().getJSONObject("identity").toString()
    private class MemoryStore(var record: PairingRecord? = null) : PairingStore {
        override fun load() = StoredPairing(record)
        override fun installationMarker() = "synthetic-marker"
        override fun save(record: PairingRecord) { this.record = record }
        override fun forget() { record = null }
    }
    @After fun cleanup() {
        fixtureStore().forget()
        File(context.noBackupFilesDir, "radio_installation_marker_$fixtureNamespace").delete()
        assertEquals(defaultIdentity, context.getSharedPreferences("radio_connection_identity", Context.MODE_PRIVATE).all)
        assertEquals(defaultKeyExists, KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.containsAlias("radio.mobile.pairing.aes.v1"))
    }

    @Test fun pythonSignedFixtureVerifiesExactBytesAndStrictIntegerContract() {
        val store = MemoryStore(pending())
        val calls = mutableListOf<String>()
        val api = RadioTransport { _, path, body, bearer ->
            calls += path
            when (path) {
                "/identity" -> { assertNull(bearer); RadioResponse(200, identity()) }
                "/pairing/exchange" -> {
                    assertNull(bearer)
                    assertEquals(store.record!!.bearer, JSONObject(body!!).getString("candidate_bearer"))
                    RadioResponse(200, JSONObject().put("protocol_version", 1).put("server_id", bundle().serverId).put("device_id", "synthetic-device")
                        .put("scopes", org.json.JSONArray(listOf("catalog:read"))).put("expires_at", clock() + 10000).toString())
                }
                else -> { assertEquals(store.record!!.bearer, bearer); RadioResponse(200, "{\"protocol_version\":1,\"server_id\":\"${bundle().serverId}\",\"authenticated_device_id\":\"synthetic-device\"}") }
            }
        }
        val repository = PairingRepository(store, api, policy, ::clock)
        runBlocking { repository.check() }
        assertTrue(repository.state.value.paired)
        assertEquals(listOf("/identity", "/pairing/exchange", "/capabilities"), calls)
        val fractional = fixture().getJSONObject("bundle").put("expires_at", clock() + 1.5).toString()
        assertThrows(ConnectionFailure::class.java) { PairingBundle.parse(fractional, policy) }
        val duplicate = fixture().getJSONObject("bundle").toString().dropLast(1) + ",\"server_id\":\"evil\"}"
        assertThrows(ConnectionFailure::class.java) { PairingBundle.parse(duplicate, policy) }
    }

    @Test fun lostReplyRetriesIdenticalPersistedExchangeAndCandidateAfterRepositoryRestart() {
        val store = MemoryStore()
        val exchanges = mutableListOf<String>()
        val api = RadioTransport { _, path, body, bearer ->
            assertNull(bearer)
            assertNotNull(store.record) // Candidate already persisted before any request.
            if (path == "/identity") RadioResponse(200, identity()) else {
                exchanges += body!!
                throw ConnectionFailure("Synthetic lost reply")
            }
        }
        runBlocking { PairingRepository(store, api, policy, ::clock).pair(fixture().getJSONObject("bundle").toString()) }
        val original = store.record!!
        runBlocking { PairingRepository(store, api, policy, ::clock).check() }
        assertEquals(2, exchanges.size)
        assertEquals(exchanges[0], exchanges[1])
        assertEquals(original.exchangeId, store.record!!.exchangeId)
        assertEquals(original.bearer, store.record!!.bearer)
        assertNull(store.record!!.deviceId)
    }

    @Test fun descriptorExpiringDuringExchangeIsRefetchedBeforeBearerDispatch() {
        val store = MemoryStore(pending())
        var currentTime = clock()
        var identities = 0
        val calls = mutableListOf<String>()
        val api = RadioTransport { _, path, _, bearer ->
            calls += path
            when (path) {
                "/identity" -> {
                    assertNull(bearer)
                    RadioResponse(200, if (++identities == 1) identity() else fixture().getJSONObject("renewed_identity").toString())
                }
                "/pairing/exchange" -> {
                    assertNull(bearer)
                    currentTime = fixture().getJSONObject("descriptor_payload").getLong("expires_at")
                    RadioResponse(200, "{\"protocol_version\":1,\"server_id\":\"${bundle().serverId}\",\"device_id\":\"synthetic-device\",\"scopes\":[\"catalog:read\"],\"expires_at\":${currentTime + 9999}}")
                }
                else -> {
                    assertEquals(2, identities)
                    assertEquals(store.record!!.bearer, bearer)
                    RadioResponse(200, "{\"protocol_version\":1,\"server_id\":\"${bundle().serverId}\",\"authenticated_device_id\":\"synthetic-device\"}")
                }
            }
        }
        val repository = PairingRepository(store, api, policy) { currentTime }
        runBlocking { repository.check() }
        assertEquals(listOf("/identity", "/pairing/exchange", "/identity", "/capabilities"), calls)
        assertTrue(repository.state.value.paired)
        assertEquals("Paired · connection verified", repository.state.value.message)
        assertEquals(2L, store.record!!.revision)
    }

    @Test fun invalidIdentityNeverSendsTicketOrBearerAndRevocationStopsRetries() {
        val record = pending().copy(deviceId = "synthetic-device", credentialExpiresAt = clock() + 9999)
        val store = MemoryStore(record)
        var calls = 0
        val invalid = JSONObject(identity()).put("signature", WireBase64.encode(ByteArray(70) { 1 })).toString()
        val bad = RadioTransport { _, path, body, bearer ->
            calls++; assertEquals("/identity", path); assertNull(body); assertNull(bearer); RadioResponse(200, invalid)
        }
        runBlocking { PairingRepository(store, bad, policy, ::clock).check() }
        assertEquals(1, calls)
        assertEquals(record.bearer, store.record!!.bearer)
        val revoked = RadioTransport { _, path, _, _ -> calls++; if (path == "/identity") RadioResponse(200, identity()) else RadioResponse(401, "") }
        val repository = PairingRepository(store, revoked, policy, ::clock)
        runBlocking { repository.check() }
        assertTrue(repository.state.value.blocked)
        val stopped = calls
        runBlocking { repository.check() }
        assertEquals(stopped, calls)
        assertEquals("synthetic-device", store.record!!.deviceId)
        assertEquals(record.bearer, store.record!!.bearer)
    }

    @Test fun securePendingAndPairedRoundtripAndRestoreKeyLossQuarantine() {
        val store = fixtureStore()
        store.forget()
        val record = pending(store.installationMarker())
        store.save(record)
        assertEquals(record, fixtureStore().load().record) // Same-install restore/load.
        val marker = File(context.noBackupFilesDir, "radio_installation_marker_$fixtureNamespace")
        val originalMarker = marker.readText()
        marker.writeText(WireBase64.encode(ByteArray(32) { 4 }))
        assertTrue(store.load().quarantined) // A restored pending exchange cannot upload.
        marker.writeText(originalMarker)
        val ciphertext = File(context.filesDir, "mobile_credentials/$fixtureNamespace/pairing.enc").readBytes().toString(Charsets.ISO_8859_1)
        assertFalse(ciphertext.contains(record.bearer)); assertFalse(ciphertext.contains(record.bundle.ticketSecret))
        val paired = record.copy(deviceId = "synthetic-device", credentialExpiresAt = clock() + 9999)
        store.save(paired)
        assertEquals(paired, store.load().record)
        marker.writeText(WireBase64.encode(ByteArray(32) { 4 }))
        assertTrue(store.load().quarantined) // Another installation received the expectation.
        marker.delete()
        assertTrue(store.load().quarantined)
        assertThrows(ConnectionFailure::class.java) { store.installationMarker() } // Never reconstruct restored marker.
        marker.writeText(originalMarker)
        assertEquals(paired, store.load().record)
        KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry("radio.mobile.pairing.aes.v1_$fixtureNamespace") }
        assertTrue(store.load().quarantined)
        assertNull(store.load().record)
    }

    @Test fun backupRulesExcludeCredentialDirectoryInBothTransferFormats() {
        fun exclusions(resource: Int): List<Pair<String?, String?>> {
            val parser = context.resources.getXml(resource)
            val result = mutableListOf<Pair<String?, String?>>()
            while (parser.eventType != org.xmlpull.v1.XmlPullParser.END_DOCUMENT) {
                if (parser.eventType == org.xmlpull.v1.XmlPullParser.START_TAG && parser.name == "exclude")
                    result += parser.getAttributeValue(null, "domain") to parser.getAttributeValue(null, "path")
                parser.next()
            }
            parser.close()
            return result
        }
        assertEquals(listOf("file" to "mobile_credentials/"), exclusions(R.xml.backup_rules))
        assertEquals(listOf("file" to "mobile_credentials/", "file" to "mobile_credentials/"), exclusions(R.xml.data_extraction_rules))
        assertFalse(NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted("example.com"))
        assertTrue(NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted("127.0.0.1"))
    }

    @Test fun loopbackClientRefusesRedirectsAndOversizeWithoutForwardingAuthorization() {
        val executor = Executors.newSingleThreadExecutor()
        ServerSocket(18766, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            fun reply(headers: String) = executor.submit {
                server.accept().use { socket ->
                    socket.soTimeout = 5000
                    val reader = socket.getInputStream().bufferedReader()
                    val lines = mutableListOf<String>()
                    while (true) { val line = reader.readLine() ?: break; if (line.isEmpty()) break; lines += line }
                    assertFalse(lines.any { it.startsWith("Authorization:", true) })
                    socket.getOutputStream().write(headers.toByteArray())
                }
            }
            val redirect = reply("HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:18767/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            assertThrows(ConnectionFailure::class.java) { runBlocking { RadioApiClient(policy).request(bundle().endpoint, "/identity", null, null) } }
            redirect.get()
            val huge = reply("HTTP/1.1 200 OK\r\nContent-Length: 999999\r\nConnection: close\r\n\r\n")
            assertThrows(ConnectionFailure::class.java) { runBlocking { RadioApiClient(policy).request(bundle().endpoint, "/identity", null, null) } }
            huge.get()
        }
        executor.shutdownNow()
    }

    @Test fun tricklingResponseHasTotalDeadlineAndCancellationDisconnects(): Unit = runBlocking {
        val executor = Executors.newSingleThreadExecutor()
        try {
            ServerSocket(18766, 1, InetAddress.getByName("127.0.0.1")).use { server ->
                val trickle = executor.submit {
                    server.accept().use { socket ->
                        runCatching {
                            val reader = socket.getInputStream().bufferedReader()
                            while (!reader.readLine().isNullOrEmpty()) { /* Read headers only. */ }
                            val output = socket.getOutputStream()
                            output.write("HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n".toByteArray())
                            repeat(100) { output.write('x'.code); output.flush(); Thread.sleep(40) }
                        }
                    }
                }
                val start = System.nanoTime()
                assertThrows(ConnectionFailure::class.java) { runBlocking { RadioApiClient(policy, 250).request(bundle().endpoint, "/identity", null, null) } }
                assertTrue("Total deadline must bound a continually readable stream", (System.nanoTime() - start) / 1000000 < 2000)
                trickle.get()
                val stalled = executor.submit {
                    server.accept().use { socket ->
                        socket.soTimeout = 3000
                        runCatching { socket.getInputStream().readBytes() }
                    }
                }
                val request = launch { RadioApiClient(policy).request(bundle().endpoint, "/identity", null, null) }
                delay(150)
                val cancelStart = System.nanoTime()
                request.cancelAndJoin()
                assertTrue("Cancellation should promptly release the caller", (System.nanoTime() - cancelStart) / 1000000 < 2000)
                stalled.get()
            }
        } finally { executor.shutdownNow() }
        Unit
    }

    @Test fun timeoutBeforeNetworkAcquisitionCannotDispatchSecretsLater() {
        val attached = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(2) // Watchdog close plus worker finally: both must finish.
        var acquired = false
        val fake = object : HttpURLConnection(URL("http://127.0.0.1:18766")) {
            override fun disconnect() { finished.countDown() }
            override fun usingProxy() = false
            override fun connect() { acquired = true }
            override fun setRequestProperty(key: String, value: String) {
                if (key == "Accept") {
                    attached.countDown()
                    // Deliberately simulate a platform pause that does not honor interruption.
                    while (release.count > 0) try { release.await() } catch (_: InterruptedException) { }
                }
                super.setRequestProperty(key, value)
            }
            override fun getOutputStream(): java.io.OutputStream { acquired = true; return java.io.ByteArrayOutputStream() }
            override fun getResponseCode(): Int { acquired = true; return 200 }
        }
        val client = RadioApiClient(policy, 250) { fake }
        assertThrows(ConnectionFailure::class.java) { runBlocking { client.request(bundle().endpoint, "/pairing/exchange", "{}", null) } }
        assertTrue(attached.await(1, TimeUnit.SECONDS))
        release.countDown()
        assertTrue(finished.await(2, TimeUnit.SECONDS))
        assertFalse("Timed-out request must not acquire output/response after resuming", acquired)
    }
}
