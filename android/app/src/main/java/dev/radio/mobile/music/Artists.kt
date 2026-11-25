package dev.radio.mobile.music

import dev.radio.mobile.data.LocalSong

data class ArtistEntry(val name: String, val songs: List<LocalSong>)

/** Artists come from file names (see [SongNames]); there are no reliable tags to read. */
object Artists {
    const val UNKNOWN = "Unknown artist"
    private val guest = Regex("""\s+(?:feat\.?|ft\.?|featuring)\s+.*$""", RegexOption.IGNORE_CASE)

    /** The artist a song is filed under: guests ("feat. X") are dropped so a song sits with its main artist. */
    fun primary(artist: String): String = artist.replace(guest, "").trim()

    private fun sortKey(name: String) = name.lowercase().removePrefix("the ")

    fun index(songs: List<LocalSong>): List<ArtistEntry> {
        val groups = songs.groupBy { primary(it.artist).lowercase() }
        return groups.map { (key, members) ->
            // Show the spelling most of the songs use.
            val name = if (key.isEmpty()) UNKNOWN else members.groupingBy { primary(it.artist) }.eachCount().maxBy { it.value }.key
            ArtistEntry(name, members.sortedWith(compareBy(NaturalOrder) { it: LocalSong -> it.title }))
        }.sortedWith(compareBy<ArtistEntry> { it.name == UNKNOWN }.thenComparing({ sortKey(it.name) }, NaturalOrder))
    }
}
