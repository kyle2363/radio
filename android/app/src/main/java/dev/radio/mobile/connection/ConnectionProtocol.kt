package dev.radio.mobile.connection

import org.json.JSONObject
import android.util.JsonReader
import android.util.JsonToken
import java.io.StringReader
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.util.UUID

internal fun json(text: String): JSONObject = try {
    if (text.length > 32768) throw IllegalArgumentException()
    JsonReader(StringReader(text)).use { reader ->
        reader.isLenient = false
        strictValue(reader, 0)
        if (reader.peek() != JsonToken.END_DOCUMENT) throw IllegalArgumentException()
    }
    JSONObject(text)
} catch (_: Exception) { throw ConnectionFailure("Invalid server response or pairing bundle.") }

private fun strictValue(reader: JsonReader, depth: Int) {
    if (depth > 12) throw IllegalArgumentException()
    when (reader.peek()) {
        JsonToken.BEGIN_OBJECT -> {
            reader.beginObject()
            val names = mutableSetOf<String>()
            while (reader.hasNext()) {
                if (!names.add(reader.nextName()) || names.size > 128) throw IllegalArgumentException()
                strictValue(reader, depth + 1)
            }
            reader.endObject()
        }
        JsonToken.BEGIN_ARRAY -> {
            reader.beginArray()
            var count = 0
            while (reader.hasNext()) { if (++count > 256) throw IllegalArgumentException(); strictValue(reader, depth + 1) }
            reader.endArray()
        }
        JsonToken.STRING, JsonToken.NUMBER -> reader.nextString()
        JsonToken.BOOLEAN -> reader.nextBoolean()
        JsonToken.NULL -> reader.nextNull()
        else -> throw IllegalArgumentException()
    }
}
internal fun JSONObject.text(name: String, max: Int = 256): String {
    val value = opt(name) as? String ?: throw ConnectionFailure("Missing or invalid server field.")
    if (value.isEmpty() || value.length > max || value.any { it.code < 32 }) throw ConnectionFailure("Missing or invalid server field.")
    return value
}
internal fun JSONObject.number(name: String): Long = when (val value = opt(name)) {
    is Int -> value.toLong()
    is Long -> value
    else -> throw ConnectionFailure("Missing or invalid server number.")
}
internal fun JSONObject.version() { if (number("protocol_version") != 1L) throw ConnectionFailure("Unsupported server protocol.") }

data class PairingBundle(
    val serverId: String, val publicKeyPin: String, val endpoint: String, val ticketId: String,
    val ticketSecret: String, val deviceName: String, val expiresAt: Long,
) {
    // Never use data-class toString in diagnostics: this value contains the one-time secret.
    override fun toString() = "PairingBundle(redacted)"
    internal fun toJson() = JSONObject().put("protocol_version", 1).put("server_id", serverId)
        .put("public_key_pin", publicKeyPin).put("endpoint", endpoint).put("ticket_id", ticketId)
        .put("ticket_secret", ticketSecret).put("device_name", deviceName).put("expires_at", expiresAt)
    companion object {
        fun parse(text: String, policy: EndpointPolicy): PairingBundle {
            if (text.toByteArray().size > 8192) throw ConnectionFailure("Pairing bundle is too large.")
            val obj = json(text).apply { version() }
            val pin = obj.text("public_key_pin", 64)
            if (!pin.matches(Regex("[0-9a-f]{64}"))) throw ConnectionFailure("Invalid trusted server key pin.")
            return PairingBundle(obj.text("server_id", 128), pin, policy.validate(obj.text("endpoint", 2048)),
                obj.text("ticket_id", 128), WireBase64.secret(obj.text("ticket_secret", 43)), obj.text("device_name", 80), obj.number("expires_at"))
        }
    }
}

data class PairingRecord(
    val bundle: PairingBundle, val bearer: String, val exchangeId: String, val installationMarker: String,
    val deviceId: String? = null, val credentialExpiresAt: Long? = null,
    val generation: Long = 0, val revision: Long = 0, val authorizationBlocked: Boolean = false,
) {
    override fun toString() = "PairingRecord(redacted)"
    val identityAnchor: String get() = "${bundle.serverId}:$exchangeId"
    internal fun toJson() = JSONObject().put("bundle", bundle.toJson()).put("bearer", bearer).put("exchange_id", exchangeId)
        .put("installation_marker", installationMarker).put("device_id", deviceId ?: JSONObject.NULL)
        .put("credential_expires_at", credentialExpiresAt ?: JSONObject.NULL).put("generation", generation)
        .put("revision", revision).put("authorization_blocked", authorizationBlocked)
    companion object {
        internal fun parse(text: String, policy: EndpointPolicy): PairingRecord {
            val obj = json(text)
            val exchange = obj.text("exchange_id", 36)
            if (runCatching { UUID.fromString(exchange).toString() }.getOrNull() != exchange) throw ConnectionFailure("Pairing storage needs repair.")
            return PairingRecord(PairingBundle.parse(obj.getJSONObject("bundle").toString(), policy), WireBase64.secret(obj.text("bearer", 43)),
                exchange, obj.text("installation_marker", 43), if (obj.isNull("device_id")) null else obj.text("device_id", 128),
                if (obj.isNull("credential_expires_at")) null else obj.number("credential_expires_at"), obj.number("generation"), obj.number("revision"), obj.getBoolean("authorization_blocked"))
        }
    }
}

internal fun parseDescriptor(envelope: String, record: PairingRecord, now: Long): Descriptor {
    val obj = json(envelope)
    val bytes = DescriptorSignatures.verify(record.bundle.publicKeyPin, obj.text("public_key", 512), obj.text("payload", 12000), obj.text("signature", 128))
    val text = try { Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString() }
    catch (_: Exception) { throw ConnectionFailure("Invalid signed server data.") }
    val payload = json(text).apply { version() }
    val descriptor = Descriptor(payload.text("server_id", 128), payload.text("endpoint", 2048), payload.number("connection_generation"),
        payload.number("publication_revision"), payload.number("issued_at"), payload.number("expires_at"), payload.opt("available") == true)
    descriptor.validate(record.bundle.serverId, record.bundle.endpoint, record.generation, record.revision, now)
    return descriptor
}
