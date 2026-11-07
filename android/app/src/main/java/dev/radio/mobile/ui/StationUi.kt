@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package dev.radio.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*
import dev.radio.mobile.ui.theme.LocalStationColors

// Checkpoint 4.2 Radio surfaces. Copy and layout follow the accepted redesign canvas.

private val OnAirDot = Color(0xFFFB923C)
private val OnAirText = Color(0xFFFDBA74)
private val OnAirFill = Color(0x29FB923C)

internal fun moodLabel(mood: String) = mood.replaceFirstChar { it.uppercase() }
internal fun clock(ms: Long): String = "%d:%02d".format(ms / 60000, (ms / 1000) % 60)

@Composable
fun OnAirPill(text: String, modifier: Modifier = Modifier) {
    Row(modifier.background(OnAirFill, RoundedCornerShape(14.dp)).padding(horizontal = 10.dp, vertical = 4.dp).testTag("on-air"),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.size(7.dp).background(OnAirDot, CircleShape))
        Text(text.uppercase(), color = OnAirText, fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
    }
}

/** The dark station surface used for the Radio tab's card in every appearance. */
@Composable
private fun StationSurface(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalStationColors.current
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(24.dp), color = Color(colors.base), contentColor = Color.White) {
        Column(Modifier.padding(horizontal = 18.dp, vertical = 18.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
private fun StationLabel(text: String, color: Color = Color(LocalStationColors.current.muted)) =
    Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.Medium, letterSpacing = 1.sp)

@Composable
private fun StationOutlinedButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, symbol: Symbol? = null) {
    val colors = LocalStationColors.current
    OutlinedButton(onClick, modifier.heightIn(min = 44.dp), enabled = enabled, border = androidx.compose.foundation.BorderStroke(1.dp, Color(colors.outline)),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White, disabledContentColor = Color(0xFF8B9AB0)), contentPadding = PaddingValues(horizontal = 10.dp)) {
        symbol?.let { RadioIcon(it, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)) }
        Text(text, maxLines = 1)
    }
}

@Composable
private fun StationFilledButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, symbol: Symbol? = null) {
    val colors = LocalStationColors.current
    Button(onClick, modifier.fillMaxWidth().heightIn(min = 52.dp), enabled = enabled,
        colors = ButtonDefaults.buttonColors(containerColor = Color.White, contentColor = Color(colors.base), disabledContainerColor = Color(colors.track), disabledContentColor = Color(0xFF8B9AB0))) {
        symbol?.let { RadioIcon(it, modifier = Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)) }
        Text(text, style = MaterialTheme.typography.titleSmall)
    }
}

enum class StationBlock { AccessLost, Scanning, Empty, Loading }

@Composable
fun OffAirStationCard(canStart: Boolean, onPickForMe: () -> Unit, onFromSong: () -> Unit, modifier: Modifier = Modifier) {
    StationSurface(modifier.testTag("station-card")) {
        StationLabel("OFF AIR")
        Text("Start a station", style = MaterialTheme.typography.headlineSmall)
        Text("Radio picks from your music and keeps the queue going as it plays.", color = Color(LocalStationColors.current.muted), style = MaterialTheme.typography.bodyMedium)
        StationFilledButton("Pick for me", onPickForMe, Modifier.testTag("start-radio"), enabled = canStart, symbol = Symbol.Play)
        StationOutlinedButton("Start from a song", onFromSong, Modifier.fillMaxWidth().testTag("choose-radio-song"), enabled = canStart, symbol = Symbol.Search)
    }
}

