package dev.radio.mobile.connection

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.security.SecureRandom
import java.util.UUID

data class ConnectionState(
    val loaded: Boolean = false, val busy: Boolean = false, val serverId: String? = null,
    val endpoint: String? = null, val paired: Boolean = false, val pending: Boolean = false,
    val quarantined: Boolean = false, val blocked: Boolean = false, val message: String = "Not paired",
)

/** Explicit user actions only. This repository has no connection to the local player. */
class PairingRepository(
    private val store: PairingStore, private val transport: RadioTransport,
    private val policy: EndpointPolicy, private val now: () -> Long = { System.currentTimeMillis() / 1000 },
) {
    private val mutableState = MutableStateFlow(ConnectionState())
    val state = mutableState.asStateFlow()
    private val mutex get() = pairingMutex
    companion object { private val pairingMutex = Mutex() }
    private fun publish(snapshot: StoredPairing, message: String? = null) {
        dev.radio.mobile.data.JournalIdentityProvider.publishPairing(snapshot)
        val record = snapshot.record
        mutableState.value = ConnectionState(loaded = true, serverId = record?.bundle?.serverId, endpoint = record?.bundle?.endpoint,
            paired = record?.deviceId != null, pending = record != null && record.deviceId == null, quarantined = snapshot.quarantined,
            blocked = record?.authorizationBlocked == true, message = message ?: when {
                snapshot.quarantined -> "Pairing quarantined after restore or key loss. Forget pairing and pair again. Local music is available."
                record?.authorizationBlocked == true -> "Credential expired or revoked. Forget pairing and pair again."
                record?.deviceId != null -> "Paired · connection not checked"
                record != null -> "Pairing incomplete · retry with the same saved exchange"
                else -> "Not paired"
            })
    }
    suspend fun load() = withContext(Dispatchers.IO) { mutex.withLock { publish(store.load()) } }
    suspend fun forget() = action { store.forget(); "Pairing forgotten on this phone" }
    suspend fun pair(bundleText: String) = action {
        val snapshot = usable()
        var record = snapshot.record
        if (record?.deviceId != null) throw ConnectionFailure("Forget the existing pairing before pairing another server.")
        if (record == null) {
            val bundle = PairingBundle.parse(bundleText, policy)
            if (bundle.expiresAt <= now() || bundle.expiresAt > now() + 360) throw ConnectionFailure("Pairing ticket has expired or the device clock needs attention.")
            record = PairingRecord(bundle, WireBase64.encode(ByteArray(32).also { SecureRandom().nextBytes(it) }), UUID.randomUUID().toString(), store.installationMarker())
            store.save(record) // The candidate and retry ID must survive a lost exchange reply.
        } else if (bundleText.isNotBlank() && PairingBundle.parse(bundleText, policy) != record.bundle) {
            throw ConnectionFailure("A different pairing is pending. Retry it or forget pairing first.")
        }
        exchange(record)
        "Paired · connection verified"
    }
    suspend fun check() = action {
        val record = usable().record ?: throw ConnectionFailure("Pair this phone first.")
        if (record.deviceId == null) exchange(record) else capabilities(verifyIdentity(record))
        "Paired · connection verified"
    }
    internal class CatalogSession internal constructor(val serverId: String, internal val anchor: String, internal val deviceId: String, internal val bearer: String) {
        internal var identityExpiresAt: Long = 0
        internal var identityGeneration: Long = -1
        internal var identityRevision: Long = -1
        override fun toString() = "CatalogSession(redacted)"
    }
    /** A snapshot carries no authority unless rechecked against the current protected record. */
    internal suspend fun catalogSession(): CatalogSession = withContext(Dispatchers.IO) { mutex.withLock {
        val record = usable().record ?: throw ConnectionFailure("Pair this phone before importing a catalog.")
        val device = record.deviceId ?: throw ConnectionFailure("Finish pairing before importing a catalog.")
        requireCredential(record)
        CatalogSession(record.bundle.serverId, record.identityAnchor, device, record.bearer)
    } }
    private fun sessionRecord(session: CatalogSession): PairingRecord {
        val record = usable().record ?: throw ConnectionFailure("Pairing changed. Previous catalog kept.")
        if (record.identityAnchor != session.anchor || record.deviceId != session.deviceId || record.bearer != session.bearer || record.bundle.serverId != session.serverId)
            throw ConnectionFailure("Pairing changed. Previous catalog kept.")
        requireCredential(record)
        return record
    }
    private fun requireCredential(record: PairingRecord) {
        if (record.credentialExpiresAt == null || record.credentialExpiresAt <= now()) {
            store.save(record.copy(authorizationBlocked = true))
            publish(store.load())
            throw ConnectionFailure("Credential expired or revoked. Previous catalog kept. Pair again to retry.")
        }
    }
    internal suspend fun catalogDownload(session: CatalogSession, path: String, maxBytes: Int): RadioBinaryResponse = withContext(Dispatchers.IO) { mutex.withLock {
        val initial = sessionRecord(session)
        val record = if (session.identityExpiresAt <= now() + 16 || session.identityGeneration != initial.generation || session.identityRevision != initial.revision) {
            val verified = freshIdentity(verifyIdentity(initial))
            session.identityExpiresAt = verified.identityExpiresAt
            session.identityGeneration = verified.record.generation
            session.identityRevision = verified.record.revision
            verified.record
        } else initial
        sessionRecord(session) // Forget/re-pair and identity storage must still agree.
        val binary = transport as? RadioBinaryTransport ?: throw ConnectionFailure("Catalog download transport is unavailable.")
        val response = binary.download(record.bundle.endpoint, path, record.bearer, maxBytes)
        sessionRecord(session)
        if (response.status == 401 || response.status == 403) {
            store.save(record.copy(authorizationBlocked = true)); publish(store.load())
            throw ConnectionFailure("Credential expired or revoked. Previous catalog kept. Pair again to retry.")
        }
        response
    } }
    /** Commit shares the pairing lock: forgetting/re-pairing cannot race the active-pointer flip. */
    internal suspend fun <T> adoptCatalog(session: CatalogSession, block: suspend () -> T): T = mutex.withLock {
        sessionRecord(session)
        block()
    }
    private suspend fun action(block: suspend () -> String) = withContext(Dispatchers.IO) {
        mutex.withLock {
            mutableState.value = mutableState.value.copy(busy = true)
            try { val message = block(); publish(store.load(), message) }
            catch (cancelled: CancellationException) { publish(store.load()); throw cancelled }
            catch (failure: ConnectionFailure) { publish(store.load(), failure.safeMessage) }
            catch (_: Exception) { publish(store.load(), "Connection could not be completed. Saved pairing can be retried.") }
        }
    }
    private fun usable(): StoredPairing {
        val snapshot = store.load()
        if (snapshot.quarantined) throw ConnectionFailure("Restored or inaccessible pairing is quarantined. Forget pairing and pair again.")
        if (snapshot.record?.authorizationBlocked == true) throw ConnectionFailure("Credential expired or revoked. Forget pairing and pair again.")
        return snapshot
    }
    private data class Verified(val record: PairingRecord, val identityExpiresAt: Long)
    private suspend fun verifyIdentity(record: PairingRecord): Verified {
        val response = transport.request(record.bundle.endpoint, "/identity", null, null)
        requireStatus(response)
        val descriptor = parseDescriptor(response.body, record, now())
        val verified = record.copy(generation = descriptor.generation, revision = descriptor.revision)
        store.save(verified) // Persist the accepted floor before transmitting credentials.
        return Verified(verified, descriptor.expiresAt)
    }
    private suspend fun freshIdentity(verified: Verified): Verified {
        // A transport operation has a hard 15-second cumulative deadline. Reserve that lease
        // before dispatch, including executor admission and upload, and revalidate at most once.
        if (verified.identityExpiresAt > now() + 16) return verified
        val refreshed = verifyIdentity(verified.record)
        if (refreshed.identityExpiresAt <= now() + 16) throw ConnectionFailure("Server identity is too close to expiry. Retry shortly.")
        return refreshed
    }
    private suspend fun exchange(pending: PairingRecord) {
        val verified = freshIdentity(verifyIdentity(pending))
        val record = verified.record
        val request = JSONObject().put("protocol_version", 1).put("ticket_id", record.bundle.ticketId).put("ticket_secret", record.bundle.ticketSecret)
            .put("exchange_id", record.exchangeId).put("device_name", record.bundle.deviceName).put("candidate_bearer", record.bearer)
        val response = transport.request(record.bundle.endpoint, "/pairing/exchange", request.toString(), null)
        requireStatus(response, record)
        val result = json(response.body).apply { version() }
        if (result.text("server_id", 128) != record.bundle.serverId) throw ConnectionFailure("Pairing response came from a different server.")
        val scopes = result.optJSONArray("scopes")
        if (scopes == null || scopes.length() != 1 || scopes.opt(0) != "catalog:read") throw ConnectionFailure("Unexpected pairing permissions.")
        val expiry = result.number("expires_at")
        if (expiry <= now()) throw ConnectionFailure("Paired credential has expired.")
        val paired = record.copy(deviceId = result.text("device_id", 128), credentialExpiresAt = expiry)
        store.save(paired)
        capabilities(verified.copy(record = paired))
    }
    private suspend fun capabilities(verified: Verified) {
        val record = freshIdentity(verified).record
        if (record.credentialExpiresAt == null || record.credentialExpiresAt <= now()) {
            store.save(record.copy(authorizationBlocked = true))
            throw ConnectionFailure("Credential expired or revoked. Forget pairing and pair again.")
        }
        val response = transport.request(record.bundle.endpoint, "/capabilities", null, record.bearer)
        requireStatus(response, record)
        val result = json(response.body).apply { version() }
        if (result.text("server_id", 128) != record.bundle.serverId || result.text("authenticated_device_id", 128) != record.deviceId)
            throw ConnectionFailure("Authenticated server or device identity does not match this pairing.")
    }
    private fun requireStatus(response: RadioResponse, record: PairingRecord? = null) {
        if (response.status in 200..299) return
        if (response.status == 401 || response.status == 403) {
            if (record != null) store.save(record.copy(authorizationBlocked = true))
            throw ConnectionFailure("Pairing ticket or credential expired or revoked. Forget pairing and pair again.")
        }
        if (response.status == 429) throw ConnectionFailure("Too many connection attempts. Wait before retrying.", response.retryAfterSeconds, rateLimited = true)
        throw ConnectionFailure(when (response.status) {
            409 -> "Pairing exchange conflicts with its saved receipt. Forget pairing and issue a new ticket."
            410 -> "Pairing ticket has expired. Forget pairing and issue a new ticket."
            429 -> "Too many connection attempts. Wait before retrying."
            else -> "Server could not complete the request. Saved pairing can be retried."
        })
    }
}
