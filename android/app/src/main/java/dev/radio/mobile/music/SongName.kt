package dev.radio.mobile.music

import java.util.concurrent.ConcurrentHashMap

/** Display artist and title read from a file name. [artist] is empty when the name has no "Artist - Title" shape. */
data class SongName(val artist: String, val title: String)

/**
 * Library files are usually "Artist - Title.mp3", but not always, and some carry extra text after the title. Only the
 * first " - " separates artist from title; anything after it stays in the title (it may be a version such as
 * "Remastered"). Bracketed upload noise such as "(Official Video)" or "[Lyrics]" is dropped; brackets with any other
 * word, such as "(Live)" or "(Remix)", are kept. A leading track number ("01 - ", "01. ") is ignored.
 */
object SongNames {
    private val cache = ConcurrentHashMap<String, SongName>()
    private val extension = Regex("(?i)\\.mp3$")
    private val separator = Regex("\\s+[-–—]\\s+")
    private val trackNumber = Regex("^\\d{1,3}(?:\\s*[.)]\\s*|\\s+[-–—]\\s+)")
    private val bracket = Regex("\\s*[(\\[]([^)\\]]*)[)\\]]")
    private val noise = Regex("(?i)(?:official|music|lyrics?|video|audio|visuali[sz]er|hd|hq|4k|mv|full|version|\\s|&|/|-)+")

    fun of(fileName: String): SongName = cache.getOrPut(fileName) { parse(fileName) }

    internal fun parse(fileName: String): SongName {
        val stem = fileName.replace(extension, "").trim()
        val cleaned = bracket.replace(stem) { match -> if (noise.matches(match.groupValues[1].trim())) "" else match.value }.trim()
        // Only a number followed by another "Artist - Title" is a track number; "311 - Amber" keeps its artist.
        val body = cleaned.replaceFirst(trackNumber, "").trim().takeIf { it != cleaned && separator.containsMatchIn(it) } ?: cleaned
        val parts = body.split(separator, limit = 2)
        if (parts.size == 2 && parts[0].isNotBlank() && parts[1].isNotBlank()) return SongName(parts[0].trim(), parts[1].trim())
        return SongName("", body.ifEmpty { stem.ifEmpty { fileName } })
    }
}
