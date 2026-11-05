package dev.radio.mobile

import dev.radio.mobile.connection.*
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64
import org.junit.Assert.*
import org.junit.Test

class ConnectionSecurityTest {
    @Test fun releaseRequiresExactHttpsOriginAndDebugOnlyAllowsUsbLoopback() {
        assertEquals("https://radio.example", EndpointPolicy(false).validate("https://radio.example"))
        listOf("http://127.0.0.1:18766", "https://radio.example/", "https://user@radio.example", "https://radio.example?q=secret", "https://radio.example/path", "https://RADIO.example").forEach {
            assertThrows(ConnectionFailure::class.java) { EndpointPolicy(false).validate(it) }
        }
        assertEquals("http://127.0.0.1:18766", EndpointPolicy(true).validate("http://127.0.0.1:18766"))
        listOf("http://localhost:18766", "http://127.0.0.1:8765", "http://192.168.1.2:18766").forEach {
            assertThrows(ConnectionFailure::class.java) { EndpointPolicy(true).validate(it) }
        }
    }

    @Test fun remoteEndpointReplacementIsRestrictedToExactQuickTunnelHttpsOrigins() {
        assertEquals("https://abc.trycloudflare.com", EndpointPolicy(false).validateRemoteEndpoint("https://abc.trycloudflare.com"))
        listOf("http://abc.trycloudflare.com", "https://trycloudflare.com", "https://abc.trycloudflare.com.evil.example",
            "https://abc.trycloudflare.com:8443", "https://abc.trycloudflare.com/").forEach {
            assertThrows(ConnectionFailure::class.java) { EndpointPolicy(false).validateRemoteEndpoint(it) }
        }
    }

    @Test fun standardP256DerSignatureCoversTransmittedPayloadBytes() {
        val keys = KeyPairGenerator.getInstance("EC").apply { initialize(java.security.spec.ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        val bytes = "{\"server_id\":\"synthetic\", \"unicode\":\"海\"}".toByteArray(Charsets.UTF_8)
        val signature = Signature.getInstance("SHA256withECDSA").run { initSign(keys.private); update(bytes); sign() }
        val b64 = Base64.getUrlEncoder().withoutPadding()
        val key = b64.encodeToString(keys.public.encoded)
        val pin = DescriptorSignatures.pin(keys.public.encoded)
        assertArrayEquals(bytes, DescriptorSignatures.verify(pin, key, b64.encodeToString(bytes), b64.encodeToString(signature)))
        assertThrows(ConnectionFailure::class.java) { DescriptorSignatures.verify(pin, key, b64.encodeToString(bytes + 32), b64.encodeToString(signature)) }
        val other = KeyPairGenerator.getInstance("EC").apply { initialize(java.security.spec.ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        assertThrows(ConnectionFailure::class.java) { DescriptorSignatures.verify(pin, b64.encodeToString(other.public.encoded), b64.encodeToString(bytes), b64.encodeToString(signature)) }
    }

    @Test fun restoresRequireBothOriginalMarkerAndDecryptableCredential() {
        assertEquals(InstallTrust.Unpaired, installationTrust(null, "new", false))
        assertEquals(InstallTrust.Valid, installationTrust("original", "original", true))
        assertEquals(InstallTrust.Quarantined, installationTrust("original", "new", true))
        assertEquals(InstallTrust.Quarantined, installationTrust("original", null, true))
        assertEquals(InstallTrust.Quarantined, installationTrust("original", "original", false))
    }

    @Test fun descriptorFencesAreLexicographicAndRequireFreshExactOrigin() {
        val descriptor = Descriptor("server", "https://radio.example", 2, 1, 1000, 1300, true)
        descriptor.validate("server", "https://radio.example", 1, 99, 1050)
        assertThrows(ConnectionFailure::class.java) { descriptor.validate("server", "https://radio.example", 3, 1, 1050) }
        assertThrows(ConnectionFailure::class.java) { descriptor.validate("server", "https://radio.example", 2, 2, 1050) }
        assertThrows(ConnectionFailure::class.java) { descriptor.validate("other", "https://radio.example", 1, 1, 1050) }
        assertThrows(ConnectionFailure::class.java) { descriptor.validate("server", "https://other.example", 1, 1, 1050) }
        assertThrows(ConnectionFailure::class.java) { descriptor.validate("server", "https://radio.example", 1, 1, 1300) }
        assertThrows(ConnectionFailure::class.java) { descriptor.validate("server", "https://radio.example", 1, 1, 900) }
    }
}
