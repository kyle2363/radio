package dev.radio.mobile.data

import dev.radio.mobile.portable.PlaylistSyncRules
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.Charset
import java.nio.charset.CodingErrorAction
import java.util.Locale

/**
 * Reads .m3u/.m3u8 playlists and matches their entries to songs in the granted Music folder.
 *
 * The paths inside a playlist usually point at another device (a PC drive, a different storage
 * root), so they are never opened: only the file name and the trailing folder names are compared
 * with the scanned library. Nothing here touches an audio file.
 */
object M3u {
    data class Entry(val path: String, val title: String? = null)
    data class Parsed(val name: String?, val entries: List<Entry>)
    /** [songIds] in playlist order without repeats; [missing] entries that matched nothing or more than one song. */
    data class Match(val songIds: List<String>, val missing: List<Entry>)

    /** UTF-8 (`.m3u8` and most modern `.m3u`), falling back to Windows-1252 for older players. */
    fun decode(bytes: ByteArray): String = try {
        Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
    } catch (_: CharacterCodingException) { String(bytes, Charset.forName("windows-1252")) }

    fun parse(text: String): Parsed {
        var name: String? = null
        var title: String? = null
        val entries = ArrayList<Entry>()
        for (raw in text.trimStart(BOM).lineSequence()) {
            val line = raw.trim()
            when {
                line.isEmpty() -> {}
                line.startsWith("#PLAYLIST:", ignoreCase = true) -> name = line.substringAfter(':').trim().ifEmpty { null }
                line.startsWith("#EXTINF:", ignoreCase = true) -> title = line.substringAfter(',', "").trim().ifEmpty { null }
                line.startsWith("#") -> {}
                else -> { entries += Entry(fileUriPath(line), title); title = null }
            }
        }
        return Parsed(name, entries)
    }

    /** The #PLAYLIST tag, else the file name without its extension, as a valid synced playlist name. */
    fun playlistName(tag: String?, displayName: String?): String {
        val clean = { value: String? -> value?.filter { it.code >= 32 }?.trim()?.take(PlaylistSyncRules.NAME_LIMIT)?.trim()?.ifEmpty { null } }
        return clean(tag) ?: clean(displayName?.replace(Regex("\\.m3u8?$", RegexOption.IGNORE_CASE), ""))
            ?: "Imported playlist"
    }

    fun match(entries: List<Entry>, songs: List<LocalSong>): Match {
        val byFile = songs.groupBy { it.fileName.lowercase(Locale.ROOT) }
        val byTitle = songs.groupBy { key(it.fileName.substringBeforeLast('.')) }
        val ids = LinkedHashSet<String>()
        val missing = ArrayList<Entry>()
        for (entry in entries) {
            val found = if (STREAM.containsMatchIn(entry.path)) null else best(entry, byFile, byTitle)
            if (found == null) missing += entry else ids += found.id
        }
        return Match(ids.toList(), missing)
    }

    private fun best(entry: Entry, byFile: Map<String, List<LocalSong>>, byTitle: Map<String, List<LocalSong>>): LocalSong? {
        val parts = segments(entry.path)
        val file = parts.lastOrNull() ?: return null
        val dirs = parts.dropLast(1)
        val candidates = byFile[file]
        if (candidates.isNullOrEmpty()) return entry.title?.let { byTitle[key(it)] }?.singleOrNull()
        // Count matching folder names from the deepest one up; the closest match wins.
        val scored = candidates.groupBy { song ->
            val folders = segments(song.folderPath)
            (0 until minOf(dirs.size, folders.size)).takeWhile { dirs[dirs.size - 1 - it] == folders[folders.size - 1 - it] }.count()
        }
        val top = scored.getValue(scored.keys.max())
        return top.singleOrNull() ?: top.filter { it.available }.singleOrNull()
    }

    private val BOM = 0xFEFF.toChar()
    private val STREAM = Regex("^[a-z][a-z0-9+.-]*://", RegexOption.IGNORE_CASE)
    private fun segments(path: String) = path.replace('\\', '/').split('/')
        .filter { it.isNotEmpty() && it != "." && it != ".." }.map { it.lowercase(Locale.ROOT) }
    private fun key(value: String) = value.lowercase(Locale.ROOT).split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

    /** `file:///a/b%20c.mp3` becomes `/a/b c.mp3`; any other line is kept as written. */
    private fun fileUriPath(line: String): String {
        if (!line.startsWith("file:", ignoreCase = true)) return line
        val path = "/" + line.substring(5).trimStart('/')
        val out = ByteArrayOutputStream()
        var i = 0
        while (i < path.length) {
            val hex = if (path[i] == '%' && i + 2 < path.length) path.substring(i + 1, i + 3).toIntOrNull(16) else null
            if (hex != null) { out.write(hex); i += 3 } else { out.write(path[i].toString().toByteArray(Charsets.UTF_8)); i++ }
        }
        return out.toString(Charsets.UTF_8.name())
    }
}
