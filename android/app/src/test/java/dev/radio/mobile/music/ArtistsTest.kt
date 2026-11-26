package dev.radio.mobile.music

import dev.radio.mobile.data.LocalSong
import org.junit.Assert.assertEquals
import org.junit.Test

class ArtistsTest {
    private fun song(id: String, file: String) = LocalSong(id, "root", "f", "content://$id", file, "Music", null, 0, null, null)

    @Test fun groupsSongsByArtistIgnoringCaseAndIndexesFeaturedGuests() {
        val songs = listOf(
            song("1", "Daft Punk - One More Time.mp3"),
            song("2", "daft punk - Around the World.mp3"),
            song("3", "Daft Punk feat. Pharrell Williams - Get Lucky.mp3"),
            song("4", "Daft Punk ft. Julian Casablancas - Instant Crush.mp3"),
            song("5", "Muse - Uprising.mp3"),
        )
        val index = Artists.catalog(songs).entries
        assertEquals(listOf("Daft Punk", "Julian Casablancas", "Muse", "Pharrell Williams"), index.map { it.name })
        assertEquals(listOf("Around the World", "Get Lucky", "Instant Crush", "One More Time"), index[0].songs.map { it.title })
        assertEquals(emptySet<String>(), index[1].leadSongIds)
        assertEquals(listOf("4"), index[1].songs.map { it.id })
    }

    @Test fun collabSplitsWhenOnlyOneSideHasSongsOfItsOwn() {
        val songs = listOf(song("1", "Alan Walker - Faded.mp3"), song("2", "Alan Walker & Ava Max - Alone, Pt. II.mp3"),
            song("3", "Alan Walker, Sabrina Carpenter & Farruko - On My Way.mp3"))
        val catalog = Artists.catalog(songs)
        assertEquals(listOf("Alan Walker", "Ava Max"), catalog.credits.getValue("2").lead)
        assertEquals(listOf("Alan Walker", "Sabrina Carpenter", "Farruko"), catalog.credits.getValue("3").lead)
        assertEquals(setOf("1", "2", "3"), catalog.byId.getValue("alan walker").leadSongIds)
        assertEquals(listOf("Alan Walker", "Ava Max", "Farruko", "Sabrina Carpenter"), catalog.entries.map { it.name })
    }

    @Test fun groupNamesThatAppearOnTheirOwnStayWholeInsideACollab() {
        val songs = listOf(song("1", "Above & Beyond - Sun & Moon.mp3"), song("2", "Seven Lions - Higher.mp3"),
            song("3", "Above & Beyond & Seven Lions - Song.mp3"))
        val catalog = Artists.catalog(songs)
        assertEquals(listOf("Above & Beyond", "Seven Lions"), catalog.credits.getValue("3").lead)
        assertEquals(listOf("Above & Beyond", "Seven Lions"), catalog.entries.map { it.name })
    }

    @Test fun severalFeaturedGuestsInTheTitleEachGetCredited() {
        val songs = listOf(song("1", "K-391 - Song (feat. Alan Walker, Julie Bergan & Seungri).mp3"))
        assertEquals(listOf("Alan Walker", "Julie Bergan", "Seungri"), Artists.catalog(songs).credits.getValue("1").guests)
        val oxford = listOf(song("2", "Fairlane - Solo.mp3"), song("3", "Fairlane, ROZES, & JT Roach - Song.mp3"))
        assertEquals(listOf("Fairlane", "ROZES", "JT Roach"), Artists.catalog(oxford).credits.getValue("3").lead)
    }

    @Test fun correctedGroupNameIsKeptWholeInOtherCredits() {
        val group = song("1", "Above & Beyond & Seven Lions - Song.mp3")
        val songs = listOf(group, song("2", "Seven Lions - Higher.mp3"), song("3", "Above & Beyond & Seven Lions - Other.mp3"),
            song("4", "Seven Lions & Above & Beyond - Again.mp3"))
        val catalog = Artists.catalog(songs, mapOf(Artists.correctionKey(group) to ArtistCredit(listOf("Above & Beyond", "Seven Lions"))))
        assertEquals(listOf("Seven Lions", "Above & Beyond"), catalog.credits.getValue("4").lead)
    }

    @Test fun sortsIgnoringTheAndPutsUnknownLast() {
        val songs = listOf(song("1", "The Killers - Mr. Brightside.mp3"), song("2", "Intro.mp3"), song("3", "Arcade Fire - Wake Up.mp3"), song("4", "Muse - Uprising.mp3"))
        assertEquals(listOf("Arcade Fire", "The Killers", "Muse", Artists.UNKNOWN), Artists.catalog(songs).entries.map { it.name })
    }

    @Test fun keepsDuosTogether() {
        val songs = listOf(song("1", "Simon & Garfunkel - The Boxer.mp3"))
        assertEquals(listOf("Simon & Garfunkel"), Artists.catalog(songs).entries.map { it.name })
    }

    @Test fun coLeadsWithSoloEvidenceAndExplicitCreditsAppearOnEachPage() {
        val songs = listOf(song("a", "Alice - Solo.mp3"), song("b", "Bob - Solo.mp3"),
            song("c", "Alice & Bob - Together (feat. Jo; Jules).mp3"), song("d", "Bob; Casey - Duet.mp3"))
        val catalog = Artists.catalog(songs)
        assertEquals(ArtistCredit(listOf("Alice", "Bob"), listOf("Jo", "Jules")), catalog.credits["c"])
        assertEquals(setOf("a", "c"), catalog.byId.getValue("alice").leadSongIds)
        assertEquals(setOf("b", "c", "d"), catalog.byId.getValue("bob").leadSongIds)
        assertEquals(listOf("c"), catalog.byId.getValue("jo").songs.map { it.id })
        assertEquals(setOf("d"), catalog.byId.getValue("casey").leadSongIds)
    }

    @Test fun correctingAnAmbiguousCreditUpdatesItsSongsAndDeduplicatesRepeatedNames() {
        val first = song("1", "Nova x June - Together.mp3")
        val second = song("2", "nova x june - Again.mp3")
        val songs = listOf(first, second)
        assertEquals(1, Artists.catalog(songs).entries.size)
        val catalog = Artists.catalog(songs, mapOf(Artists.correctionKey(first) to ArtistCredit(listOf("Nova", "June"), listOf("nova"))))
        assertEquals(listOf("June", "Nova"), catalog.entries.map { it.name })
        for (entry in catalog.entries) assertEquals(listOf("2", "1"), entry.songs.map { it.id })
    }

    @Test fun correctionCanKeepAKnownCompoundTogetherOrNameOneUncreditedTrack() {
        val band = song("1", "Simon & Garfunkel - The Boxer.mp3")
        val unnamed = song("2", "Intro.mp3")
        val songs = listOf(band, unnamed, song("3", "Outro.mp3"), song("4", "Simon - Solo.mp3"), song("5", "Garfunkel - Solo.mp3"))
        val catalog = Artists.catalog(songs, mapOf(
            Artists.correctionKey(band) to ArtistCredit(listOf("Simon & Garfunkel")),
            Artists.correctionKey(unnamed) to ArtistCredit(listOf("Nova")),
        ))
        assertEquals(listOf("1"), catalog.byId.getValue("simon & garfunkel").songs.map { it.id })
        assertEquals(listOf("2"), catalog.byId.getValue("nova").songs.map { it.id })
        assertEquals(listOf("3"), catalog.byId.getValue("").songs.map { it.id })
    }
}
