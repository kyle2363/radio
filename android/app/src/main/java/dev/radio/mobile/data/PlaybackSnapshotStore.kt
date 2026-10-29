package dev.radio.mobile.data

import dev.radio.mobile.playback.PlaybackSnapshot
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.playback.snapshot
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import org.json.JSONArray
import org.json.JSONObject

/** One ordered writer outlives services. A load waits behind every earlier checkpoint. */
internal class PlaybackSnapshotStore(dao: LibraryDao, scope: CoroutineScope) {
    private sealed interface Request {
        data class Save(val state: PlaybackState) : Request
        data class Load(val reply: CompletableDeferred<PlaybackSnapshot?>) : Request
    }
    private val requests = Channel<Request>(Channel.UNLIMITED, onUndeliveredElement = {
        if (it is Request.Load) it.reply.cancel()
    })
    init {
        scope.launch {
            try {
                for (request in requests) when (request) {
                    is Request.Save -> try {
                        request.state.snapshot()?.let { dao.savePlaybackSnapshot(LocalPlaybackSnapshot(payload = encode(it))) }
                    } catch (cancelled: CancellationException) { throw cancelled }
                    catch (error: Exception) { android.util.Log.w("RadioPlayback", "Couldn't save playback bookmark", error) }
                    is Request.Load -> try {
                        request.reply.complete(dao.playbackSnapshot()?.let { decode(it.payload) })
                    } catch (cancelled: CancellationException) { request.reply.cancel(); throw cancelled }
                    catch (error: Exception) { request.reply.completeExceptionally(error) }
                }
            } finally { requests.cancel() }
        }
    }
    fun save(state: PlaybackState) {
        // Never overwrite an existing bookmark with the empty state during startup.
        if (state.currentId != null && state.queue.isNotEmpty()) requests.trySend(Request.Save(state))
    }
    suspend fun load(): PlaybackSnapshot? {
        val reply = CompletableDeferred<PlaybackSnapshot?>()
        requests.send(Request.Load(reply))
        return reply.await()
    }
    private fun encode(saved: PlaybackSnapshot) = JSONObject().put("version", 1)
        .put("queue", JSONArray(saved.queueIds)).put("order", JSONArray(saved.playOrder))
        .put("current", saved.currentId).put("positionMs", saved.positionMs)
        .put("source", saved.source).put("shuffled", saved.shuffled).toString()

    private fun decode(payload: String): PlaybackSnapshot {
        val json = JSONObject(payload)
        require(json.getInt("version") == 1) { "Unsupported playback bookmark" }
        fun ids(key: String): List<String> = json.getJSONArray(key).let { array ->
            List(array.length()) { array.getString(it) }.also { require(it.none(String::isBlank)) }
        }
        return PlaybackSnapshot(ids("queue"), ids("order"), json.getString("current"),
            json.getLong("positionMs"), json.getString("source"), json.getBoolean("shuffled"))
    }
}
