package dev.radio.mobile.music

import dev.radio.mobile.data.LocalSong

data class ArtistCredit(val lead: List<String>, val guests: List<String> = emptyList()) {
    val names get() = lead + guests
}

data class ArtistEntry(val id: String, val name: String, val songs: List<LocalSong>, val leadSongIds: Set<String>)

data class ArtistCatalog(val entries: List<ArtistEntry>, val credits: Map<String, ArtistCredit>) {
    val byId = entries.associateBy { it.id }
}

/** Artist browsing uses the library's filename credits and optional local corrections. */
object Artists {
    const val UNKNOWN = "Unknown artist"
    private val featuring = Regex("""\s+(?:feat\.?|ft\.?|featuring)\s+""", RegexOption.IGNORE_CASE)
    /** "&", "x", "+", "and" and list commas, including the Oxford ", &" / ", and". */
    private val ambiguous = Regex("""\s+(?:&|x|\+|and)\s+|,\s+(?:(?:&|and)\s+)?""", RegexOption.IGNORE_CASE)
    private val whitespace = Regex("""\s+""")

    /** The lead credit still used by Radio and listening stats. */
    fun primary(artist: String): String = artist.split(featuring, limit = 2).first().trim()

    fun key(name: String) = name.trim().replace(whitespace, " ").lowercase()

    private fun sortKey(name: String) = key(name).removePrefix("the ")

    fun section(entry: ArtistEntry) = if (entry.id.isEmpty()) "#" else
        sortKey(entry.name).firstOrNull()?.takeIf(Char::isLetter)?.uppercaseChar()?.toString() ?: "#"

    fun names(raw: String) = raw.split(';').map(String::trim).filter(String::isNotEmpty).distinctBy(::key)

    fun correctionKey(song: LocalSong) = key(song.artist).ifEmpty { "track:${song.id}" }

    /** The parts of "A, B & C" and the separators between them. */
    private fun tokens(name: String) = ambiguous.split(name).map(String::trim) to ambiguous.findAll(name).map { it.value }.toList()

    /**
     * Reads a credit left to right, taking the longest known group or solo name at each step; parts nobody is known
     * by become names of their own. Without any known part a lead credit stays whole ("Simon & Garfunkel").
     */
    private fun segment(name: String, solo: Set<String>, groups: Set<String>, splitUnknown: Boolean): List<String> {
        val (parts, separators) = tokens(name)
        if (parts.size < 2) return listOf(name)
        fun span(from: Int, to: Int) = buildString {
            append(parts[from])
            for (i in from until to) append(separators[i]).append(parts[i + 1])
        }.trim()
        val out = mutableListOf<String>()
        var known = false
        var i = 0
        while (i < parts.size) {
            val group = (parts.lastIndex downTo i + 1).firstOrNull { key(span(i, it)) in groups }
            when {
                group != null -> { out += span(i, group); known = true; i = group + 1 }
                else -> { if (key(parts[i]) in solo) known = true; out += parts[i]; i++ }
            }
        }
        return if (known || splitUnknown) out.filter(String::isNotEmpty) else listOf(name)
    }

    fun catalog(songs: List<LocalSong>, corrections: Map<String, ArtistCredit> = emptyMap()): ArtistCatalog {
        val rawParts = songs.map { it.artist }.distinct().associateWith { it.split(featuring) }
        val corrected = corrections.values.flatMap { it.names }
        // Solo names: credited somewhere without "&", "x", "and" or a comma (corrections count too).
        val solo = (rawParts.values.flatten().flatMap(::names) + corrected)
            .filterNot(ambiguous::containsMatchIn).mapTo(mutableSetOf(), ::key)
        // Group names stay whole inside bigger credits: a whole lead credit such as "Above & Beyond" none of whose
        // parts has songs of its own, or any name a correction spelled with a separator.
        val groupNames = (rawParts.values.flatMap { names(it.first()) }.filter { name ->
            ambiguous.containsMatchIn(name) && tokens(name).first.none { key(it) in solo }
        } + corrected.filter(ambiguous::containsMatchIn)).mapTo(mutableSetOf(), ::key)
        /** Lead credits split once any part is a known name; guest lists ("feat. A, B & C") always split. */
        fun split(credit: String, guests: Boolean = false): List<String> = names(credit).flatMap { segment(it, solo, groupNames, guests) }
        val parsed = rawParts.mapValues { (_, parts) -> ArtistCredit(split(parts.first()), parts.drop(1).flatMap { split(it, guests = true) }) }
        val credits = songs.associate { song ->
            val credit = corrections[correctionKey(song)] ?: parsed.getValue(song.artist)
            val lead = credit.lead.distinctBy(::key)
            val leadKeys = lead.mapTo(mutableSetOf(), ::key)
            song.id to ArtistCredit(lead, credit.guests.distinctBy(::key).filterNot { key(it) in leadKeys })
        }
        data class Membership(val name: String, val song: LocalSong, val lead: Boolean)
        val groups = songs.flatMap { song ->
            val credit = credits.getValue(song.id)
            credit.lead.ifEmpty { if (credit.guests.isEmpty()) listOf("") else emptyList() }.map { Membership(it, song, true) } +
                credit.guests.map { Membership(it, song, false) }
        }.groupBy { key(it.name) }
        val entries = groups.map { (id, members) ->
            val name = if (id.isEmpty()) UNKNOWN else members.groupingBy { it.name }.eachCount().maxBy { it.value }.key
            val tracks = members.map { it.song }.sortedWith(compareBy(NaturalOrder) { it.title })
            ArtistEntry(id, name, tracks, members.filter { it.lead }.mapTo(mutableSetOf()) { it.song.id })
        }.sortedWith(compareBy<ArtistEntry> { it.id.isEmpty() }.thenComparing({ sortKey(it.name) }, NaturalOrder))
        return ArtistCatalog(entries, credits)
    }
}
