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

        Text("Guests change the feel", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        Text("A guest may switch the mood, or another of your feels in a feel station, until their set ends.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            (listOf<String?>(null) + TalkSettings.GUEST_SHIFTS).forEach { value ->
                FilterChip(settings.guestShift == value, onClick = { onChange(settings.copy(guestShift = value)) },
                    label = { Text(value?.replaceFirstChar { it.uppercase() } ?: "Desktop") },
                    modifier = Modifier.testTag("talk-guest-shift-${value ?: "default"}"))
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

/**
 * Radio -> Tune, Slowed (checkpoint 14). Needs Talk on: the guest always says so before the music slows ("no voice,
 * no effect"). The phone plays its own files slowed; the desktop only voices the lines.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SlowedSection(talkOn: Boolean, settings: dev.radio.mobile.recommendation.SlowedSettings, bank: dev.radio.mobile.recommendation.SlowedBankState,
    allowedFeels: Int, onChange: (dev.radio.mobile.recommendation.SlowedSettings) -> Unit, onRefresh: () -> Unit, modifier: Modifier = Modifier) {
    LaunchedEffect(talkOn && settings.enabled) { if (talkOn && settings.enabled) onRefresh() }
    Column(modifier.fillMaxWidth().padding(top = 16.dp).testTag("slowed-section"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(settings.enabled, enabled = talkOn, role = Role.Switch,
            onValueChange = { onChange(settings.copy(enabled = it)) }).testTag("slowed-toggle"), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("SLOWED", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(when { !talkOn -> "Needs Talk on"; !settings.enabled -> "Off · a guest DJ slows Radio down for a set"; else -> slowedBankLine(bank) },
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("slowed-state"))
            }
            Switch(settings.enabled && talkOn, onCheckedChange = null, enabled = talkOn)
        }
        if (!talkOn || !settings.enabled) return@Column
        Text("How often", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            dev.radio.mobile.recommendation.SlowedFrequency.entries.forEach { f ->
                FilterChip(settings.frequency == f, onClick = { onChange(settings.copy(frequency = f)) }, label = { Text(f.label) },
                    modifier = Modifier.testTag("slowed-frequency-${f.name.lowercase()}"))
            }
        }
        Text("Slowed length", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        Text("Sets run 4 to ${settings.maxSongs} songs; ones you start sit at the top.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            (dev.radio.mobile.recommendation.SlowedSettings.MIN_SONGS..dev.radio.mobile.recommendation.SlowedSettings.MAX_SONGS).forEach { n ->
                FilterChip(settings.maxSongs == n, onClick = { onChange(settings.copy(maxSongs = n)) }, label = { Text("Up to $n") },
                    modifier = Modifier.testTag("slowed-max-$n"))
            }
        }
        Text("Sounds", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            dev.radio.mobile.playback.SlowedPreset.entries.forEach { p ->
                val on = p in settings.presets
                FilterChip(on, onClick = { onChange(settings.copy(presets = if (on) settings.presets - p else settings.presets + p)) },
                    enabled = !(on && settings.presets.size == 1), label = { Text(p.label) }, modifier = Modifier.testTag("slowed-preset-${p.id}"))
            }
        }
        Text(if (allowedFeels == 0) "In plain Radio, a set plays songs from feels that allow Slowed. Turn it on for a feel in Library → Feels."
            else "Plays in plain Radio and in the $allowedFeels feel station${if (allowedFeels == 1) "" else "s"} that allow it.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("slowed-feels-note"))
    }
}

private fun slowedBankLine(bank: dev.radio.mobile.recommendation.SlowedBankState): String {
    if (bank.syncing && bank.count == 0) return "Fetching the guests' lines…"
    if (bank.count == 0) return bank.error?.let { "No lines stored yet · $it" } ?: "No lines stored yet · they arrive while the desktop is reachable"
    val days = (System.currentTimeMillis() - bank.updatedAt) / 86_400_000L
    val updated = when { bank.updatedAt == 0L -> ""; days <= 0 -> " · updated today"; days == 1L -> " · updated yesterday"; else -> " · updated $days days ago" }
    return "${bank.count} lines stored$updated"
}
