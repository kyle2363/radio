package dev.radio.mobile

import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.JournalIdentityProvider
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.recommendation.*
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CompletableDeferred

/**
 * Scripted desktop for instrumented tests. It answers the frozen wire contract (plan section 8): picks come from the
 * songs it was given, minus the anchor and the request's exclusions, in [slots] order.
 */
class ScriptedConnectedLink(private val serverIds: List<String>) : ConnectedServerLink {
    val posts = CopyOnWriteArrayList<Pair<String, String>>()
    @Volatile var gate: CompletableDeferred<Unit>? = null
    @Volatile var permitted = true
    @Volatile var slots = listOf("exploit", "exploit", "explore", "wildcard", "context")
    @Volatile var vibeLocked = false
    private var epoch = 0L

    fun count(suffix: String) = posts.count { it.first.endsWith(suffix) }
    override suspend fun access() = ConnectedAccess("srv:device", true, permitted)

    override suspend fun post(path: String, body: String): RadioResponse {
        posts += path to body
        val request = ConnectedJson.parse(body) as Map<*, *>
        return when {
            path == "/availability" -> RadioResponse(200, ConnectedJson.write(mapOf("digest" to request["digest"], "count" to (request["song_ids"] as List<*>).size, "catalog_id" to request["catalog_id"], "catalog_revision" to request["catalog_revision"])))
            path == "/contexts" || !path.endsWith("/recommendations") -> RadioResponse(200, ConnectedJson.write(mapOf("context_id" to "ctx-aaaaaaaaaaaaaaaa", "epoch" to ++epoch, "expires_in" to 1800)))
            else -> {
                gate?.await()
                val excluded = ((request["exclude_song_ids"] as? List<*>).orEmpty() + request["anchor_song_id"]).toSet()
                val ids = serverIds.filter { it !in excluded }.take(slots.size)
                RadioResponse(200, ConnectedJson.write(linkedMapOf("request_id" to request["request_id"], "result_id" to "result-${posts.size}", "context_id" to "ctx-aaaaaaaaaaaaaaaa",
                    "epoch" to request["epoch"], "availability_digest" to request["availability_digest"], "catalog_revision" to request["catalog_revision"],
                    "anchor_song_id" to request["anchor_song_id"], "status" to "ok", "ranked_by" to "llm", "vibe_locked" to vibeLocked,
                    "picks" to ids.mapIndexed { i, id -> linkedMapOf("song_id" to id, "slot" to slots[i], "reason" to "Because $id") })))
            }
        }
    }

    companion object {
        /** Maps every song to server ID "s<index>" under one synthetic catalog. */
        fun publishMappings(songs: List<LocalSong>): Long {
            val generation = JournalIdentityProvider.invalidateMappings()
            JournalIdentityProvider.publishMappings(generation, songs.mapIndexed { index, song ->
                JournalIdentityProvider.Entry(song, "srv", "cat", 12, "s$index", "confirmed:synthetic:hash")
            })
            return generation
        }
        fun serverIds(count: Int) = (0 until count).map { "s$it" }
    }
}