@Composable
fun BlockedStationCard(block: StationBlock, scanned: Int, total: Int, onChooseFolder: () -> Unit, onBrowse: () -> Unit, onRescan: () -> Unit, modifier: Modifier = Modifier) {
    val muted = Color(LocalStationColors.current.muted)
    StationSurface(modifier.testTag("station-blocked-${block.name.lowercase()}")) {
        when (block) {
            StationBlock.AccessLost -> {
                StationLabel("CAN'T START", OnAirText)
                Text("Radio can't reach your music", style = MaterialTheme.typography.titleLarge)
                Text("Android removed this app's access to the Music folder. Your library, likes and history are still saved. Choose the same folder again to continue.",
                    color = muted, style = MaterialTheme.typography.bodyMedium)
                StationFilledButton("Choose Music folder", onChooseFolder, Modifier.testTag("station-choose-folder"), symbol = Symbol.Folder)
            }
            StationBlock.Scanning -> {
                Row(Modifier.fillMaxWidth()) { StationLabel("SCANNING LIBRARY"); Spacer(Modifier.weight(1f)); if (total > 0) Text("$scanned of $total", color = muted, fontSize = 12.sp) }
                Text("Radio will be ready when the scan finishes", style = MaterialTheme.typography.titleMedium)
                if (total > 0) LinearProgressIndicator(progress = { (scanned.toFloat() / total).coerceIn(0f, 1f) }, Modifier.fillMaxWidth(),
                    color = Color(LocalStationColors.current.progress), trackColor = Color(LocalStationColors.current.track))
                else LinearProgressIndicator(Modifier.fillMaxWidth(), color = Color(LocalStationColors.current.progress), trackColor = Color(LocalStationColors.current.track))
                Text("Music that's already playing keeps playing.", color = muted, style = MaterialTheme.typography.bodySmall)
            }
            StationBlock.Empty -> {
                StationLabel("NOTHING TO PLAY")
                Text("No songs Radio can pick from", style = MaterialTheme.typography.titleMedium)
                Text("Every available song is disliked or missing. Add music to your folder, or remove a dislike from Now Playing.", color = muted, style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StationOutlinedButton("Browse music", onBrowse, Modifier.weight(1f))
                    StationOutlinedButton("Rescan", onRescan, Modifier.weight(1f))
                }
            }
            StationBlock.Loading -> {
                StationLabel("GETTING READY")
                Text("Loading your Radio", style = MaterialTheme.typography.titleMedium)
                LinearProgressIndicator(Modifier.fillMaxWidth(), color = Color(LocalStationColors.current.progress), trackColor = Color(LocalStationColors.current.track))
            }
        }
    }
}

