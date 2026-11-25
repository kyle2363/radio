package dev.radio.mobile.data

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.ln
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Checkpoint 8C extra measures for a period.
 * - [discoveryRate]: share of plays that were a song's first-ever play
 * - [libraryCoverage]: share of the available library ever played (all time)
 * - [variety]: normalised artist entropy, 0 (one artist) to 100 (perfectly even)
 * - [loyalty]: share of listening time from your top 20 songs
 * - [comebacks]: songs back in rotation after 90+ days away, most recent first
 */
data class Measures(val discoveryRate: Double?, val libraryCoverage: Double?, val variety: Int?, val loyalty: Double?, val comebacks: List<String>)

/** One period's Wrapped cards. Song keys are local ids; [topFeel] is a feel id. */
data class Wrapped(
    val period: StatsPeriod, val listeningMs: Long, val plays: Int, val distinctSongs: Int,
    val topSong: Ranked?, val topArtist: Ranked?, val longestStreak: Int,
    /** Most-played song with at least three plays and no skips in the period. */
    val couldntSkip: Ranked?,
    /** Song whose full-listen rate rose most against the previous period, with the rise (0..1). */
    val biggestGrower: Pair<String, Double>?,
    /** Songs first heard in the period, most played first. */
    val discoveries: List<String>,
    val clockLabel: String?, val topFeel: Ranked?, val measures: Measures,
    /** Up to [FunStats.WRAPPED_TOP] most-played songs, for the top-song card's list. */
    val topSongs: List<Ranked> = emptyList(),
    /** Listening per local hour (0–23) in the period, for the listening-clock picture. */
    val hours: LongArray = LongArray(24),
    val currentStreak: Int = 0,
) { val empty get() = plays == 0 }

enum class Milestone(val id: String, val title: String, val detail: String) {
    Plays1000("plays_1000", "1,000 plays", "A thousand songs started on this phone."),
    Hours100("hours_100", "100 hours", "A hundred hours of music."),
    Streak30("streak_30", "30-day streak", "Music every day for a month."),
    FolderComplete("folder_complete", "Folder complete", "Every song in a folder heard."),
    Song100("song_100", "100 plays of one song", "One song played a hundred times."),
}

/** [progress] is 0..1; [note] names the folder or song behind it, when there is one. */
data class MilestoneProgress(val milestone: Milestone, val achieved: Boolean, val progress: Double, val note: String? = null)

object FunStats {
    const val COMEBACK_DAYS = 90L
    const val WRAPPED_TOP = 5
    const val LOYALTY_TOP = 20
    const val MIN_FOLDER_SONGS = 3
    private const val DAY_MS = 86_400_000L

    private fun rowsIn(all: List<StatsAttempt>, range: Pair<Long, Long>?) =
        if (range == null) all else all.filter { it.startedAt >= range.first && it.startedAt < range.second }

    /** The attempt id of each song's first-ever play. */
    private fun firstPlays(all: List<StatsAttempt>): Set<String> =
        all.groupBy { it.localId }.values.mapTo(HashSet()) { plays -> plays.minWith(compareBy<StatsAttempt> { it.startedAt }.thenBy { it.attemptId }).attemptId }

    fun measures(all: List<StatsAttempt>, period: StatsPeriod, now: Long, zone: ZoneId, available: Set<String>, artistOf: (String) -> String?): Measures {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val range = StatsCalculator.range(period, today, zone)
        val rows = rowsIn(all, range)
        val firsts = firstPlays(all)
        val discovery = rows.takeIf { it.isNotEmpty() }?.let { r -> r.count { it.attemptId in firsts }.toDouble() / r.size }
        val played = all.mapTo(HashSet()) { it.localId }
        val coverage = available.takeIf { it.isNotEmpty() }?.let { lib -> lib.count { it in played }.toDouble() / lib.size }
        val byArtist = rows.groupingBy { artistOf(it.localId)?.ifBlank { null } ?: "?" }.eachCount()
        val variety = when {
            rows.isEmpty() -> null
            byArtist.size <= 1 -> 0
            else -> {
                val total = rows.size.toDouble()
                val entropy = -byArtist.values.sumOf { c -> (c / total).let { p -> p * ln(p) } }
                (entropy / ln(byArtist.size.toDouble()) * 100).roundToInt().coerceIn(0, 100)
            }
        }
        val heard = rows.groupBy { it.localId }.mapValues { (_, v) -> v.sumOf { it.heard } }
        val total = heard.values.sum()
        val loyalty = total.takeIf { it > 0 }?.let { heard.values.sortedDescending().take(LOYALTY_TOP).sum().toDouble() / it }
        val comebacks = HashMap<String, Long>()
        for ((id, plays) in all.groupBy { it.localId }) {
            val starts = plays.map { it.startedAt }.sorted()
            for (i in 1 until starts.size) {
                val b = starts[i]
                val inRange = range == null || (b >= range.first && b < range.second)
                if (inRange && b - starts[i - 1] >= COMEBACK_DAYS * DAY_MS) comebacks[id] = maxOf(comebacks[id] ?: 0L, b)
            }
        }
        return Measures(discovery, coverage, variety, loyalty, comebacks.entries.sortedByDescending { it.value }.map { it.key })
    }

