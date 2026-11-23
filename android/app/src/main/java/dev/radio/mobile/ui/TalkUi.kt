package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.radio.mobile.recommendation.ConnectedUi
import dev.radio.mobile.recommendation.TalkClipInfo
import dev.radio.mobile.recommendation.TalkSettings
import dev.radio.mobile.recommendation.TalkVolume

/**
 * Radio -> Tune, Talk (checkpoint 13B). Off by default and separate from the desktop's own talk switch. Anything left
 * on "Desktop" follows the desktop's current value, so desktop tuning reaches the phone with no phone change.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun TalkSection(connected: ConnectedUi, paired: Boolean, settings: TalkSettings, status: String, onChange: (TalkSettings) -> Unit, modifier: Modifier = Modifier) {
    val capability = connected.status.talk
    // Talk needs the paired desktop for the voices, not Desktop picks: the phone's own Radio picks the songs.
    val usable = paired
    Column(modifier.fillMaxWidth().padding(top = 16.dp).testTag("talk-section"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(settings.enabled, enabled = usable, role = Role.Switch,
            onValueChange = { onChange(settings.copy(enabled = it)) }).testTag("talk-toggle"), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("TALK", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(talkLine(usable, settings, capability?.let { if (it.available) null else it.message }, status),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("talk-state"))
            }
            Switch(settings.enabled, onCheckedChange = null, enabled = usable)
        }
        if (!usable) {
            Text("Talk comes from your desktop's DJ. Pair this phone to hear it.", style = MaterialTheme.typography.bodySmall)
            return@Column
        }
        // Controls show only when the desktop advertises talk.
        if (capability == null || !settings.enabled) return@Column

        Text("How often", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            (listOf<Int?>(null) + (TalkSettings.MIN_FREQUENCY..TalkSettings.MAX_FREQUENCY)).forEach { n ->
                FilterChip(settings.frequency == n, onClick = { onChange(settings.copy(frequency = n)) }, label = { Text(n?.let { "Every $it" } ?: "Desktop") },
                    modifier = Modifier.testTag("talk-frequency-${n ?: "default"}"))
            }
        }
        TriChoice("Callers", settings.callers, "talk-callers") { onChange(settings.copy(callers = it)) }
        TriChoice("Monologues", settings.monologues, "talk-monologues") { onChange(settings.copy(monologues = it)) }
        TriChoice("Guests", settings.guests, "talk-guests") { onChange(settings.copy(guests = it)) }

        Text("Songs per guest set", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            (listOf<Int?>(null) + (TalkSettings.MIN_GUEST_SONGS..TalkSettings.MAX_GUEST_SONGS)).forEach { n ->
                FilterChip(settings.guestSongs == n, onClick = { onChange(settings.copy(guestSongs = n)) }, label = { Text(n?.toString() ?: "Desktop") },
                    modifier = Modifier.testTag("talk-guest-songs-${n ?: "default"}"))
            }
        }

        Text("Talk volume", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            TalkVolume.entries.forEachIndexed { index, volume ->
                SegmentedButton(settings.volume == volume, onClick = { onChange(settings.copy(volume = volume)) },
                    shape = SegmentedButtonDefaults.itemShape(index, TalkVolume.entries.size), modifier = Modifier.testTag("talk-volume-${volume.name.lowercase()}"), icon = {}) { Text(volume.label) }
            }
        }
        Text("Talk plays in Radio and feel stations, with or without Desktop picks, while your desktop is reachable. Music never waits for it; a clip that isn't ready in time is skipped.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = { onChange(settings.desktopDefaults()) }, enabled = !settings.usesDesktopDefaults,
            modifier = Modifier.fillMaxWidth().testTag("talk-reset")) { Text("Use desktop defaults") }
    }
}

private fun talkLine(usable: Boolean, settings: TalkSettings, unavailable: String?, status: String) = when {
    !usable -> "Off"
    !settings.enabled -> "Off · the desktop's DJ, callers and guests between songs"
    unavailable != null -> "Unavailable: $unavailable. Music continues."
    else -> status
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TriChoice(label: String, value: Boolean?, tag: String, onChange: (Boolean?) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        val values = listOf<Boolean?>(null, true, false)
        SingleChoiceSegmentedButtonRow {
            values.forEachIndexed { index, v ->
                SegmentedButton(value == v, onClick = { onChange(v) }, shape = SegmentedButtonDefaults.itemShape(index, values.size),
                    modifier = Modifier.testTag("$tag-${when (v) { null -> "default"; true -> "on"; false -> "off" }}"), icon = {}) {
                    Text(when (v) { null -> "Desktop"; true -> "On"; false -> "Off" })
                }
            }
        }
    }
}

/** The lyrics sheet while talk plays: who said what. */
@Composable
fun TalkTranscript(info: TalkClipInfo, modifier: Modifier = Modifier) {
    LazyColumn(modifier.fillMaxWidth().testTag("talk-transcript"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text(info.title, style = MaterialTheme.typography.titleMedium) }
        if (info.transcript.isEmpty()) item { Text("No transcript for this clip.", style = MaterialTheme.typography.bodyMedium) }
        items(info.transcript) { line ->
            Column {
                Text(info.speakerName(line.speaker), style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.primary)
                Text(line.text, style = MaterialTheme.typography.bodyLarge)
            }
        }
    }
}

/** The transcript as a bottom sheet over the Radio Now Playing screen. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TalkTranscriptSheet(info: TalkClipInfo, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        TalkTranscript(info, Modifier.padding(horizontal = 24.dp, vertical = 8.dp).heightIn(max = 520.dp))
    }
}

/** "Caller · Nova, Sam": the segment and who is on, shown wherever the Radio surfaces name the current item. */
fun talkSpeakerLine(info: TalkClipInfo): String =
    if (info.speakers.isEmpty()) "${info.typeLabel} · Talk" else "${info.typeLabel} · ${info.speakers.joinToString(", ") { it.name }}"
