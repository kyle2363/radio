package dev.radio.mobile.connection

import dev.radio.mobile.recommendation.ConnectedAccess
import dev.radio.mobile.recommendation.ConnectedJson
import dev.radio.mobile.recommendation.ConnectedServerLink

/** Production [ConnectedServerLink]: every call revalidates the saved pairing. */
internal class PairingConnectedLink(private val pairing: PairingRepository) : ConnectedServerLink {
    private var session: PairingRepository.ProfileSyncSession? = null

    override suspend fun access(): ConnectedAccess {
        val fresh = pairing.connectedSession()
        session = fresh
        val response = pairing.connectedRequest(fresh, "/capabilities")
        if (response.status == 403) return ConnectedAccess("${fresh.serverId}:${fresh.deviceId}", capable = false, permitted = false)
        if (response.status !in 200..299) throw ConnectionFailure("Server could not report its capabilities.")
        val root = runCatching { ConnectedJson.parse(response.body) as? Map<*, *> }.getOrNull() ?: throw ConnectionFailure("Invalid server response.")
        if (root["server_id"] != fresh.serverId || root["authenticated_device_id"] != fresh.deviceId)
            throw ConnectionFailure("Authenticated server or device identity does not match this pairing.")
        return ConnectedAccess.parse("${fresh.serverId}:${fresh.deviceId}", response.body)
    }

    override suspend fun post(path: String, body: String): RadioResponse {
        val active = session ?: pairing.connectedSession().also { session = it }
        return pairing.connectedRequest(active, path, body)
    }
}
