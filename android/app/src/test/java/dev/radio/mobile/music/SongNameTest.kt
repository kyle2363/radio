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
    @Test fun hidesLabelAndUploadTagsForDisplay() {
        assertEquals(SongName("Elektronomia", "Sky High"), SongNames.parse("Elektronomia - Sky High [NCS Release].mp3"))
        assertEquals(SongName("Muzzy", "Endgame"), SongNames.parse("Muzzy - Endgame [Monstercat Release].mp3"))
        assertEquals(SongName("Artist", "Song"), SongNames.parse("Artist - Song [NCS10 Release].mp3"))
        assertEquals(SongName("Artist", "Song"), SongNames.parse("Artist - Song [Copyright Free].mp3"))
        assertEquals(SongName("Artist", "Song"), SongNames.parse("Artist - Song (Lyrics ⁄ AMV).mp3"))
        assertEquals(SongName("Artist", "Song"), SongNames.parse("Artist - Song [NCS Fanmade].mp3"))
        assertEquals(SongName("Electro Light ft. Kathryn MacLean", "The Edge"), SongNames.parse("Electro Light ft. Kathryn MacLean - The Edge [NCS Release].mp3"))
    }
    @Test fun hidesTrailingUnbracketedNoise() {
        assertEquals(SongName("Defqwop feat. Strix", "Heart Afire"), SongNames.parse("Defqwop - Heart Afire (feat. Strix) (LETRA) SUBTITULOS - ESPAÑOL.mp3"))
        assertEquals(SongName("Rival x Cadmium feat. Harley Bird", "Seasons [NIVIRO Remix]"), SongNames.parse("Rival x Cadmium - Seasons (feat. Harley Bird) [NIVIRO Remix]   NCS Release.mp3"))
    }
    @Test fun movesGuestsToTheArtistLine() {
        assertEquals(SongName("Alex Skrindo feat. Axol", "Get Up Again"), SongNames.parse("Alex Skrindo - Get Up Again (feat. Axol) [NCS Release].mp3"))
        assertEquals(SongName("Alan Walker feat. Au Ra and Tomine Harket", "Darkside"), SongNames.parse("Alan Walker - Darkside (feat. Au Ra and Tomine Harket).mp3"))
        assertEquals(SongName("Artist feat. Guest", "Song (Remix)"), SongNames.parse("Artist - Song (ft. Guest) (Remix).mp3"))
        assertEquals(SongName("Luna Artist feat. DNAKM", "Luna [Mendum Remix]"), SongNames.parse("Luna Artist - Luna (Feat. DNAKM) [Mendum Remix] [NCS Release].mp3"))
        // Without an artist part there is no line to move the guest to.
        assertEquals(SongName("", "Song (feat. Guest)"), SongNames.parse("Song (feat. Guest).mp3"))
    }
    @Test fun keepsVersionMarkers() {
        assertEquals(SongName("Artist", "Song [Edit]"), SongNames.parse("Artist - Song [Edit].mp3"))
        assertEquals(SongName("Artist", "Song (Live Performance)"), SongNames.parse("Artist - Song (Live Performance).mp3"))
        assertEquals(SongName("Artist", "Song (Acoustic)"), SongNames.parse("Artist - Song (Acoustic).mp3"))
    }
    @Test fun guestsStayWithTheMainArtist() = assertEquals("Alex Skrindo", Artists.primary("Alex Skrindo feat. Axol"))
}