    private fun fullRate(rows: List<StatsAttempt>): Double? = rows.filter { it.endKind != null }.takeIf { it.size >= 2 }?.let { f -> f.count { it.fullListen }.toDouble() / f.size }

    fun wrapped(all: List<StatsAttempt>, songs: Map<String, LocalSong>, period: StatsPeriod, now: Long, zone: ZoneId,
        artistOf: (LocalSong) -> String = { dev.radio.mobile.music.Artists.primary(it.artist) }, feelOf: (String) -> String? = { null }): Wrapped {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val range = StatsCalculator.range(period, today, zone)
        val rows = rowsIn(all, range)
        val summary = StatsCalculator.summarize(all, songs, period, now, zone, artistOf)
        val o = summary.overview
        val couldntSkip = rows.groupBy { it.localId }.filter { (_, v) -> v.size >= 3 && v.none { it.skipped } }
            .map { (k, v) -> Ranked(k, v.size, v.sumOf { it.heard }) }
            .maxWithOrNull(compareBy<Ranked> { it.plays }.thenBy { it.listeningMs }.thenByDescending { it.key })
        val previous = range?.let { (start, _) ->
            val startDay = Instant.ofEpochMilli(start).atZone(zone).toLocalDate()
            val prev = when (period) { StatsPeriod.Week -> startDay.minusWeeks(1); StatsPeriod.Month -> startDay.minusMonths(1); else -> startDay.minusYears(1) }
            all.filter { it.startedAt >= prev.atStartOfDay(zone).toInstant().toEpochMilli() && it.startedAt < start }
        }
        val grower = previous?.let { before ->
            val earlier = before.groupBy { it.localId }
            rows.groupBy { it.localId }.mapNotNull { (id, v) ->
                val now = fullRate(v) ?: return@mapNotNull null
                val then = earlier[id]?.let(::fullRate) ?: return@mapNotNull null
                (id to now - then).takeIf { it.second > 0 }
            }.maxWithOrNull(compareBy<Pair<String, Double>> { it.second }.thenByDescending { it.first })
        }
        val firsts = firstPlays(all)
        val playsInRange = rows.groupingBy { it.localId }.eachCount()
        val discoveries = rows.filter { it.attemptId in firsts }.map { it.localId }.distinct()
            .sortedWith(compareByDescending<String> { playsInRange[it] ?: 0 }.thenBy { it })
        val days = rows.filter { it.heard >= StatsCalculator.STREAK_MIN_MS }.mapTo(HashSet()) { Instant.ofEpochMilli(it.startedAt).atZone(zone).toLocalDate() }
        val longest = StatsCalculator.streaks(days, today).second
        val topFeel = rows.mapNotNull { row -> feelOf(row.localId)?.let { it to row } }.groupBy({ it.first }, { it.second })
            .map { (k, v) -> Ranked(k, v.size, v.sumOf { it.heard }) }
            .maxWithOrNull(compareBy<Ranked> { it.listeningMs }.thenBy { it.plays }.thenByDescending { it.key })
        return Wrapped(period, o.listeningMs, o.plays, o.distinctSongs, summary.topSongs.firstOrNull(), summary.topArtists.firstOrNull(), longest,
            couldntSkip, grower, discoveries, summary.clockLabel, topFeel,
            measures(all, period, now, zone, songs.values.filter { it.available }.mapTo(HashSet()) { it.id }) { id -> songs[id]?.let(artistOf) },
            topSongs = summary.topSongs.take(WRAPPED_TOP),
            hours = LongArray(24) { summary.weekdayHours[it] + summary.weekendHours[it] },
            currentStreak = summary.currentStreak)
    }

