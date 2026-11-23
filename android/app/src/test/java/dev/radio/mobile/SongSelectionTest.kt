package dev.radio.mobile

import dev.radio.mobile.data.LocalFolder
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.ui.SongSelection
import dev.radio.mobile.ui.expandSelection
import dev.radio.mobile.ui.folderGroups
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SongSelectionTest {
    private val order = listOf("a", "b", "c", "d", "e", "f")

    @Test fun startsInactiveAndLongPressSelectsTheRow() {
        val empty = SongSelection()
        assertFalse(empty.active)
        val started = empty.begin("c")
        assertTrue(started.active)
        assertEquals(setOf("c"), started.ids)
    }

    @Test fun tapsToggleAndDeselectingTheLastSongEndsSelection() {
        val selection = SongSelection().begin("a").toggle("b")
        assertEquals(setOf("a", "b"), selection.ids)
        val fewer = selection.toggle("a").toggle("b")
        assertFalse(fewer.active)
    }

    @Test fun dragSweepsARangeFromTheLongPressedRowInEitherDirection() {
        val down = SongSelection().begin("b").sweep(order, "e")
        assertEquals(setOf("b", "c", "d", "e"), down.ids)
        // Moving back shrinks the sweep to the new range instead of keeping rows the finger left.
        val back = down.sweep(order, "c")
        assertEquals(setOf("b", "c"), back.ids)
        val up = SongSelection().begin("d").sweep(order, "a")
        assertEquals(setOf("a", "b", "c", "d"), up.ids)
    }

    @Test fun aSecondSweepKeepsWhatWasAlreadySelected() {
        val first = SongSelection().begin("a").toggle("f")
        val swept = first.begin("c").sweep(order, "d")
        assertEquals(setOf("a", "c", "d", "f"), swept.ids)
    }

    @Test fun sweepWithUnknownRowsChangesNothing() {
        val selection = SongSelection().begin("b")
        assertEquals(selection, selection.sweep(order, "zzz"))
        assertEquals(SongSelection(), SongSelection().sweep(order, "c"))
    }

    @Test fun selectAllAndOrderedFollowTheListNotTheTapOrder() {
        val tapped = SongSelection().begin("e").toggle("a").toggle("c")
        assertEquals(listOf("a", "c", "e"), tapped.ordered(order))
        assertEquals(order.toSet(), tapped.selectAll(order).ids)
        assertTrue(tapped.selectAll(order).allOf(order))
        assertFalse(tapped.allOf(order))
    }

    @Test fun retainDropsSongsThatLeftTheList() {
        val selection = SongSelection().begin("a").toggle("b").retain(setOf("b", "c"))
        assertEquals(setOf("b"), selection.ids)
        assertFalse(SongSelection().begin("a").retain(emptySet()).active)
    }

    private fun folder(id: String, parent: String?) = LocalFolder(id, "root", parent, id, id, null)
    private fun song(id: String, folder: String) = LocalSong(id = id, rootUri = "root", folderId = folder, uri = "content://$id", fileName = "$id.mp3",
        folderPath = folder, modifiedAt = 0, firstSeenAt = 0, byteSize = 1, durationMs = 1000)

    @Test fun aFolderStandsForEverySongUnderItInListOrder() {
        val folders = listOf(folder("root", null), folder("rock", "root"), folder("live", "rock"), folder("jazz", "root"))
        // Already sorted the way the library shows them.
        val songs = listOf(song("s1", "live"), song("s2", "jazz"), song("s3", "rock"), song("s4", "root"))
        val groups = folderGroups(folders, songs, listOf("rock", "jazz"))
        assertEquals(listOf("s1", "s3"), groups["folder:rock"]?.map { it.id })
        assertEquals(listOf("s2"), groups["folder:jazz"]?.map { it.id })
        assertEquals(2, groups.size)
    }

    @Test fun anEmptyFolderStandsForNoSongs() {
        val groups = folderGroups(listOf(folder("root", null), folder("empty", "root")), emptyList(), listOf("empty"))
        assertEquals(emptyList<LocalSong>(), groups["folder:empty"])
    }

    @Test fun expandingKeepsSelectionOrderAndDropsRepeats() {
        val a = song("a", "f"); val b = song("b", "f"); val c = song("c", "f")
        val songs = mapOf("a" to a, "b" to b)
        val groups = mapOf("folder:x" to listOf(c, a))
        // A folder and a song inside it are both chosen: each song once, where it first appears.
        assertEquals(listOf("c", "a", "b"), expandSelection(listOf("folder:x", "a", "b"), songs, groups).map { it.id })
        // Ids with no song (a playlist entry whose file is gone) are skipped.
        assertEquals(listOf("a"), expandSelection(listOf("gone", "a"), songs, groups).map { it.id })
    }
}
