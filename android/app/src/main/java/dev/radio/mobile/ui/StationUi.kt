@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package dev.radio.mobile.ui

import androidx.compose.ui.semantics.contentDescription
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.basicMarquee
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*
import androidx.compose.ui.graphics.luminance
import dev.radio.mobile.ui.theme.LocalStationColors
import dev.radio.mobile.ui.theme.MoodColors
import dev.radio.mobile.ui.theme.RadioShapes

// Checkpoint 4.2 Radio surfaces. Copy and layout follow the accepted redesign canvas.

private val OnAirDot = Color(dev.radio.mobile.ui.theme.StationColors.Ocean.onAir)
private val OnAirText = Color(dev.radio.mobile.ui.theme.StationColors.Ocean.onAirText)
private val OnAirFill = OnAirDot.copy(alpha = .16f)
/** Icon tint on station tiles; the muted station text colour, so it follows the palette. */
@Composable private fun stationIconTint() = Color(LocalStationColors.current.muted)

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

/** Quiet indicator that the paired desktop is (or isn't) reachable for Desktop picks and talk. Never a call to action. */
@Composable
private fun ServerChipView(chip: dev.radio.mobile.recommendation.ServerChip, muted: Color) {
    val dot = when (chip) {
        dev.radio.mobile.recommendation.ServerChip.Connected -> Color(0xFF6FCF97)
        dev.radio.mobile.recommendation.ServerChip.Offline -> muted.copy(alpha = .5f)
        dev.radio.mobile.recommendation.ServerChip.Checking -> muted
    }
    Row(Modifier.padding(start = 8.dp).border(1.dp, muted.copy(alpha = .35f), RoundedCornerShape(10.dp)).padding(horizontal = 8.dp, vertical = 2.dp)
        .semantics(mergeDescendants = true) {}.testTag("server-chip-${chip.name.lowercase()}"),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
        Box(Modifier.size(6.dp).background(dot, CircleShape))
        Text(chip.label, color = muted, fontSize = 11.sp, maxLines = 1)
    }
}

