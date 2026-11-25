package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.data.LocalFolder
import dev.radio.mobile.music.*
import org.junit.Assert.*
import org.junit.Test

class LibraryOrderingTest {
    private fun song(id: String, modified: Long?, added: Long, duration: Long?) =
        LocalSong(id, "tree", "folder", "content://test/$id", "$id.mp3", "", modified, added, 1L, duration)
    @Test fun sortsUseRealTimestampsAndPutUnknownValuesLast() {
        val songs = listOf(song("unknown", null, 30, null), song("old", 100, 20, 3000), song("new", 200, 10, 1000))
        assertEquals(listOf("new", "old", "unknown"), orderSongs(songs, LibrarySort.ModifiedNewest).map { it.id })
        assertEquals(listOf("old", "new", "unknown"), orderSongs(songs, LibrarySort.ModifiedOldest).map { it.id })
        assertEquals(listOf("unknown", "old", "new"), orderSongs(songs, LibrarySort.AddedNewest).map { it.id })
        assertEquals(listOf("old", "new", "unknown"), orderSongs(songs, LibrarySort.Duration).map { it.id })
    }
    @Test fun documentIdentityIncludesProviderAndUsesDocumentNotFilenameOrTree() {
        assertEquals(localDocumentId("provider", "doc"), localDocumentId("provider", "doc"))
        assertNotEquals(localDocumentId("provider", "doc"), localDocumentId("other", "doc"))
        assertNotEquals(localDocumentId("provider", "doc"), localDocumentId("provider", "another-doc"))
    }
    @Test fun folderDurationAndAddedSortIncludeNestedTracks() {
        val folders = listOf(LocalFolder("a", "tree", "root", "A", "A", 1L), LocalFolder("b", "tree", "root", "B", "B", 2L), LocalFolder("nested", "tree", "a", "Nested", "A/Nested", 3L))
        val tracks = listOf(song("old", 1, 100, 3000).copy(folderId = "nested"), song("new", 2, 200, 1000).copy(folderId = "b"))
        assertEquals(listOf("a", "b"), orderFolders(folders, tracks, "root", LibrarySort.Duration).map { it.id })
        assertEquals(listOf("b", "a"), orderFolders(folders, tracks, "root", LibrarySort.AddedNewest).map { it.id })
    }
    @Test fun namesSortNaturallyAndTitleSortUsesTheTitle() {
        fun named(id: String, file: String) = song(id, 1, 1, 1).copy(fileName = file)
        val songs = listOf(named("b", "Zed - Alpha 10.mp3"), named("a", "Abe - Omega.mp3"), named("c", "Zed - Alpha 2.mp3"))
        assertEquals(listOf("c", "b", "a"), orderSongs(songs, LibrarySort.Title).map { it.id })
        assertEquals(listOf("a", "c", "b"), orderSongs(songs, LibrarySort.Filename).map { it.id })
        val folders = listOf("song 10", "song 2", "Song 1", "song 1b").map { LocalFolder(it, "tree", "root", it, it, 1L) }
        assertEquals(listOf("Song 1", "song 1b", "song 2", "song 10"), orderFolders(folders, emptyList(), "root", LibrarySort.Title).map { it.id })
    }
    @Test fun naturalOrderComparesDigitRunsAsNumbers() {
        val sorted = listOf("track 100", "Track 9", "track 10", "track 010", "a", "track").sortedWith(NaturalOrder)
        assertEquals(listOf("a", "track", "Track 9", "track 10", "track 010", "track 100"), sorted)
    }
}
