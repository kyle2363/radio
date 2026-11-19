package dev.radio.mobile.ui

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.layer.GraphicsLayer
import androidx.compose.ui.graphics.layer.drawLayer
import androidx.compose.ui.graphics.rememberGraphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.FileProvider
import dev.radio.mobile.data.*
import dev.radio.mobile.music.Artists
import dev.radio.mobile.recommendation.RadioRepository
import dev.radio.mobile.ui.theme.MoodColors
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.ZoneId
import kotlin.math.ln
import kotlin.math.roundToInt

/*
 * Checkpoint 8C fun stats. Everything here is worked out on the phone from the local journal (and, for the taste map,
 * the imported catalog). Share images are rendered locally and handed to the share sheet; nothing is uploaded.
 */

/** Each song's strongest personal feel (scale feels like Energy aren't categories, so they're left out). */
internal fun topFeelOf(feels: FeelsState): (String) -> String? {
    val usable = feels.active.filter { !it.feel.scale }.associateBy { it.feel.feelId }
    return { id -> feels.bySong[id]?.filter { it.member && it.feelId in usable }?.maxByOrNull { it.score }?.feelId }
}

private fun pct(value: Double?) = value?.let { "${(it * 100).roundToInt()}%" } ?: "–"

// ---------------------------------------------------------------- Listening style (extra measures)

