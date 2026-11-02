package dev.radio.mobile.portable

/** Raw binary batches stay far below the future decimal 100 MB transfer limit.
 * Smaller 8 MB buffers also fit the existing transport ceiling and phone memory budget. */
object CatalogTransfers {
    const val MAX_BYTES = 8_000_000
    const val MAX_COUNT = 64

    fun batches(descriptors: List<VectorDescriptor>): List<List<VectorDescriptor>> {
        val result = mutableListOf<List<VectorDescriptor>>()
        var current = mutableListOf<VectorDescriptor>()
        var bytes = 0L
        for (descriptor in descriptors) {
            catalogRequire(descriptor.byteLength in 1..MAX_BYTES, "Vector exceeds the catalog transfer limit.")
            if (current.size == MAX_COUNT || bytes + descriptor.byteLength > MAX_BYTES) {
                result += current.toList(); current = mutableListOf(); bytes = 0
            }
            current += descriptor; bytes += descriptor.byteLength
        }
        if (current.isNotEmpty()) result += current.toList()
        return result
    }

    fun path(catalog: ValidatedCatalog, batch: List<VectorDescriptor>): String =
        "/vector-batch?revision=${catalog.revision}&manifest_sha256=${catalog.sha256}&digests=${batch.joinToString(","){it.sha256}}"

    fun validate(batch: List<VectorDescriptor>, bytes: ByteArray): List<Pair<VectorDescriptor, ByteArray>> {
        catalogRequire(batch.size in 1..MAX_COUNT && bytes.size <= MAX_BYTES)
        catalogRequire(bytes.size.toLong() == batch.sumOf { it.byteLength.toLong() }, "Catalog batch length mismatch. Previous catalog kept.")
        var offset = 0
        return batch.map { descriptor ->
            val vector = bytes.copyOfRange(offset, offset + descriptor.byteLength)
            offset += descriptor.byteLength
            CatalogValidator.validateBlob(descriptor, vector)
            descriptor to vector
        }
    }
}
