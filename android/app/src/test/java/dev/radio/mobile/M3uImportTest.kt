package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.data.M3u
import org.junit.Assert.*
import org.junit.Test

class M3uImportTest {
    private fun song(id: String, folder: String, file: String, available: Boolean = true) =
        LocalSong(id, "tree", "f-$folder", "content://fixture/$id", file, folder, 1, 1, 1, 60000, available)

    @Test fun parsesExtendedPlaylistWithBomCrLfCommentsAndUrls() {
        val text = "\uFEFF#EXTM3U\r\n#PLAYLIST:Road Trip\r\n\r\n#EXTINF:215,Daft Punk - One More Time\r\n" +
            "C:\\Users\\me\\Music\\Daft Punk\\One More Time.mp3\r\n# just a comment\r\n" +
            "file:///storage/emulated/0/Music/Air/La%20Femme%20d'Argent.mp3\r\nhttp://radio.example/stream\r\n  ../Rock/Live/Song.mp3  \r\n"
        val parsed = M3u.parse(text)
        assertEquals("Road Trip", parsed.name)
        assertEquals(listOf("C:\\Users\\me\\Music\\Daft Punk\\One More Time.mp3", "/storage/emulated/0/Music/Air/La Femme d'Argent.mp3",
            "http://radio.example/stream", "../Rock/Live/Song.mp3"), parsed.entries.map { it.path })
        assertEquals("Daft Punk - One More Time", parsed.entries[0].title)
        assertNull(parsed.entries[1].title)
    }

    @Test fun plainListWithoutHeaderHasNoName() {
        val parsed = M3u.parse("a.mp3\nb.mp3\n")
        assertNull(parsed.name)
        assertEquals(listOf("a.mp3", "b.mp3"), parsed.entries.map { it.path })
    }

    @Test fun decodesUtf8AndFallsBackToWindows1252() {
        assertEquals("Café.mp3", M3u.decode("Café.mp3".toByteArray(Charsets.UTF_8)))
        assertEquals("Café.mp3", M3u.decode("Café.mp3".toByteArray(charset("windows-1252"))))
    }

    @Test fun nameComesFromFileWhenPlaylistTagIsMissing() {
        assertEquals("Road Trip", M3u.playlistName(null, "Road Trip.m3u8"))
        assertEquals("Gym", M3u.playlistName("  Gym ", "other.m3u"))
        assertEquals("Imported playlist", M3u.playlistName(null, null))
        assertEquals(80, M3u.playlistName("x".repeat(200), null).length)
    }

    @Test fun matchesByFolderEvidenceCaseInsensitivelyAndKeepsOrder() {
        val songs = listOf(song("live", "Rock/Live", "Song.mp3"), song("pop", "Pop", "Song.mp3"),
            song("daft", "Electronic/Daft Punk", "One More Time.mp3"), song("air", "", "La Femme d'Argent.mp3"))
        val entries = M3u.parse("C:\\Users\\me\\Music\\daft punk\\ONE MORE TIME.MP3\n../Rock/Live/Song.mp3\n" +
            "/storage/emulated/0/Music/Air/La Femme d'Argent.mp3\n../Rock/Live/Song.mp3\n").entries
        val result = M3u.match(entries, songs)
        assertEquals(listOf("daft", "live", "air"), result.songIds)
        assertTrue(result.missing.isEmpty())
    }

    @Test fun sameFileNameWithoutFolderEvidenceIsAmbiguousAndReported() {
        val songs = listOf(song("a", "A", "Song.mp3"), song("b", "B", "Song.mp3"), song("c", "C", "Other.mp3"))
        val result = M3u.match(M3u.parse("/sdcard/Music/Song.mp3\nOther.mp3\nhttp://radio.example/stream\nMissing.mp3\n").entries, songs)
        assertEquals(listOf("c"), result.songIds)
        assertEquals(listOf("/sdcard/Music/Song.mp3", "http://radio.example/stream", "Missing.mp3"), result.missing.map { it.path })
    }

    @Test fun prefersAvailableCopyWhenFolderEvidenceTies() {
        val songs = listOf(song("gone", "A", "Song.mp3", available = false), song("here", "B", "Song.mp3"))
        assertEquals(listOf("here"), M3u.match(M3u.parse("Song.mp3").entries, songs).songIds)
    }

    @Test fun fallsBackToExtinfTitleWhenTheFileWasRenamed() {
        val songs = listOf(song("t", "Mix", "Artist - Title.mp3"))
        val result = M3u.match(M3u.parse("#EXTM3U\n#EXTINF:100,Artist - Title\nold/track01.mp3\n").entries, songs)
        assertEquals(listOf("t"), result.songIds)
    }
}
