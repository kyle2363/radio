package dev.radio.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.graphics.toColorInt
import dev.radio.mobile.data.FeelCatalog
import dev.radio.mobile.ui.theme.RadioShapes
import dev.radio.mobile.data.FeelRepository
import dev.radio.mobile.data.FeelSummary
import dev.radio.mobile.data.FeelsState
import dev.radio.mobile.data.LocalFeel
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.PreviewController
import dev.radio.mobile.playback.PreviewState
import dev.radio.mobile.portable.FeelRules
import dev.radio.mobile.recommendation.FeelSprint
import dev.radio.mobile.recommendation.SprintInput
import dev.radio.mobile.recommendation.ThisOrThat
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.random.Random

/**
 * Where the Feels page is: the list, one feel, or a sprint / this-or-that over one feel. Hoisted so the app's Back
 * can step out: this-or-that is a sprint-level page ([sprint] and [pairs] true), so `copy(sprint = false)` returns
 * to the feel and the leftover [pairs] flag is ignored there.
 */
data class FeelsNav(val feelId: String? = null, val sprint: Boolean = false, val pairs: Boolean = false)

internal fun feelColor(hex: String): Color = runCatching { Color(hex.toColorInt()) }.getOrDefault(Color.Gray)

/** Colours offered when recolouring or adding a feel: the starter tints, Energy first. */
internal val feelPalette: List<String> = (listOf(FeelCatalog.energy.color) + FeelCatalog.starters.map { it.color }).distinct()

private fun isEnergy(id: String) = id == FeelCatalog.ENERGY_FEEL_ID

/** Nearest Hot/Warm/Mellow/Cold bucket for a score, for showing your current Energy rating. */
internal fun energyBucket(score: Double?): String? = score?.let { s -> FeelCatalog.ENERGY_BUCKETS.minBy { abs(it.second - s) }.first }

@Composable
private fun Swatch(hex: String, size: Int = 18, selected: Boolean = false, onClick: (() -> Unit)? = null) {
    Box(Modifier.size(size.dp).background(feelColor(hex), CircleShape)
        .then(if (selected) Modifier.border(3.dp, MaterialTheme.colorScheme.onSurface, CircleShape) else Modifier)
        .then(if (onClick != null) Modifier.clickable(onClick = onClick).semantics { contentDescription = "Colour $hex"; if (selected) stateDescription = "Selected" } else Modifier))
}

/**
 * Feels page (checkpoint 9B): first-run starter picker, then the list, one feel, or a sprint.
 * Writes report failures through [onMessage]; nothing here plays music except preview clips.
 */
@Composable
fun FeelsScreen(feels: FeelRepository, state: FeelsState, songs: List<LocalSong>, nav: FeelsNav, onNav: (FeelsNav) -> Unit,
    preview: PreviewController?, sessions: () -> List<List<String>>, onMessage: (String) -> Unit, modifier: Modifier = Modifier,
    /** "Start radio from this" for a feel with songs in it; Energy is not a station. */
    onRadio: ((LocalFeel) -> Unit)? = null) {
    val scope = rememberCoroutineScope()
    fun write(block: suspend () -> Result<*>) { scope.launch { block().exceptionOrNull()?.let { onMessage(it.message ?: "Couldn't save that.") } } }
    val open = nav.feelId?.let { id -> state.feels.firstOrNull { it.feel.feelId == id } }
    when {
        !state.loaded -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        state.needsSetup -> FeelSetup(modifier) { keys -> write { feels.setUp(keys) } }
        open != null && nav.sprint && nav.pairs -> ThisOrThatScreen(feels, state, open.feel, songs, preview, sessions, onMessage,
            onDone = { onNav(FeelsNav(open.feel.feelId)) }, modifier = modifier)
        open != null && nav.sprint -> FeelSprintScreen(feels, state, open.feel, songs, preview, sessions, onMessage, onDone = { onNav(FeelsNav(open.feel.feelId)) }, modifier = modifier)
        open != null -> FeelDetail(open, state, songs, onSprint = { onNav(FeelsNav(open.feel.feelId, sprint = true)) },
            onPairs = { onNav(FeelsNav(open.feel.feelId, sprint = true, pairs = true)) },
            onEdit = { name, color, archived -> write { feels.edit(open.feel.feelId, name, color, archived) } },
            onRemove = { song -> write { feels.tag(open.feel.feelId, song, false) } }, modifier = modifier,
            onRadio = onRadio?.takeIf { canStartRadio(open) }?.let { start -> { start(open.feel) } })
        else -> FeelList(state, onOpen = { onNav(FeelsNav(it)) }, onCreate = { name, color -> write { feels.create(name, color) } },
            onRestore = { write { feels.edit(it, archived = false) } }, modifier = modifier, onRadio = onRadio)
    }
}

