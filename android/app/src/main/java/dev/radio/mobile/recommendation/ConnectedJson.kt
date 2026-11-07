package dev.radio.mobile.recommendation

/**
 * Tiny strict JSON codec for the connected-recommendation wire. It has no Android dependency so
 * the request/reply logic can be verified in plain JVM tests (Android's org.json is stubbed there).
 * Values: Map<String,Any?>, List<Any?>, String, Long, Double, Boolean, null.
 */
internal object ConnectedJson {
    fun write(value: Any?): String = StringBuilder().also { append(it, value) }.toString()

    private fun append(out: StringBuilder, value: Any?) {
        when (value) {
            null -> out.append("null")
            is String -> quote(out, value)
            is Boolean -> out.append(value)
            is Int, is Long -> out.append(value.toString())
            is Double -> { require(value.isFinite()); out.append(value.toString()) }
            is Float -> { require(value.isFinite()); out.append(value.toDouble().toString()) }
            is Map<*, *> -> {
                out.append('{')
                var first = true
                for ((k, v) in value) {
                    if (!first) out.append(',')
                    first = false
                    quote(out, k as String)
                    out.append(':')
                    append(out, v)
                }
                out.append('}')
            }
            is Iterable<*> -> {
                out.append('[')
                var first = true
                for (v in value) {
                    if (!first) out.append(',')
                    first = false
                    append(out, v)
                }
                out.append(']')
            }
            else -> throw IllegalArgumentException("Unsupported JSON value")
        }
    }

    private fun quote(out: StringBuilder, text: String) {
        out.append('"')
        for (c in text) {
            when {
                c == '"' -> out.append("\\\"")
                c == '\\' -> out.append("\\\\")
                c == '\n' -> out.append("\\n")
                c == '\r' -> out.append("\\r")
                c == '\t' -> out.append("\\t")
                c.code < 32 -> out.append("\\u" + c.code.toString(16).padStart(4, '0'))
                else -> out.append(c)
            }
        }
        out.append('"')
    }

    /** Throws IllegalArgumentException on malformed input, duplicate keys or excessive depth. */
    fun parse(text: String): Any? {
        val parser = Parser(text)
        val value = parser.value(0)
        parser.skip()
        require(parser.index == text.length) { "Trailing data" }
        return value
    }

    private class Parser(val s: String) {
        var index = 0

        fun skip() {
            while (index < s.length && (s[index] == ' ' || s[index] == '\t' || s[index] == '\r' || s[index] == '\n')) index++
        }

        fun value(depth: Int): Any? {
            require(depth <= 12) { "Too deep" }
            skip()
            require(index < s.length) { "Unexpected end" }
            val c = s[index]
            return when {
                c == '{' -> obj(depth)
                c == '[' -> arr(depth)
                c == '"' -> str()
                c == 't' -> literal("true", true)
                c == 'f' -> literal("false", false)
                c == 'n' -> literal("null", null)
                c == '-' || c.isDigit() -> num()
                else -> throw IllegalArgumentException("Unexpected character")
            }
        }

        fun literal(word: String, result: Any?): Any? {
            require(s.startsWith(word, index))
            index += word.length
            return result
        }

        fun obj(depth: Int): Map<String, Any?> {
            index++
            val map = LinkedHashMap<String, Any?>()
            skip()
            if (index < s.length && s[index] == '}') { index++; return map }
            while (true) {
                skip()
                require(index < s.length && s[index] == '"')
                val key = str()
                require(key !in map) { "Duplicate key" }
                skip()
                require(index < s.length && s[index] == ':')
                index++
                map[key] = value(depth + 1)
                skip()
                require(index < s.length)
                val next = s[index++]
                if (next == '}') return map
                require(next == ',') { "Bad object" }
            }
        }

        fun arr(depth: Int): List<Any?> {
            index++
            val list = ArrayList<Any?>()
            skip()
            if (index < s.length && s[index] == ']') { index++; return list }
            while (true) {
                list.add(value(depth + 1))
                skip()
                require(index < s.length)
                val next = s[index++]
                if (next == ']') return list
                require(next == ',') { "Bad array" }
            }
        }

        fun str(): String {
            index++
            val out = StringBuilder()
            while (true) {
                require(index < s.length)
                val c = s[index++]
                if (c == '"') return out.toString()
                require(c.code >= 32) { "Control character" }
                if (c != '\\') { out.append(c); continue }
                require(index < s.length)
                when (val e = s[index++]) {
                    '"', '\\', '/' -> out.append(e)
                    'n' -> out.append('\n')
                    'r' -> out.append('\r')
                    't' -> out.append('\t')
                    'b' -> out.append('\b')
                    'f' -> out.append('\u000c')
                    'u' -> {
                        require(index + 4 <= s.length)
                        out.append(s.substring(index, index + 4).toInt(16).toChar())
                        index += 4
                    }
                    else -> throw IllegalArgumentException("Bad escape")
                }
            }
        }

        fun num(): Any {
            val start = index
            if (s[index] == '-') index++
            while (index < s.length && (s[index].isDigit() || s[index] == '.' || s[index] == 'e' || s[index] == 'E' || s[index] == '+' || s[index] == '-')) index++
            val token = s.substring(start, index)
            return token.toLongOrNull() ?: token.toDouble().also { require(it.isFinite()) }
        }
    }
}
