package dev.radio.mobile.playback

import dev.radio.mobile.data.LocalSong

/** A paused bookmark: deliberately contains no playWhenReady or isPlaying flag. */
data class PlaybackSnapshot(
    val queueIds: List<String>, val playOrder: List<String>, val currentId: String,
    val positionMs: Long, val source: String, val shuffled: Boolean
)

/** Talk is never bookmarked: during a clip the bookmark is the song it introduces, from its start. */
internal fun PlaybackState.snapshot(): PlaybackSnapshot? = songCurrentId?.takeIf { id -> queue.any { it.id == id } }?.let {
    PlaybackSnapshot(queue.map { song -> song.id }, playOrder.toList(), it, if (talkPlaying) 0L else positionMs, source, shuffled)
}

internal data class RestoredPlayback(
    val songs: List<LocalSong>, val playOrder: List<String>, val currentId: String,
    val positionMs: Long, val source: String, val shuffled: Boolean, val error: String?
)

/** Resolve identities against today's catalog, retaining known unavailable files as such. */
internal fun resolvePlaybackSnapshot(saved: PlaybackSnapshot, catalog: List<LocalSong>, accessLost: Boolean): RestoredPlayback? {
    val byId = catalog.associateBy { it.id }
    val songs = saved.queueIds.distinct().mapNotNull(byId::get).filter { it.uri.startsWith("content://") }
    if (songs.isEmpty()) return null
    val ids = songs.map { it.id }
    val known = ids.toSet()
    val order = if (saved.shuffled) (saved.playOrder.filter { it in known } + ids).distinct() else ids
    val current = songs.firstOrNull { it.id == saved.currentId }
        ?: order.firstNotNullOfOrNull { id -> byId[id]?.takeIf { it.available } } ?: songs.first()
    val sameSong = current.id == saved.currentId
    val position = if (sameSong) saved.positionMs.coerceAtLeast(0).let { ms ->
        current.durationMs?.takeIf { it > 0 }?.let { ms.coerceAtMost(it - 1) } ?: ms
    } else 0L
    val error = when {
        accessLost -> "Music folder access has expired. Choose the folder again before playing."
        !current.available -> "The saved song is unavailable. Rescan Music or choose another song."
        !sameSong -> "The previous song is no longer in this library. The remaining queue is restored paused."
        else -> null
    }
    return RestoredPlayback(songs, order, current.id, position, saved.source, saved.shuffled, error)
}