@Composable
private fun FeelSetup(modifier: Modifier, onDone: (Set<String>) -> Unit) {
    var ticked by remember { mutableStateOf(FeelCatalog.starters.map { it.key }.toSet()) }
    Column(modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).testTag("feel-setup"), contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)) {
            item {
                Text("Pick your feels", style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
                Text("Feels describe how a song feels to you, not how it sounds. Keep the ones you want; you can rename, recolour or add more later. Energy is always included.",
                    Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            items(FeelCatalog.starters, key = { it.key }) { starter ->
                val on = starter.key in ticked
                Row(Modifier.fillMaxWidth().clickable { ticked = if (on) ticked - starter.key else ticked + starter.key }.heightIn(min = 56.dp)
                    .padding(vertical = 4.dp).testTag("starter-${starter.key}"), verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(on, onCheckedChange = null)
                    Swatch(starter.color, 20)
                    Column(Modifier.weight(1f).padding(start = 12.dp)) {
                        Text(starter.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
                        Text(starter.meaning, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        Button(onClick = { onDone(ticked) }, Modifier.fillMaxWidth().padding(16.dp).testTag("feel-setup-done"), shape = RadioShapes.Soft) {
            Text(if (ticked.isEmpty()) "Just Energy for now" else "Keep ${ticked.size} feels")
        }
    }
}

@Composable
private fun FeelList(state: FeelsState, onOpen: (String) -> Unit, onCreate: (String, String) -> Unit, onRestore: (String) -> Unit, modifier: Modifier,
    onRadio: ((LocalFeel) -> Unit)? = null) {
    var adding by remember { mutableStateOf(false) }
    val archived = state.feels.filter { it.feel.archived }
    // Energy first: it is built in and sits above your tags.
    val active = state.active.sortedBy { if (isEnergy(it.feel.feelId)) 0 else 1 }
    LazyColumn(modifier.fillMaxSize().testTag("feel-list"), contentPadding = PaddingValues(vertical = 8.dp)) {
        item {
            Text("Tag songs by how they feel to you. Radio uses these alongside its moods.", Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        items(active, key = { it.feel.feelId }) { summary ->
            FeelRow(summary, onRadio = onRadio?.takeIf { canStartRadio(summary) }?.let { start -> { start(summary.feel) } }) { onOpen(summary.feel.feelId) }
        }
        item {
            TextButton(onClick = { adding = true }, Modifier.padding(horizontal = 12.dp).testTag("add-feel")) {
                RadioIcon(Symbol.Add, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Add a feel")
            }
        }
        if (archived.isNotEmpty()) {
            item { Text("Archived", Modifier.padding(start = 20.dp, top = 16.dp, bottom = 4.dp), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary) }
            items(archived, key = { "archived-" + it.feel.feelId }) { summary ->
                Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Swatch(summary.feel.color)
                    Text(summary.feel.name, Modifier.weight(1f).padding(start = 14.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    TextButton(onClick = { onRestore(summary.feel.feelId) }, modifier = Modifier.testTag("restore-${summary.feel.name}")) { Text("Restore") }
                }
            }
        }
    }
    if (adding) FeelEditDialog(null, onDismiss = { adding = false }) { name, color, _ -> adding = false; onCreate(name, color) }
}

/** The built-in explanation for Energy and the starter feels; your own feels have none. */
internal fun feelMeaning(feelId: String): String? = (listOf(FeelCatalog.energy) + FeelCatalog.starters).firstOrNull { it.feelId == feelId }?.meaning

private fun summaryLine(summary: FeelSummary): String =
    if (isEnergy(summary.feel.feelId)) "Built in · ${summary.rated} rated · ${summary.settled.toInt()}% settled"
    else "${summary.members} songs · ${summary.settled.toInt()}% settled"

/** A feel station needs at least one song in the feel; Energy is a scale, not a station. */
private fun canStartRadio(summary: FeelSummary) = !isEnergy(summary.feel.feelId) && summary.members > 0

@Composable
private fun FeelRow(summary: FeelSummary, onRadio: (() -> Unit)? = null, onClick: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    // Long-press offers "Start radio from this" for feels that can be a station.
    Box { DropdownMenu(menu, { menu = false }) {
        DropdownMenuItem(text = { Text("Start radio from this") }, leadingIcon = { RadioIcon(Symbol.Radio) }, onClick = { menu = false; onRadio?.invoke() },
            modifier = Modifier.testTag("feel-radio-${summary.feel.name}"))
    } }
    Row(Modifier.fillMaxWidth().combinedClickable(onClick = onClick, onLongClickLabel = if (onRadio != null) "Radio options" else null, onLongClick = onRadio?.let { { menu = true } }).heightIn(min = 60.dp).padding(horizontal = 20.dp, vertical = 6.dp).testTag("feel-${summary.feel.name}"),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(40.dp).background(feelColor(summary.feel.color), RadioShapes.Thumb))
        Column(Modifier.weight(1f).padding(start = 14.dp)) {
            Text(summary.feel.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            feelMeaning(summary.feel.feelId)?.let { Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            Text(summaryLine(summary), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        RadioIcon(Symbol.Forward, modifier = Modifier.size(18.dp))
    }
}

/** Add (feel == null) or edit a feel. Energy can be renamed and recoloured but never archived. */
@Composable
private fun FeelEditDialog(feel: LocalFeel?, onDismiss: () -> Unit, onSave: (name: String, color: String, archived: Boolean?) -> Unit) {
    var name by remember { mutableStateOf(feel?.name ?: "") }
    var color by remember { mutableStateOf(feel?.color ?: feelPalette[1]) }
    val trimmed = name.trim()
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text(if (feel == null) "Add a feel" else "Edit ${feel.name}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(name, { name = it.take(40) }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("feel-name"))
                Text("Colour", style = MaterialTheme.typography.labelLarge)
                @OptIn(ExperimentalLayoutApi::class)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    feelPalette.forEach { hex -> Swatch(hex, 32, selected = hex.equals(color, ignoreCase = true)) { color = hex } }
                }
                if (feel != null && !isEnergy(feel.feelId)) TextButton(onClick = { onSave(feel.name, feel.color, true) }, modifier = Modifier.testTag("archive-feel")) {
                    Text("Archive this feel", color = MaterialTheme.colorScheme.error)
                }
            }
        },
        confirmButton = { TextButton(onClick = { onSave(trimmed, color, null) }, enabled = FeelCatalog.validName(trimmed), modifier = Modifier.testTag("save-feel")) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

@Composable
private fun FeelDetail(summary: FeelSummary, state: FeelsState, songs: List<LocalSong>, onSprint: () -> Unit, onPairs: () -> Unit,
    onEdit: (String?, String?, Boolean?) -> Unit, onRemove: (LocalSong) -> Unit, modifier: Modifier, onRadio: (() -> Unit)? = null) {
    val feel = summary.feel
    var editing by remember { mutableStateOf(false) }
    val energy = isEnergy(feel.feelId)
    val rows = remember(state.bySong, songs, feel.feelId) {
        val byId = songs.associateBy { it.id }
        state.bySong.mapNotNull { (id, list) -> list.firstOrNull { it.feelId == feel.feelId }?.takeIf { energy || it.member }?.let { s -> byId[id]?.let { it to s } } }
            .sortedByDescending { it.second.score }
    }
    LazyColumn(modifier.fillMaxSize().testTag("feel-detail"), contentPadding = PaddingValues(vertical = 8.dp)) {
        item {
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(48.dp).background(feelColor(feel.color), RadioShapes.Leaf))
                Column(Modifier.weight(1f).padding(start = 14.dp)) {
                    Text(feel.name, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
                    feelMeaning(feel.feelId)?.let { Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("feel-meaning")) }
                    Text(summaryLine(summary), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (onRadio != null) IconButton(onClick = onRadio, modifier = Modifier.testTag("feel-radio")) { RadioIcon(Symbol.Radio, "Start radio from ${feel.name}") }
                TextButton(onClick = { editing = true }, modifier = Modifier.testTag("edit-feel")) { Text("Edit") }
            }
            Button(onClick = onSprint, Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp).heightIn(min = 52.dp).testTag("start-sprint"), shape = RadioShapes.Squircle) {
                Text("Start sprint")
            }
            Text(if (energy) "A sprint shows about 20 songs, one at a time: rate each Hot, Warm, Mellow or Cold." else "A sprint shows about 20 songs, one at a time: Yes, No or Skip. No is saved too and sharpens the next deck.",
                Modifier.padding(horizontal = 20.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            val pairable = remember(state.bySong, songs, feel.feelId) { pairPool(state, feel.feelId, songs).size }
            OutlinedButton(onClick = onPairs, enabled = pairable >= 2, modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp).heightIn(min = 52.dp).testTag("start-pairs"),
                shape = RadioShapes.Squircle) { Text("This or that") }
            Text(if (pairable >= 2) "Rounds of 10 pairs: pick which song is more ${feel.name}. Pairs come where the ranking is least sure."
                else if (energy) "Rate at least 2 songs to compare them." else "Add at least 2 songs to ${feel.name} to compare them.",
                Modifier.padding(horizontal = 20.dp).testTag("pairs-hint"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("Ranking · ${summary.settled.toInt()}% settled", Modifier.padding(start = 20.dp, top = 20.dp, bottom = 4.dp).testTag("ranking-header"),
                style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
            if (rows.isEmpty()) Text("Nothing yet. Start a sprint, or tag songs from Now Playing.", Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        itemsIndexed(rows, key = { _, it -> it.first.id }) { rank, (song, s) ->
            LibraryRow("${rank + 1}. ${song.title}", if (energy) energyBucket(s.score) ?: "" else song.artist.ifBlank { song.folderPath }, Symbol.Music, trailing = {
                if (!energy) TextButton(onClick = { onRemove(song) }, modifier = Modifier.testTag("remove-${song.id}")) { Text("Remove") }
            }) {}
        }
    }
    if (editing) FeelEditDialog(feel, onDismiss = { editing = false }) { name, color, archived ->
        editing = false
        if (archived == true) onEdit(null, null, true) else onEdit(name, color, null)
    }
}

/** A preview clip control: play/stop plus a scrubber over the whole song that restarts the clip where you drop it. */
@Composable
private fun PreviewBar(song: LocalSong, preview: PreviewController?) {
    if (preview == null) return
    val clip by preview.previewState.collectAsState()
    val playing = (clip as? PreviewState.Playing)?.takeIf { it.songId == song.id }
    var drag by remember(song.id) { mutableStateOf<Float?>(null) }
    val duration = (playing?.durationMs?.takeIf { it > 0 } ?: song.durationMs ?: 0L).coerceAtLeast(1L)
    val fraction = drag ?: playing?.let { it.positionMs.toFloat() / duration } ?: 0.35f
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        FilledTonalIconButton(onClick = { if (playing != null) preview.stopPreview() else preview.playPreview(song) }, modifier = Modifier.testTag("preview-toggle")) {
            RadioIcon(if (playing != null) Symbol.Stop else Symbol.Play, if (playing != null) "Stop preview" else "Play preview")
        }
        Slider(fraction.coerceIn(0f, 1f), onValueChange = { drag = it }, onValueChangeFinished = { drag?.let { preview.playPreview(song, it) }; drag = null },
            modifier = Modifier.weight(1f).padding(start = 8.dp).testTag("preview-scrub").semantics { contentDescription = "Preview position" })
    }
}

@Composable
private fun FeelSprintScreen(feels: FeelRepository, state: FeelsState, feel: LocalFeel, songs: List<LocalSong>, preview: PreviewController?,
    sessions: () -> List<List<String>>, onMessage: (String) -> Unit, onDone: () -> Unit, modifier: Modifier) {
    val scope = rememberCoroutineScope()
    val energy = isEnergy(feel.feelId)
    // Pause main music for the sprint; resume it (only if it was playing) when the screen goes away.
    DisposableEffect(preview) { preview?.enterPreview(); onDispose { preview?.exitPreview() } }
    var round by remember { mutableIntStateOf(0) }
    var index by remember(round) { mutableIntStateOf(0) }
    var added by remember(round) { mutableIntStateOf(0) }
    // The deck is fixed per round; answers change the states but must not reshuffle the cards under your thumb.
    val deck = remember(feel.feelId, round) {
        val states = state.bySong.mapNotNull { (id, list) -> list.firstOrNull { it.feelId == feel.feelId }?.let { id to FeelRules.FeelState(it.score, it.confidence, it.member) } }.toMap()
        val ownEnergy = state.bySong.mapNotNull { (id, list) -> list.firstOrNull { it.feelId == FeelCatalog.ENERGY_FEEL_ID }?.let { id to it.score } }.toMap()
        val starter = FeelCatalog.starters.firstOrNull { it.feelId == feel.feelId }
        val features = dev.radio.mobile.recommendation.FeelFeatures.latest.value // matched catalog features (9C); your own Energy wins
        FeelSprint.deck(SprintInput(songs, states, vectors = features.vectors, tags = features.tags, energy = features.energy + ownEnergy, sessions = sessions(),
            hintTags = starter?.hintTags.orEmpty(), hintEnergy = starter?.hintEnergy), Random(System.nanoTime()))
    }
    fun answer(value: Double?) {
        val song = deck.getOrNull(index) ?: return
        preview?.stopPreview()
        index++
        if (value == null) return
        if (!energy && value >= 1.0) added++
        scope.launch { feels.rate(feel.feelId, song, value).exceptionOrNull()?.let { onMessage(it.message ?: "Couldn't save that.") } }
    }
    val song = deck.getOrNull(index)
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp).testTag("feel-sprint"),
        verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Swatch(feel.color, 14)
            Text(if (energy) "How much does it lift you?" else "Is this ${feel.name}?", Modifier.weight(1f).padding(start = 10.dp), style = MaterialTheme.typography.titleMedium)
            if (song != null) Text("${index + 1} of ${deck.size}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("sprint-progress"))
        }
        if (song == null) {
            Text(if (deck.isEmpty()) "No more songs to show for ${feel.name}." else if (energy) "Done. Energy ratings go to both Radios." else "Done · $added added to ${feel.name}.",
                style = MaterialTheme.typography.titleLarge, modifier = Modifier.testTag("sprint-done"))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (deck.isNotEmpty()) Button(onClick = { round++ }, shape = RadioShapes.Soft, modifier = Modifier.testTag("sprint-again")) { Text("Another round") }
                OutlinedButton(onClick = onDone, shape = RadioShapes.Soft) { Text("Back to ${feel.name}") }
            }
            return@Column
        }
        Surface(Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow) {
            Column(Modifier.padding(20.dp).testTag("sprint-card"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Box(Modifier.fillMaxWidth().height(120.dp).background(feelColor(feel.color).copy(alpha = .35f), RoundedShapeFor(feel)), contentAlignment = Alignment.Center) {
                    RadioIcon(Symbol.Music, modifier = Modifier.size(40.dp))
                }
                Text(song.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.testTag("sprint-title"))
                Text(song.artist.ifBlank { song.folderPath.ifBlank { "Local MP3" } }, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                PreviewBar(song, preview)
            }
        }
        if (energy) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FeelCatalog.ENERGY_BUCKETS.forEach { (label, value) ->
                    FilledTonalButton(onClick = { answer(value) }, Modifier.weight(1f).heightIn(min = 52.dp).testTag("bucket-$label"), shape = RadioShapes.Soft,
                        contentPadding = PaddingValues(horizontal = 4.dp)) { Text(label, maxLines = 1) }
                }
            }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { answer(0.0) }, Modifier.weight(1f).heightIn(min = 56.dp).testTag("sprint-no"), shape = RadioShapes.Soft) { Text("No") }
                Button(onClick = { answer(1.0) }, Modifier.weight(1.4f).heightIn(min = 56.dp).testTag("sprint-yes"), shape = RadioShapes.Squircle) { Text("Yes") }
            }
        }
        TextButton(onClick = { answer(null) }, Modifier.align(Alignment.CenterHorizontally).testTag("sprint-skip")) { Text("Skip") }
    }
}

/** This feel's states for available songs on this phone. */
private fun feelStates(state: FeelsState, feelId: String, songs: List<LocalSong>): Map<String, FeelRules.FeelState> {
    val available = songs.filter { it.available }.mapTo(HashSet()) { it.id }
    return state.bySong.mapNotNull { (id, list) -> list.firstOrNull { it.feelId == feelId }?.takeIf { id in available }
        ?.let { id to FeelRules.FeelState(it.score, it.confidence, it.member) } }.toMap()
}

/** Songs this-or-that may pair, matching [ThisOrThat.round]'s pool. */
private fun pairPool(state: FeelsState, feelId: String, songs: List<LocalSong>) =
    feelStates(state, feelId, songs).filter { (_, s) -> isEnergy(feelId) || s.member || s.confidence < ThisOrThat.LOW_CONFIDENCE }

@Composable
private fun ThisOrThatScreen(feels: FeelRepository, state: FeelsState, feel: LocalFeel, songs: List<LocalSong>, preview: PreviewController?,
    sessions: () -> List<List<String>>, onMessage: (String) -> Unit, onDone: () -> Unit, modifier: Modifier) {
    val scope = rememberCoroutineScope()
    // Same as the sprint: pause main music here, resume it (only if it was playing) when the screen goes away.
    DisposableEffect(preview) { preview?.enterPreview(); onDispose { preview?.exitPreview() } }
    fun write(block: suspend () -> Result<*>) { scope.launch { block().exceptionOrNull()?.let { onMessage(it.message ?: "Couldn't save that.") } } }
    val settledNow = state.feels.firstOrNull { it.feel.feelId == feel.feelId }?.settled ?: 0.0
    var round by remember { mutableIntStateOf(0) }
    var index by remember(round) { mutableIntStateOf(0) }
    val before = remember(round) { settledNow }
    val byId = remember(songs) { songs.associateBy { it.id } }
    // Fixed per round so answers don't reshuffle the pairs under your thumb.
    val pairs = remember(feel.feelId, round) {
        val states = feelStates(state, feel.feelId, songs)
        ThisOrThat.round(states, sessions().flatten().toSet(), states.keys, Random(System.nanoTime()), scale = isEnergy(feel.feelId))
            .mapNotNull { (a, b) -> byId[a]?.let { x -> byId[b]?.let { y -> x to y } } }
    }
    val pair = pairs.getOrNull(index)
    fun next() { preview?.stopPreview(); index++ }
    fun pick(winner: LocalSong, loser: LocalSong, tie: Boolean = false) { next(); write { feels.compare(feel.feelId, winner, loser, tie) } }
    fun misfit(song: LocalSong) { next(); write { feels.tag(feel.feelId, song, false) } }
    val clip = preview?.previewState?.collectAsState()?.value
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp).testTag("this-or-that"),
        verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Swatch(feel.color, 14)
            Text("Which is more ${feel.name}?", Modifier.weight(1f).padding(start = 10.dp), style = MaterialTheme.typography.titleMedium)
            if (pair != null) Text("${index + 1} of ${pairs.size}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("pairs-progress"))
        }
        if (pair == null) {
            Text(if (pairs.isEmpty()) "Not enough songs to compare in ${feel.name}." else "Done", style = MaterialTheme.typography.titleLarge, modifier = Modifier.testTag("pairs-done"))
            if (pairs.isNotEmpty()) Text("Ranking settled: ${before.toInt()}% → ${settledNow.toInt()}%", style = MaterialTheme.typography.bodyLarge, modifier = Modifier.testTag("pairs-settled"))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (pairs.isNotEmpty()) Button(onClick = { round++ }, shape = RadioShapes.Soft, modifier = Modifier.testTag("pairs-again")) { Text("Another round") }
                OutlinedButton(onClick = onDone, shape = RadioShapes.Soft) { Text("Back to ${feel.name}") }
            }
            return@Column
        }
        val (left, right) = pair
        Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            listOf("left" to left, "right" to right).forEach { (side, song) ->
                val playing = (clip as? PreviewState.Playing)?.songId == song.id
                Surface(onClick = { if (preview != null) { if (playing) preview.stopPreview() else preview.playPreview(song) } },
                    modifier = Modifier.weight(1f).fillMaxHeight().testTag("pair-card-$side"), shape = MaterialTheme.shapes.large,
                    color = if (playing) feelColor(feel.color).copy(alpha = .35f) else MaterialTheme.colorScheme.surfaceContainerLow) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        RadioIcon(if (playing) Symbol.Stop else Symbol.Play, if (playing) "Stop clip" else "Play clip", modifier = Modifier.size(28.dp))
                        Text(song.title, style = MaterialTheme.typography.titleMedium, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.testTag("pair-title-$side"))
                        Text(song.artist.ifBlank { song.folderPath.ifBlank { "Local MP3" } }, style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Spacer(Modifier.weight(1f))
                        TextButton(onClick = { misfit(song) }, contentPadding = PaddingValues(horizontal = 4.dp), modifier = Modifier.testTag("pair-misfit-$side")) {
                            Text("Doesn't fit", style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = { pick(left, right) }, Modifier.weight(1f).heightIn(min = 56.dp).testTag("pick-left"), shape = RadioShapes.Squircle) { Text("This one", maxLines = 1) }
            Button(onClick = { pick(right, left) }, Modifier.weight(1f).heightIn(min = 56.dp).testTag("pick-right"), shape = RadioShapes.Squircle) { Text("That one", maxLines = 1) }
        }
        TextButton(onClick = { pick(left, right, tie = true) }, Modifier.align(Alignment.CenterHorizontally).testTag("pick-tie")) { Text("Can't decide") }
    }
}

@Suppress("FunctionName")
private fun RoundedShapeFor(feel: LocalFeel) = if (isEnergy(feel.feelId)) RadioShapes.Soft else RadioShapes.Leaf

/** Now Playing chip: shows the current song's feels and opens the tag sheet. */
@Composable
fun FeelChip(song: LocalSong, state: FeelsState, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val names = state.bySong[song.id].orEmpty().filter { it.member && !isEnergy(it.feelId) }
        .mapNotNull { s -> state.active.firstOrNull { it.feel.feelId == s.feelId }?.feel?.name }
    AssistChip(onClick = onClick, label = { Text(when (names.size) { 0 -> "Feel"; 1 -> names[0]; else -> "${names[0]} +${names.size - 1}" }, maxLines = 1) },
        modifier = modifier.testTag("feel-chip"), shape = RadioShapes.Leaf,
        leadingIcon = { Swatch(names.firstOrNull()?.let { n -> state.active.first { it.feel.name == n }.feel.color } ?: FeelCatalog.energy.color, 12) })
}

/** Two taps to tag: open the sheet, tap a feel. Energy is the four buckets. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun FeelSheet(song: LocalSong, feels: FeelRepository, state: FeelsState, onSetUp: () -> Unit, onMessage: (String) -> Unit, onDismiss: () -> Unit) {
    val scope = rememberCoroutineScope()
    fun write(block: suspend () -> Result<*>) { scope.launch { block().exceptionOrNull()?.let { onMessage(it.message ?: "Couldn't save that.") } } }
    val mine = state.bySong[song.id].orEmpty().associateBy { it.feelId }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp).padding(bottom = 24.dp).testTag("feel-sheet"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("How does it feel?", style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
            Text(song.title, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("Energy", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
            val current = energyBucket(mine[FeelCatalog.ENERGY_FEEL_ID]?.score)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FeelCatalog.ENERGY_BUCKETS.forEach { (label, value) ->
                    FilterChip(current == label, onClick = { write { feels.rate(FeelCatalog.ENERGY_FEEL_ID, song, value) } }, label = { Text(label) },
                        modifier = Modifier.weight(1f).testTag("sheet-bucket-$label"))
                }
            }
            val tags = state.active.filter { !isEnergy(it.feel.feelId) }
            Text("Your feels", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
            if (tags.isEmpty()) TextButton(onClick = onSetUp, modifier = Modifier.testTag("feel-sheet-setup")) { Text(if (state.needsSetup) "Pick your feels" else "Add a feel") }
            else FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                tags.forEach { summary ->
                    val on = mine[summary.feel.feelId]?.member == true
                    FilterChip(on, onClick = { write { feels.tag(summary.feel.feelId, song, !on) } }, label = { Text(summary.feel.name) },
                        leadingIcon = { Swatch(summary.feel.color, 12) }, modifier = Modifier.testTag("sheet-feel-${summary.feel.name}"),
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = feelColor(summary.feel.color), selectedLabelColor = Color(0xFF111827)))
                }
            }
        }
    }
}
