package dev.radio.mobile.data

import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters

/**
 * One actual start with its outcome and (from checkpoint 8A) where it came from. Built by a single SQL join so stats
 * never load the raw journal; everything after that is pure and unit-tested.
 */
data class StatsAttempt(val attemptId: String, val localId: String, val startedAt: Long, val endKind: String?,
    val listenedMs: Long?, val completion: Double?, val terminationReason: String?,
    val sourceKind: String?, val sourceId: String?, val stationOrigin: String?, val trigger: String?) {
    /** Completion ≥ 0.9, a natural end, or a skip so late it counts as heard (checkpoint 8 definitions). */
    val fullListen: Boolean get() = (completion ?: 0.0) >= 0.9 || terminationReason == "natural_end" || terminationReason == "late_navigation"
    val skipped: Boolean get() = endKind == "song_skipped"
    val heard: Long get() = listenedMs ?: 0
}

enum class StatsPeriod(val label: String) { Week("This week"), Month("This month"), Year("This year"), All("All time") }

data class Overview(val listeningMs: Long, val plays: Int, val distinctSongs: Int, val fullListenRate: Double?, val skipRate: Double?)
data class Ranked(val key: String, val plays: Int, val listeningMs: Long)
data class SourceShare(val kind: String, val plays: Int, val listeningMs: Long)
data class RadioOrigin(val origin: String, val plays: Int, val full: Int, val skips: Int)
data class Session(val start: Long, val end: Long, val plays: Int, val listeningMs: Long)

data class StatsSummary(
    val period: StatsPeriod, val overview: Overview, val previous: Overview?,
    /** Minutes heard per local day for the past year, oldest first, ending today. */
    val calendar: List<Pair<LocalDate, Long>>,
    /** Listening per local hour (0–23). */
    val weekdayHours: LongArray, val weekendHours: LongArray, val clockLabel: String?,
    val topSongs: List<Ranked>, val topArtists: List<Ranked>, val topFolders: List<Ranked>, val topPlaylists: List<Ranked>,
    val currentStreak: Int, val longestStreak: Int, val longestSession: Session?,
    val sources: List<SourceShare>, val chosenPlays: Int, val autoPlays: Int, val contextSince: Long?,
    val radio: List<RadioOrigin>,
    val totalPlays: Int,
)

/** Per-song history for the song detail page. */
data class SongStats(val plays: Int, val listeningMs: Long, val fullListenRate: Double?, val firstHeard: Long?, val lastHeard: Long?,
    /** Plays per week for the last 12 weeks, oldest first. */
    val weekly: IntArray,
    /** Ten buckets over the song's length: how many skips happened in each tenth. */
    val skipMap: IntArray, val sources: List<SourceShare>, val hours: LongArray)

object StatsCalculator {
    const val SESSION_GAP_MS = 30 * 60_000L
    const val STREAK_MIN_MS = 30_000L
    private val CHOSEN = setOf("tap", "media_button", "widget", "next", "previous")

