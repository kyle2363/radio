package dev.radio.mobile

import dev.radio.mobile.portable.*
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class CatalogValidatorTest {
    @Test fun canonicalWirePreservesPythonFloatingPointTokensAndUnicode() {
        val source = "{\"a\":1e-07,\"b\":-0.0,\"c\":1e+20,\"海\":\"🎵\\n\"}"
        assertEquals(source, CatalogJson.parse(source.toByteArray()).canonical())
        assertThrows(CatalogException::class.java) { CatalogJson.parse("{\"a\":1,\"a\":2}".toByteArray()) }
        assertThrows(CatalogException::class.java) { CatalogJson.parse("{\"a\":NaN}".toByteArray()) }
        listOf("{\"a\":1,\"\\u0061\":2}", "{\"a\":1e999}", "{\"b\":1,\"a\":2}", "{\"a\":1} ", "\"\\ud800\"").forEach { invalid ->
            assertThrows(CatalogException::class.java) { CatalogJson.parse(invalid.toByteArray()) }
        }
        assertThrows(CatalogException::class.java) { CatalogJson.parse(byteArrayOf(0xc3.toByte(), 0x28)) }
    }
    @Test fun binaryValidationChecksLittleEndianFiniteNonzeroAndNormalization() {
        val bytes = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putFloat(0.6f).putFloat(0.8f).array()
        val descriptor = VectorDescriptor("song", "text", "model", "v1", 2, "cosine", "l2", "float32", 8, catalogSha256(bytes))
        CatalogValidator.validateBlob(descriptor, bytes)
        assertThrows(CatalogException::class.java) { CatalogValidator.validateBlob(descriptor, bytes.copyOf(7)) }
        val zero = ByteArray(8)
        assertThrows(CatalogException::class.java) { CatalogValidator.validateBlob(descriptor.copy(sha256 = catalogSha256(zero)), zero) }
        val nan = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putFloat(Float.NaN).putFloat(1f).array()
        assertThrows(CatalogException::class.java) { CatalogValidator.validateBlob(descriptor.copy(sha256 = catalogSha256(nan)), nan) }
    }
    private fun manifest(songs: List<CatalogJson>, descriptors: List<CatalogJson>, included: List<CatalogJson>): ByteArray {
        val fields = mapOf("schema" to CatalogJson.Str("radio.portable.catalog"), "version" to CatalogJson.Num("1"), "catalog_id" to CatalogJson.Str("synthetic"), "revision" to CatalogJson.Num("1"),
            "base_revision" to CatalogJson.Null, "base_sha256" to CatalogJson.Null, "songs" to CatalogJson.Arr(songs), "embeddings" to CatalogJson.Arr(descriptors),
            "included_blobs" to CatalogJson.Arr(included), "removed_song_ids" to CatalogJson.Arr(emptyList()))
        return CatalogJson.Obj(fields + ("manifest_sha256" to CatalogJson.Str(catalogSha256(CatalogJson.Obj(fields).canonical().toByteArray())))).canonical().toByteArray()
    }
    private fun descriptor(song: String, digest: String, dimension: Int, dtype: String): CatalogJson.Obj = CatalogJson.Obj(mapOf(
        "song_id" to CatalogJson.Str(song), "space" to CatalogJson.Str("text"), "model" to CatalogJson.Str("synthetic"), "model_version" to CatalogJson.Str("1"),
        "dimension" to CatalogJson.Num(dimension.toString()), "dtype" to CatalogJson.Str(dtype), "metric" to CatalogJson.Str("ip"), "normalization" to CatalogJson.Str("none"),
        "byte_length" to CatalogJson.Num((dimension * if (dtype == "float32") 4 else 2).toString()), "sha256" to CatalogJson.Str(digest), "blob" to CatalogJson.Str("vectors/$digest.bin")))
    @Test fun identicalStoredBytesMayHaveDistinctValidVectorEncodingsAndProvenance() {
        val bytes = byteArrayOf(0, 0x3c, 0, 0x3c)
        val digest = catalogSha256(bytes)
        val songs = listOf("a", "b").map { CatalogJson.Obj(mapOf("id" to CatalogJson.Str(it))) }
        val catalog = CatalogValidator.manifest(manifest(songs, listOf(descriptor("a", digest, 1, "float32"), descriptor("b", digest, 2, "float16")), listOf(CatalogJson.Str(digest))))
        assertEquals(1, catalog.uniqueBlobs.size)
        catalog.descriptors.forEach { CatalogValidator.validateBlob(it, bytes) }
    }
    @Test fun aggregateVectorAndSongQuotasRejectBeforeAnyBlobAllocation() {
        val descriptors = (1..1025).map { descriptor("song", it.toString(16).padStart(64, '0'), 65536, "float32").let { value ->
            CatalogJson.Obj(value.fields + ("song_id" to CatalogJson.Str("song-$it")))
        } }
        val songs = (1..1025).map { CatalogJson.Obj(mapOf("id" to CatalogJson.Str("song-$it"))) }
        assertThrows(CatalogException::class.java) { CatalogValidator.manifest(manifest(songs, descriptors, descriptors.map { it["sha256"] })) }
        val tooManySongs = (1..50001).map { CatalogJson.Obj(mapOf("id" to CatalogJson.Str("song-$it"))) }
        assertThrows(CatalogException::class.java) { CatalogValidator.manifest(manifest(tooManySongs, emptyList(), emptyList())) }
    }
}
