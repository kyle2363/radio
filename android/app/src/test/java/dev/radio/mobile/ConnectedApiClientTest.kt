package dev.radio.mobile

import dev.radio.mobile.connection.*
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class ConnectedApiClientTest {
    private class Fake(url: URL, val reply: String = "{}", val status: Int = 200) : HttpURLConnection(url) {
        val written = ByteArrayOutputStream()
        val headers = mutableMapOf<String, String>()
        override fun connect() = Unit
        override fun disconnect() = Unit
        override fun usingProxy() = false
        override fun setRequestProperty(key: String, value: String) { headers[key] = value }
        override fun getOutputStream(): OutputStream = written
        override fun getResponseCode() = status
        override fun getInputStream(): InputStream = ByteArrayInputStream(reply.toByteArray())
        override fun getErrorStream(): InputStream = ByteArrayInputStream(reply.toByteArray())
        override fun getContentLengthLong() = -1L
        override fun getHeaderField(name: String?): String? = null
    }

    private val bearer = WireBase64.encode(ByteArray(32) { 7 })
    private val id = "ctx-aaaaaaaaaaaaaaaa"
    private fun client(connections: MutableList<Fake>, reply: String = "{}", status: Int = 200) =
        RadioApiClient(EndpointPolicy(true), openConnection = { url -> Fake(url, reply, status).also { connections += it } })

    @Test fun recommendationsAloneGetTheSeventyFiveSecondDeadlineAndAMatchingReadTimeout() {
        val client = RadioApiClient(EndpointPolicy(true))
        assertEquals(75_000L, client.deadlineFor("/contexts/$id/recommendations"))
        listOf("/availability", "/contexts", "/contexts/$id", "/events", "/capabilities", "/identity").forEach { assertEquals(15_000L, client.deadlineFor(it)) }
        assertEquals(75_000L, CONNECTED_RECOMMEND_DEADLINE_MS)
        val connections = mutableListOf<Fake>()
        runBlocking { client(connections).request("http://127.0.0.1:18766", "/contexts/$id/recommendations", "{\"protocol_version\":1}", bearer) }
        assertEquals(80_000, connections.single().readTimeout)
        runBlocking { client(connections).request("http://127.0.0.1:18766", "/availability", "{}", bearer) }
        assertEquals(10_000, connections.last().readTimeout)
    }

    @Test fun routesArePostWithJsonAndABearer() {
        val connections = mutableListOf<Fake>()
        val response = runBlocking { client(connections, "{\"epoch\":1}").request("http://127.0.0.1:18766", "/contexts", "{\"protocol_version\":1}", bearer) }
        assertEquals(200, response.status)
        val c = connections.single()
        assertEquals("POST", c.requestMethod)
        assertEquals("http://127.0.0.1:18766/api/portable/v1/contexts", c.url.toString())
        assertEquals("application/json", c.headers["Content-Type"])
        assertEquals("Bearer $bearer", c.headers["Authorization"])
        assertEquals("{\"protocol_version\":1}", c.written.toString())
    }

    @Test fun unsupportedShapesAreRefusedBeforeAnyConnection() {
        val connections = mutableListOf<Fake>()
        val client = client(connections)
        fun refused(path: String, body: String?, token: String? = bearer) =
            assertThrows(ConnectionFailure::class.java) { runBlocking { client.request("http://127.0.0.1:18766", path, body, token) } }
        refused("/contexts/short/recommendations", "{}")
        refused("/contexts/$id/other", "{}")
        refused("/contexts/$id/recommendations?x=1", "{}")
        refused("/contexts/$id", null)             // connected routes are POST only
        refused("/availability", null)
        refused("/availability", "{}", null)       // always authenticated
        refused("/contexts", "x".repeat(8 * 1024 + 1))
        refused("/contexts/$id/recommendations", "x".repeat(16 * 1024 + 1))
        refused("/availability", "x".repeat(256 * 1024 + 1))
        assertTrue(connections.all { it.written.size() == 0 }) // nothing is ever sent
        val before = connections.size
        runBlocking { client.request("http://127.0.0.1:18766", "/availability", "x".repeat(200 * 1024), bearer) }
        assertEquals(200 * 1024, connections[before].written.size())
    }

    @Test fun recommendationRepliesMayBeLargerThanOtherCalls() {
        val big = "{\"p\":\"" + "x".repeat(60_000) + "\"}"
        val connections = mutableListOf<Fake>()
        val ok = runBlocking { client(connections, big).request("http://127.0.0.1:18766", "/contexts/$id/recommendations", "{}", bearer) }
        assertEquals(big.length, ok.body.length)
        assertThrows(ConnectionFailure::class.java) { runBlocking { client(connections, big).request("http://127.0.0.1:18766", "/contexts", "{}", bearer) } }
    }
}
