package dev.radio.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.recommendation.ConnectedPick
import dev.radio.mobile.recommendation.ConnectedPolicy
import dev.radio.mobile.recommendation.ConnectedStatus
import dev.radio.mobile.recommendation.ConnectedTuning
import dev.radio.mobile.recommendation.ConnectedUi
import java.text.DateFormat
import java.util.Date

// Checkpoint 4.2 vocabulary: everything the desktop contributes is "Desktop" / "Desktop picks".

internal fun slotLabel(slot: String) = when (slot) {
    "explore" -> "Explore"
    "wildcard" -> "Wildcard"
    "context" -> "Fits now"
    else -> slot.replaceFirstChar { it.uppercase() }
}

/**
 * Choices from the desktop for what could play after the current song. Ignoring the card changes nothing; tapping
 * "Play next" inserts the song right after the current one as an ordinary manual queue entry. Callers only compose
 * this when [options] is non-empty, so there is no empty or placeholder state to show.
 */
@Composable
fun UpNextOptionsCard(options: List<ConnectedPick>, onPlayNext: (ConnectedPick) -> Unit, modifier: Modifier = Modifier, summary: String? = null) {
    if (options.isEmpty()) return
    Card(modifier.fillMaxWidth().testTag("up-next-options"), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Desktop picks", Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
                Text("Optional · queue plays on", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            summary?.let { Text(it, Modifier.padding(top = 2.dp, bottom = 4.dp).testTag("up-next-summary"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            // Each pick is its own tile: song first, then where it is from, then why the desktop chose it.
            Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                options.forEach { pick ->
                    Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerHighest, RoundedCornerShape(14.dp))
                        .padding(start = 12.dp, end = 8.dp, top = 10.dp, bottom = 10.dp).testTag("up-next-option-${pick.song.id}"), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(pick.song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                            Text(songSubtitle(pick.song), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text(slotLabel(pick.slot) + if (pick.reason.isNotBlank()) " · " + pick.reason else "", maxLines = 2, overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                        }
                        OutlinedButton(onClick = { onPlayNext(pick) }, modifier = Modifier.padding(start = 8.dp).testTag("play-next-${pick.song.id}")) { Text("Play next") }
                    }
                }
            }
        }
    }
}

/** Small marker for queue rows that came from the desktop. */
@Composable
fun ServerAiMarker(modifier: Modifier = Modifier) = OriginTag("Desktop", modifier)

/** Settings, Server: status, permission and the last result. The same switch also lives in Radio, Tune. */
@Composable
fun ConnectedRadioSection(status: ConnectedStatus, paired: Boolean, onToggle: (Boolean) -> Unit, onCheck: () -> Unit, modifier: Modifier = Modifier, summary: String? = null) {
    Column(modifier.fillMaxWidth().padding(top = 16.dp).testTag("connected-radio")) {
        Text("Desktop picks", style = MaterialTheme.typography.titleSmall)
        Text("While Radio plays, ask your desktop for similar songs and a few picks for what could play next. Off by default. Radio works the same without it.",
            Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(status.enabled, role = Role.Switch, onValueChange = onToggle).testTag("connected-radio-toggle"), verticalAlignment = Alignment.CenterVertically) {
            Text("Use Desktop picks", Modifier.weight(1f))
            Switch(status.enabled, onCheckedChange = null)
        }
        Text(permissionText(status, paired), Modifier.testTag("connected-radio-permission"), style = MaterialTheme.typography.bodySmall)
        if (status.enabled) {
            Text("Last result: " + lastResult(status), Modifier.padding(top = 4.dp).testTag("connected-radio-result"),
                style = MaterialTheme.typography.bodySmall, color = if (status.lastOk == false) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
            summary?.let { Text("Current set: $it", Modifier.padding(top = 2.dp).testTag("connected-radio-summary"), style = MaterialTheme.typography.bodySmall) }
            Text(if (status.tunable == false) "This desktop doesn't support tuning yet." else "Tune Desktop picks from Radio, Tune.", Modifier.padding(top = 2.dp),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = onCheck, enabled = paired, modifier = Modifier.testTag("connected-radio-check")) { Text("Check permission") }
        }
    }
}

private fun permissionText(status: ConnectedStatus, paired: Boolean) = when {
    !paired -> "Pair this phone to use Desktop picks."
    !status.enabled -> "Off. Turn on to ask your desktop for picks."
    status.permitted == false -> "Not permitted on desktop. Allow Desktop picks for this phone in the desktop's Server Settings."
    status.permitted == true -> "Permitted on desktop"
    else -> "Permission is checked when the next song starts."
}

private fun lastResult(status: ConnectedStatus): String {
    val at = status.lastAt.takeIf { it > 0 }?.let { " · " + DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(it)) }.orEmpty()
    return if (status.pendingAnchor != null) "waiting for the desktop…" else status.lastMessage + at
}

private enum class DesktopState(val dot: Color?, val line: String) {
    Unpaired(null, "No desktop paired"),
    Off(null, "Off"),
    NotAllowed(Color(0xFFEA580C), "Not allowed on desktop"),
    Failed(Color(0xFFDC2626), "Desktop didn't answer"),
    Asking(Color(0xFF2563EB), "Asking your desktop…"),
    Ready(Color(0xFF16A34A), "Paired desktop · allowed"),
}

/** Tune sheet, DESKTOP PICKS: the switch, the connection state and (when on) the 4.1 tuning. */
@Composable
fun DesktopPicksSection(connected: ConnectedUi, paired: Boolean, tuning: ConnectedTuning, onToggle: (Boolean) -> Unit, onChange: (ConnectedTuning) -> Unit, onPair: () -> Unit, modifier: Modifier = Modifier) {
    val status = connected.status
    val state = when {
        !paired -> DesktopState.Unpaired
        !status.enabled -> DesktopState.Off
        status.permitted == false -> DesktopState.NotAllowed
        status.pendingAnchor != null -> DesktopState.Asking
        status.lastOk == false -> DesktopState.Failed
        else -> DesktopState.Ready
    }
    Column(modifier.fillMaxWidth().padding(top = 16.dp).testTag("desktop-picks")) {
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(status.enabled, enabled = paired, role = Role.Switch, onValueChange = onToggle).testTag("desktop-picks-toggle"),
            verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("DESKTOP PICKS", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    state.dot?.let { Box(Modifier.size(7.dp).background(it, CircleShape)); Spacer(Modifier.width(6.dp)) }
                    val at = if (state == DesktopState.Failed) status.lastAt.takeIf { it > 0 }?.let { " · " + DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(it)) }.orEmpty() else ""
                    Text(state.line + at, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("desktop-picks-state"))
                }
            }
            Switch(status.enabled, onCheckedChange = null, enabled = paired)
        }
        when (state) {
            DesktopState.Unpaired -> {
                Text("Pair this phone with your desktop to get extra picks while Radio plays.", style = MaterialTheme.typography.bodySmall)
                TextButton(onPair, modifier = Modifier.testTag("desktop-picks-pair")) { Text("Pair in Settings › Server") }
            }
            DesktopState.NotAllowed -> Text("On your desktop, open Server Settings and allow Desktop picks for this phone.", style = MaterialTheme.typography.bodySmall)
            DesktopState.Failed -> Text("Radio is picking on this phone for now and will ask again when the next song starts.", style = MaterialTheme.typography.bodySmall)
            else -> Unit
        }
        if (status.enabled && paired) ConnectedTuningSection(tuning, status.tunable, connected.set?.applied?.pockets == "inactive", onChange,
            lastUsed = connected.set?.let(ConnectedPolicy::summary))
    }
}

