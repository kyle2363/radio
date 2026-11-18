package dev.radio.mobile.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import dev.radio.mobile.music.Artists
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.*
import dev.radio.mobile.recommendation.StationOrigin
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/** Checkpoint 8B: everything is computed on the phone from the local journal; nothing is sent anywhere. */
@Composable
fun StatsScreen(journal: ListeningJournalRepository, songs: List<LocalSong>, onSong: (LocalSong) -> Unit, modifier: Modifier = Modifier,
    playlistName: (String) -> String? = { null }) {
    var period by rememberSaveable { mutableStateOf(StatsPeriod.Month) }
    val byId = remember(songs) { songs.associateBy { it.id } }
    val zone = remember { ZoneId.systemDefault() }
    // Re-read when the journal records something new, so a song that just finished shows up.
    val historyHead = journal.state.collectAsState().value.history.firstOrNull()?.eventId
    val attempts by produceState<List<StatsAttempt>?>(null, historyHead) { value = withContext(Dispatchers.IO) { journal.statsAttempts() } }
    val summary by produceState<StatsSummary?>(null, attempts, period, byId) {
        val rows = attempts ?: return@produceState
        value = withContext(Dispatchers.Default) { StatsCalculator.summarize(rows, byId, period, System.currentTimeMillis(), zone) { Artists.primary(it.artist) } }
    }
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).testTag("stats-scroll"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            StatsPeriod.entries.forEach { option ->
                FilterChip(period == option, onClick = { period = option }, label = { Text(option.label, maxLines = 1) }, modifier = Modifier.testTag("stats-period-${option.name}"))
            }
        }
        val s = summary
        if (s == null) { Box(Modifier.fillMaxWidth().height(200.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }; return@Column }
        if (s.totalPlays == 0) {
            StatsCard { Text("No listening yet", style = MaterialTheme.typography.titleMedium); Text("Play some music and your stats appear here. Everything stays on this phone.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            return@Column
        }
        OverviewCard(s)
        StatsCard {
            CardTitle("Listening calendar", "Past year · minutes per day")
            CalendarHeatmap(s.calendar, attempts.orEmpty(), byId, zone, onSong)
        }
        StatsCard {
            CardTitle("Clock of your day", s.clockLabel ?: "")
            DayClock(s.weekdayHours, s.weekendHours)
        }
        TopListsCard(s, byId, playlistName, onSong)
        StatsCard {
            CardTitle("Streaks", "Days with at least 30 seconds of listening")
            Row(Modifier.fillMaxWidth()) {
                BigNumber("${s.currentStreak}", if (s.currentStreak == 1) "day now" else "days now", Modifier.weight(1f))
                BigNumber("${s.longestStreak}", "longest", Modifier.weight(1f))
                BigNumber(s.longestSession?.let { duration(it.listeningMs) } ?: "–", "longest session", Modifier.weight(1f))
            }
        }
        SourcesCard(s)
        if (s.radio.isNotEmpty()) RadioReportCard(s)
        Spacer(Modifier.height(24.dp))
    }
}

@Composable private fun StatsCard(content: @Composable ColumnScope.() -> Unit) =
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceContainer) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp), content = content)
    }
