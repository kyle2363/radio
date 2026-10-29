package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.playbackQueue
import org.junit.Assert.*
import org.junit.Test

class PlaybackQueueTest {
    private fun song(id: String, available: Boolean = true) = LocalSong(id, "tree", "folder", "content://fixture/$id", "Same name.mp3", "Folder", 1, 1, 1, 1000, available)
    @Test fun queueCapturesVisibleOrderAndKeepsDifferentRecordingsWithSameFilename() {
        val list = mutableListOf(song("b"), song("a"), song("missing", false), song("b"))
        val queue = playbackQueue(list, "a")
        list.reverse(); list.clear()
        assertEquals(listOf("b", "a"), queue.map { it.id })
    }
    @Test fun unavailableOrAbsentSelectionCannotStartAnotherSongByAccident() {
        assertTrue(playbackQueue(listOf(song("missing", false), song("other")), "missing").isEmpty())
        assertTrue(playbackQueue(listOf(song("other")), "absent").isEmpty())
    }
}