/** The dark station surface used for the Radio tab's card in every appearance. */
@Composable
private fun StationSurface(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalStationColors.current
    // In dark mode the page background is the station colour itself, so the card gets an outline to keep its edge.
    val blends = MaterialTheme.colorScheme.background == Color(colors.base)
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(36.dp), color = Color(colors.base), contentColor = Color.White,
        border = if (blends) androidx.compose.foundation.BorderStroke(1.dp, Color(colors.outline)) else null) {
        Column(Modifier.padding(horizontal = 20.dp, vertical = 20.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
private fun StationLabel(text: String, color: Color = Color(LocalStationColors.current.muted)) =
    Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.Medium, letterSpacing = 1.sp)

@Composable
private fun StationOutlinedButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, symbol: Symbol? = null) {
    val colors = LocalStationColors.current
    // Large text: drop the icon first, then let the label wrap to two lines rather than cutting words off.
    val large = androidx.compose.ui.platform.LocalDensity.current.fontScale > 1.15f
    val press = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
    OutlinedButton(onClick, modifier.heightIn(min = 44.dp).pressScale(press), enabled = enabled, border = androidx.compose.foundation.BorderStroke(1.dp, Color(colors.outline)),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White, disabledContentColor = Color.White.copy(alpha = .38f)), contentPadding = PaddingValues(horizontal = 10.dp),
        interactionSource = press) {
        if (!large) symbol?.let { RadioIcon(it, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)) }
        Text(text, maxLines = 2, textAlign = TextAlign.Center, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun StationFilledButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, symbol: Symbol? = null) {
    val colors = LocalStationColors.current
    val press = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
    Button(onClick, modifier.fillMaxWidth().heightIn(min = 60.dp).pressScale(press, .97f), enabled = enabled, shape = RadioShapes.Squircle, interactionSource = press,
        colors = ButtonDefaults.buttonColors(containerColor = Color.White, contentColor = Color(colors.base), disabledContainerColor = Color(colors.track), disabledContentColor = Color.White.copy(alpha = .45f))) {
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
fun OnAirStationCard(audio: PlaybackState, radio: RadioState, onOpenPlayer: () -> Unit, onVibe: () -> Unit, onNewMix: () -> Unit, onEnd: () -> Unit, modifier: Modifier = Modifier,
    positionMs: () -> Long = { audio.positionMs }, feel: dev.radio.mobile.data.LocalFeel? = null, server: dev.radio.mobile.recommendation.ServerChip? = null) {
    val colors = LocalStationColors.current
    val muted = Color(colors.muted)
    val song = audio.current
    val talk = audio.talk?.takeIf { it.current }
    val locked = radio.vibeLocalId != null
    StationSurface(modifier.testTag("station-card")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OnAirPill("On air")
            Spacer(Modifier.weight(1f))
            // A feel station names the feel (checkpoint 9C), a song or playlist station its start; otherwise the mood.
            Box(Modifier.padding(end = 6.dp).size(8.dp).background(feel?.let { feelColor(it.color) } ?: Color(MoodColors.of(radio.mood).accent), CircleShape))
            Text("${stationName(radio, feel)} · ${ExplorationPreset.nearest(radio.exploration).label}", color = muted, fontSize = 12.sp, maxLines = 1,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false).testTag("station-tuning-label"))
        }
        // Checkpoint 13B: talk between songs is on air too, with the song it introduces.
        if (talk != null) Row(Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = "Open player", onClick = onOpenPlayer).testTag("station-talk"),
            verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(56.dp).background(Color(colors.tile), RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
                CompositionLocalProvider(LocalContentColor provides stationIconTint()) { RadioIcon(Symbol.Person, modifier = Modifier.size(26.dp)) }
            }
            Column(Modifier.weight(1f).padding(start = 14.dp)) {
                Text(talk.info.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                StationElapsed(talkSpeakerLine(talk.info), positionMs, audio.durationMs, muted)
                audio.queue.firstOrNull { it.id == talk.nextId }?.let { Text("Then: ${it.title}", color = muted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            }
        } else if (song != null) Row(Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = "Open player", onClick = onOpenPlayer).testTag("station-now-playing"),
            verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(56.dp).background(Color(colors.tile), RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
                CompositionLocalProvider(LocalContentColor provides stationIconTint()) { RadioIcon(Symbol.Music, modifier = Modifier.size(26.dp)) }
            }
            Column(Modifier.weight(1f).padding(start = 14.dp)) {
                Text(song.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                StationElapsed(song.folderPath.ifBlank { "Local music" }, positionMs, audio.durationMs, muted)
            }
        }
        val duration = audio.durationMs ?: 0L
        // The progress lambda is read while drawing, so position ticks redraw the bar without recomposing the card.
        LinearProgressIndicator(progress = { if (duration > 0) (positionMs().toFloat() / duration).coerceIn(0f, 1f) else 0f }, Modifier.fillMaxWidth(),
            color = Color(colors.progress), trackColor = Color(colors.track), drawStopIndicator = {})
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(audio.stationSeedTitle?.let { "Station started from $it" } ?: "Station picked for you", color = muted, fontSize = 12.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).testTag("station-seed"))
            if (server != null) ServerChipView(server, muted)
        }
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
            } else if (radio.showsVibeLock) StationOutlinedButton("Vibe lock", onVibe, Modifier.weight(1f).testTag("station-vibe-lock"), enabled = radio.vibeAvailable, symbol = Symbol.Lock)
            StationOutlinedButton("New mix", onNewMix, Modifier.weight(1f).testTag("new-mix"), symbol = Symbol.Refresh)
            StationOutlinedButton("End", onEnd, Modifier.weight(1f).testTag("end-station-card"), symbol = Symbol.Stop)
        }
    }
}

internal fun vibeMinutes(radio: RadioState) = ((radio.vibeUntil - System.currentTimeMillis()).coerceAtLeast(0) / 60000) + 1

@Composable
fun MoodChips(selected: String, onSelect: (String) -> Unit, enabled: Boolean = true) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(0.dp)) {
        // Each mood has its own colour: a dot when unselected, the whole chip when selected.
        val lightSurface = MaterialTheme.colorScheme.surface.luminance() > .5f
        OfflineRecommender.MOODS.keys.forEach { mood ->
            val colors = MoodColors.of(mood)
            val chosen = selected == mood
            FilterChip(chosen, onClick = { onSelect(mood) }, enabled = enabled, label = { Text(moodLabel(mood), fontWeight = if (chosen) FontWeight.Bold else FontWeight.Medium) },
                // Selected: a check, so the choice reads as chosen even for the pale neutral colour.
                leadingIcon = { if (chosen) RadioIcon(Symbol.Check, modifier = Modifier.size(16.dp)) else Box(Modifier.size(10.dp).background(Color(if (lightSurface) colors.field else colors.accent), CircleShape)) },
                shape = CircleShape,
                colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Color(colors.accent), selectedLabelColor = Color(colors.onAccent), selectedLeadingIconColor = Color(colors.onAccent)),
                modifier = Modifier.testTag("radio-mood-$mood"))
        }
    }
}

