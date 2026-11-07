package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.recommendation.ConnectedPick
import dev.radio.mobile.recommendation.ConnectedStatus
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
fun UpNextOptionsCard(options: List<ConnectedPick>, onPlayNext: (ConnectedPick) -> Unit, modifier: Modifier = Modifier) {
    if (options.isEmpty()) return
    Card(modifier.fillMaxWidth().testTag("up-next-options")) {
        Column(Modifier.padding(16.dp)) {
            Text("Up next options", style = MaterialTheme.typography.titleSmall)
            Text("From your desktop. Ignore these and your queue plays on.", Modifier.padding(top = 2.dp, bottom = 4.dp),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
fun ConnectedRadioSection(status: ConnectedStatus, paired: Boolean, onToggle: (Boolean) -> Unit, onCheck: () -> Unit, modifier: Modifier = Modifier) {
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
            TextButton(onClick = onCheck, enabled = paired, modifier = Modifier.testTag("connected-radio-check")) { Text("Check permission") }
        }
    }
}
