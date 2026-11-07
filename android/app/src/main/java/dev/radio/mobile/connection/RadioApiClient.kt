package dev.radio.mobile.connection

import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

const val CONNECTED_RECOMMEND_DEADLINE_MS = 75_000L
data class RadioResponse(val status: Int, val body: String, val retryAfterSeconds: Int? = null)
data class RadioBinaryResponse(val status: Int, val body: ByteArray, val retryAfterSeconds: Int? = null, val catalogBatches: Boolean = false)
interface RadioBinaryTransport {
    suspend fun download(endpoint: String, path: String, bearer: String, maxBytes: Int): RadioBinaryResponse
}
fun interface RadioTransport {
    suspend fun request(endpoint: String, path: String, body: String?, bearer: String?): RadioResponse
}

/** Standard platform TLS/hostname validation; redirects never carry bootstrap or bearer data. */
class RadioApiClient(private val policy: EndpointPolicy, private val deadlineMs: Long = 15000,
    private val recommendDeadlineMs: Long = CONNECTED_RECOMMEND_DEADLINE_MS,
    private val openConnection: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection },
) : RadioTransport, RadioBinaryTransport {
    init { require(deadlineMs in 1..15000); require(recommendDeadlineMs in 1..CONNECTED_RECOMMEND_DEADLINE_MS) }
    companion object {
        /** Connected-recommendation routes (plan section 8). All are POST with a JSON body. */
        internal enum class ConnectedRoute(val maxRequestBytes: Int, val maxResponseBytes: Int) {
            AVAILABILITY(256 * 1024, 32768), CONTEXT(8 * 1024, 32768), RECOMMEND(16 * 1024, 128 * 1024)
        }
        private val contextId = "[A-Za-z0-9_-]{16,64}"
        internal fun connectedRoute(path: String): ConnectedRoute? = when {
            path == "/availability" -> ConnectedRoute.AVAILABILITY
            path == "/contexts" || path.matches(Regex("/contexts/$contextId")) -> ConnectedRoute.CONTEXT
            path.matches(Regex("/contexts/$contextId/recommendations")) -> ConnectedRoute.RECOMMEND
            else -> null
        }
        private val io = ThreadPoolExecutor(2, 2, 30, TimeUnit.SECONDS, ArrayBlockingQueue(4)) { task -> Thread(task, "radio-connection-io").apply { isDaemon = true } }
        private val deadlines = Executors.newSingleThreadScheduledExecutor { task -> Thread(task, "radio-connection-deadline").apply { isDaemon = true } }
        private val closing = ThreadPoolExecutor(2, 2, 30, TimeUnit.SECONDS, ArrayBlockingQueue(8)) { task -> Thread(task, "radio-connection-close").apply { isDaemon = true } }
        private fun disconnectAsync(connection: HttpURLConnection?) {
            if (connection != null) runCatching { closing.execute { connection.disconnect() } }
        }
    }
    override suspend fun request(endpoint: String, path: String, body: String?, bearer: String?): RadioResponse {
        val limit = when { path.startsWith("/events?cursor=") -> 2 * 1024 * 1024; path=="/events" -> 128 * 1024; else -> connectedRoute(path)?.maxResponseBytes ?: 32768 }
        val result = execute(endpoint, path, body, bearer, limit, false)
        return RadioResponse(result.status, result.body.toString(Charsets.UTF_8), result.retryAfterSeconds)
    }
    override suspend fun download(endpoint: String, path: String, bearer: String, maxBytes: Int): RadioBinaryResponse {
        if (maxBytes !in 1..(16 * 1024 * 1024)) throw ConnectionFailure("Invalid catalog response limit.")
        return execute(endpoint, path, null, bearer, maxBytes, true)
    }
    /** Only the recommendations call, which waits on the desktop's LLM, gets the long deadline. */
    internal fun deadlineFor(path: String): Long = if (connectedRoute(path) == ConnectedRoute.RECOMMEND) recommendDeadlineMs else deadlineMs
    private suspend fun execute(endpoint: String, path: String, body: String?, bearer: String?, maxBytes: Int, binary: Boolean): RadioBinaryResponse = suspendCancellableCoroutine { continuation ->
        val active = AtomicReference<HttpURLConnection?>(null)
        val completed = AtomicBoolean(false)
        val submitted = AtomicReference<Future<*>?>(null)
        val timeout = deadlines.schedule({
            if (completed.compareAndSet(false, true)) continuation.resumeWithException(ConnectionFailure("Server request timed out. Saved pairing can be retried."))
            submitted.get()?.cancel(true)
            disconnectAsync(active.get())
        }, deadlineFor(path), TimeUnit.MILLISECONDS)
        val future = try { io.submit {
            try {
                if (!continuation.isActive) return@submit
                val result = perform(endpoint, path, body, bearer, maxBytes, binary, { active.set(it) }) {
                    if (completed.get() || !continuation.isActive || Thread.currentThread().isInterrupted) throw ConnectionFailure("Connection cancelled or timed out.")
                }
                if (completed.compareAndSet(false, true)) continuation.resume(result)
            } catch (failure: Exception) {
                if (completed.compareAndSet(false, true)) continuation.resumeWithException(failure)
            } finally { timeout.cancel(false); active.get()?.disconnect() }
        } } catch (_: Exception) {
            timeout.cancel(false)
            if (completed.compareAndSet(false, true)) continuation.resumeWithException(ConnectionFailure("Connection is busy. Retry shortly."))
            null
        }
        submitted.set(future)
        if (completed.get()) future?.cancel(true)
        continuation.invokeOnCancellation { completed.set(true); timeout.cancel(false); future?.cancel(true); disconnectAsync(active.get()) }
    }

    private fun perform(endpoint: String, path: String, body: String?, bearer: String?, maxBytes: Int, binary: Boolean, connected: (HttpURLConnection) -> Unit, ensureActive: () -> Unit): RadioBinaryResponse {
        ensureActive()
        policy.validate(endpoint)
        val batchPath = path.length <= 4514 && path.matches(Regex("/vector-batch\\?revision=[1-9][0-9]{0,18}&manifest_sha256=[0-9a-f]{64}&digests=[0-9a-f]{64}(?:,[0-9a-f]{64}){0,63}"))
        val catalogPath = batchPath || path.matches(Regex("/catalog(?:\\?(?:revision=[1-9][0-9]{0,18})(?:&base_revision=[1-9][0-9]{0,18})?)?")) || path.matches(Regex("/vectors/[a-f0-9]{64}"))
        val eventRead = path.matches(Regex("/events\\?cursor=(?:0|[1-9][0-9]{0,18})&limit=(?:[1-9]|[1-9][0-9]|[1-4][0-9]{2}|500)"))
        val eventWrite = path == "/events"
        val profileRead = path == "/profile"
        val route = connectedRoute(path)
        if (if (binary) !catalogPath else path !in setOf("/identity", "/pairing/exchange", "/capabilities") && !eventRead && !eventWrite && !profileRead && route == null) throw ConnectionFailure("Unsupported mobile request.")
        if (route != null && body == null) throw ConnectionFailure("Unsupported mobile request.")
        val connection = openConnection(URL("$endpoint/api/portable/v1$path"))
        try {
            connected(connection)
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 5000
            connection.readTimeout = if (route == ConnectedRoute.RECOMMEND) (recommendDeadlineMs + 5000).toInt() else 10000
            connection.useCaches = false
            connection.setRequestProperty("Accept", if (path.startsWith("/vectors/") || batchPath) "application/octet-stream" else "application/json")
            connection.setRequestProperty("Accept-Encoding", "identity")
            ensureActive()
            if (bearer != null) {
                if (path != "/capabilities" && !binary && !eventRead && !eventWrite && !profileRead && route == null) throw ConnectionFailure("Credentials are not allowed on the identity request.")
                connection.setRequestProperty("Authorization", "Bearer ${WireBase64.secret(bearer)}")
            }
            if (body != null) {
                val bytes = body.toByteArray(Charsets.UTF_8)
                if (bytes.size > 2 * 1024 * 1024 || path != "/pairing/exchange" && !eventWrite && route == null || path == "/pairing/exchange" && bytes.size > 8192 ||
                    route != null && (bytes.size > route.maxRequestBytes || bearer == null)) throw ConnectionFailure("Invalid mobile request.")
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setFixedLengthStreamingMode(bytes.size)
                ensureActive()
                connection.outputStream.use { ensureActive(); it.write(bytes) }
            }
            ensureActive()
            val code = connection.responseCode
            ensureActive()
            if (code in 300..399) throw ConnectionFailure("Server redirect refused. Repair pairing from the trusted desktop.")
            val limit = if (code in 200..299) maxBytes else minOf(maxBytes, 32768)
            if (connection.contentLengthLong > limit || connection.getHeaderField("Content-Encoding")?.let { it != "identity" } == true) throw ConnectionFailure("Server response is too large or uses unsupported encoding.")
            val input = if (code in 200..299) connection.inputStream else connection.errorStream
            val output = ByteArrayOutputStream()
            input?.use { stream ->
                val buffer = ByteArray(4096)
                while (true) {
                    ensureActive()
                    val count = stream.read(buffer)
                    ensureActive()
                    if (count < 0) break
                    if (output.size() + count > limit) throw ConnectionFailure("Server response is too large.")
                    output.write(buffer, 0, count)
                }
            }
            val retryAfter = connection.getHeaderField("Retry-After")?.takeIf { it.matches(Regex("[0-9]{1,2}")) }?.toIntOrNull()?.takeIf { it in 1..60 }
            return RadioBinaryResponse(code, output.toByteArray(), retryAfter, binary && code == 200 && path == "/catalog" && connection.getHeaderField("X-Radio-Catalog-Batches") == "1")
        } catch (failure: ConnectionFailure) { throw failure }
        catch (_: Exception) { throw ConnectionFailure("Cannot reach the server. Check USB forwarding or the server address.") }
        finally { connection.disconnect() }
    }
}