/** "Your feels" (checkpoint 9C): one chip per active tag feel. Tapping the selected feel returns to the mood station. */
@Composable
fun FeelChips(feels: List<dev.radio.mobile.data.LocalFeel>, selected: String?, onSelect: (String?) -> Unit, enabled: Boolean = true) {
    FlowRow(Modifier.testTag("radio-feels"), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(0.dp)) {
        feels.forEach { feel ->
            val chosen = selected == feel.feelId
            val accent = runCatching { Color(android.graphics.Color.parseColor(feel.color)) }.getOrDefault(MaterialTheme.colorScheme.primary)
            FilterChip(chosen, onClick = { onSelect(if (chosen) null else feel.feelId) }, enabled = enabled,
                label = { Text(feel.name, fontWeight = if (chosen) FontWeight.Bold else FontWeight.Medium) },
                leadingIcon = { if (chosen) RadioIcon(Symbol.Check, modifier = Modifier.size(16.dp)) else Box(Modifier.size(10.dp).background(accent, CircleShape)) },
                shape = CircleShape,
                // Feel colours are light tints, so the selected chip uses dark text.
                colors = FilterChipDefaults.filterChipColors(selectedContainerColor = accent, selectedLabelColor = Color.Black, selectedLeadingIconColor = Color.Black),
                modifier = Modifier.testTag("radio-feel-${feel.feelId}"))
        }
    }
}

