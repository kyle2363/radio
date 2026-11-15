package dev.radio.mobile

import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.connection.EndpointPolicy
import dev.radio.mobile.connection.RadioApiClient
import dev.radio.mobile.connection.WireBase64
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import org.junit.Assert.*
import org.junit.Test

class LyricsApiClientTest {
    private val bearer = WireBase64.encode(ByteArray(32) { 11 })
    private val hash = "a".repeat(64)
    private val path = "/lyrics?revision=12&manifest_sha256=$hash&song_id=42"

    private class Fake(
        url: URL,
        private val reply: ByteArray = "{}".toByteArray(),
        private val status: Int = 200,
        private val declaredLength: Long = -1L,
        private val encoding: String? = null,
        private val stream: InputStream? = null,
        private val responseGate: CountDownLatch? = null,
    ) : HttpURLConnection(url) {
        val written = ByteArrayOutputStream()
        val headers = mutableMapOf<String, String>()
        val disconnected = AtomicBoolean(false)
        override fun connect() = Unit
        override fun disconnect() { disconnected.set(true) }
        override fun usingProxy() = false
        override fun setRequestProperty(key: String, value: String) { headers[key] = value }
        override fun getOutputStream(): OutputStream = written
        override fun getResponseCode(): Int {
            responseGate?.await()
            return status
        }
        override fun getInputStream(): InputStream = stream ?: ByteArrayInputStream(reply)
        override fun getErrorStream(): InputStream = stream ?: ByteArrayInputStream(reply)
        override fun getContentLengthLong() = declaredLength
        override fun getHeaderField(name: String?): String? = if (name.equals("Content-Encoding", true)) encoding else null
    }

    private fun client(
        connections: MutableList<Fake>,
        response: ByteArray = "{}".toByteArray(),
        status: Int = 200,
        length: Long = -1L,
        encoding: String? = null,
        deadlineMs: Long = 15_000,
        stream: InputStream? = null,
        responseGate: CountDownLatch? = null,
    ) = RadioApiClient(EndpointPolicy(true), deadlineMs = deadlineMs, openConnection = { url ->
        Fake(url, response, status, length, encoding, stream, responseGate).also(connections::add)
    })

    @Test fun lyricsUsesExactAuthenticatedGetWithoutBody() = runBlocking {
        val connections = mutableListOf<Fake>()
        val response = "{\"text\":\"lyrics\"}"
        val result = client(connections, response.toByteArray()).request("http://127.0.0.1:18766", path, null, bearer)
        val connection = connections.single()
        assertEquals(200, result.status)
        assertEquals(response, result.body)
        assertEquals("GET", connection.requestMethod)
        assertEquals("http://127.0.0.1:18766/api/portable/v1$path", connection.url.toString())
        assertEquals("Bearer $bearer", connection.headers["Authorization"])
        assertNull(connection.headers["Content-Type"])
        assertEquals(0, connection.written.size())
        assertFalse(connection.doOutput)
        assertFalse(connection.instanceFollowRedirects)
        assertEquals("identity", connection.headers["Accept-Encoding"])
    }

    @Test fun lyricsPathIsStrictAndBearerIsRequired() = runBlocking {
        val connections = mutableListOf<Fake>()
        val api = client(connections)
        listOf(
            "/lyrics?song_id=42&revision=12&manifest_sha256=$hash",
            "/lyrics?revision=12&manifest_sha256=${hash.uppercase()}&song_id=42",
            "/lyrics?revision=12&manifest_sha256=$hash&song_id=42&extra=x",
            "/lyrics?revision=12&revision=12&manifest_sha256=$hash&song_id=42",
            "/lyrics?revision=0&manifest_sha256=$hash&song_id=42",
        ).forEach { invalid ->
            assertThrows(ConnectionFailure::class.java) { runBlocking { api.request("http://127.0.0.1:18766", invalid, null, bearer) } }
        }
        assertThrows(ConnectionFailure::class.java) { runBlocking { api.request("http://127.0.0.1:18766", path, "{}", bearer) } }
        assertThrows(ConnectionFailure::class.java) { runBlocking { api.request("http://127.0.0.1:18766", path, null, null) } }
        assertTrue(connections.isEmpty())
    }

    @Test fun lyricsAcceptJsonLargerThanThirtyTwoKiBButBoundTheFullBody(): Unit = runBlocking {
        val accepted = "{\"text\":\"${"x".repeat(40 * 1024)}\"}".toByteArray()
        val connections = mutableListOf<Fake>()
        val result = client(connections, accepted).request("http://127.0.0.1:18766", path, null, bearer)
        assertEquals(accepted.size, result.body.toByteArray().size)

        val tooLarge = ByteArray(512 * 1024 + 1) { 'x'.code.toByte() }
        assertThrows(ConnectionFailure::class.java) {
            runBlocking { client(mutableListOf(), tooLarge).request("http://127.0.0.1:18766", path, null, bearer) }
        }
        assertThrows(ConnectionFailure::class.java) {
            runBlocking { client(mutableListOf(), byteArrayOf(), length = 512 * 1024L + 1).request("http://127.0.0.1:18766", path, null, bearer) }
        }
    }

    @Test fun lyricsRejectInvalidUtf8UnsupportedEncodingAndRedirects() = runBlocking {
        assertThrows(ConnectionFailure::class.java) {
            runBlocking { client(mutableListOf(), byteArrayOf(0xC3.toByte(), 0x28)).request("http://127.0.0.1:18766", path, null, bearer) }
        }
        assertThrows(ConnectionFailure::class.java) {
            runBlocking { client(mutableListOf(), "{}".toByteArray(), encoding = "gzip").request("http://127.0.0.1:18766", path, null, bearer) }
        }
        val connections = mutableListOf<Fake>()
        assertThrows(ConnectionFailure::class.java) {
            runBlocking { client(connections, status = 302).request("http://127.0.0.1:18766", path, null, bearer) }
        }
        assertFalse(connections.single().instanceFollowRedirects)
    }

    @Test fun lyricsUseFifteenSecondDefaultDeadlineAndEnforceInjectedHardDeadline() = runBlocking {
        val normal = RadioApiClient(EndpointPolicy(true))
        assertEquals(15_000L, normal.deadlineFor(path))
        val gate = CountDownLatch(1)
        val connections = mutableListOf<Fake>()
        val quick = client(connections, deadlineMs = 40, responseGate = gate)
        val started = System.nanoTime()
        assertThrows(ConnectionFailure::class.java) {
            runBlocking { quick.request("http://127.0.0.1:18766", path, null, bearer) }
        }
        assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) < 2_000)
        gate.countDown()
        assertEquals(40, connections.single().readTimeout)
    }

    @Test fun cancellingLyricsRequestDisconnectsTheActiveConnection() = runBlocking {
        val reading = CountDownLatch(1)
        val blocked = object : InputStream() {
            override fun read(): Int {
                reading.countDown()
                Thread.sleep(60_000)
                return -1
            }
        }
        val connections = mutableListOf<Fake>()
        val api = client(connections, stream = blocked)
        val request = async { api.request("http://127.0.0.1:18766", path, null, bearer) }
        withContext(Dispatchers.IO) { assertTrue(reading.await(2, TimeUnit.SECONDS)) }
        request.cancelAndJoin()
        withContext(Dispatchers.IO) {
            val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
            while (!connections.single().disconnected.get() && System.nanoTime() < until) Thread.sleep(5)
        }
        assertTrue(connections.single().disconnected.get())
    }
}
