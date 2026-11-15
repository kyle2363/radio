package dev.radio.mobile

import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.data.LyricsEnvelope
import dev.radio.mobile.data.LyricsEnvelopeValidator
import java.security.MessageDigest
import org.junit.Assert.*
import org.junit.Test

class LyricsContractTest {
    private val hash = "a".repeat(64)
    private fun digest(text: String) = MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }
    private fun quote(text: String) = "\"" + text.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + "\""
    private fun envelope(text: String) = """{"schema":"radio.portable.lyrics","version":1,"catalog_id":"cat","revision":12,"manifest_sha256":"$hash","song_id":"42","status":"available","text":${quote(text)},"text_sha256":"${digest(text)}"}"""

    @Test fun validatesExactIdentityDigestAndPreservesLyricsText() {
        val result = LyricsEnvelopeValidator.parse(envelope("Verse one\n\nVerse two"), "cat", 12, hash, "42")
        assertEquals(LyricsEnvelope.Available("Verse one\n\nVerse two", digest("Verse one\n\nVerse two")), result)
    }

    @Test fun reportsOnlySupportedUnavailableReasons() {
        val text = """{"schema":"radio.portable.lyrics","version":1,"catalog_id":"cat","revision":12,"manifest_sha256":"$hash","song_id":"42","status":"unavailable","reason":"missing","text":null,"text_sha256":null}"""
        assertEquals(LyricsEnvelope.Unavailable("missing"), LyricsEnvelopeValidator.parse(text, "cat", 12, hash, "42"))
    }

    @Test fun rejectsIdentityMismatchUnknownFieldsInvalidDigestAndOversizedText() {
        val good = envelope("hi")
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(good, "other", 12, hash, "42") }
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(good.replace(digest("hi"), "b".repeat(64)), "cat", 12, hash, "42") }
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(good.dropLast(1) + ",\"extra\":true}", "cat", 12, hash, "42") }
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(envelope("x".repeat(65_537)), "cat", 12, hash, "42") }
    }

    @Test fun rejectsDuplicateKeysAndNonIntegerIdentityFields() {
        val good = envelope("hi")
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(good.replace("{", "{\"schema\":\"radio.portable.lyrics\","), "cat", 12, hash, "42") }
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(good.replace("\"version\":1", "\"version\":\"1\""), "cat", 12, hash, "42") }
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(good.replace("\"revision\":12", "\"revision\":12.0"), "cat", 12, hash, "42") }
    }

    @Test fun checksUtf8ByteBoundaryAndAcceptsMultibyteLyricsAtLimit() {
        val exact = "🎵".repeat(16_384)
        assertEquals(65_536, exact.toByteArray(Charsets.UTF_8).size)
        assertEquals(exact, (LyricsEnvelopeValidator.parse(envelope(exact), "cat", 12, hash, "42") as LyricsEnvelope.Available).text)
        val over = "🎵".repeat(16_385)
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(envelope(over), "cat", 12, hash, "42") }
    }

    @Test fun rejectsEmptyOrWhitespaceOnlyAvailableLyrics() {
        assertThrows(ConnectionFailure::class.java) { LyricsEnvelopeValidator.parse(envelope(" \n "), "cat", 12, hash, "42") }
    }
}
