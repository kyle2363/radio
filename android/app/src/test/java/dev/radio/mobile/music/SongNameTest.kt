package dev.radio.mobile.music

import org.junit.Assert.assertEquals
import org.junit.Test

class SongNameTest {
    @Test fun splitsArtistAndTitle() = assertEquals(SongName("Daft Punk", "One More Time"), SongNames.parse("Daft Punk - One More Time.mp3"))
    @Test fun keepsExtraTextAfterTheTitle() = assertEquals(SongName("Artist", "Song - Remastered 2011"), SongNames.parse("Artist - Song - Remastered 2011.mp3"))
    @Test fun dropsUploadNoiseButKeepsVersions() {
        assertEquals(SongName("Artist", "Song"), SongNames.parse("Artist - Song (Official Music Video).mp3"))
        assertEquals(SongName("Artist", "Song"), SongNames.parse("Artist - Song [Lyrics].MP3"))
        assertEquals(SongName("Artist", "Song (Live 2020)"), SongNames.parse("Artist - Song (Live 2020).mp3"))
        assertEquals(SongName("Artist", "Song (Remix)"), SongNames.parse("Artist - Song (Remix) [HD].mp3"))
    }
    @Test fun ignoresLeadingTrackNumbers() {
        assertEquals(SongName("Artist", "Song"), SongNames.parse("01 - Artist - Song.mp3"))
        assertEquals(SongName("Artist", "Song"), SongNames.parse("07. Artist - Song.mp3"))
        assertEquals(SongName("311", "Amber"), SongNames.parse("311 - Amber.mp3"))
        assertEquals(SongName("", "01. Intro"), SongNames.parse("01. Intro.mp3"))
    }
    @Test fun namesWithoutASeparatorAreAllTitle() {
        assertEquals(SongName("", "Amber Lights"), SongNames.parse("Amber Lights.mp3"))
        assertEquals(SongName("", "Spider-Man Theme"), SongNames.parse("Spider-Man Theme.mp3"))
        assertEquals(SongName("", "Radio 12"), SongNames.parse("Radio 12.mp3"))
    }
    @Test fun neverReturnsAnEmptyTitle() {
        assertEquals(SongName("", "(Official Video)"), SongNames.parse("(Official Video).mp3"))
        assertEquals(SongName("", "12"), SongNames.parse("12.mp3"))
    }
}
