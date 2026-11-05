package dev.radio.mobile.portable

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

class CatalogException(message: String) : Exception(message)
internal fun catalogRequire(condition: Boolean, message: String = "Invalid catalog structure.") { if (!condition) throw CatalogException(message) }

/** Canonical Python wire JSON, not a generic JSONObject reserializer. Numeric tokens are
 * retained exactly: Python's 1e-07, 1e+20 and -0.0 must never round-trip through Double.
 * The server transmits canonical_json bytes. Noncanonical whitespace/escaping/key order
 * is rejected. Unknown additive fields participate in the same checksum unchanged. */
sealed class CatalogJson {
    data class Obj(val fields: Map<String, CatalogJson>) : CatalogJson() {
        operator fun get(key: String): CatalogJson = fields[key] ?: throw CatalogException("Missing catalog field.")
        fun text(key: String, max: Int = 4096): String = get(key).text(max)
        fun integer(key: String): Long = get(key).integer()
        fun array(key: String): List<CatalogJson> = (get(key) as? Arr)?.values ?: throw CatalogException("Invalid catalog array.")
    }
    data class Arr(val values: List<CatalogJson>) : CatalogJson()
    data class Str(val value: String) : CatalogJson()
    data class Num(val token: String) : CatalogJson()
    data class Bool(val value: Boolean) : CatalogJson()
    data object Null : CatalogJson()

    fun text(max: Int = 4096): String {
        val value = (this as? Str)?.value ?: throw CatalogException("Invalid catalog string.")
        catalogRequire(value.isNotEmpty() && value.length <= max && value.none { it.code < 32 })
        return value
    }
    fun integer(): Long {
        val value = (this as? Num)?.token ?: throw CatalogException("Invalid catalog integer.")
        catalogRequire(value.matches(Regex("-?(0|[1-9][0-9]*)")))
        return value.toLongOrNull() ?: throw CatalogException("Catalog integer is too large.")
    }
    fun obj(): Obj = this as? Obj ?: throw CatalogException("Invalid catalog object.")
    fun canonical(): String = buildString { appendCanonical(this@CatalogJson) }

    companion object {
        const val MAX_BYTES = 16 * 1024 * 1024
        fun parse(bytes: ByteArray): CatalogJson {
            val value = parseStrict(bytes)
            catalogRequire(value.canonical() == try { Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString() } catch (_: Exception) { "" }, "Server must send canonical catalog JSON bytes.")
            return value
        }
        /** Strict duplicate-key/type-safe parsing for JSON APIs whose outer object order is not canonical. */
        fun parseStrict(bytes: ByteArray): CatalogJson {
            catalogRequire(bytes.size <= MAX_BYTES, "Catalog manifest exceeds the phone limit.")
            val text = try { Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString() }
            catch (_: Exception) { throw CatalogException("Catalog is not valid UTF-8.") }
            val parser = Parser(text)
            val value = parser.value(0)
            catalogRequire(parser.position == text.length, "Trailing or noncanonical catalog data.")
            return value
        }
        private val scalarOrder = Comparator<String> { a, b ->
            val x = a.codePoints().toArray(); val y = b.codePoints().toArray()
            var result = 0
            for (index in 0 until minOf(x.size, y.size)) { result = x[index].compareTo(y[index]); if (result != 0) break }
            if (result == 0) x.size.compareTo(y.size) else result
        }
        private fun StringBuilder.appendCanonical(value: CatalogJson) {
            when (value) {
                is Obj -> { append('{'); value.fields.keys.sortedWith(scalarOrder).forEachIndexed { i, key -> if (i > 0) append(','); quoted(key); append(':'); appendCanonical(value.fields.getValue(key)) }; append('}') }
                is Arr -> { append('['); value.values.forEachIndexed { i, item -> if (i > 0) append(','); appendCanonical(item) }; append(']') }
                is Str -> quoted(value.value)
                is Num -> append(value.token)
                is Bool -> append(value.value)
                Null -> append("null")
            }
        }
        private fun StringBuilder.quoted(text: String) {
            append('"')
            for (character in text) when (character) {
                '"' -> append("\\\""); '\\' -> append("\\\\"); '\b' -> append("\\b"); '\u000c' -> append("\\f")
                '\n' -> append("\\n"); '\r' -> append("\\r"); '\t' -> append("\\t")
                else -> if (character.code < 32) append("\\u" + character.code.toString(16).padStart(4, '0')) else append(character)
            }
            append('"')
        }
    }

    private class Parser(val text: String) {
        var position = 0
        var nodes = 0
        fun value(depth: Int): CatalogJson {
            catalogRequire(depth <= 20 && ++nodes <= 2_000_000, "Catalog nesting or item limit exceeded.")
            catalogRequire(position < text.length)
            return when (text[position]) {
                '{' -> {
                    position++; val fields = linkedMapOf<String, CatalogJson>()
                    if (!take('}')) { do { catalogRequire(position < text.length && text[position] == '"'); val key = string(); catalogRequire(!fields.containsKey(key)); expect(':'); fields[key] = value(depth + 1) } while (take(',')); expect('}') }
                    Obj(fields)
                }
                '[' -> { position++; val values = mutableListOf<CatalogJson>(); if (!take(']')) { do { values += value(depth + 1) } while (take(',')); expect(']') }; Arr(values) }
                '"' -> Str(string())
                't' -> { literal("true"); Bool(true) }; 'f' -> { literal("false"); Bool(false) }; 'n' -> { literal("null"); Null }
                else -> {
                    val start = position
                    while (position < text.length && text[position] in "-+0123456789.eE") position++
                    val number = text.substring(start, position)
                    catalogRequire(number.matches(Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?")))
                    // Huge integers are bounded by the manifest; floating point must be finite.
                    if (number.any { it in ".eE" }) catalogRequire(number.toDoubleOrNull()?.isFinite() == true)
                    Num(number)
                }
            }
        }
        private fun literal(value: String) { catalogRequire(text.startsWith(value, position)); position += value.length }
        private fun take(character: Char): Boolean = if (position < text.length && text[position] == character) { position++; true } else false
        private fun expect(character: Char) { catalogRequire(take(character)) }
        private fun string(): String {
            expect('"'); val result = StringBuilder()
            while (position < text.length) {
                val character = text[position++]
                if (character == '"') {
                    val string = result.toString()
                    var index = 0
                    while (index < string.length) { val c = string[index++]; if (c.isHighSurrogate()) { catalogRequire(index < string.length && string[index].isLowSurrogate()); index++ } else catalogRequire(!c.isLowSurrogate()) }
                    return string
                }
                catalogRequire(character.code >= 32)
                if (character != '\\') result.append(character) else {
                    catalogRequire(position < text.length)
                    when (val escaped = text[position++]) {
                        '"', '\\', '/' -> result.append(escaped)
                        'b' -> result.append('\b'); 'f' -> result.append('\u000c'); 'n' -> result.append('\n'); 'r' -> result.append('\r'); 't' -> result.append('\t')
                        'u' -> { catalogRequire(position + 4 <= text.length); val code = text.substring(position, position + 4).toIntOrNull(16) ?: throw CatalogException("Invalid JSON escape."); result.append(code.toChar()); position += 4 }
                        else -> throw CatalogException("Invalid JSON escape.")
                    }
                }
            }
            throw CatalogException("Unterminated catalog string.")
        }
    }
}
