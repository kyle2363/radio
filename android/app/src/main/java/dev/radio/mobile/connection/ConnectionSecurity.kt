package dev.radio.mobile.connection

import java.net.URI
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

class ConnectionFailure(val safeMessage: String, val retryAfterSeconds: Int? = null, val rateLimited: Boolean = false) : Exception(safeMessage)

/** Endpoints are exact origins. The sole HTTP exception is the USB debug port. */
class EndpointPolicy(private val debug: Boolean) {
    fun isTrustedUsb(endpoint: String): Boolean = debug && endpoint == "http://127.0.0.1:18766"
    fun validateRemoteEndpoint(endpoint: String): String {
        val exact = validate(endpoint)
        val uri = URI(exact)
        if (uri.scheme != "https" || uri.port != -1 || uri.host == null || !uri.host.endsWith(".trycloudflare.com"))
            throw ConnectionFailure("Use the current HTTPS Quick Tunnel address supplied by the trusted desktop.")
        return exact
    }
    fun validate(endpoint: String): String {
        val uri = runCatching { URI(endpoint) }.getOrNull()
            ?: throw ConnectionFailure("Invalid server endpoint.")
        val https = uri.scheme == "https" && !uri.host.isNullOrBlank() &&
            uri.host == uri.host.lowercase() && uri.port in -1..65535 && uri.port != 0
        val usb = isTrustedUsb(endpoint)
        if ((!https && !usb) || uri.rawUserInfo != null || !uri.rawPath.isNullOrEmpty() ||
            uri.rawQuery != null || uri.rawFragment != null || uri.rawAuthority != uri.host + (if (uri.port == -1) "" else ":${uri.port}")) {
            throw ConnectionFailure("Use an exact HTTPS server origin. USB loopback is available only in debug builds.")
        }
        return endpoint
    }
}

object WireBase64 {
    fun decode(text: String, max: Int): ByteArray {
        if (text.isEmpty() || text.length > (max * 4 + 2) / 3 || !text.matches(Regex("[A-Za-z0-9_-]+")))
            throw ConnectionFailure("Invalid signed server data.")
        val bytes = runCatching { Base64.getUrlDecoder().decode(text) }.getOrNull()
            ?: throw ConnectionFailure("Invalid signed server data.")
        if (bytes.size > max || encode(bytes) != text) throw ConnectionFailure("Invalid signed server data.")
        return bytes
    }
    fun encode(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    fun secret(text: String): String {
        if (decode(text, 32).size != 32) throw ConnectionFailure("Invalid pairing secret.")
        return text
    }
}

object DescriptorSignatures {
    fun pin(publicKey: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(publicKey).joinToString("") { "%02x".format(it) }
    fun verify(pin: String, publicKey: String, payload: String, signature: String): ByteArray {
        try {
            val keyBytes = WireBase64.decode(publicKey, 256)
            if (!pin.matches(Regex("[0-9a-f]{64}")) || pin(keyBytes) != pin) throw ConnectionFailure("Server identity changed. Pair again from the trusted desktop.")
            val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(keyBytes)) as? ECPublicKey
                ?: throw ConnectionFailure("Invalid server signing key.")
            val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }.getParameterSpec(ECParameterSpec::class.java)
            if (key.params.curve != expected.curve || key.params.generator != expected.generator || key.params.order != expected.order || key.params.cofactor != expected.cofactor)
                throw ConnectionFailure("Invalid server signing key.")
            val bytes = WireBase64.decode(payload, 8192)
            val valid = Signature.getInstance("SHA256withECDSA").run { initVerify(key); update(bytes); verify(WireBase64.decode(signature, 80)) }
            if (!valid) throw ConnectionFailure("Server identity signature is invalid.")
            return bytes
        } catch (failure: ConnectionFailure) { throw failure }
        catch (_: Exception) { throw ConnectionFailure("Server identity signature is invalid.") }
    }
}

enum class InstallTrust { Unpaired, Valid, Quarantined }
fun installationTrust(expectedMarker: String?, actualMarker: String?, canDecrypt: Boolean): InstallTrust = when {
    expectedMarker == null -> InstallTrust.Unpaired
    actualMarker == expectedMarker && canDecrypt -> InstallTrust.Valid
    else -> InstallTrust.Quarantined
}

data class Descriptor(val serverId: String, val endpoint: String, val generation: Long, val revision: Long, val issuedAt: Long, val expiresAt: Long, val available: Boolean) {
    fun validate(server: String, origin: String, generationFloor: Long, revisionFloor: Long, now: Long) {
        if (serverId != server || endpoint != origin) throw ConnectionFailure("Server identity or endpoint does not match this pairing.")
        if (generation < generationFloor || (generation == generationFloor && revision < revisionFloor) || generation < 1 || revision < 1)
            throw ConnectionFailure("Server identity is older than the accepted revision. Repair pairing on the desktop.")
        if (!available) throw ConnectionFailure("Server is not accepting connections.")
        if (issuedAt > now + 60 || issuedAt < now - 600 || expiresAt <= now || expiresAt <= issuedAt || expiresAt - issuedAt > 600)
            throw ConnectionFailure("Server identity has expired or the device clock needs attention.")
    }
}
