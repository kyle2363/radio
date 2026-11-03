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
}
