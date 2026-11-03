package dev.radio.mobile.recommendation

/** The scoring namespace for a catalog vector descriptor. */
data class RadioVectorIdentity(val model: String, val version: String, val legacy: Boolean = false)

/**
 * Controls which catalog model descriptors can enter the local recommender.
 * Unknown legacy exporters may be opted in only when their source collection is explicit and
 * their identity is scoped to the immutable catalog snapshot.
 */
object CatalogVectorPolicy {
    private val collectionName = Regex("[A-Za-z0-9][A-Za-z0-9._-]*")
    private val snapshotHash = Regex("[0-9a-f]{64}")

    fun isLegacyCollection(model: String, version: String): Boolean {
        if (version != "unknown" || !model.startsWith("legacy_unknown:")) return false
        val name = model.removePrefix("legacy_unknown:")
        return name.isNotEmpty() && name.matches(collectionName) && name != "." && name != ".."
    }

    fun identity(
        model: String,
        version: String,
        snapshot: String,
        allowLegacy: Boolean = false
    ): RadioVectorIdentity? {
        if (model.isBlank() || version.isBlank()) return null
        if (isLegacyCollection(model, version)) {
            if (!allowLegacy || !snapshot.matches(snapshotHash)) return null
            return RadioVectorIdentity(model, "unknown@snapshot:$snapshot", legacy = true)
        }
        val lowerModel = model.lowercase()
        val lowerVersion = version.lowercase()
        if ("unknown" in lowerModel || "unknown" in lowerVersion ||
            "legacy" in lowerModel || "legacy" in lowerVersion) return null
        return RadioVectorIdentity(model, version, legacy = false)
    }
}
