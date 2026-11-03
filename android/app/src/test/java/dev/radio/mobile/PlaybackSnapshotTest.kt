package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.*
import org.junit.Assert.*
import org.junit.Test

class PlaybackSnapshotTest {
    private fun song(id: String, available: Boolean = true) = LocalSong(id, "tree", "folder", "content://fixture/$id", "$id.mp3", "Folder", 1, 1, 1, 10000, available)
    private val saved = PlaybackSnapshot(listOf("a", "b", "c"), listOf("c", "a", "b"), "a", 3456, "October", true)
    @Test fun restoreKeepsNaturalAndShuffledOrdersWithoutRepinningCurrentSong() {
        val result = resolvePlaybackSnapshot(saved, listOf(song("b"), song("c"), song("a")), false)!!
        assertEquals(listOf("a", "b", "c"), result.songs.map { it.id })
        assertEquals(listOf("c", "a", "b"), result.playOrder)
        assertEquals("a", result.currentId)
        assertEquals(3456L, result.positionMs)
        assertEquals("October", result.source)
        assertTrue(result.shuffled)
        assertNull(result.error)
    }
    @Test fun missingRecordsAndDuplicateOrderEntriesCannotCreateInvalidPlayerIndices() {
        val result = resolvePlaybackSnapshot(saved.copy(playOrder = listOf("c", "gone", "c")), listOf(song("b"), song("c")), false)!!
        assertEquals(listOf("b", "c"), result.songs.map { it.id })
        assertEquals(listOf("c", "b"), result.playOrder)
        assertEquals("c", result.currentId)
        assertEquals(0L, result.positionMs)
        assertNotNull(result.error)
        assertNull(resolvePlaybackSnapshot(saved, emptyList(), false))
    }
    @Test fun unavailableFilesAndRevokedAccessRemainExplicitAndPositionsAreBounded() {
        val catalog = listOf(song("a", false), song("b"), song("c"))
        val missing = resolvePlaybackSnapshot(saved.copy(positionMs = Long.MAX_VALUE), catalog, false)!!
        assertEquals("a", missing.currentId)
        assertFalse(missing.songs.first().available)
        assertEquals(9999L, missing.positionMs)
        assertNotNull(missing.error)
        val lost = resolvePlaybackSnapshot(saved.copy(positionMs = -100, shuffled = false), catalog, true)!!
        assertEquals(0L, lost.positionMs)
        assertEquals(listOf("a", "b", "c"), lost.playOrder)
        assertTrue(lost.error!!.contains("access"))
    }
}
