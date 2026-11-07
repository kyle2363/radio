package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.recommendation.ConnectedPick
import dev.radio.mobile.recommendation.ConnectedStatus
import dev.radio.mobile.recommendation.ConnectedTuning
import java.text.DateFormat
import java.util.Date

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
    Card(modifier.fillMaxWidth().testTag("up-next-options")) {
        Column(Modifier.padding(16.dp)) {
            Text("Up next options", style = MaterialTheme.typography.titleSmall)
            Text("From your desktop. Ignore these and your queue plays on.", Modifier.padding(top = 2.dp, bottom = 4.dp),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            summary?.let { Text(it, Modifier.padding(bottom = 4.dp).testTag("up-next-summary"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            options.forEach { pick ->
                Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(vertical = 4.dp).testTag("up-next-option-${pick.song.id}"), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(pick.song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                        Text(songSubtitle(pick.song), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(slotLabel(pick.slot) + if (pick.reason.isNotBlank()) " · " + pick.reason else "", maxLines = 2, overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                    }
                    TextButton(onClick = { onPlayNext(pick) }, modifier = Modifier.testTag("play-next-${pick.song.id}")) { Text("Play next") }
                }
            }
        }
    }
}

/** Small marker for queue rows that came from the desktop's similar picks. */
@Composable
fun ServerAiMarker(modifier: Modifier = Modifier) {
    Text("Server AI", modifier, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
}

/** Settings, Server: toggle, permission state and the last result/reason. */
@Composable
fun ConnectedRadioSection(status: ConnectedStatus, paired: Boolean, onToggle: (Boolean) -> Unit, onCheck: () -> Unit, modifier: Modifier = Modifier, summary: String? = null) {
    val permission = when {
        !paired -> "Pair this phone to use Connected Radio."
        !status.enabled -> "Off. Turn on to ask your desktop for picks."
        status.permitted == false -> "Not permitted on desktop. Allow Connected Radio for this phone in the desktop's Server Settings."
        status.permitted == true -> "Permitted on desktop"
        else -> "Permission is checked when the next song starts."
    }
    Column(modifier.fillMaxWidth().padding(top = 16.dp).testTag("connected-radio")) {
        Text("Connected Radio", style = MaterialTheme.typography.titleSmall)
        Text("While Radio plays, ask your desktop for similar songs and a few options for what could play next. Off by default. Radio works the same without it.",
            Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(status.enabled, role = Role.Switch, onValueChange = onToggle).testTag("connected-radio-toggle"), verticalAlignment = Alignment.CenterVertically) {
            Text("Use Connected Radio", Modifier.weight(1f))
            Switch(status.enabled, onCheckedChange = null)
        }
        Text(permission, Modifier.testTag("connected-radio-permission"), style = MaterialTheme.typography.bodySmall)
        if (status.enabled) {
            val at = status.lastAt.takeIf { it > 0 }?.let { " · " + DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(it)) }.orEmpty()
            Text("Last result: " + (if (status.pendingAnchor != null) "waiting for the desktop…" else status.lastMessage + at), Modifier.padding(top = 4.dp).testTag("connected-radio-result"),
                style = MaterialTheme.typography.bodySmall, color = if (status.lastOk == false) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
            summary?.let { Text("Current set: $it", Modifier.padding(top = 2.dp).testTag("connected-radio-summary"), style = MaterialTheme.typography.bodySmall) }
            if (status.tunable == false) Text("This desktop doesn't support tuning yet.", Modifier.padding(top = 2.dp), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            else Text("Tune Connected Radio from Radio, Tune.", Modifier.padding(top = 2.dp), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = onCheck, enabled = paired, modifier = Modifier.testTag("connected-radio-check")) { Text("Check permission") }
        }
    }
}

/**
 * Tune sheet, Server section (checkpoint 4.1): phone-only overrides sent with Connected Radio requests. Nothing here
 * changes desktop settings or offline Radio. Each control writes immediately; a change applies from the next song.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ConnectedTuningSection(tuning: ConnectedTuning, tunable: Boolean?, pocketsInactive: Boolean, onChange: (ConnectedTuning) -> Unit, modifier: Modifier = Modifier) {
    val enabled = tunable != false
    var advanced by remember { mutableStateOf(tuning.reranking != null || tuning.pockets != null) }
    Column(modifier.fillMaxWidth().padding(top = 20.dp).testTag("connected-tuning")) {
        Text("Server", style = MaterialTheme.typography.titleSmall)
        Text(if (enabled) "Only for picks from your desktop. Your desktop's own Radio is unchanged." else "This desktop doesn't support tuning yet. Update the desktop app to use these settings.",
            Modifier.padding(top = 4.dp).testTag(if (enabled) "connected-tuning-note" else "connected-tuning-unsupported"),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

        Text("Variety", Modifier.padding(top = 16.dp), style = MaterialTheme.typography.labelLarge)
        var variety by remember(tuning.variety) { mutableFloatStateOf((tuning.variety ?: 0.0).toFloat()) }
        Slider(variety, onValueChange = { variety = it }, enabled = enabled,
            onValueChangeFinished = { onChange(tuning.copy(variety = variety.toDouble().takeIf { it > 0.005 })) }, modifier = Modifier.testTag("connected-variety"))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("Steady", style = MaterialTheme.typography.bodySmall); Text("Varied", style = MaterialTheme.typography.bodySmall)
        }

        Text("Picks per song", Modifier.padding(top = 12.dp, bottom = 4.dp), style = MaterialTheme.typography.labelLarge)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            (ConnectedTuning.MIN_PICKS..ConnectedTuning.MAX_PICKS).forEach { n ->
                FilterChip((tuning.picks ?: ConnectedTuning.MIN_PICKS) == n, enabled = enabled, onClick = { onChange(tuning.copy(picks = n.takeIf { it != ConnectedTuning.MIN_PICKS })) },
                    label = { Text("$n") }, modifier = Modifier.testTag("connected-picks-$n"))
            }
        }

        Text("Similar picks", Modifier.padding(top = 12.dp, bottom = 4.dp), style = MaterialTheme.typography.labelLarge)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(tuning.similar == null, enabled = enabled, onClick = { onChange(tuning.copy(similar = null)) }, label = { Text("Server decides") },
                modifier = Modifier.testTag("connected-similar-auto"))
            (ConnectedTuning.MIN_SIMILAR..ConnectedTuning.MAX_SIMILAR).forEach { n ->
                FilterChip(tuning.similar == n, enabled = enabled, onClick = { onChange(tuning.copy(similar = n)) }, label = { Text("$n") }, modifier = Modifier.testTag("connected-similar-$n"))
            }
        }
        val picks = tuning.picks ?: ConnectedTuning.MIN_PICKS
        Text(tuning.similar?.let { "Up to $it similar songs join the queue; ${picks - it} more appear as options." } ?: "The desktop picks the mix from Exploration; the rest of the $picks appear as options.",
            Modifier.padding(top = 4.dp).testTag("connected-mix"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

        Text("Energy", Modifier.padding(top = 12.dp, bottom = 4.dp), style = MaterialTheme.typography.labelLarge)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(null to "Off", "auto" to "Follow recent", "rising" to "Rising", "steady" to "Steady", "falling" to "Falling").forEach { (value, label) ->
                FilterChip(tuning.energy == value, enabled = enabled, onClick = { onChange(tuning.copy(energy = value)) }, label = { Text(label) },
                    modifier = Modifier.testTag("connected-energy-${value ?: "off"}"))
            }
        }

        TextButton(onClick = { advanced = !advanced }, modifier = Modifier.padding(top = 4.dp).testTag("connected-advanced")) { Text(if (advanced) "Hide advanced" else "Advanced") }
        if (advanced) {
            Text("LLM ranking", Modifier.padding(bottom = 4.dp), style = MaterialTheme.typography.labelLarge)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(null to "Desktop default", "on" to "On", "off" to "Off").forEach { (value, label) ->
                    FilterChip(tuning.reranking == value, enabled = enabled, onClick = { onChange(tuning.copy(reranking = value)) }, label = { Text(label) },
                        modifier = Modifier.testTag("connected-ranking-${value ?: "default"}"))
                }
            }
            Text("Off answers faster without the desktop's language model.", Modifier.padding(top = 4.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(top = 4.dp)
                .toggleable(tuning.pockets == true, enabled = enabled, role = Role.Switch, onValueChange = { onChange(tuning.copy(pockets = it.takeIf { on -> on })) })
                .testTag("connected-pockets"), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Taste pockets")
                    Text(if (tuning.pockets == true && pocketsInactive) "Needs more listening history on the desktop" else "Lean toward groups of songs you return to",
                        Modifier.testTag("connected-pockets-note"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(tuning.pockets == true, onCheckedChange = null, enabled = enabled)
            }
        }
        Text("Applies from the next song. Vibe lock keeps 4 similar + 1 option.", Modifier.padding(top = 8.dp),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        TextButton(onClick = { onChange(ConnectedTuning()) }, enabled = !tuning.isDefault, modifier = Modifier.testTag("connected-tuning-reset")) { Text("Use desktop defaults") }
    }
}