@Composable
fun OnAirStationCard(audio: PlaybackState, radio: RadioState, onOpenPlayer: () -> Unit, onVibe: () -> Unit, onNewMix: () -> Unit, onEnd: () -> Unit, modifier: Modifier = Modifier) {
    val colors = LocalStationColors.current
    val muted = Color(colors.muted)
    val song = audio.current
    val locked = radio.vibeLocalId != null
    StationSurface(modifier.testTag("station-card")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OnAirPill("On air")
            Spacer(Modifier.weight(1f))
            Text("${moodLabel(radio.mood)} · ${ExplorationPreset.nearest(radio.exploration).label}", color = muted, fontSize = 12.sp, modifier = Modifier.testTag("station-tuning-label"))
        }
        if (song != null) Row(Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = "Open player", onClick = onOpenPlayer).testTag("station-now-playing"),
            verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(56.dp).background(Color(colors.tile), RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
                CompositionLocalProvider(LocalContentColor provides Color(0xFFBFD0EA)) { RadioIcon(Symbol.Music, modifier = Modifier.size(26.dp)) }
            }
            Column(Modifier.weight(1f).padding(start = 14.dp)) {
                Text(song.title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text("${song.folderPath.ifBlank { "Local music" }} · ${clock(audio.positionMs)} / ${audio.durationMs?.let(::clock) ?: "--:--"}",
                    color = muted, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        val duration = audio.durationMs ?: 0L
        LinearProgressIndicator(progress = { if (duration > 0) (audio.positionMs.toFloat() / duration).coerceIn(0f, 1f) else 0f }, Modifier.fillMaxWidth(),
            color = Color(colors.progress), trackColor = Color(colors.track), drawStopIndicator = {})
        Text(audio.stationSeedTitle?.let { "Station started from $it" } ?: "Station picked for you", color = muted, fontSize = 12.sp, modifier = Modifier.testTag("station-seed"))
        if (locked) Row(Modifier.fillMaxWidth().background(Color(colors.panel), RoundedCornerShape(14.dp)).padding(horizontal = 12.dp, vertical = 10.dp).testTag("station-vibe"),
            verticalAlignment = Alignment.CenterVertically) {
            CompositionLocalProvider(LocalContentColor provides Color(colors.progress)) { RadioIcon(Symbol.Lock, modifier = Modifier.size(18.dp)) }
            Column(Modifier.padding(start = 10.dp)) {
                Text("Locked to this song's vibe", style = MaterialTheme.typography.labelLarge)
                Text("${radio.vibeSongsLeft} songs or ${vibeMinutes(radio)} min left", color = muted, fontSize = 12.sp)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (locked) Button(onVibe, Modifier.weight(1f).heightIn(min = 44.dp).testTag("station-vibe-lock"), contentPadding = PaddingValues(horizontal = 8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(colors.progress), contentColor = Color(colors.base))) {
                RadioIcon(Symbol.Lock, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Release")
            } else StationOutlinedButton("Vibe lock", onVibe, Modifier.weight(1f).testTag("station-vibe-lock"), enabled = radio.vibeAvailable, symbol = Symbol.Lock)
            StationOutlinedButton("New mix", onNewMix, Modifier.weight(1f).testTag("new-mix"), symbol = Symbol.Refresh)
            StationOutlinedButton("End", onEnd, Modifier.weight(1f).testTag("end-station-card"), symbol = Symbol.Stop)
        }
    }
}

internal fun vibeMinutes(radio: RadioState) = ((radio.vibeUntil - System.currentTimeMillis()).coerceAtLeast(0) / 60000) + 1

@Composable
fun MoodChips(selected: String, onSelect: (String) -> Unit, enabled: Boolean = true) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(0.dp)) {
        OfflineRecommender.MOODS.keys.forEach { mood ->
            FilterChip(selected == mood, onClick = { onSelect(mood) }, enabled = enabled, label = { Text(moodLabel(mood)) }, modifier = Modifier.testTag("radio-mood-$mood"))
        }
    }
}

@Composable
fun ExplorationPresets(value: Double, onSelect: (ExplorationPreset) -> Unit, enabled: Boolean = true) {
    val current = ExplorationPreset.nearest(value)
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().testTag("radio-presets")) {
        ExplorationPreset.entries.forEachIndexed { index, preset ->
            SegmentedButton(selected = current == preset, onClick = { onSelect(preset) }, enabled = enabled,
                shape = SegmentedButtonDefaults.itemShape(index, ExplorationPreset.entries.size), modifier = Modifier.testTag("radio-preset-${preset.name.lowercase()}"),
                icon = {}) { Text(preset.label, maxLines = 1) }
        }
    }
}

@Composable
fun ComingUp(audio: PlaybackState, onRemove: (String) -> Unit, onFullQueue: () -> Unit, modifier: Modifier = Modifier) {
    val byId = remember(audio.queue) { audio.queue.associateBy { it.id } }
    val current = audio.playOrder.indexOf(audio.currentId)
    val upcoming = if (current >= 0) audio.playOrder.drop(current + 1).mapNotNull(byId::get) else emptyList()
    Column(modifier.fillMaxWidth().testTag("coming-up")) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Coming up", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            TextButton(onFullQueue, modifier = Modifier.testTag("open-full-queue")) { Text("Full queue") }
        }
        upcoming.take(3).forEachIndexed { index, song ->
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).semantics(mergeDescendants = true) {}.testTag("coming-up-${song.id}"), verticalAlignment = Alignment.CenterVertically) {
                Text("${index + 1}", Modifier.width(28.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Column(Modifier.weight(1f)) {
                    Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                    OriginLine(song, audio.origins[song.id])
                }
                IconButton({ onRemove(song.id) }) { RadioIcon(Symbol.Close, "Remove ${song.title}") }
            }
        }
        Text(if (upcoming.isEmpty()) "Radio is picking the next songs." else "Radio adds more as these play.", Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** "Artist · reason", with Desktop and Added by you shown as small tags. */
@Composable
internal fun OriginLine(song: LocalSong, origin: StationOrigin?, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(songSubtitle(song) + if (origin != null && origin != StationOrigin.Desktop && origin != StationOrigin.User) " · ${origin.label}" else "",
            Modifier.weight(1f, fill = false), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = color)
        if (origin == StationOrigin.Desktop || origin == StationOrigin.User) OriginTag(origin.label, Modifier.padding(start = 6.dp))
    }
}

@Composable
internal fun OriginTag(label: String, modifier: Modifier = Modifier) {
    Text(label, modifier.background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(9.dp)).padding(horizontal = 6.dp, vertical = 1.dp),
        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onPrimaryContainer)
}

/** One row of "Add to the station": a local suggestion or a desktop option. */
data class StationAddRow(val song: LocalSong, val reason: String, val desktop: Boolean)