@Composable
fun ExplorationPresets(value: Double, onSelect: (ExplorationPreset) -> Unit, enabled: Boolean = true) {
    val current = ExplorationPreset.nearest(value)
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().testTag("radio-presets")) {
        ExplorationPreset.entries.forEachIndexed { index, preset ->
            SegmentedButton(selected = current == preset, onClick = { onSelect(preset) }, enabled = enabled,
                shape = SegmentedButtonDefaults.itemShape(index, ExplorationPreset.entries.size),
                colors = SegmentedButtonDefaults.colors(activeContainerColor = MaterialTheme.colorScheme.primary, activeContentColor = MaterialTheme.colorScheme.onPrimary),
                // Four names share the row: the longest gets more room and all use a smaller label so none is cut off.
                modifier = Modifier.weight(if (preset == ExplorationPreset.Adventurous) 1.4f else 1f).testTag("radio-preset-${preset.name.lowercase()}"),
                contentPadding = PaddingValues(horizontal = 4.dp), icon = {}) { Text(preset.label, maxLines = 1, softWrap = false, style = MaterialTheme.typography.labelMedium) }
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
internal fun OriginLine(song: LocalSong, origin: StationOrigin?, color: Color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines: Int = 1, showArtist: Boolean = true) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(songSubtitle(song, showArtist = showArtist) + if (origin != null && origin != StationOrigin.Desktop && origin != StationOrigin.User) " · ${origin.label}" else "",
            Modifier.weight(1f, fill = false), maxLines = maxLines, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = color)
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
fun StartFromSongSheet(songs: List<LocalSong>, queueSize: Int, queueSource: String, onStart: (String) -> Unit, onDismiss: () -> Unit, playing: Boolean = true) {
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
                // A warm caution tone that suits both light and dark sheets.
                val darkSheet = MaterialTheme.colorScheme.surface.luminance() < .5f
                if (queueSize > 0) Row(Modifier.fillMaxWidth().background(if (darkSheet) Color(0xFF4A3310) else Color(0xFFFFF4E5), RoundedCornerShape(14.dp)).padding(12.dp).testTag("radio-seed-replace-warning")) {
                    Text("This replaces your current queue ($queueSize ${if (queueSize == 1) "song" else "songs"}${queueSource.takeIf { it.isNotBlank() }?.let { " from “$it”" } ?: ""}). ${if (playing) "The song playing now stops." else "Your paused song is replaced."}",
                        color = if (darkSheet) Color(0xFFFFD8A8) else Color(0xFF7A3E00), style = MaterialTheme.typography.bodySmall)
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
                    // The reason is the answer to the sheet's question, so it leads and is the largest text.
                    Text(originExplanation(origin), style = MaterialTheme.typography.titleLarge, modifier = Modifier.testTag("why-song-reason"))
                    Text(song.title, Modifier.padding(top = 6.dp), style = MaterialTheme.typography.titleSmall)
                    Text(songSubtitle(song), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            StationDetails(radio)
            Text("Similarity is a model's judgement, not a confidence score. Legacy desktop similarity is in Settings.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** What the on-air card calls the station: the feel, "<song or playlist> radio", or the mood. */
internal fun stationName(radio: RadioState, feel: dev.radio.mobile.data.LocalFeel?): String =
    feel?.name ?: radio.seed?.let { "${it.label.ifBlank { "Song" }} radio" } ?: moodLabel(radio.mood)

internal fun originExplanation(origin: StationOrigin?) = when (origin) {
    StationOrigin.Start -> "You started the station from this song."
    StationOrigin.Similar -> "Similar sound to the song before it."
    StationOrigin.Taste -> "Matches what you usually play and like."
    StationOrigin.Mood -> "Fits the station's mood and energy."
    StationOrigin.Explore -> "Something new near your usual music, picked to explore."
    StationOrigin.Wildcard -> "A wildcard: less similar on purpose, to keep things fresh."
    StationOrigin.Context -> "Fits right now, close in energy to the last song."
    StationOrigin.Vibe -> "Matches the locked vibe."
    StationOrigin.Feel -> "In your feel, or close to songs that are."
    StationOrigin.Seed -> "Sounds like the song or playlist you started this station from."
    StationOrigin.Desktop -> "Picked by your desktop."
    StationOrigin.User -> "You added this song."
    null -> "Radio picked this song on this phone."
}

/**
 * Now Playing song title: up to two lines; a title that would need more becomes one line that scrolls sideways, so a
 * long name never pushes the controls down. Scrolling follows the system's animation setting via basicMarquee.
 */
@Composable
fun SongTitle(title: String, modifier: Modifier = Modifier) {
    val style = MaterialTheme.typography.headlineSmall
    val measurer = androidx.compose.ui.text.rememberTextMeasurer()
    BoxWithConstraints(modifier) {
        // Decided from the width the title has now, never latched: a moment of narrow width (a screen transition)
        // must not leave a short title scrolling with its first letter faded.
        val width = constraints.maxWidth
        val scroll = remember(title, style, width) {
            width != androidx.compose.ui.unit.Constraints.Infinity && width > 0 &&
                measurer.measure(title, style, maxLines = 2, constraints = androidx.compose.ui.unit.Constraints(maxWidth = width)).hasVisualOverflow
        }
        // The scrolling title fades at its edges instead of cutting letters in half.
        if (scroll) Text(title, Modifier.fillMaxWidth().fadeEdges(20.dp).basicMarquee(iterations = Int.MAX_VALUE, spacing = androidx.compose.foundation.MarqueeSpacing.fractionOfContainer(1f / 4f))
            .testTag("song-title-scrolling"), style = style, maxLines = 1)
        else Text(title, Modifier.testTag("song-title"), style = style, maxLines = 2)
    }
}

/** Mini-player while on air: station surface, ON AIR dot, Pause and Skip. */
@Composable
fun StationMiniPlayer(song: LocalSong, status: String, playWhenReady: Boolean, canPlay: Boolean, canNext: Boolean, onOpen: () -> Unit, onToggle: () -> Unit, onNext: () -> Unit, mood: String = "neutral",
    positionMs: () -> Long = { 0L }, durationMs: Long? = null) =
    StationMiniPlayer(song.title, status, playWhenReady, canPlay, canNext, onOpen, onToggle, onNext, mood, positionMs = positionMs, durationMs = durationMs)

/** The on-air pill for any item: a song, or a talk clip ([symbol] Person, [nextLabel] "Skip talk"). */
@Composable
fun StationMiniPlayer(title: String, status: String, playWhenReady: Boolean, canPlay: Boolean, canNext: Boolean, onOpen: () -> Unit, onToggle: () -> Unit, onNext: () -> Unit,
    mood: String = "neutral", symbol: Symbol = Symbol.Music, nextLabel: String = "Next song",
    positionMs: () -> Long = { 0L }, durationMs: Long? = null) {
    val colors = LocalStationColors.current
    val drag = rememberDragOffset()
    val scope = rememberCoroutineScope()
    // The mood's dark tone fills the pill, so "on air" is recognisable from any tab.
    // A mood change fades the pill to its new colour.
    Surface(color = dev.radio.mobile.ui.theme.animatedColor(Color(dev.radio.mobile.ui.theme.MoodColors.of(mood).field), "mini-mood"), contentColor = Color.White, shape = RoundedCornerShape(18.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp)
            .miniPlayerGestures(onOpen, drag, scope)
            .progressLine(positionMs, durationMs, OnAirText, Color.White.copy(alpha = .18f)).testTag("mini-player")) {
        Row(Modifier.padding(start = 16.dp, end = 4.dp).heightIn(min = 68.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).background(Color(colors.tile), RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClickLabel = "Open player", onClick = onOpen), contentAlignment = Alignment.Center) {
                CompositionLocalProvider(LocalContentColor provides stationIconTint()) { RadioIcon(symbol, modifier = Modifier.size(20.dp)) }
            }
            Column(Modifier.weight(1f).testTag("mini-open").clickable(role = Role.Button, onClickLabel = "Open player", onClick = onOpen).padding(horizontal = 12.dp, vertical = 8.dp)) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(6.dp).background(OnAirDot, CircleShape)); Spacer(Modifier.width(6.dp))
                    Text(status, color = OnAirText, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            val press = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
            IconButton(onToggle, enabled = canPlay, modifier = Modifier.pressScale(press, .88f), interactionSource = press) { PlayPauseIcon(playWhenReady) }
            IconButton(onNext, enabled = canNext, modifier = Modifier.testTag("mini-next")) { RadioIcon(Symbol.Next, nextLabel) }
        }
    }
}

/** Shown in Now Playing while Radio chooses its first songs, so the old queue's controls never appear in between. */
@Composable
fun RadioTuningIn(mood: String, modifier: Modifier = Modifier) {
    val colors = LocalStationColors.current
    Column(modifier.padding(horizontal = 24.dp).testTag("radio-tuning-in"), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Box(Modifier.size(160.dp).background(Color(colors.tile), RoundedCornerShape(20.dp)), contentAlignment = Alignment.Center) {
            TuningDial(mood, Modifier.fillMaxSize().padding(14.dp))
        }
        Spacer(Modifier.height(24.dp))
        Text("Tuning in…", style = MaterialTheme.typography.titleLarge)
        Text("Picking songs for a ${moodLabel(mood)} station on this phone", Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
    }
}

/** "Folder · 1:23 / 4:56"; its own scope so a position tick recomposes only this line. */
@Composable
private fun StationElapsed(folder: String, positionMs: () -> Long, durationMs: Long?, color: Color) {
    Text("$folder · ${clock(positionMs())} / ${durationMs?.let(::clock) ?: "--:--"}",
        color = color, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
}

/**
 * The small filled pill under the song title (Like, Feel, Vibe, Sleep). One height and shape for every chip so a row of
 * them sits level; [onClick] null makes it plain information, [selected] non-null makes it a toggle.
 */
@Composable
fun PlayerPill(text: String, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, selected: Boolean? = null, enabled: Boolean = true,
               leading: (@Composable () -> Unit)? = null) {
    val on = selected == true
    val container = if (on) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceContainerHigh
    val content = if (on) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface
    val body: @Composable () -> Unit = {
        Row(Modifier.height(36.dp).padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            leading?.invoke()
            Text(text, style = MaterialTheme.typography.labelLarge, fontWeight = if (on) FontWeight.Bold else null, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
    when {
        onClick != null && selected != null -> Surface(selected, onClick, modifier, enabled = enabled, shape = CircleShape, color = container, contentColor = content, content = body)
        onClick != null -> Surface(onClick, modifier, enabled = enabled, shape = CircleShape, color = container, contentColor = content, content = body)
        else -> Surface(modifier, shape = CircleShape, color = container, contentColor = content, content = body)
    }
}

/** Radio Now Playing body below the top bar (station theme is provided by the caller). */
@Composable
fun RadioNowPlaying(
    audio: PlaybackState, song: LocalSong?, radio: RadioState, positionMs: () -> Long, liked: Boolean, feedbackReady: Boolean, canRead: Boolean, minimal: Boolean, compact: Boolean,
    options: List<ConnectedPick>, optionsSummary: String?,
    onScrub: (Float) -> Unit, onScrubFinished: () -> Unit, onLike: () -> Unit, onNotForMe: () -> Unit,
    onPrevious: () -> Unit, onToggle: () -> Unit, onNext: () -> Unit, onVibe: () -> Unit,
    onShowQueue: () -> Unit, onSleep: () -> Unit, onPlayNext: (ConnectedPick) -> Unit, onRescan: () -> Unit,
    journalError: String?, onRetryJournal: () -> Unit, modifier: Modifier = Modifier, onLyrics: (() -> Unit)? = null,
    /** The live sleep countdown, read only by its chip. */
    sleep: () -> dev.radio.mobile.playback.SleepTimerState = { audio.sleepTimer },
    /** Checkpoint 9B: the feel chip that opens the tag sheet. */
    feelChip: (@Composable () -> Unit)? = null,
    /** Checkpoint 13B: a talk clip on air. Same station screen; song-only controls step aside. */
    talk: dev.radio.mobile.playback.TalkNow? = null,
    onTranscript: (() -> Unit)? = null,
    /** The artwork size the screen has room for; falls back to the fixed sizes. */
    heroSize: androidx.compose.ui.unit.Dp? = null,
    creditedArtists: List<String> = emptyList(),
    onArtist: (String) -> Unit = {},
) {
    val locked = radio.vibeLocalId != null
    val talking = talk != null
    val colors = LocalStationColors.current
    // Artwork, title and chips sit at the top; the transport and Next row are anchored to the bottom (thumb reach), with
    // the spare height between them. Short screens scroll instead.
    BoxWithConstraints(modifier.fillMaxSize()) {
    val viewport = maxHeight
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp).testTag("radio-now-playing")) {
    Column(Modifier.fillMaxWidth().heightIn(min = viewport), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Spacer(Modifier.height(4.dp))
        val artSize by animateDpAsState(if (minimal || compact || audio.error != null) 160.dp else heroSize ?: 220.dp, Motion.standard(), label = "station-artwork")
        Box(Modifier.align(Alignment.CenterHorizontally).size(artSize)
            .background(Color(colors.tile), RoundedCornerShape(20.dp)), contentAlignment = Alignment.Center) {
            CompositionLocalProvider(LocalContentColor provides if (audio.error != null) OnAirText else stationIconTint()) {
                RadioIcon(if (audio.error != null) Symbol.Warning else if (talking) Symbol.Person else Symbol.Music, talk?.info?.typeLabel, Modifier.size(56.dp))
            }
        }
        if (talk != null) Column(Modifier.fillMaxWidth()) {
            Text(talk.info.title, Modifier.fillMaxWidth().testTag("talk-title"), style = MaterialTheme.typography.headlineSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(talkSpeakerLine(talk.info), Modifier.testTag("talk-speakers"), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        } else if (song != null) Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                SongTitle(song.title, Modifier.fillMaxWidth())
                if (creditedArtists.isNotEmpty()) ArtistCreditLink(song, creditedArtists, onArtist)
                OriginLine(song, audio.origins[song.id], maxLines = 2, showArtist = creditedArtists.isEmpty())
            }
            IconToggleButton(liked, { onLike() }, enabled = feedbackReady, modifier = Modifier.testTag("favorite-song")) {
                CompositionLocalProvider(LocalContentColor provides if (liked) OnAirText else LocalContentColor.current) { HeartIcon(liked, if (liked) "Liked" else "Like") }
            }
        }
        // Left-aligned under the title, like the standard player's Like/Feel row.
        val showFeel = !talking && feelChip != null
        if (showFeel || locked || audio.sleepTimer.active) FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp), itemVerticalAlignment = Alignment.CenterVertically) {
            if (showFeel) feelChip?.invoke()
            // Information, not a control: a plain pill rather than a disabled (greyed) chip.
            if (locked) PlayerPill("Vibe · ${radio.vibeSongsLeft} songs · ${vibeMinutes(radio)} min", Modifier.testTag("vibe-chip"),
                leading = { RadioIcon(Symbol.Lock, modifier = Modifier.size(14.dp)) })
            if (audio.sleepTimer.active) PlayerPill("Sleep · ${sleepLabel(sleep())}", Modifier.testTag("sleep-countdown"), onClick = onSleep,
                leading = { RadioIcon(Symbol.Clock, modifier = Modifier.size(14.dp)) })
        }
        // The "why Vibe lock is off" note gets its own line so it never wraps beside a chip.
        // Without a desktop catalog Vibe lock can never work, so it stays out of sight rather than explaining itself on every song.
        if (!minimal && !locked && !talking && !radio.vibeAvailable && radio.showsVibeLock) Text(if (radio.legacyVectorTracks > 0 && !radio.legacyVectorsEnabled) "Vibe lock needs desktop similarity. Turn it on in Settings → Radio." else radio.vibeReason ?: "Vibe lock needs a matched song with recommendation data.",
            Modifier.fillMaxWidth(), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
        Spacer(Modifier.weight(1f))
        PlaybackProgress(positionMs, audio.durationMs, audio.seekable, onScrub, onScrubFinished, timeColor = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.fillMaxWidth().testTag("player-transport"), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            // Disabled controls dim into the surface; Material's default disabled fill is lighter here and read as "selected".
            val disabledFill = Color(colors.panel).copy(alpha = .45f)
            val disabledIcon = Color.White.copy(alpha = .32f)
            val tonal = IconButtonDefaults.filledTonalIconButtonColors(containerColor = Color(colors.panel), contentColor = Color.White,
                disabledContainerColor = disabledFill, disabledContentColor = disabledIcon)
            val playPress = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
            val previousPress = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
            val nextPress = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
            // Paused, the main button rounds into a pill; playing, it firms into the squircle.
            val corner by animateDpAsState(if (audio.playWhenReady) 30.dp else 40.dp, Motion.standard(), label = "play-shape")
            // Talk is never rated, so Not for me and Vibe lock keep their space but are not shown.
            if (talking) Spacer(Modifier.size(48.dp)) else FilledTonalIconButton(onNotForMe, enabled = feedbackReady && audio.canNext, modifier = Modifier.size(48.dp).testTag("not-for-me"), shape = RadioShapes.Soft, colors = tonal) { RadioIcon(Symbol.Block, "Not for me (dislike and skip)") }
            FilledTonalIconButton(onPrevious, enabled = audio.canPrevious, modifier = Modifier.size(52.dp).pressScale(previousPress, .9f), colors = tonal, interactionSource = previousPress) { RadioIcon(Symbol.Previous, "Previous song") }
            FilledIconButton(onToggle, enabled = canRead, modifier = Modifier.size(width = if (compact) 76.dp else 92.dp, height = if (compact) 60.dp else 76.dp).pressScale(playPress),
                shape = RoundedCornerShape(corner), interactionSource = playPress,
                colors = IconButtonDefaults.filledIconButtonColors(containerColor = Color.White, contentColor = Color(colors.base))) {
                PlayPauseIcon(audio.playWhenReady, Modifier.size(32.dp))
            }
            FilledTonalIconButton(onNext, enabled = audio.canNext, modifier = Modifier.size(52.dp).pressScale(nextPress, .9f).testTag("player-next"), colors = tonal, interactionSource = nextPress) { RadioIcon(Symbol.Next, if (talking) "Skip talk" else "Next song") }
            val moodColors = MoodColors.of(radio.mood)
            if (talking || !locked && !radio.showsVibeLock) Spacer(Modifier.size(48.dp)) else FilledTonalIconToggleButton(locked, { onVibe() }, enabled = locked || radio.vibeAvailable, modifier = Modifier.size(48.dp).testTag("vibe-lock"), shape = RadioShapes.Soft,
                colors = IconButtonDefaults.filledTonalIconToggleButtonColors(containerColor = Color(colors.panel), contentColor = Color.White,
                    disabledContainerColor = disabledFill, disabledContentColor = disabledIcon,
                    checkedContainerColor = Color(moodColors.accent), checkedContentColor = Color(moodColors.onAccent))) {
                RadioIcon(Symbol.Lock, if (locked) "Release vibe lock" else "Vibe lock")
            }
        }
        AnimatedVisibility(!minimal, enter = expandVertically(Motion.standard()) + fadeIn(Motion.standard()), exit = shrinkVertically(Motion.quick()) + fadeOut(Motion.quick())) {
          Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            // During talk the clip is outside the play order; next is the song it introduces.
            val nextSong = if (talk != null) audio.queue.firstOrNull { it.id == talk.nextId } else audio.playOrder.indexOf(audio.currentId).takeIf { it >= 0 }?.let { audio.playOrder.getOrNull(it + 1) }?.let { id -> audio.queue.firstOrNull { it.id == id } }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Row(Modifier.weight(1f).background(Color(colors.panel), CircleShape).clip(CircleShape).clickable(role = Role.Button, onClickLabel = "Show queue", onClick = onShowQueue)
                    .heightIn(min = 52.dp).padding(horizontal = 18.dp).testTag("next-up"), verticalAlignment = Alignment.CenterVertically) {
                    Text("NEXT", color = Color(colors.progress), fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
                    Text(nextSong?.title ?: "Radio is picking…", Modifier.weight(1f).padding(horizontal = 12.dp),
                        maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                    RadioIcon(Symbol.Forward, modifier = Modifier.size(18.dp))
                }
                // Lyrics is one tap away here; the overflow menu keeps its own Lyrics item.
                if (talking && onTranscript != null) FilledTonalButton(onTranscript, Modifier.heightIn(min = 52.dp).testTag("open-transcript-button"), shape = RadioShapes.Soft,
                    colors = ButtonDefaults.filledTonalButtonColors(containerColor = Color(colors.tile), contentColor = Color.White)) { Text("Transcript") }
                else if (!talking && onLyrics != null) FilledTonalButton(onLyrics, Modifier.heightIn(min = 52.dp).testTag("open-lyrics-button"), shape = RadioShapes.Soft,
                    colors = ButtonDefaults.filledTonalButtonColors(containerColor = Color(colors.tile), contentColor = Color.White)) { Text("Lyrics") }
            }
            if (!talking && options.isNotEmpty()) UpNextOptionsCard(options, onPlayNext, summary = optionsSummary)
          }
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
    }
}


/** Checkpoint 10: a stable FM-style frequency for a station (88.1–107.9, odd tenths), shown as the dial settles. */
fun stationFrequency(station: String): Float {
    val channels = ((107.9f - 88.1f) / 0.2f).toInt() + 1 // 100 channels
    val index = Math.floorMod(station.hashCode() * 31 + 7, channels)
    return (881 + index * 2) / 10f
}

/**
 * Checkpoint 10: the station "tuning" animation. The needle sweeps across the band and settles on the station with a
 * small spring overshoot while the frequency counts in. Compose scales it with the animator setting, so reduced motion
 * shows the settled dial at once.
 */
@Composable
fun TuningDial(station: String, modifier: Modifier = Modifier) {
    val accent = Color(MoodColors.of(station.substringBefore(':')).accent)
    val tick = LocalContentColor.current.copy(alpha = .45f)
    val target = stationFrequency(station)
    val needle = remember(station) { androidx.compose.animation.core.Animatable(87.5f) }
    LaunchedEffect(station) {
        needle.snapTo(87.5f)
        needle.animateTo(108f, androidx.compose.animation.core.tween(420, easing = androidx.compose.animation.core.FastOutSlowInEasing))
        needle.animateTo(target, androidx.compose.animation.core.spring(dampingRatio = .45f, stiffness = 120f))
    }
    Column(modifier.testTag("tuning-dial").semantics { this.contentDescription = "Tuning to ${"%.1f".format(target)}" },
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Text("%.1f".format(needle.value.coerceIn(87.5f, 108f)), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        androidx.compose.foundation.Canvas(Modifier.fillMaxWidth().height(36.dp)) {
            val span = 108f - 87.5f
            for (i in 0..40) {
                val x = size.width * i / 40f
                val major = i % 5 == 0
                drawLine(tick, androidx.compose.ui.geometry.Offset(x, size.height * (if (major) .25f else .5f)), androidx.compose.ui.geometry.Offset(x, size.height),
                    strokeWidth = if (major) 2.dp.toPx() else 1.dp.toPx())
            }
            val x = size.width * ((needle.value - 87.5f) / span).coerceIn(0f, 1f)
            drawLine(accent, androidx.compose.ui.geometry.Offset(x, 0f), androidx.compose.ui.geometry.Offset(x, size.height), strokeWidth = 3.dp.toPx(), cap = androidx.compose.ui.graphics.StrokeCap.Round)
        }
    }
}
