package dev.radio.mobile.portable

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import kotlin.math.abs
import kotlin.math.sqrt

fun catalogSha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
data class VectorDescriptor(val songId: String, val space: String, val model: String, val modelVersion: String,
    val dimension: Int, val metric: String, val normalization: String, val dtype: String, val byteLength: Int, val sha256: String)
data class ValidatedCatalog(val catalogId: String, val revision: Long, val sha256: String, val baseRevision: Long?, val baseSha256: String?,
    val songs: Map<String, String>, val descriptors: List<VectorDescriptor>, val included: Set<String>, val bytes: ByteArray) {
    val uniqueBlobs: Map<String, VectorDescriptor> get() = descriptors.associateBy { it.sha256 }
}

object CatalogValidator {
    const val MAX_SONGS = 50_000
    const val MAX_VECTORS = 100_000
    const val MAX_BINARY_BYTES = 256L * 1024 * 1024
    const val MAX_CACHE_BYTES = 512L * 1024 * 1024
    private val digestPattern = Regex("[0-9a-f]{64}")
    fun manifest(bytes: ByteArray, base: ValidatedCatalog? = null): ValidatedCatalog {
        val value = CatalogJson.parse(bytes).obj()
        catalogRequire(value.text("schema") == "radio.portable.catalog" && value.integer("version") == 1L, "Unsupported catalog schema or version.")
        val checksum = value.text("manifest_sha256", 64)
        catalogRequire(digestPattern.matches(checksum))
        catalogRequire(catalogSha256(CatalogJson.Obj(value.fields - "manifest_sha256").canonical().toByteArray(Charsets.UTF_8)) == checksum, "Catalog manifest checksum mismatch.")
        val catalogId = value.text("catalog_id", 256)
        val revision = value.integer("revision").also { catalogRequire(it > 0) }
        val baseRevision = if (value["base_revision"] == CatalogJson.Null) null else value.integer("base_revision").also { catalogRequire(it > 0) }
        val baseSha = if (value["base_sha256"] == CatalogJson.Null) null else value.text("base_sha256", 64)
        if (baseRevision != null) catalogRequire(base != null && base.catalogId == catalogId && base.revision == baseRevision && base.sha256 == baseSha && revision > baseRevision, "Catalog requires its exact imported base revision.")
        else catalogRequire(baseSha == null)
        val rows = value.array("songs")
        catalogRequire(rows.size <= MAX_SONGS, "Catalog has too many songs for this phone.")
        val songs = linkedMapOf<String, String>()
        for (row in rows) {
            val song = row.obj(); val id = song.text("id", 256)
            catalogRequire(!songs.containsKey(id), "Duplicate catalog song identity.")
            for (field in listOf("folder", "filename")) {
                val label = song.fields[field]
                if (label != null && label != CatalogJson.Null) {
                    val text = (label as? CatalogJson.Str)?.value ?: throw CatalogException("Invalid catalog relative label.")
                    catalogRequire(text.length <= 4096 && ':' !in text && !text.startsWith('/') && !text.startsWith('\\') && ".." !in text.replace('\\', '/').split('/'))
                    if (field == "filename") catalogRequire('/' !in text && '\\' !in text)
                }
            }
            songs[id] = row.canonical()
        }
        val removed = value.array("removed_song_ids").map { it.text(256) }
        catalogRequire(removed.size <= MAX_SONGS && removed.size == removed.toSet().size && removed.none { it in songs }, "Invalid catalog tombstones.")
        val entries = value.array("embeddings")
        catalogRequire(entries.size <= MAX_VECTORS, "Catalog has too many vectors for this phone.")
        val seen = mutableSetOf<Pair<String, String>>()
        val unique = mutableMapOf<String, VectorDescriptor>()
        val descriptors = entries.map { raw ->
            val desc = raw.obj()
            val songId = desc.text("song_id", 256); val space = desc.text("space", 16)
            catalogRequire(songId in songs && space in setOf("text", "audio") && seen.add(songId to space), "Unknown or duplicate vector reference.")
            val dimension = desc.integer("dimension").also { catalogRequire(it in 1..65536) }.toInt()
            val dtype = desc.text("dtype", 16).also { catalogRequire(it in setOf("float32", "float16")) }
            val length = dimension * if (dtype == "float32") 4 else 2
            catalogRequire(desc.integer("byte_length") == length.toLong())
            val digest = desc.text("sha256", 64).also { catalogRequire(digestPattern.matches(it)) }
            catalogRequire(desc.text("blob", 80) == "vectors/$digest.bin", "Unsafe catalog blob path.")
            val metric = desc.text("metric", 16).also { catalogRequire(it in setOf("cosine", "l2", "ip")) }
            val norm = desc.text("normalization", 16).also { catalogRequire(it in setOf("none", "l2", "unknown")) }
            val item = VectorDescriptor(songId, space, desc.text("model", 512).also { catalogRequire(it.isNotBlank()) }, desc.text("model_version", 512).also { catalogRequire(it.isNotBlank()) }, dimension, metric, norm, dtype, length, digest)
            // Bytes may be interpreted by multiple valid encodings/provenance groups.
            // Deduplicate storage only; every descriptor is validated separately.
            unique[digest]?.let { catalogRequire(it.byteLength == length) }
            unique[digest] = item
            item
        }
        catalogRequire(unique.values.sumOf { it.byteLength.toLong() } <= MAX_BINARY_BYTES, "Catalog vectors exceed the phone storage limit.")
        val includedRows = value.array("included_blobs").map { it.text(64).also { digest -> catalogRequire(digestPattern.matches(digest)) } }
        catalogRequire(includedRows.size <= MAX_VECTORS && includedRows.size == includedRows.toSet().size)
        val included = includedRows.toSet()
        catalogRequire(included.all { it in unique }, "Unreferenced catalog blob.")
        val baseEncodings = base?.descriptors?.map { it.sha256 to it.dtype }?.toSet().orEmpty()
        for (desc in descriptors) if (desc.sha256 !in included) catalogRequire(baseRevision != null && (desc.sha256 to desc.dtype) in baseEncodings, "Missing catalog blob outside the exact base.")
        return ValidatedCatalog(catalogId, revision, checksum, baseRevision, baseSha, songs, descriptors, included, bytes)
    }
    fun validateBlob(descriptor: VectorDescriptor, bytes: ByteArray) {
        catalogRequire(bytes.size == descriptor.byteLength && catalogSha256(bytes) == descriptor.sha256, "Catalog vector length or checksum mismatch.")
        val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        var squares = 0.0
        repeat(descriptor.dimension) {
            val value = if (descriptor.dtype == "float32") buffer.float else half(buffer.short.toInt() and 0xffff)
            catalogRequire(value.isFinite(), "Non-finite catalog vector.")
            squares += value.toDouble() * value
        }
        val norm = sqrt(squares)
        catalogRequire(norm.isFinite() && norm > 0.0, "Zero or invalid catalog vector.")
        if (descriptor.normalization == "l2") catalogRequire(abs(norm - 1.0) <= 0.01, "Catalog vector declares invalid normalization.")
    }
    internal fun half(bits: Int): Float {
        val sign = if (bits and 0x8000 == 0) 1.0 else -1.0
        val exponent = bits ushr 10 and 31; val fraction = bits and 1023
        return when (exponent) { 0 -> (sign * Math.scalb(fraction.toDouble(), -24)).toFloat(); 31 -> if (fraction == 0) (sign * Double.POSITIVE_INFINITY).toFloat() else Float.NaN; else -> (sign * Math.scalb(1.0 + fraction / 1024.0, exponent - 15)).toFloat() }
    }
}