@Composable
fun AddToStation(rows: List<StationAddRow>, enabled: Boolean, refreshing: Boolean, onAdd: (StationAddRow) -> Unit, onRefresh: () -> Unit, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer, RoundedCornerShape(20.dp)).padding(start = 16.dp, end = 8.dp, top = 12.dp, bottom = 6.dp)
        .testTag("add-to-station")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Add to the station", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                Text("Not in the queue yet", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onRefresh, enabled = !refreshing, modifier = Modifier.testTag("refresh-radio")) { Text(if (refreshing) "Refreshing…" else "More") }
        }
        if (rows.isEmpty()) Text(if (refreshing) "Finding songs…" else "No other songs to suggest right now.", Modifier.padding(vertical = 10.dp),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        rows.forEach { row ->
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).semantics(mergeDescendants = true) {}.testTag("station-add-${row.song.id}"), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(row.song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(songSubtitle(row.song) + if (!row.desktop && row.reason.isNotBlank()) " · ${row.reason}" else "", Modifier.weight(1f, fill = false),
                            maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (row.desktop) OriginTag("Desktop · ${row.reason}", Modifier.padding(start = 6.dp))
                    }
                }
                FilledTonalIconButton({ onAdd(row) }, enabled = enabled, modifier = Modifier.testTag(if (row.desktop) "play-next-${row.song.id}" else "radio-add-${row.song.id}")) {
                    RadioIcon(Symbol.Add, "Play ${row.song.title} next")
                }
            }
        }
    }
}

@Composable
fun EndStationSheet(upcoming: Int, onFinish: () -> Unit, onStopNow: () -> Unit, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.padding(horizontal = 24.dp).padding(bottom = 24.dp).testTag("end-station-sheet"), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("End the station?", style = MaterialTheme.typography.titleLarge)
            Text("Mood, tuning, likes and history are kept either way.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            EndChoice(Symbol.Queue, "Finish what's queued",
                if (upcoming > 0) "The current song and the $upcoming coming up keep playing. Radio stops adding more." else "The current song keeps playing. Radio stops adding more.",
                Modifier.testTag("end-finish-queued"), onFinish)
            EndChoice(Symbol.Stop, "Stop now", "Pauses and ends the station. What's queued stays as a normal queue you can edit or shuffle.", Modifier.testTag("end-stop-now"), onStopNow)
            TextButton(onDismiss, Modifier.fillMaxWidth()) { Text("Keep listening") }
        }
    }
}