@Composable private fun CardTitle(title: String, subtitle: String) {
    Column { Text(title, style = MaterialTheme.typography.titleMedium)
        if (subtitle.isNotBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
}
@Composable private fun BigNumber(value: String, label: String, modifier: Modifier = Modifier, change: String? = null) = Column(modifier) {
    Text(value, style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.primary, maxLines = 1)
    Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    if (change != null) Text(change, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

internal fun duration(ms: Long): String {
    val minutes = ms / 60_000
    return when { minutes >= 600 -> "${minutes / 60} h"; minutes >= 60 -> "${minutes / 60} h ${minutes % 60} m"; ms > 0 && minutes == 0L -> "<1 m"; else -> "$minutes m" }
}
private fun percent(value: Double?) = value?.let { "${(it * 100).roundToInt()}%" } ?: "–"
private fun change(now: Number, before: Number?): String? {
    if (before == null) return null
    val a = now.toDouble(); val b = before.toDouble()
    if (b == 0.0) return if (a > 0) "new" else null
    val delta = ((a - b) / b * 100).roundToInt()
    return if (delta >= 0) "▲ $delta%" else "▼ ${-delta}%"
}
private fun pointChange(now: Double?, before: Double?): String? {
    if (now == null || before == null) return null
    val delta = ((now - before) * 100).roundToInt()
    return if (delta >= 0) "▲ $delta pts" else "▼ ${-delta} pts"
}

@Composable private fun OverviewCard(s: StatsSummary) = StatsCard {
    val o = s.overview; val p = s.previous
    CardTitle(s.period.label, if (p != null) "Compared with the previous ${s.period.name.lowercase()}" else "")
    Row(Modifier.fillMaxWidth()) {
        BigNumber(duration(o.listeningMs), "listening", Modifier.weight(1f), change(o.listeningMs, p?.listeningMs))
        BigNumber("${o.plays}", "plays", Modifier.weight(1f), change(o.plays, p?.plays))
        BigNumber("${o.distinctSongs}", "songs", Modifier.weight(1f), change(o.distinctSongs, p?.distinctSongs))
    }
    Row(Modifier.fillMaxWidth()) {
        BigNumber(percent(o.fullListenRate), "heard in full", Modifier.weight(1f), pointChange(o.fullListenRate, p?.fullListenRate))
        BigNumber(percent(o.skipRate), "skipped", Modifier.weight(1f), pointChange(o.skipRate, p?.skipRate))
        Spacer(Modifier.weight(1f))
    }
}

@Composable private fun CalendarHeatmap(days: List<Pair<LocalDate, Long>>, attempts: List<StatsAttempt>, songs: Map<String, LocalSong>, zone: ZoneId, onSong: (LocalSong) -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val empty = MaterialTheme.colorScheme.surfaceVariant
    var selected by remember { mutableStateOf<LocalDate?>(null) }
    val first = days.first().first
    val offset = first.dayOfWeek.value - 1 // Monday = row 0
    val columns = (offset + days.size + 6) / 7
    val levels = remember(days) {
        val positive = days.map { it.second }.filter { it > 0 }.sorted()
        if (positive.isEmpty()) listOf(1L, 1L, 1L) else listOf(positive[positive.size / 4], positive[positive.size / 2], positive[positive.size * 3 / 4])
    }
    fun shade(minutes: Long): Color = when {
        minutes <= 0 -> empty
        minutes <= levels[0] -> accent.copy(alpha = 0.3f)
        minutes <= levels[1] -> accent.copy(alpha = 0.5f)
        minutes <= levels[2] -> accent.copy(alpha = 0.75f)
        else -> accent
    }
    val total = days.count { it.second > 0 }
    Canvas(Modifier.fillMaxWidth().aspectRatio(columns / 7f).semantics { contentDescription = "Listening calendar: $total days with music in the past year. Tap a day for details." }
        .pointerInput(days) {
            detectTapGestures { pos ->
                val cell = size.width / columns.toFloat()
                val index = (pos.x / cell).toInt() * 7 + (pos.y / cell).toInt() - offset
                selected = days.getOrNull(index)?.first?.takeIf { it != selected }
            }
        }) {
        val cell = size.width / columns
        val gap = cell * 0.15f
        days.forEachIndexed { i, (date, minutes) ->
            val slot = i + offset
            val x = (slot / 7) * cell; val y = (slot % 7) * cell
            drawRoundRect(if (date == selected) Color.White.copy(alpha = 0.9f) else shade(minutes), Offset(x + gap / 2, y + gap / 2), Size(cell - gap, cell - gap), CornerRadius(cell * 0.2f))
            if (date == selected) drawRoundRect(shade(minutes), Offset(x + gap, y + gap), Size(cell - gap * 2, cell - gap * 2), CornerRadius(cell * 0.2f))
        }
    }
    val day = selected
    if (day == null) Text("Tap a day to see what you played.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    else {
        val start = day.atStartOfDay(zone).toInstant().toEpochMilli(); val end = day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        val rows = attempts.filter { it.startedAt in start until end }
        Text("${day.format(DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM))} · ${duration(rows.sumOf { it.heard })} · ${rows.size} plays · ${StatsCalculator.sessions(rows).size} sessions",
            style = MaterialTheme.typography.labelLarge)
        rows.groupBy { it.localId }.entries.sortedByDescending { e -> e.value.sumOf { it.heard } }.take(5).forEach { (id, plays) ->
            val song = songs[id]
            Text("${song?.title ?: "Song not in library"} · ${plays.size}×", Modifier.fillMaxWidth().clickable(enabled = song != null) { song?.let(onSong) }.padding(vertical = 4.dp),
                maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable private fun DayClock(weekday: LongArray, weekend: LongArray) {
    var which by rememberSaveable { mutableIntStateOf(0) }
    val values = when (which) { 1 -> weekday; 2 -> weekend; else -> LongArray(24) { weekday[it] + weekend[it] } }
    val accent = MaterialTheme.colorScheme.primary
    val track = MaterialTheme.colorScheme.surfaceVariant
    val label = MaterialTheme.colorScheme.onSurfaceVariant
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        listOf("All days", "Weekdays", "Weekends").forEachIndexed { i, text -> FilterChip(which == i, onClick = { which = i }, label = { Text(text) }) }
    }
    val peak = (0 until 24).maxByOrNull { values[it] }?.takeIf { values[it] > 0 }
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(220.dp).semantics { contentDescription = peak?.let { "Most listening around ${hourLabel(it)}" } ?: "No listening in this period" }) {
            val max = values.max().coerceAtLeast(1)
            val center = Offset(size.width / 2, size.height / 2)
            val inner = size.minDimension * 0.18f; val outer = size.minDimension * 0.48f
            for (h in 0 until 24) {
                val angle = (h / 24.0 * 2 * PI - PI / 2).toFloat()
                val dir = Offset(cos(angle), sin(angle))
                drawLine(track, center + dir * inner, center + dir * outer, strokeWidth = size.minDimension * 0.035f, cap = StrokeCap.Round)
                val length = inner + (outer - inner) * (values[h].toFloat() / max)
                if (values[h] > 0) drawLine(accent, center + dir * inner, center + dir * length, strokeWidth = size.minDimension * 0.035f, cap = StrokeCap.Round)
            }
        }
        Text(peak?.let { "Peak\n${hourLabel(it)}" } ?: "", style = MaterialTheme.typography.labelMedium, color = label, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text("Midnight at the top, noon at the bottom", style = MaterialTheme.typography.bodySmall, color = label)
    }
}
private fun hourLabel(h: Int) = when (h) { 0 -> "midnight"; 12 -> "noon"; in 1..11 -> "$h am"; else -> "${h - 12} pm" }

@Composable private fun TopListsCard(s: StatsSummary, songs: Map<String, LocalSong>, playlistName: (String) -> String?, onSong: (LocalSong) -> Unit) = StatsCard {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    CardTitle("Top lists", "By plays")
    val tabs = listOf("Songs", "Artists", "Folders", "Playlists")
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { tabs.forEachIndexed { i, t -> FilterChip(tab == i, onClick = { tab = i }, label = { Text(t) }) } }
    val list = when (tab) { 0 -> s.topSongs; 1 -> s.topArtists; 2 -> s.topFolders; else -> s.topPlaylists }
    if (list.isEmpty()) Text(if (tab == 3) "Playlists you play from show up here (recorded since this update)." else "Nothing yet for this period.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    val max = list.maxOfOrNull { it.plays }?.coerceAtLeast(1) ?: 1
    list.forEachIndexed { i, item ->
        val song = if (tab == 0) songs[item.key] else null
        val name = when (tab) { 0 -> song?.title ?: "Song not in library"; 3 -> SmartList.of(item.key)?.title ?: playlistName(item.key) ?: "Deleted playlist"; else -> item.key.ifBlank { "Unknown" } }
        Column(Modifier.fillMaxWidth().clickable(enabled = song != null) { song?.let(onSong) }.padding(vertical = 4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${i + 1}", Modifier.width(28.dp), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                Text(name, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("${item.plays} · ${duration(item.listeningMs)}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Box(Modifier.padding(start = 28.dp, top = 2.dp).fillMaxWidth(item.plays.toFloat() / max).height(3.dp).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.5f), RoundedCornerShape(2.dp)))
        }
    }
}

private fun sourceLabel(kind: String) = when (kind) {
    "radio" -> "Radio"; "playlist" -> "Playlists"; "smart_list" -> "Smart lists"; "folder" -> "Folders"; "artist" -> "Artists"
    "all_songs" -> "All songs"; "search" -> "Search"; "queue_edit" -> "Queue edits"; "restore" -> "Resumed"; else -> kind
}

@Composable private fun SourcesCard(s: StatsSummary) = StatsCard {
    val since = s.contextSince?.let { Instant.ofEpochMilli(it).atZone(ZoneId.systemDefault()).toLocalDate().format(DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM)) }
    CardTitle("Where it comes from", since?.let { "Recorded since $it" } ?: "Recorded from now on")
    if (s.sources.isEmpty()) { Text("Plays from now on will show whether they came from Radio, a playlist, a folder or search.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant); return@StatsCard }
    val colors = MoodPalette
    val total = s.sources.sumOf { it.listeningMs }.coerceAtLeast(1)
    Row(Modifier.fillMaxWidth().height(14.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(7.dp))) {
        s.sources.forEachIndexed { i, src -> if (src.listeningMs > 0) Box(Modifier.weight(src.listeningMs.toFloat() / total).fillMaxHeight().background(colors[i % colors.size])) }
    }
    s.sources.forEachIndexed { i, src ->
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(10.dp).background(colors[i % colors.size], RoundedCornerShape(3.dp)))
            Text(sourceLabel(src.kind), Modifier.padding(start = 8.dp).weight(1f))
            Text("${(src.listeningMs * 100 / total)}% · ${src.plays} plays", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    val chosen = s.chosenPlays; val auto = s.autoPlays
    if (chosen + auto > 0) Text("You picked ${chosen * 100 / (chosen + auto)}% of songs yourself; the rest played next on their own.", style = MaterialTheme.typography.bodySmall)
}
private val MoodPalette = dev.radio.mobile.ui.theme.MoodColors.all.map { Color(it.accent) }

@Composable private fun RadioReportCard(s: StatsSummary) = StatsCard {
    CardTitle("Radio report", "How often you finish each kind of Radio pick")
    s.radio.forEach { r ->
        val label = runCatching { StationOrigin.valueOf(r.origin).label }.getOrDefault(r.origin)
        Column(Modifier.padding(vertical = 2.dp)) {
            Row { Text(label, Modifier.weight(1f)); Text("${r.full}/${r.plays} finished · ${r.skips} skipped", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            Row(Modifier.fillMaxWidth().height(6.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(3.dp))) {
                if (r.full > 0) Box(Modifier.weight(r.full.toFloat()).fillMaxHeight().background(MaterialTheme.colorScheme.primary, RoundedCornerShape(3.dp)))
                if (r.plays - r.full > 0) Spacer(Modifier.weight((r.plays - r.full).toFloat()))
            }
        }
    }
}

/** Song detail (8B): opened from any song's menu and from Now Playing. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SongStatsSheet(song: LocalSong, journal: ListeningJournalRepository, onDismiss: () -> Unit) {
    val zone = remember { ZoneId.systemDefault() }
    val stats by produceState<SongStats?>(null, song.id) {
        value = withContext(Dispatchers.IO) { StatsCalculator.song(journal.statsAttempts(), song.id, song.durationMs, System.currentTimeMillis(), zone) }
    }
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("song-stats")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 32.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(song.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (song.artist.isNotBlank()) Text(song.artist, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            val s = stats
            if (s == null) { CircularProgressIndicator(); return@Column }
            if (s.plays == 0) { Text("You haven't played this song on this phone yet.", style = MaterialTheme.typography.bodyMedium); return@Column }
            val fmt = DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM)
            fun date(at: Long?) = at?.let { Instant.ofEpochMilli(it).atZone(zone).toLocalDate().format(fmt) } ?: "–"
            Row(Modifier.fillMaxWidth()) {
                BigNumber("${s.plays}", "plays", Modifier.weight(1f)); BigNumber(duration(s.listeningMs), "listening", Modifier.weight(1f)); BigNumber(percent(s.fullListenRate), "heard in full", Modifier.weight(1f))
            }
            Text("First heard ${date(s.firstHeard)} · last heard ${date(s.lastHeard)}", style = MaterialTheme.typography.bodySmall)
            Text("Plays per week", style = MaterialTheme.typography.labelLarge)
            Sparkline(s.weekly)
            Text("Where you skip", style = MaterialTheme.typography.labelLarge)
            SkipMap(s.skipMap)
            if (s.sources.isNotEmpty()) Text("Comes from: " + s.sources.joinToString { "${sourceLabel(it.kind)} (${it.plays})" }, style = MaterialTheme.typography.bodySmall)
            (0 until 24).maxByOrNull { s.hours[it] }?.let { Text("Usually played around ${hourLabel(it)}", style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable private fun Sparkline(values: IntArray) {
    val accent = MaterialTheme.colorScheme.primary; val track = MaterialTheme.colorScheme.surfaceVariant
    Canvas(Modifier.fillMaxWidth().height(48.dp).semantics { contentDescription = "Plays per week for 12 weeks: ${values.joinToString()}" }) {
        val max = (values.maxOrNull() ?: 0).coerceAtLeast(1)
        val w = size.width / values.size
        values.forEachIndexed { i, v ->
            val h = size.height * v / max
            drawRoundRect(track, Offset(i * w + w * 0.15f, 0f), Size(w * 0.7f, size.height), CornerRadius(4f))
            if (v > 0) drawRoundRect(accent, Offset(i * w + w * 0.15f, size.height - h), Size(w * 0.7f, h), CornerRadius(4f))
        }
    }
}

@Composable private fun SkipMap(buckets: IntArray) {
    val accent = MaterialTheme.colorScheme.error; val track = MaterialTheme.colorScheme.surfaceVariant
    val total = buckets.sum()
    if (total == 0) { Text("You've never skipped it.", style = MaterialTheme.typography.bodySmall); return }
    Canvas(Modifier.fillMaxWidth().height(20.dp).semantics { contentDescription = "Skips across the song, start to end: ${buckets.joinToString()}" }) {
        val max = buckets.max().coerceAtLeast(1)
        val w = size.width / buckets.size
        drawRoundRect(track, Offset.Zero, size, CornerRadius(8f))
        buckets.forEachIndexed { i, v -> if (v > 0) drawRect(accent.copy(alpha = 0.25f + 0.75f * v / max), Offset(i * w, 0f), Size(w, size.height)) }
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text("Start", style = MaterialTheme.typography.labelSmall); Text("$total skips", style = MaterialTheme.typography.labelSmall); Text("End", style = MaterialTheme.typography.labelSmall)
    }
}