    fun range(period: StatsPeriod, today: LocalDate, zone: ZoneId): Pair<Long, Long>? {
        val start = when (period) {
            StatsPeriod.Week -> today.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY))
            StatsPeriod.Month -> today.withDayOfMonth(1)
            StatsPeriod.Year -> today.withDayOfYear(1)
            StatsPeriod.All -> return null
        }
        val end = when (period) { StatsPeriod.Week -> start.plusWeeks(1); StatsPeriod.Month -> start.plusMonths(1); else -> start.plusYears(1) }
        return start.atStartOfDay(zone).toInstant().toEpochMilli() to end.atStartOfDay(zone).toInstant().toEpochMilli()
    }
    private fun previousRange(period: StatsPeriod, today: LocalDate, zone: ZoneId): Pair<Long, Long>? {
        val (start, _) = range(period, today, zone) ?: return null
        val startDay = Instant.ofEpochMilli(start).atZone(zone).toLocalDate()
        val prev = when (period) { StatsPeriod.Week -> startDay.minusWeeks(1); StatsPeriod.Month -> startDay.minusMonths(1); else -> startDay.minusYears(1) }
        return prev.atStartOfDay(zone).toInstant().toEpochMilli() to start
    }

    fun overview(rows: List<StatsAttempt>): Overview {
        val finished = rows.filter { it.endKind != null }
        return Overview(rows.sumOf { it.heard }, rows.size, rows.mapTo(HashSet()) { it.localId }.size,
            finished.takeIf { it.isNotEmpty() }?.let { f -> f.count { it.fullListen }.toDouble() / f.size },
            finished.takeIf { it.isNotEmpty() }?.let { f -> f.count { it.skipped }.toDouble() / f.size })
    }

    /** Consecutive local days with at least one play heard for 30 s or more. Today without a play yet doesn't break the streak. */
    fun streaks(days: Set<LocalDate>, today: LocalDate): Pair<Int, Int> {
        var longest = 0; var run = 0; var last: LocalDate? = null
        for (day in days.sorted()) {
            run = if (last != null && ChronoUnit.DAYS.between(last, day) == 1L) run + 1 else 1
            longest = maxOf(longest, run); last = day
        }
        var current = 0
        var cursor = if (today in days) today else today.minusDays(1)
        while (cursor in days) { current++; cursor = cursor.minusDays(1) }
        return current to longest
    }

    fun sessions(rows: List<StatsAttempt>): List<Session> {
        val out = mutableListOf<Session>()
        var start = 0L; var end = 0L; var plays = 0; var heard = 0L
        for (row in rows.sortedBy { it.startedAt }) {
            val rowEnd = row.startedAt + row.heard
            if (plays > 0 && row.startedAt - end < SESSION_GAP_MS) { end = maxOf(end, rowEnd); plays++; heard += row.heard }
            else { if (plays > 0) out += Session(start, end, plays, heard); start = row.startedAt; end = rowEnd; plays = 1; heard = row.heard }
        }
        if (plays > 0) out += Session(start, end, plays, heard)
        return out
    }

    fun clockLabel(weekday: LongArray, weekend: LongArray): String? {
        val total = LongArray(24) { weekday[it] + weekend[it] }
        val sum = total.sum(); if (sum <= 0) return null
        fun share(hours: IntRange) = hours.sumOf { total[it % 24] }.toDouble() / sum
        val morning = share(5..9); val night = share(21..27); val commute = (share(7..9) + share(16..18))
        return when {
            night >= 0.4 -> "Night owl"
            morning >= 0.35 -> "Early bird"
            commute >= 0.45 -> "Commuter"
            else -> "All-day"
        }
    }

    private fun rank(rows: List<StatsAttempt>, key: (StatsAttempt) -> String?, limit: Int = 10) =
        rows.groupBy(key).filterKeys { !it.isNullOrBlank() }
            .map { (k, v) -> Ranked(k!!, v.size, v.sumOf { it.heard }) }
            .sortedWith(compareByDescending<Ranked> { it.plays }.thenByDescending { it.listeningMs }.thenBy { it.key }).take(limit)

    fun summarize(all: List<StatsAttempt>, songs: Map<String, LocalSong>, period: StatsPeriod, now: Long, zone: ZoneId,
        artistOf: (LocalSong) -> String = { dev.radio.mobile.music.Artists.primary(it.artist) }): StatsSummary {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val range = range(period, today, zone)
        val rows = if (range == null) all else all.filter { it.startedAt >= range.first && it.startedAt < range.second }
        val previous = previousRange(period, today, zone)?.let { (a, b) -> overview(all.filter { it.startedAt in a until b }) }
        fun day(at: Long) = Instant.ofEpochMilli(at).atZone(zone).toLocalDate()

        val perDay = HashMap<LocalDate, Long>()
        val streakDays = HashSet<LocalDate>()
        for (row in all) {
            val d = day(row.startedAt)
            perDay[d] = (perDay[d] ?: 0) + row.heard
            if (row.heard >= STREAK_MIN_MS) streakDays += d
        }
        val calendar = (364 downTo 0).map { back -> today.minusDays(back.toLong()).let { it to (perDay[it] ?: 0L) / 60_000 } }
        val weekday = LongArray(24); val weekend = LongArray(24)
        for (row in rows) {
            val t = Instant.ofEpochMilli(row.startedAt).atZone(zone)
            val target = if (t.dayOfWeek == DayOfWeek.SATURDAY || t.dayOfWeek == DayOfWeek.SUNDAY) weekend else weekday
            target[t.hour] += row.heard
        }
        val (current, longest) = streaks(streakDays, today)
        val contextual = rows.filter { it.sourceKind != null }
        val sources = contextual.groupBy { it.sourceKind!! }.map { (k, v) -> SourceShare(k, v.size, v.sumOf { it.heard }) }.sortedByDescending { it.listeningMs }
        val radio = rows.filter { it.sourceKind == "radio" && it.endKind != null }.groupBy { it.stationOrigin ?: "Unknown" }
            .map { (k, v) -> RadioOrigin(k, v.size, v.count { it.fullListen }, v.count { it.skipped }) }.sortedByDescending { it.plays }
        return StatsSummary(period, overview(rows), previous, calendar, weekday, weekend, clockLabel(weekday, weekend),
            topSongs = rank(rows, { it.localId }),
            topArtists = rank(rows, { songs[it.localId]?.let(artistOf) }),
            topFolders = rank(rows, { songs[it.localId]?.folderPath?.takeIf { p -> p.isNotBlank() } }),
            topPlaylists = rank(rows.filter { it.sourceKind == "playlist" || it.sourceKind == "smart_list" }, { it.sourceId }),
            currentStreak = current, longestStreak = longest, longestSession = sessions(rows).maxByOrNull { it.listeningMs },
            sources = sources, chosenPlays = contextual.count { it.trigger in CHOSEN }, autoPlays = contextual.count { it.trigger !in CHOSEN },
            contextSince = all.filter { it.sourceKind != null }.minOfOrNull { it.startedAt }, radio = radio, totalPlays = all.size)
    }

    fun song(all: List<StatsAttempt>, localId: String, durationMs: Long?, now: Long, zone: ZoneId): SongStats {
        val rows = all.filter { it.localId == localId }
        val finished = rows.filter { it.endKind != null }
        val weekStart = Instant.ofEpochMilli(now).atZone(zone).toLocalDate().with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY))
        val weekly = IntArray(12)
        for (row in rows) {
            val back = ChronoUnit.WEEKS.between(Instant.ofEpochMilli(row.startedAt).atZone(zone).toLocalDate()
                .with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)), weekStart).toInt()
            if (back in 0..11) weekly[11 - back]++
        }
        val skipMap = IntArray(10)
        rows.filter { it.skipped }.forEach { row ->
            val fraction = row.completion ?: durationMs?.takeIf { it > 0 }?.let { row.heard.toDouble() / it } ?: return@forEach
            skipMap[(fraction * 10).toInt().coerceIn(0, 9)]++
        }
        val hours = LongArray(24)
        rows.forEach { hours[Instant.ofEpochMilli(it.startedAt).atZone(zone).hour] += maxOf(it.heard, 1) }
        return SongStats(rows.size, rows.sumOf { it.heard },
            finished.takeIf { it.isNotEmpty() }?.let { f -> f.count { it.fullListen }.toDouble() / f.size },
            rows.minOfOrNull { it.startedAt }, rows.maxOfOrNull { it.startedAt }, weekly, skipMap,
            rows.filter { it.sourceKind != null }.groupBy { it.sourceKind!! }.map { (k, v) -> SourceShare(k, v.size, v.sumOf { it.heard }) }.sortedByDescending { it.plays },
            hours)
    }
}