@Composable
private fun EndChoice(symbol: Symbol, title: String, body: String, modifier: Modifier, onClick: () -> Unit) {
    OutlinedCard(onClick, modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.Top) {
            Box(Modifier.size(40.dp).background(MaterialTheme.colorScheme.primaryContainer, CircleShape), contentAlignment = Alignment.Center) { RadioIcon(symbol, modifier = Modifier.size(20.dp)) }
            Column(Modifier.padding(start = 14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text(body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
fun LeaveRadioDialog(title: String, source: String, onLeave: () -> Unit, onPlayNext: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, modifier = Modifier.testTag("leave-radio-dialog"),
        icon = { OnAirPill("On air") }, title = { Text("Leave Radio?") },
        text = { Text("Playing “$title” starts a normal queue from $source and ends the station. Your mood, tuning and likes are kept.") },
        confirmButton = {
            Column(horizontalAlignment = Alignment.End) {
                TextButton(onLeave, modifier = Modifier.testTag("leave-radio-play")) { Text("Play song and leave Radio") }
                TextButton(onPlayNext, modifier = Modifier.testTag("leave-radio-play-next")) { Text("Play next in Radio instead") }
                TextButton(onDismiss, modifier = Modifier.testTag("leave-radio-cancel")) { Text("Cancel") }
            }
        })
}

@Composable
fun StartFromSongSheet(songs: List<LocalSong>, queueSize: Int, queueSource: String, onStart: (String) -> Unit, onDismiss: () -> Unit) {
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf<String?>(null) }
    val visible = remember(songs, query) {
        val needle = query.trim()
        if (needle.isEmpty()) songs else songs.filter { it.title.contains(needle, true) || it.artist.contains(needle, true) || it.folderPath.contains(needle, true) }
    }
    ModalBottomSheet(onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.fillMaxWidth().fillMaxHeight(.92f).testTag("radio-seed-sheet")) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Start from a song", Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
                TextButton(onDismiss) { Text("Cancel") }
            }
            OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 8.dp).testTag("radio-seed-search"),
                label = { Text("Search your library") }, singleLine = true, leadingIcon = { RadioIcon(Symbol.Search) },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.None))
            Text("${visible.size} songs · disliked and missing songs are hidden", Modifier.padding(horizontal = 24.dp), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            LazyColumn(Modifier.fillMaxWidth().weight(1f).padding(horizontal = 12.dp)) {
                items(visible, key = { it.id }) { song ->
                    val chosen = song.id == selected
                    Row(Modifier.fillMaxWidth().heightIn(min = 60.dp)
                        .background(if (chosen) MaterialTheme.colorScheme.primaryContainer else Color.Transparent, RoundedCornerShape(16.dp))
                        .selectable(chosen, role = Role.RadioButton, onClick = { selected = song.id }).padding(horizontal = 12.dp).testTag("radio-seed-${song.id}"),
                        verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(40.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
                            RadioIcon(if (chosen) Symbol.Check else Symbol.Music, modifier = Modifier.size(20.dp))
                        }
                        Column(Modifier.padding(start = 12.dp)) {
                            Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                            Text(songSubtitle(song), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
                if (visible.isEmpty()) item { Text("No matching songs", Modifier.padding(16.dp)) }
            }
            HorizontalDivider()
            Column(Modifier.padding(horizontal = 24.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (queueSize > 0) Row(Modifier.fillMaxWidth().background(Color(0xFFFFF4E5), RoundedCornerShape(14.dp)).padding(12.dp).testTag("radio-seed-replace-warning")) {
                    Text("This replaces your current queue ($queueSize ${if (queueSize == 1) "song" else "songs"}${queueSource.takeIf { it.isNotBlank() }?.let { " from “$it”" } ?: ""}). The song playing now stops.",
                        color = Color(0xFF7A3E00), style = MaterialTheme.typography.bodySmall)
                }
                Button({ selected?.let(onStart) }, Modifier.fillMaxWidth().heightIn(min = 52.dp).testTag("confirm-start-radio"), enabled = selected != null,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(LocalStationColors.current.base), contentColor = Color.White)) {
                    RadioIcon(Symbol.Play, modifier = Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text("Start station from this song")
                }
            }
        }
    }
}

/** Library and recommendation-data counts, in words (similarity is model evidence, not a confidence score). */
@Composable
fun StationDetails(radio: RadioState, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().testTag("station-details"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("STATION DETAILS", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        DetailRow("Songs Radio can pick", radio.diagnostics.eligibleTracks, "detail-eligible")
        DetailRow("Matched to your desktop", radio.matched, "detail-matched")
        DetailRow("Only on this phone", radio.localOnly, "detail-local")
        DetailRow("With description data", radio.diagnostics.textVectorTracks, "detail-text")
        DetailRow("With audio data", radio.diagnostics.audioVectorTracks, "detail-audio")
        if (radio.legacyVectorTracks > 0) DetailRow("Legacy desktop similarity (${if (radio.legacyVectorsEnabled) "on" else "off"})", radio.legacyVectorTracks, "detail-legacy")
        Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}.testTag("detail-signals")) {
            Text("Listening signals", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
            Text("${radio.diagnostics.tasteEvents} (${radio.diagnostics.contributingTasteEvents} used)", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun DetailRow(label: String, value: Int, tag: String) {
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}.testTag(tag)) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        Text("%,d".format(value), style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun WhyThisSongSheet(song: LocalSong?, origin: StationOrigin?, radio: RadioState, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 24.dp).padding(bottom = 24.dp).testTag("why-song-sheet"), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Why this song?", Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
                Button(onDismiss) { Text("Done") }
            }
            if (song != null) OutlinedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(song.title, style = MaterialTheme.typography.titleSmall)
                    Text(songSubtitle(song), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(originExplanation(origin), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("why-song-reason"))
                }
            }
            StationDetails(radio)
            Text("Similarity is a model's judgement, not a confidence score. Legacy desktop similarity is in Settings.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

internal fun originExplanation(origin: StationOrigin?) = when (origin) {
    StationOrigin.Start -> "You started the station from this song."
    StationOrigin.Similar -> "Similar sound to the song before it."
    StationOrigin.Taste -> "Matches what you usually play and like."
    StationOrigin.Mood -> "Fits the station's mood and energy."
    StationOrigin.Explore -> "Something new near your usual music, picked to explore."
    StationOrigin.Wildcard -> "A wildcard: less similar on purpose, to keep things fresh."
    StationOrigin.Context -> "Fits right now, close in energy to the last song."
    StationOrigin.Vibe -> "Matches the locked vibe."
    StationOrigin.Desktop -> "Picked by your desktop."
    StationOrigin.User -> "You added this song."
    null -> "Radio picked this song on this phone."
}

/** Mini-player while on air: station surface, ON AIR dot, Pause and Skip. */
@Composable
fun StationMiniPlayer(song: LocalSong, status: String, playWhenReady: Boolean, canPlay: Boolean, canNext: Boolean, onOpen: () -> Unit, onToggle: () -> Unit, onNext: () -> Unit) {
    val colors = LocalStationColors.current
    Surface(color = Color(colors.base), contentColor = Color.White, modifier = Modifier.fillMaxWidth().testTag("mini-player")) {
        Row(Modifier.padding(start = 16.dp, end = 4.dp).heightIn(min = 68.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).background(Color(colors.tile), RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
                CompositionLocalProvider(LocalContentColor provides Color(0xFFBFD0EA)) { RadioIcon(Symbol.Music, modifier = Modifier.size(20.dp)) }
            }
            Column(Modifier.weight(1f).clickable(role = Role.Button, onClickLabel = "Open player", onClick = onOpen).padding(horizontal = 12.dp, vertical = 8.dp)) {
                Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(6.dp).background(OnAirDot, CircleShape)); Spacer(Modifier.width(6.dp))
                    Text(status, color = OnAirText, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            IconButton(onToggle, enabled = canPlay) { RadioIcon(if (playWhenReady) Symbol.Pause else Symbol.Play, if (playWhenReady) "Pause" else "Play") }
            IconButton(onNext, enabled = canNext, modifier = Modifier.testTag("mini-next")) { RadioIcon(Symbol.Next, "Next song") }
        }
    }
}

/** Radio Now Playing body below the top bar (station theme is provided by the caller). */
@Composable
fun RadioNowPlaying(
    audio: PlaybackState, song: LocalSong, radio: RadioState, position: Float, liked: Boolean, feedbackReady: Boolean, canRead: Boolean, minimal: Boolean, compact: Boolean,
    options: List<ConnectedPick>, optionsSummary: String?,
    onScrub: (Float) -> Unit, onScrubFinished: () -> Unit, onLike: () -> Unit, onNotForMe: () -> Unit,
    onPrevious: () -> Unit, onToggle: () -> Unit, onNext: () -> Unit, onVibe: () -> Unit,
    onShowQueue: () -> Unit, onSleep: () -> Unit, onPlayNext: (ConnectedPick) -> Unit, onRescan: () -> Unit,
    journalError: String?, onRetryJournal: () -> Unit, modifier: Modifier = Modifier,
) {
    val locked = radio.vibeLocalId != null
    val colors = LocalStationColors.current
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp).testTag("radio-now-playing"), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Spacer(Modifier.height(4.dp))
        Box(Modifier.align(Alignment.CenterHorizontally).size(if (minimal || compact || audio.error != null) 160.dp else 220.dp)
            .background(Color(colors.tile), RoundedCornerShape(20.dp)), contentAlignment = Alignment.Center) {
            CompositionLocalProvider(LocalContentColor provides if (audio.error != null) OnAirText else Color(0xFFBFD0EA)) {
                RadioIcon(if (audio.error != null) Symbol.Warning else Symbol.Music, modifier = Modifier.size(56.dp))
            }
        }
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Text(song.title, style = MaterialTheme.typography.headlineSmall)
                OriginLine(song, audio.origins[song.id])
            }
            IconToggleButton(liked, { onLike() }, enabled = feedbackReady, modifier = Modifier.testTag("favorite-song")) {
                CompositionLocalProvider(LocalContentColor provides if (liked) OnAirText else LocalContentColor.current) { RadioIcon(Symbol.Heart, if (liked) "Liked" else "Like") }
            }
        }
        audio.error?.let { message ->
            Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.errorContainer, RoundedCornerShape(18.dp))
                .border(1.dp, Color(0xFF7F3B3B), RoundedCornerShape(18.dp)).padding(16.dp).testTag("playback-error"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("This file can't be played", style = MaterialTheme.typography.titleSmall)
                Text(message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onErrorContainer)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onNext, Modifier.weight(1f), enabled = audio.canNext, colors = ButtonDefaults.buttonColors(containerColor = Color.White, contentColor = Color(colors.base))) { Text("Skip to next") }
                    OutlinedButton(onRescan, Modifier.weight(1f)) { Text("Rescan library") }
                }
            }
        }
        Column {
            PlayerSeekBar(position, (audio.durationMs ?: 0L) / 1000f, onSeek = onScrub, modifier = Modifier.fillMaxWidth(), enabled = audio.seekable, onSeekFinished = onScrubFinished)
            Row(Modifier.fillMaxWidth()) {
                Text(clock((position * 1000).toLong()), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.weight(1f))
                Text(audio.durationMs?.let(::clock) ?: "--:--", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Row(Modifier.fillMaxWidth().testTag("player-transport"), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            IconButton(onNotForMe, enabled = feedbackReady && audio.canNext, modifier = Modifier.testTag("not-for-me")) { RadioIcon(Symbol.Block, "Not for me (dislike and skip)") }
            IconButton(onPrevious, enabled = audio.canPrevious) { RadioIcon(Symbol.Previous, "Previous song") }
            FilledIconButton(onToggle, enabled = canRead, modifier = Modifier.size(72.dp),
                colors = IconButtonDefaults.filledIconButtonColors(containerColor = Color.White, contentColor = Color(colors.base))) {
                RadioIcon(if (audio.playWhenReady) Symbol.Pause else Symbol.Play, if (audio.playWhenReady) "Pause" else "Play", Modifier.size(30.dp))
            }
            IconButton(onNext, enabled = audio.canNext, modifier = Modifier.testTag("player-next")) { RadioIcon(Symbol.Next, "Next song") }
            IconToggleButton(locked, { onVibe() }, enabled = locked || radio.vibeAvailable, modifier = Modifier.testTag("vibe-lock"),
                colors = IconButtonDefaults.iconToggleButtonColors(checkedContainerColor = Color(colors.progress), checkedContentColor = Color(colors.base))) {
                RadioIcon(Symbol.Lock, if (locked) "Release vibe lock" else "Vibe lock")
            }
        }
        if (!minimal) {
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally)) {
                if (audio.sleepTimer.active) AssistChip(onSleep, { Text("Sleep · ${sleepLabel(audio.sleepTimer)}") }, Modifier.testTag("sleep-countdown"), leadingIcon = { RadioIcon(Symbol.Clock, modifier = Modifier.size(16.dp)) })
                if (locked) SuggestionChip({}, { Text("Vibe · ${radio.vibeSongsLeft} songs · ${vibeMinutes(radio)} min") }, enabled = false, modifier = Modifier.testTag("vibe-chip"))
                else if (!radio.vibeAvailable) Text("Vibe lock needs a matched song with recommendation data.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val nextSong = audio.playOrder.indexOf(audio.currentId).takeIf { it >= 0 }?.let { audio.playOrder.getOrNull(it + 1) }?.let { id -> audio.queue.firstOrNull { it.id == id } }
            Row(Modifier.fillMaxWidth().background(Color(colors.panel), RoundedCornerShape(14.dp)).clickable(role = Role.Button, onClickLabel = "Show queue", onClick = onShowQueue)
                .heightIn(min = 48.dp).padding(horizontal = 14.dp).testTag("next-up"), verticalAlignment = Alignment.CenterVertically) {
                Text("NEXT", color = Color(colors.progress), fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
                Text(nextSong?.title ?: "Radio is picking…", Modifier.weight(1f).padding(horizontal = 12.dp),
                    maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                RadioIcon(Symbol.Forward, modifier = Modifier.size(18.dp))
            }
            if (options.isNotEmpty()) UpNextOptionsCard(options, onPlayNext, summary = optionsSummary)
        }
        journalError?.let { failure ->
            Row(Modifier.fillMaxWidth().background(Color(colors.panel), RoundedCornerShape(14.dp)).padding(start = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(failure, Modifier.weight(1f).testTag("journal-error"), style = MaterialTheme.typography.bodySmall)
                TextButton(onRetryJournal, modifier = Modifier.testTag("retry-journal")) { Text("Retry") }
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}
