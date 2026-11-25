package dev.radio.mobile

import dev.radio.mobile.music.SongSearch
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SongSearchTest {
    private data class S(val title: String, val artist: String = "", val file: String = "$title.mp3", val folder: String = "Music")
    private fun search(songs: List<S>, q: String) =
        SongSearch.filter(songs, q) { listOf(it.title, it.artist, it.file, it.folder) }.map { it.title }

    private val songs = listOf(S("Midnight City", "M83"), S("Strobe", "deadmau5"), S("Sandstorm", "Darude"), S("Starboy", "The Weeknd"))

    @Test fun blankQueryFindsNothing() = assertEquals(emptyList<String>(), search(songs, "  "))

    @Test fun exactMatchesComeBeforeTypoMatches() {
        // "strobe" is exact for Strobe; "starboy" is not within one edit, so only Strobe.
        assertEquals(listOf("Strobe"), search(songs, "strobe"))
        // "stobe" is a typo of strobe (1 edit) and of nothing else.
        assertEquals(listOf("Strobe"), search(songs, "stobe"))
        // Exact hit on one song outranks a typo hit on another.
        assertEquals(listOf("Sandstorm", "Sandstrom"), search(listOf(S("Sandstrom"), S("Sandstorm")), "sandstorm"))
    }

    @Test fun oneTypoInAWordStillMatches() {
        assertEquals(listOf("Midnight City"), search(songs, "midnigt city"))
        assertEquals(listOf("Sandstorm"), search(songs, "sandstrom"))  // transposition
        assertEquals(listOf("Strobe"), search(songs, "deadmau"))       // a prefix is still a plain match
    }

    @Test fun shortWordsMustMatchExactly() {
        assertTrue(search(songs, "m84").isEmpty())  // under 4 letters: no typo allowance
    }

    @Test fun everyWordMustMatch() {
        assertEquals(listOf("Midnight City"), search(songs, "midnigt m83"))
        assertTrue(search(songs, "midnight sandstorm").isEmpty())
    }

    @Test fun editDistanceCountsTranspositionAsOne() {
        assertEquals(1, SongSearch.distance("sandstrom", "sandstorm", 2))
        assertEquals(3, SongSearch.distance("kitten", "sitting", 2))  // capped at limit + 1
    }

    @Test fun recentSearchesAreDeduplicatedNewestFirstAndBounded() {
        var recent = emptyList<String>()
        listOf("a", "b", "A", "c", "d", "e", "f", "g", "h", "i").forEach { recent = SongSearch.remember(recent, it) }
        assertEquals(listOf("i", "h", "g", "f", "e", "d", "c", "A"), recent)
        assertEquals(recent, SongSearch.remember(recent, "  "))
    }

    @Test fun aPrebuiltIndexGivesTheSameResults() {
        val index = SongSearch.index(songs) { listOf(it.title, it.artist, it.file, it.folder) }
        for (q in listOf("mid", "strobe", "sandstrom", "the weeknd", "midnight sandstorm", "", "  ", "starbo"))
            assertEquals(q, search(songs, q), index.filter(q).map { it.title })
    }
}
