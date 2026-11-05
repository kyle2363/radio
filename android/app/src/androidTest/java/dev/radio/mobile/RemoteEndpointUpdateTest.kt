package dev.radio.mobile

import dev.radio.mobile.connection.*
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class RemoteEndpointUpdateTest {
    private class MemoryStore(var value: StoredPairing) : PairingStore {
        override fun load() = value
        override fun installationMarker() = "install-marker"
        override fun save(record: PairingRecord) { value = StoredPairing(record) }
        override fun forget() { value = StoredPairing() }
    }

    private fun fixture(generation: Long = 2, revision: Long = 4, endpoint: String = "https://new.trycloudflare.com", oldEndpoint: String = "https://old.trycloudflare.com"): Triple<MemoryStore, String, String> {
        val kp = KeyPairGenerator.getInstance("EC").apply { initialize(java.security.spec.ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        val b64 = Base64.getUrlEncoder().withoutPadding()
        val origin = endpoint
        val payload = """{"protocol_version":1,"server_id":"server-1","endpoint":"$origin","connection_generation":$generation,"publication_revision":$revision,"issued_at":1000,"expires_at":1300,"available":true}""".toByteArray(Charsets.UTF_8)
        val envelope = """{"public_key":"${b64.encodeToString(kp.public.encoded)}","payload":"${b64.encodeToString(payload)}","signature":"${b64.encodeToString(Signature.getInstance("SHA256withECDSA").run { initSign(kp.private); update(payload); sign() })}"}"""
        val bundle = PairingBundle("server-1", DescriptorSignatures.pin(kp.public.encoded), oldEndpoint, "ticket", "A".repeat(43), "phone", 1200)
        val record = PairingRecord(bundle, "B".repeat(43), "exchange-id", "install-marker", "device-1", 2000, 1, 3,
            trustedUsbEndpoint = oldEndpoint.takeIf { it == "http://127.0.0.1:18766" })
        return Triple(MemoryStore(StoredPairing(record)), envelope, origin)
    }

    @Test fun replacementVerifiesBothEnvelopesWithoutBearerThenPersistsFenceBeforeAuth() = runBlocking {
        val (store, envelope, origin) = fixture()
        val requests = mutableListOf<Triple<String, String, String?>>()
        val transport = RadioTransport { endpoint, path, _, bearer ->
            requests += Triple(endpoint, path, bearer)
            when (path) {
                "/identity" -> RadioResponse(200, envelope)
                "/capabilities" -> {
                    assertEquals(origin, store.value.record?.bundle?.endpoint)
                    assertEquals(2L, store.value.record?.generation)
                    assertEquals(4L, store.value.record?.revision)
                    assertEquals("B".repeat(43), bearer)
                    RadioResponse(200, """{"protocol_version":1,"server_id":"server-1","authenticated_device_id":"device-1"}""")
                }
                else -> error("Unexpected request")
            }
        }
        val repo = PairingRepository(store, transport, EndpointPolicy(false), now = { 1050 })
        repo.load()
        repo.updateConnection(envelope)
        assertEquals("Server address verified and updated", repo.state.value.message)
        assertEquals(listOf("/identity", "/capabilities"), requests.map { it.second })
        assertEquals(origin, requests.first().first)
        assertNull(requests.first().third)
    }

    @Test fun forgedKeyStaleAndNonHttpsOriginsAreRejectedBeforeNetwork() = runBlocking {
        val (store, envelope, _) = fixture()
        var calls = 0
        val repo = PairingRepository(store, RadioTransport { _, _, _, _ -> calls++; RadioResponse(200, envelope) }, EndpointPolicy(false), now = { 1050 })
        repo.load()
        val forged = envelope.replace(Regex("\"payload\":\"[^\"]+\""), "\"payload\":\"eyJzZXJ2ZXJfaWQiOiJvdGhlciJ9\"")
        repo.updateConnection(forged)
        assertEquals(0, calls)
        assertEquals("https://old.trycloudflare.com", store.value.record?.bundle?.endpoint)
        assertTrue(repo.state.value.message.contains("signature", ignoreCase = true))

        val (staleStore, staleDescriptor, _) = fixture(generation = 1, revision = 2)
        val stale = PairingRepository(staleStore, RadioTransport { _, _, _, _ -> calls++; RadioResponse(200, staleDescriptor) }, EndpointPolicy(false), now = { 1050 })
        stale.load(); stale.updateConnection(staleDescriptor)
        assertEquals(0, calls)
        assertEquals("https://old.trycloudflare.com", staleStore.value.record?.bundle?.endpoint)

        val (httpStore, httpDescriptor, _) = fixture(endpoint = "http://bad.trycloudflare.com")
        val http = PairingRepository(httpStore, RadioTransport { _, _, _, _ -> calls++; RadioResponse(200, httpDescriptor) }, EndpointPolicy(false), now = { 1050 })
        http.load(); http.updateConnection(httpDescriptor)
        assertEquals(0, calls)
    }

    @Test fun validPasteDoesNotReleaseBearerWhenNewOriginIdentityFailsVerification() = runBlocking {
        val (store, offered, _) = fixture()
        val brokenIdentity = offered.replace(Regex("\"signature\":\"[^\"]+\""), "\"signature\":\"AAAA\"")
        val requests = mutableListOf<Pair<String, String?>>()
        val repo = PairingRepository(store, RadioTransport { endpoint, path, _, bearer ->
            requests += endpoint to bearer
            RadioResponse(200, brokenIdentity)
        }, EndpointPolicy(false), now = { 1050 })
        repo.load()
        repo.updateConnection(offered)
        assertEquals(listOf("https://new.trycloudflare.com" to null), requests)
        assertEquals("https://old.trycloudflare.com", store.value.record?.bundle?.endpoint)
        assertTrue(repo.state.value.message.contains("signature", ignoreCase = true))
    }

    @Test fun cancellingDuringCredentialFreeIdentityFetchKeepsTheCurrentEndpoint() = runBlocking {
        val (store, offered, _) = fixture()
        val reachedIdentity = CompletableDeferred<Unit>()
        var authenticatedCalls = 0
        val repo = PairingRepository(store, RadioTransport { _, path, _, bearer ->
            if (path == "/identity") {
                assertNull(bearer)
                reachedIdentity.complete(Unit)
                awaitCancellation()
            }
            authenticatedCalls++
            RadioResponse(500, "")
        }, EndpointPolicy(false), now = { 1050 })
        repo.load()
        val update = launch { repo.updateConnection(offered) }
        reachedIdentity.await()
        update.cancelAndJoin()
        assertEquals("https://old.trycloudflare.com", store.value.record?.bundle?.endpoint)
        assertEquals(0, authenticatedCalls)
    }

    @Test fun usbReturnIsExplicitPinnedAndCredentialFreeUntilVerified() = runBlocking {
        val usb = "http://127.0.0.1:18766"
        val (store, identity, _) = fixture(endpoint = usb, oldEndpoint = usb)
        val requests = mutableListOf<Pair<String, String?>>()
        val transport = RadioTransport { _, path, _, bearer ->
            requests += path to bearer
            if (path == "/identity") RadioResponse(200, identity)
            else {
                assertEquals(usb, store.value.record?.bundle?.endpoint)
                RadioResponse(200, """{"protocol_version":1,"server_id":"server-1","authenticated_device_id":"device-1"}""")
            }
        }
        val debug = PairingRepository(store, transport, EndpointPolicy(true), now = { 1050 })
        debug.load()
        debug.returnToTrustedUsb()
        assertEquals(usb, store.value.record?.bundle?.endpoint)
        assertEquals(listOf("/identity" to null, "/capabilities" to "B".repeat(43)), requests)
        val release = PairingRepository(store, transport, EndpointPolicy(false), now = { 1050 })
        release.load(); release.returnToTrustedUsb()
        assertTrue(release.state.value.message.contains("debug", ignoreCase = true))
        assertEquals(2, requests.size)
    }
}
