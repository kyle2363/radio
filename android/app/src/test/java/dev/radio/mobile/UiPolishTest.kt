package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.music.isHiddenEntry
import dev.radio.mobile.ui.folderLabel
import dev.radio.mobile.ui.nowPlayingLine
import dev.radio.mobile.ui.songSubtitle
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UiPolishTest {
    private fun song(folderPath: String, artist: String = "Ada Quill", durationMs: Long? = 183_000) = LocalSong(
        id = "s1", rootUri = "root", folderId = "f1", uri = "content://x", fileName = "$artist - Small Hours.mp3", folderPath = folderPath,
        modifiedAt = 0, firstSeenAt = 0, byteSize = 1, durationMs = durationMs)

    @Test fun dotFoldersAndFilesAreHidden() {
        assertTrue(isHiddenEntry(".thumbnails"))
        assertTrue(isHiddenEntry(".trashed-1700000000-song.mp3"))
        assertFalse(isHiddenEntry("Road Trip"))
    }

    @Test fun rootSongsUseOneFolderLabelEverywhere() {
        val root = song(folderPath = "", artist = "")
        assertEquals("Top folder", folderLabel(root))
        assertTrue(songSubtitle(root).startsWith("Top folder"))
        assertEquals("Road Trip/Night Drive", folderLabel(song("Road Trip/Night Drive")))
    }

    @Test fun currentQueueRowKeepsTheArtist() {
        assertEquals("Ada Quill · Playing", nowPlayingLine(song("Misc"), "Playing"))
        assertEquals("Misc · Paused", nowPlayingLine(song("Misc", artist = ""), "Paused"))
    }
}
