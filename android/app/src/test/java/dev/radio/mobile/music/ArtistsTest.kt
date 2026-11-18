package dev.radio.mobile.music

import dev.radio.mobile.data.LocalSong
import org.junit.Assert.assertEquals
import org.junit.Test

class ArtistsTest {
    private fun song(id: String, file: String) = LocalSong(id, "root", "f", "content://$id", file, "Music", null, 0, null, null)

    @Test fun groupsSongsByArtistIgnoringCaseAndFeaturedGuests() {
        val songs = listOf(
            song("1", "Daft Punk - One More Time.mp3"),
            song("2", "daft punk - Around the World.mp3"),
            song("3", "Daft Punk feat. Pharrell Williams - Get Lucky.mp3"),
            song("4", "Daft Punk ft. Julian Casablancas - Instant Crush.mp3"),
            song("5", "Muse - Uprising.mp3"),
        )
        val index = Artists.index(songs)
        assertEquals(listOf("Daft Punk", "Muse"), index.map { it.name })
        assertEquals(listOf("Around the World", "Get Lucky", "Instant Crush", "One More Time"), index[0].songs.map { it.title })
    }

    @Test fun sortsIgnoringTheAndPutsUnknownLast() {
        val songs = listOf(song("1", "The Killers - Mr. Brightside.mp3"), song("2", "Intro.mp3"), song("3", "Arcade Fire - Wake Up.mp3"), song("4", "Muse - Uprising.mp3"))
        assertEquals(listOf("Arcade Fire", "The Killers", "Muse", Artists.UNKNOWN), Artists.index(songs).map { it.name })
    }

    @Test fun keepsDuosTogether() {
        val songs = listOf(song("1", "Simon & Garfunkel - The Boxer.mp3"))
        assertEquals(listOf("Simon & Garfunkel"), Artists.index(songs).map { it.name })
    }
}
