package dev.radio.mobile.recommendation

import dev.radio.mobile.data.*
import dev.radio.mobile.portable.PortableRegisterCandidate
import dev.radio.mobile.portable.PortableRegisterOrder
import org.json.JSONObject

internal data class SharedRadioProfile(
    val favorites: Map<String, Boolean> = emptyMap(),
    val dislikes: Map<String, Boolean> = emptyMap(),
    val blacklist: Set<String> = emptySet(),
    val energy: Map<String, Double> = emptyMap(),
    val explorationDefault: Double? = null,
    val localFavorites: Set<String> = emptySet(),
    val localDislikes: Set<String> = emptySet(),
)

/** Pending local edits participate in the same resolver as downloaded winners. */
internal suspend fun readSharedRadioProfile(db: LibraryDatabase, record: PortableCatalogRevision): SharedRadioProfile {
    val candidates = mutableMapOf<String, MutableList<Pair<PortableRegisterCandidate, JSONObject>>>()
    fun add(payload: String) {
        val event = JSONObject(payload)
        val kind = event.optString("kind")
        val sid = event.opt("song_id") as? String
        val key = if (kind == "preference_changed") "preference:${event.optString("key")}" else {
            if (sid == null || event.optString("catalog_id") != record.catalogId) return
            "$kind:$sid"
        }
        if (kind !in setOf("favorite_changed", "dislike_changed", "blacklist_changed", "energy_changed", "preference_changed")) return
        val candidate = PortableRegisterCandidate(event.getLong("logical_clock"), event.getString("device_id"), event.getString("event_id"), "")
        candidates.getOrPut(key) { mutableListOf() }.add(candidate to event)
    }
    db.journal().profileRegisters(record.serverId).forEach { add(it.payload) }
    db.journal().profileLocalEvents(record.serverId).forEach { add(it.payload) }
    val winners = candidates.mapValues { (_, entries) ->
        val winner = PortableRegisterOrder.winner(entries.map { it.first })!!
        entries.first { it.first == winner }.second
    }
    fun flags(kind: String): Map<String, Boolean> = winners.values.filter { it.optString("kind") == kind }
        .mapNotNull { event -> (event.opt("value") as? Boolean)?.let { event.getString("song_id") to it } }.toMap()
    val energy = winners.values.filter { it.optString("kind") == "energy_changed" }
        .mapNotNull { event -> (event.opt("value") as? Number)?.toDouble()?.takeIf { it.isFinite() && it in 0.0..1.0 }?.let { event.getString("song_id") to it } }.toMap()
    val exploration = (winners["preference:exploration_level"]?.opt("value") as? Number)?.toDouble()?.takeIf { it.isFinite() && it in 0.0..1.0 }
    val latestLocal = db.journal().latestFeedbackEvents()
    val origins = latestLocal.chunked(500).flatMap { db.journal().radioOrigins(it.map { row -> row.eventId }) }.associateBy { it.eventId }
    val localOnly = latestLocal.filter { row ->
        val event = JSONObject(row.payload)
        event.opt("value") == true && (event.opt("song_id") !is String ||
            event.optString("catalog_id") != record.catalogId || origins[row.eventId]?.serverId != record.serverId)
    }
    return SharedRadioProfile(flags("favorite_changed"), flags("dislike_changed"), flags("blacklist_changed").filterValues { it }.keys, energy, exploration,
        localOnly.filter { it.kind == "favorite_changed" }.map { it.localId }.toSet(),
        localOnly.filter { it.kind == "dislike_changed" }.map { it.localId }.toSet())
}
