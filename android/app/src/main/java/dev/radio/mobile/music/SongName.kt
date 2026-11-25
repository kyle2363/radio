package dev.radio.mobile.music

import java.util.concurrent.ConcurrentHashMap

/** Display artist and title read from a file name. [artist] is empty when the name has no "Artist - Title" shape. */
data class SongName(val artist: String, val title: String)

/**
 * Library files are usually "Artist - Title.mp3", but not always, and some carry extra text after the title. Only the
 * first " - " separates artist from title; anything after it stays in the title (it may be a version such as
 * "Remastered"). A leading track number ("01 - ", "01. ") is ignored.
 *
 * Display only: files are never renamed, and catalog matching reads the file name itself, not these labels.
 * - Bracketed upload noise such as "(Official Video)" or "[Lyrics]" and label tags such as "[NCS Release]",
 *   "[Monstercat Release]" or "[Copyright Free]" are dropped; brackets with any other word, such as "(Live)",
 *   "(Remix)" or "[Edit]", are kept. Trailing unbracketed tags ("SUBTITULOS - ESPAÑOL", "NCS Release") go too.
 * - A guest in the title ("Song (feat. Guest)") moves to the artist line: "Artist feat. Guest". [Artists.primary]
 *   still files the song under the main artist.
 */
object SongNames {
    private val cache = ConcurrentHashMap<String, SongName>()
    private val extension = Regex("(?i)\\.mp3$")
    private val separator = Regex("\\s+[-–—]\\s+")
    private val trackNumber = Regex("^\\d{1,3}(?:\\s*[.)]\\s*|\\s+[-–—]\\s+)")
    private val bracket = Regex("\\s*[(\\[]([^)\\]]*)[)\\]]")
    private val noise = Regex("(?iu)(?:official|music|lyrics?|letra|subtitulos?|español|espanol|video|audio|visuali[sz]er|performance|hd|hq|4k|mv|amv|full|version|" +
        "ncs\\d*|monstercat|proximity|fanmade|release|copyright(?:ed)?|free|\\s|&|/|⁄|-)+")
    /** Any "<label> Release" tag, such as "[Lowly Release]". */
    private val labelRelease = Regex("(?i).*\\brelease")
    private val subtitleWord = "(?:subtitulos?|subtitulado|español|espanol|letra|lyrics?)"
    private val trailingSubtitles = Regex("(?iu)(?:\\s+-)?\\s+$subtitleWord(?:(?:\\s+-)?\\s+$subtitleWord)*\\s*$")
    private val trailingLabel = Regex("(?i)\\s+(?:ncs\\d*|ncs fanmade|monstercat|proximity)\\s+release\\s*$")
    private val bracketGuest = Regex("(?i)\\s*[(\\[](?:feat\\.?|ft\\.?|featuring)\\s+([^)\\]]+)[)\\]]")
    private val trailingGuest = Regex("(?i)\\s+(?:feat\\.|ft\\.|featuring)\\s+(.+)$")
    private val spaces = Regex("\\s{2,}")

    fun of(fileName: String): SongName = cache.getOrPut(fileName) { parse(fileName) }

    internal fun parse(fileName: String): SongName {
        val stem = fileName.replace(extension, "").trim()
        val cleaned = bracket.replace(stem) { match ->
            val inside = match.groupValues[1].trim()
            if (noise.matches(inside) || labelRelease.matches(inside)) "" else match.value
        }.trim()
        // Only a number followed by another "Artist - Title" is a track number; "311 - Amber" keeps its artist.
        val body = cleaned.replaceFirst(trackNumber, "").trim().takeIf { it != cleaned && separator.containsMatchIn(it) } ?: cleaned
        val parts = body.split(separator, limit = 2)
        if (parts.size == 2 && parts[0].isNotBlank() && parts[1].isNotBlank()) {
            var title = parts[1].replace(trailingSubtitles, "").replace(trailingLabel, "").trim()
            val guests = mutableListOf<String>()
            title = bracketGuest.replace(title) { guests += it.groupValues[1].trim(); "" }
            if (guests.isEmpty()) trailingGuest.find(title)?.let { guests += it.groupValues[1].trim(); title = title.substring(0, it.range.first) }
            title = title.replace(spaces, " ").trim()
            if (title.isEmpty()) return SongName(parts[0].trim(), parts[1].trim())
            val artist = parts[0].trim()
            return SongName(if (guests.isEmpty()) artist else "$artist feat. ${guests.joinToString(", ")}", title)
        }
        return SongName("", body.replace(spaces, " ").ifEmpty { stem.ifEmpty { fileName } })
    }
}