/**
 * Tune sheet, Desktop picks tuning (checkpoint 4.1 contract): phone-only overrides sent with desktop requests. Nothing
 * here changes desktop settings or offline Radio. Each control writes immediately; a change applies from the next song.
 */
@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
fun ConnectedTuningSection(tuning: ConnectedTuning, tunable: Boolean?, pocketsInactive: Boolean, onChange: (ConnectedTuning) -> Unit, modifier: Modifier = Modifier, lastUsed: String? = null) {
    val enabled = tunable != false
    var advanced by remember { mutableStateOf(false) }
    Column(modifier.fillMaxWidth().padding(top = 8.dp).testTag("connected-tuning"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(if (enabled) "Only for picks from your desktop. Your desktop's own Radio is unchanged." else "Your desktop app is older and uses its own defaults. Update it to set picks, energy and more here.",
            Modifier.testTag(if (enabled) "connected-tuning-note" else "connected-tuning-unsupported"),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

        Text("Picks per song", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        val picks = tuning.picks ?: ConnectedTuning.MIN_PICKS
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            val values = (ConnectedTuning.MIN_PICKS..ConnectedTuning.MAX_PICKS).toList()
            values.forEachIndexed { index, n ->
                SegmentedButton(picks == n, onClick = { onChange(tuning.copy(picks = n.takeIf { it != ConnectedTuning.MIN_PICKS })) }, enabled = enabled,
                    shape = SegmentedButtonDefaults.itemShape(index, values.size), modifier = Modifier.testTag("connected-picks-$n"), icon = {}) { Text("$n") }
            }
        }

        Text("Added to the queue", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            val values = listOf<Int?>(null) + (ConnectedTuning.MIN_SIMILAR..ConnectedTuning.MAX_SIMILAR).toList()
            values.forEachIndexed { index, n ->
                SegmentedButton(tuning.similar == n, onClick = { onChange(tuning.copy(similar = n)) }, enabled = enabled,
                    shape = SegmentedButtonDefaults.itemShape(index, values.size), modifier = Modifier.testTag(if (n == null) "connected-similar-auto" else "connected-similar-$n"),
                    icon = {}) { Text(n?.toString() ?: "Auto") }
            }
        }
        Text(tuning.similar?.let { "$it similar songs join the queue · ${picks - it} appear as Desktop picks you can choose." }
            ?: "Your desktop sets the split from Exploration; the rest of the $picks appear as Desktop picks.",
            Modifier.testTag("connected-mix"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

        Text("Energy", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(null to "Off", "auto" to "Follow recent", "rising" to "Rising", "steady" to "Steady", "falling" to "Falling").forEach { (value, label) ->
                FilterChip(tuning.energy == value, enabled = enabled, onClick = { onChange(tuning.copy(energy = value)) }, label = { Text(label) },
                    modifier = Modifier.testTag("connected-energy-${value ?: "off"}"))
            }
        }

        Surface(onClick = { advanced = !advanced }, shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surfaceContainer,
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp).testTag("connected-advanced")) {
            Row(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("More desktop options", style = MaterialTheme.typography.bodyMedium)
                    Text("Variety · AI ranking · Taste pockets", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                RadioIcon(if (advanced) Symbol.Up else Symbol.Down, if (advanced) "Hide" else "Show")
            }
        }
        if (advanced) Column(Modifier.padding(top = 4.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Variety", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                Text(tuning.variety?.let { "${Math.round(it * 100)}%" } ?: "Desktop default", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            var variety by remember(tuning.variety) { mutableFloatStateOf((tuning.variety ?: 0.0).toFloat()) }
            Slider(variety, onValueChange = { variety = it }, enabled = enabled,
                onValueChangeFinished = { onChange(tuning.copy(variety = variety.toDouble().takeIf { it > 0.005 })) }, modifier = Modifier.testTag("connected-variety"))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Steady", style = MaterialTheme.typography.bodySmall); Text("Varied", style = MaterialTheme.typography.bodySmall)
            }
            Text("AI ranking", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                val values = listOf(null to "Desktop default", "on" to "On", "off" to "Off")
                values.forEachIndexed { index, (value, label) ->
                    SegmentedButton(tuning.reranking == value, onClick = { onChange(tuning.copy(reranking = value)) }, enabled = enabled,
                        shape = SegmentedButtonDefaults.itemShape(index, values.size), modifier = Modifier.testTag("connected-ranking-${value ?: "default"}"), icon = {}) { Text(label, maxLines = 1) }
                }
            }
            Text("Off answers faster without the desktop's language model.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(top = 4.dp)
                .toggleable(tuning.pockets == true, enabled = enabled, role = Role.Switch, onValueChange = { onChange(tuning.copy(pockets = it.takeIf { on -> on })) })
                .testTag("connected-pockets"), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Taste pockets")
                    Text(if (tuning.pockets == true && pocketsInactive) "Needs more listening history on the desktop. It turns on by itself." else "Lean toward groups of songs you return to",
                        Modifier.testTag("connected-pockets-note"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(tuning.pockets == true, onCheckedChange = null, enabled = enabled)
            }
            lastUsed?.let {
                Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer, RoundedCornerShape(14.dp)).padding(12.dp)) {
                    Text("LAST PICKS USED", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag("connected-last-used"))
                }
            }
        }
        Text("Vibe lock keeps 4 similar + 1 pick.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = { onChange(ConnectedTuning()) }, enabled = !tuning.isDefault, modifier = Modifier.fillMaxWidth().testTag("connected-tuning-reset")) { Text("Use desktop defaults") }
    }
}