@Composable
internal fun ListeningStyleCard(m: Measures, songs: Map<String, LocalSong>, onSong: (LocalSong) -> Unit) = StatsCard {
    CardTitle("Your listening style", "Discovery, coverage, variety and loyalty")
    Row(Modifier.fillMaxWidth()) {
        BigNumber(pct(m.discoveryRate), "first listens", Modifier.weight(1f))
        BigNumber(pct(m.libraryCoverage), "of library heard", Modifier.weight(1f))
    }
    Row(Modifier.fillMaxWidth()) {
        BigNumber(m.variety?.let { "$it" } ?: "–", "variety (0–100)", Modifier.weight(1f))
        BigNumber(pct(m.loyalty), "from your top 20", Modifier.weight(1f))
    }
    Text(when (val v = m.variety) {
        null -> "Variety shows how spread your listening is across artists."
        in 0..30 -> "Variety $v: you stick with a few artists."
        in 31..70 -> "Variety $v: a few favourites plus plenty of others."
        else -> "Variety $v: you spread your listening widely."
    }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    if (m.comebacks.isNotEmpty()) {
        Text("Comebacks · back after 90+ days away", style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 4.dp))
        m.comebacks.take(5).forEach { id ->
            val song = songs[id]
            Text(song?.title ?: "Song not in library", Modifier.fillMaxWidth().clickable(enabled = song != null) { song?.let(onSong) }.padding(vertical = 4.dp),
                maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
        }
        if (m.comebacks.size > 5) Text("and ${m.comebacks.size - 5} more", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ---------------------------------------------------------------- Milestones

@Composable
internal fun MilestonesCard(items: List<MilestoneProgress>) = StatsCard {
    CardTitle("Milestones", "${items.count { it.achieved }} of ${items.size} reached")
    items.forEach { item ->
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp).testTag("milestone-${item.milestone.id}"), verticalAlignment = Alignment.CenterVertically) {
            val done = item.achieved
            Box(Modifier.size(32.dp).background(if (done) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant, CircleShape), contentAlignment = Alignment.Center) {
                CompositionLocalProvider(LocalContentColor provides if (done) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant) {
                    RadioIcon(if (done) Symbol.Check else Symbol.Sparkle, modifier = Modifier.size(18.dp))
                }
            }
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(item.milestone.title, style = MaterialTheme.typography.bodyMedium, fontWeight = if (done) FontWeight.SemiBold else FontWeight.Normal)
                Text(item.note?.takeIf { it.isNotBlank() }?.let { "${item.milestone.detail} · $it" } ?: item.milestone.detail,
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (!done) LinearProgressIndicator(progress = { item.progress.toFloat() }, Modifier.fillMaxWidth().padding(top = 4.dp).height(4.dp))
            }
        }
    }
}

/** Milestones reached but not yet announced. Marks them announced, so each notice appears once, ever. */
internal fun unannouncedMilestones(context: Context, items: List<MilestoneProgress>): List<Milestone> {
    val prefs = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
    val shown = prefs.getStringSet("milestones_shown", emptySet()).orEmpty()
    val fresh = items.filter { it.achieved && it.milestone.id !in shown }.map { it.milestone }
    if (fresh.isNotEmpty()) prefs.edit().putStringSet("milestones_shown", shown + fresh.map { it.id }).apply()
    return fresh
}

// ---------------------------------------------------------------- Taste map

private object TasteMapCache {
    @Volatile private var memory: Pair<String, Map<String, Pair<Float, Float>>>? = null
    /** Reuses a projection made for this catalog revision and the same matched songs; otherwise makes and saves one. */
    fun get(context: Context, key: String, vectors: Map<String, FloatArray>): Map<String, Pair<Float, Float>> {
        memory?.takeIf { it.first == key && it.second.keys == vectors.keys }?.let { return it.second }
        val file = File(context.cacheDir, "taste_map/${key.hashCode().toUInt()}.tsv")
        val saved = runCatching {
            file.readLines().associate { line -> line.split('\t').let { it[0] to (it[1].toFloat() to it[2].toFloat()) } }
        }.getOrNull()?.takeIf { it.keys == vectors.keys }
        val points = saved ?: TasteMap.project(vectors).also { result ->
            runCatching { file.parentFile?.mkdirs(); file.writeText(result.entries.joinToString("\n") { "${it.key}\t${it.value.first}\t${it.value.second}" }) }
        }
        memory = key to points
        return points
    }
}

@Composable
internal fun TasteMapCard(radio: RadioRepository?, songs: List<LocalSong>, attempts: List<StatsAttempt>, feels: FeelsState,
    onPlay: (LocalSong) -> Unit, onRadio: (LocalSong) -> Unit) = StatsCard {
    CardTitle("Taste map", "Similar songs sit close together. Brighter dots are songs you play more.")
    val context = LocalContext.current.applicationContext
    val available = remember(songs) { songs.filter { it.available }.associateBy { it.id } }
    val points by produceState<Map<String, Pair<Float, Float>>?>(null, radio, available.keys) {
        value = withContext(Dispatchers.Default) {
            val (key, vectors) = runCatching { radio?.mapVectors() }.getOrNull() ?: return@withContext emptyMap()
            TasteMapCache.get(context, key, vectors)
        }
    }
    val map = points
    if (map == null) { Box(Modifier.fillMaxWidth().height(120.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }; return@StatsCard }
    if (map.isEmpty()) {
        Text("The map needs a catalog from your desktop. Pair on the Server screen, import the catalog and match your songs.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        return@StatsCard
    }
    val plays = remember(attempts) { attempts.groupingBy { it.localId }.eachCount() }
    val maxPlays = (plays.values.maxOrNull() ?: 0).coerceAtLeast(1)
    val feelOf = remember(feels) { topFeelOf(feels) }
    val feelColors = remember(feels) { feels.active.associate { it.feel.feelId to feelColor(it.feel.color) } }
    val accent = MaterialTheme.colorScheme.primary
    val ring = MaterialTheme.colorScheme.onSurface
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    val ids = remember(map) { map.keys.toList() }
    Canvas(Modifier.fillMaxWidth().aspectRatio(1f).background(MaterialTheme.colorScheme.surfaceContainerHighest, RoundedCornerShape(16.dp)).testTag("taste-map")
        .semantics { contentDescription = "Taste map of ${map.size} songs. Tap a dot to choose a song." }
        .pointerInput(map) {
            detectTapGestures { tap ->
                val pad = 12.dp.toPx(); val w = size.width - pad * 2; val h = size.height - pad * 2
                val best = ids.minByOrNull { id -> map.getValue(id).let { (x, y) -> (Offset(pad + x * w, pad + y * h) - tap).getDistanceSquared() } }
                selected = best?.takeIf { id -> map.getValue(id).let { (x, y) -> (Offset(pad + x * w, pad + y * h) - tap).getDistance() < 28.dp.toPx() } }
            }
        }) {
        val pad = 12.dp.toPx(); val w = size.width - pad * 2; val h = size.height - pad * 2
        val radius = (if (map.size > 1500) 2.5f else if (map.size > 400) 3.5f else 5f).dp.toPx()
        // Quiet songs first so played ones draw on top.
        ids.sortedBy { plays[it] ?: 0 }.forEach { id ->
            val (x, y) = map.getValue(id)
            val p = plays[id] ?: 0
            val glow = if (p == 0) .22f else .45f + .55f * (ln(1f + p) / ln(1f + maxPlays))
            val color = feelOf(id)?.let(feelColors::get) ?: accent
            drawCircle(color.copy(alpha = glow), radius * (if (p > 0) 1.25f else 1f), Offset(pad + x * w, pad + y * h))
        }
        selected?.let(map::get)?.let { (x, y) -> drawCircle(ring, radius * 2.6f, Offset(pad + x * w, pad + y * h), style = androidx.compose.ui.graphics.drawscope.Stroke(2.dp.toPx())) }
    }
    if (feelColors.isNotEmpty() && map.keys.any { feelOf(it) != null }) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            feels.active.filter { !it.feel.scale }.forEach { f ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(10.dp).background(feelColor(f.feel.color), CircleShape)); Text(f.feel.name, Modifier.padding(start = 4.dp), style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
    val song = selected?.let(available::get)
    if (song == null) Text("Tap a dot to play that song or start Radio there.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    else Column {
        Text(song.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text("${plays[song.id] ?: 0} plays${song.artist.takeIf { it.isNotBlank() }?.let { " · $it" } ?: ""}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 4.dp)) {
            FilledTonalButton(onClick = { onPlay(song) }, modifier = Modifier.testTag("taste-map-play")) { Text("Play") }
            OutlinedButton(onClick = { onRadio(song) }, modifier = Modifier.testTag("taste-map-radio")) { Text("Start Radio here") }
        }
    }
    val missing = remember(available, map) { available.values.filter { it.id !in map }.sortedBy { it.title.lowercase() } }
    if (missing.isNotEmpty()) {
        var open by rememberSaveable { mutableStateOf(false) }
        TextButton(onClick = { open = !open }) { Text("${missing.size} ${if (missing.size == 1) "song isn't" else "songs aren't"} on the map" + if (open) " · hide" else " · show") }
        if (open) {
            Text("Songs not matched to the desktop catalog have no position yet.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            missing.take(30).forEach { Text(it.title, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            if (missing.size > 30) Text("and ${missing.size - 30} more", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ---------------------------------------------------------------- Wrapped

@Composable
internal fun WrappedBanner(onOpen: () -> Unit) {
    val mood = MoodColors.all[2]
    Surface(onClick = onOpen, shape = RoundedCornerShape(20.dp), color = Color.Transparent, modifier = Modifier.fillMaxWidth().testTag("open-wrapped")) {
        Row(Modifier.background(Brush.horizontalGradient(listOf(Color(mood.field), Color(MoodColors.all[5].field)))).padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Wrapped", style = MaterialTheme.typography.titleMedium, color = Color.White)
                Text("Your week, month or year in music, card by card", style = MaterialTheme.typography.bodySmall, color = Color.White.copy(alpha = .8f))
            }
            CompositionLocalProvider(LocalContentColor provides Color.White) { RadioIcon(Symbol.Forward) }
        }
    }
}

private data class WrappedCard(val label: String, val value: String, val detail: String, val extra: List<String> = emptyList())

private fun wrappedCards(w: Wrapped, songs: Map<String, LocalSong>, feels: FeelsState): List<WrappedCard> {
    val span = when (w.period) { StatsPeriod.Week -> "week"; StatsPeriod.Month -> "month"; StatsPeriod.Year -> "year"; StatsPeriod.All -> "time" }
    fun title(id: String) = songs[id]?.title ?: "A song no longer in your library"
    val cards = mutableListOf(WrappedCard("Your ${span} in music", duration(w.listeningMs), "${w.plays} plays · ${w.distinctSongs} different songs"))
    w.topSong?.let { cards += WrappedCard("Top song", title(it.key), "${it.plays} plays · ${duration(it.listeningMs)}") }
    w.topArtist?.let { cards += WrappedCard("Top artist", it.key.ifBlank { "Unknown" }, "${it.plays} plays · ${duration(it.listeningMs)}") }
    w.couldntSkip?.let { cards += WrappedCard("The song you couldn't skip", title(it.key), "${it.plays} plays, not one skip") }
    w.biggestGrower?.let { (id, rise) -> cards += WrappedCard("Biggest grower", title(id), "You finish it ${(rise * 100).roundToInt()} points more often than last $span") }
    if (w.discoveries.isNotEmpty()) cards += WrappedCard("New discoveries", "${w.discoveries.size}", if (w.discoveries.size == 1) "song heard for the first time" else "songs heard for the first time",
        w.discoveries.take(3).map(::title))
    if (w.longestStreak > 0 || w.clockLabel != null) cards += WrappedCard("Your rhythm", w.clockLabel ?: "Listener",
        if (w.longestStreak > 0) "Longest streak: ${w.longestStreak} ${if (w.longestStreak == 1) "day" else "days"} in a row" else "")
    w.topFeel?.let { top -> feels.active.firstOrNull { it.feel.feelId == top.key }?.let { f -> cards += WrappedCard("Top feel", f.feel.name, "${duration(top.listeningMs)} of ${f.feel.name.lowercase()} music") } }
    val m = w.measures
    cards += WrappedCard("Your style", m.variety?.let { "Variety $it" } ?: "Variety –",
        "First listens ${pct(m.discoveryRate)} · Library heard ${pct(m.libraryCoverage)} · Top-20 loyalty ${pct(m.loyalty)}",
        if (m.comebacks.isNotEmpty()) listOf("${m.comebacks.size} ${if (m.comebacks.size == 1) "comeback" else "comebacks"}: ${title(m.comebacks.first())}") else emptyList())
    return cards
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun WrappedDialog(attempts: List<StatsAttempt>, songs: List<LocalSong>, feels: FeelsState, onDismiss: () -> Unit) {
    var period by rememberSaveable { mutableStateOf(StatsPeriod.Week) }
    val byId = remember(songs) { songs.associateBy { it.id } }
    val zone = remember { ZoneId.systemDefault() }
    val wrapped by produceState<Wrapped?>(null, attempts, period, byId, feels) {
        value = null
        val feelOf = topFeelOf(feels)
        value = withContext(Dispatchers.Default) { FunStats.wrapped(attempts, byId, period, System.currentTimeMillis(), zone, { Artists.primary(it.artist) }, feelOf) }
    }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize().testTag("wrapped"), color = Color(0xFF0B1220)) {
            Column(Modifier.fillMaxSize().systemBarsPadding().padding(vertical = 12.dp)) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf(StatsPeriod.Week, StatsPeriod.Month, StatsPeriod.Year).forEach { option ->
                            FilterChip(period == option, onClick = { period = option }, label = { Text(option.label) },
                                colors = FilterChipDefaults.filterChipColors(labelColor = Color.White, selectedContainerColor = Color.White, selectedLabelColor = Color(0xFF0B1220)),
                                modifier = Modifier.testTag("wrapped-${option.name}"))
                        }
                    }
                    IconButton(onClick = onDismiss) { CompositionLocalProvider(LocalContentColor provides Color.White) { RadioIcon(Symbol.Close, "Close") } }
                }
                val w = wrapped
                when {
                    w == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Color.White) }
                    w.empty -> Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                        Text("Nothing played ${period.label.lowercase()} yet. Come back after some music.", color = Color.White, textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium)
                    }
                    else -> {
                        val cards = remember(w, byId, feels) { wrappedCards(w, byId, feels) }
                        val pager = rememberPagerState { cards.size }
                        val layers = remember(cards) { HashMap<Int, GraphicsLayer>() }
                        HorizontalPager(pager, Modifier.weight(1f).fillMaxWidth(), contentPadding = PaddingValues(horizontal = 28.dp), pageSpacing = 12.dp) { index ->
                            val layer = rememberGraphicsLayer()
                            DisposableEffect(layer) { layers[index] = layer; onDispose { if (layers[index] === layer) layers.remove(index) } }
                            WrappedCardView(cards[index], index, cards.size, period, Modifier.fillMaxSize().padding(vertical = 16.dp)
                                .drawWithContent { layer.record { this@drawWithContent.drawContent() }; drawLayer(layer) })
                        }
                        Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                            Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                repeat(cards.size) { i -> Box(Modifier.size(if (i == pager.currentPage) 10.dp else 6.dp).background(Color.White.copy(alpha = if (i == pager.currentPage) 1f else .4f), CircleShape)) }
                            }
                            FilledTonalButton(onClick = {
                                val layer = layers[pager.currentPage] ?: return@FilledTonalButton
                                scope.launch { shareCard(context, layer) }
                            }, modifier = Modifier.testTag("wrapped-share")) { Text("Share card") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun WrappedCardView(card: WrappedCard, index: Int, total: Int, period: StatsPeriod, modifier: Modifier) {
    val mood = MoodColors.all[index % MoodColors.all.size]
    Box(modifier.background(Brush.verticalGradient(listOf(Color(mood.field), Color(0xFF0B1220))), RoundedCornerShape(28.dp)).padding(28.dp)) {
        Column(Modifier.align(Alignment.CenterStart), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(card.label.uppercase(), style = MaterialTheme.typography.labelLarge, color = Color(mood.accent), fontWeight = FontWeight.Bold)
            Text(card.value, style = if (card.value.length > 18) MaterialTheme.typography.headlineMedium else MaterialTheme.typography.displaySmall,
                color = Color.White, fontWeight = FontWeight.SemiBold, maxLines = 3, overflow = TextOverflow.Ellipsis)
            if (card.detail.isNotBlank()) Text(card.detail, style = MaterialTheme.typography.titleMedium, color = Color.White.copy(alpha = .85f))
            card.extra.forEach { Text("· $it", style = MaterialTheme.typography.bodyLarge, color = Color.White.copy(alpha = .8f), maxLines = 1, overflow = TextOverflow.Ellipsis) }
        }
        Text("Radio · ${period.label} · ${index + 1}/$total", Modifier.align(Alignment.BottomStart), style = MaterialTheme.typography.labelSmall, color = Color.White.copy(alpha = .6f))
    }
}

/** Renders the card to a PNG in the cache's shared folder and opens the share sheet. */
private suspend fun shareCard(context: Context, layer: GraphicsLayer) {
    val bitmap = layer.toImageBitmap().asAndroidBitmap()
    val file = withContext(Dispatchers.IO) {
        File(context.cacheDir, "shared").apply { mkdirs() }.resolve("wrapped.png").also { out ->
            out.outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        }
    }
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
    val send = Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(Intent.createChooser(send, "Share card"))
}