    fun milestones(all: List<StatsAttempt>, library: List<LocalSong>, now: Long, zone: ZoneId): List<MilestoneProgress> {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val hours = all.sumOf { it.heard } / 3_600_000.0
        val days = all.filter { it.heard >= StatsCalculator.STREAK_MIN_MS }.mapTo(HashSet<LocalDate>()) { Instant.ofEpochMilli(it.startedAt).atZone(zone).toLocalDate() }
        val longest = StatsCalculator.streaks(days, today).second
        val played = all.mapTo(HashSet()) { it.localId }
        val folders = library.filter { it.available && it.folderPath.isNotBlank() }.groupBy { it.folderPath }.filterValues { it.size >= MIN_FOLDER_SONGS }
            .map { (path, songs) -> path to songs.count { it.id in played }.toDouble() / songs.size }
        val bestFolder = folders.maxWithOrNull(compareBy<Pair<String, Double>> { it.second }.thenBy { it.first })
        val perSong = all.groupingBy { it.localId }.eachCount().maxWithOrNull(compareBy<Map.Entry<String, Int>> { it.value }.thenByDescending { it.key })
        val songTitle = perSong?.key?.let { id -> library.firstOrNull { it.id == id }?.title }
        fun of(m: Milestone, value: Double, goal: Double, note: String? = null) = MilestoneProgress(m, value >= goal, (value / goal).coerceIn(0.0, 1.0), note)
        return listOf(
            of(Milestone.Plays1000, all.size.toDouble(), 1000.0),
            of(Milestone.Hours100, hours, 100.0),
            of(Milestone.Streak30, longest.toDouble(), 30.0),
            of(Milestone.FolderComplete, bestFolder?.second ?: 0.0, 1.0, bestFolder?.first?.substringAfterLast('/')),
            of(Milestone.Song100, (perSong?.value ?: 0).toDouble(), 100.0, songTitle),
        )
    }
}

/**
 * Checkpoint 8C taste map: a 2-D principal-component projection of catalog embeddings, scaled into the unit square.
 * Power iteration with deflation keeps it dependency-free; a fixed start vector keeps the layout stable.
 */
object TasteMap {
    fun project(vectors: Map<String, FloatArray>, iterations: Int = 40): Map<String, Pair<Float, Float>> {
        if (vectors.isEmpty()) return emptyMap()
        if (vectors.size == 1) return mapOf(vectors.keys.first() to (.5f to .5f))
        val ids = vectors.keys.sorted()
        val dim = vectors.values.minOf { it.size }
        val mean = DoubleArray(dim)
        ids.forEach { id -> val v = vectors.getValue(id); for (d in 0 until dim) mean[d] += v[d].toDouble() }
        for (d in 0 until dim) mean[d] /= ids.size
        val rows = ids.map { id -> val v = vectors.getValue(id); DoubleArray(dim) { v[it] - mean[it] } }
        val components = ArrayList<DoubleArray>()
        repeat(2) { c ->
            var axis = DoubleArray(dim) { d -> 1.0 + ((d * 7 + c * 13) % 5) * 0.1 }
            normalize(axis)
            // Deflate: the second axis looks only at what the first doesn't explain.
            val working = if (components.isEmpty()) rows else rows.map { row -> val p = dot(row, components[0]); DoubleArray(dim) { row[it] - p * components[0][it] } }
            repeat(iterations) {
                val next = DoubleArray(dim)
                for (row in working) { val dot = dot(row, axis); for (d in 0 until dim) next[d] += dot * row[d] }
                for (prev in components) { val p = dot(next, prev); for (d in 0 until dim) next[d] -= p * prev[d] }
                if (normalize(next) == 0.0) return@repeat
                axis = next
            }
            components += axis
        }
        val xs = rows.map { dot(it, components[0]) }; val ys = rows.map { dot(it, components[1]) }
        fun scale(values: List<Double>): List<Float> {
            val lo = values.min(); val hi = values.max()
            return if (hi - lo < 1e-12) values.map { .5f } else values.map { ((it - lo) / (hi - lo)).toFloat() }
        }
        val sx = scale(xs); val sy = scale(ys)
        return ids.indices.associate { ids[it] to (sx[it] to sy[it]) }
    }
    private fun dot(a: DoubleArray, b: DoubleArray): Double { var s = 0.0; for (i in a.indices) s += a[i] * b[i]; return s }
    private fun normalize(v: DoubleArray): Double { val n = sqrt(dot(v, v)); if (n > 0) for (i in v.indices) v[i] /= n; return n }
}
