package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import dev.radio.mobile.playback.AudioSettings
import dev.radio.mobile.playback.AudioSettingsStore
import dev.radio.mobile.playback.Crossfade
import kotlin.math.roundToInt

/** Speed, skip silence, crossfade and a way into the equaliser. All of it applies to the song that is playing now. */
@Composable
fun SoundSettings(store: AudioSettingsStore, onEqualizer: () -> Unit) {
    val settings by store.settings.collectAsState()
    val info by store.equalizer.collectAsState()
    Text("Playback speed", style = MaterialTheme.typography.bodyLarge)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        AudioSettings.SPEEDS.forEach { speed ->
            FilterChip(settings.speed == speed, onClick = { store.update { it.copy(speed = speed) } }, label = { Text(speedLabel(speed)) },
                modifier = Modifier.testTag("speed-${speedLabel(speed)}"))
        }
    }
    Text("Pitch stays the same at any speed.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Row(Modifier.fillMaxWidth().padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Skip silence")
            Text("Skips quiet gaps, such as long fade-outs and hidden tracks.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(settings.skipSilence, onCheckedChange = { on -> store.update { it.copy(skipSilence = on) } }, modifier = Modifier.testTag("skip-silence"))
    }
    Text("Crossfade", Modifier.padding(top = 12.dp), style = MaterialTheme.typography.bodyLarge)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Crossfade.CHOICES.forEach { seconds ->
            FilterChip(settings.crossfadeSeconds == seconds, onClick = { store.update { it.copy(crossfadeSeconds = seconds) } },
                label = { Text(if (seconds == 0) "Off" else "$seconds s") }, modifier = Modifier.testTag("crossfade-$seconds"))
        }
    }
    Text("Blends the end of a song into the next when one follows on its own. Skipping is always instant.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Equaliser")
            Text(if (!settings.eqEnabled) "Off" else "On · " + (info?.presets?.getOrNull(settings.eqPreset) ?: "Custom"),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        FilledTonalButton(onClick = onEqualizer, modifier = Modifier.testTag("open-equalizer")) { Text("Adjust") }
    }
}

@Composable
fun EqualizerScreen(store: AudioSettingsStore) {
    val settings by store.settings.collectAsState()
    val info by store.equalizer.collectAsState()
    val unavailable by store.equalizerUnavailable.collectAsState()
    val eq = info
    if (eq == null) {
        Text(if (unavailable) "This phone doesn't offer an equaliser to apps. Speed, skip silence and crossfade still work."
            else "Play a song once, then come back to set the equaliser.", Modifier.padding(vertical = 16.dp))
        return
    }
    // What the bands are set to now: the chosen preset's levels, or the listener's own.
    val levels = if (settings.eqPreset in eq.presets.indices) eq.presetLevels[settings.eqPreset]
        else eq.bandsHz.indices.map { settings.eqLevels.getOrNull(it) ?: 0 }
    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text("Equaliser", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
        Switch(settings.eqEnabled, onCheckedChange = { on -> store.update { it.copy(eqEnabled = on) } }, modifier = Modifier.testTag("equalizer-switch"))
    }
    val enabled = settings.eqEnabled
    Text("Presets", style = MaterialTheme.typography.labelLarge)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(settings.eqPreset !in eq.presets.indices, enabled = enabled, onClick = { store.update { it.copy(eqPreset = -1, eqLevels = levels) } }, label = { Text("Custom") })
        eq.presets.forEachIndexed { index, name ->
            FilterChip(settings.eqPreset == index, enabled = enabled, onClick = { store.update { it.copy(eqPreset = index) } }, label = { Text(name) })
        }
    }
    Text("Bands", Modifier.padding(top = 16.dp), style = MaterialTheme.typography.labelLarge)
    eq.bandsHz.forEachIndexed { band, hz ->
        val db = levels.getOrElse(band) { 0 } / 100f
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(frequencyLabel(hz), Modifier.width(64.dp), style = MaterialTheme.typography.labelMedium)
            Slider(db, onValueChange = { value ->
                // Moving a band turns the current preset into the listener's own setting.
                val next = levels.toMutableList().also { it[band] = (value * 100).roundToInt() }
                store.update { it.copy(eqPreset = -1, eqLevels = next) }
            }, valueRange = eq.minLevel / 100f..eq.maxLevel / 100f, enabled = enabled,
                modifier = Modifier.weight(1f).semantics { contentDescription = "${frequencyLabel(hz)} band"; stateDescription = dbLabel(db) })
            Text(dbLabel(db), Modifier.width(56.dp), style = MaterialTheme.typography.labelMedium)
        }
    }
    if (eq.bassBoost) {
        Text("Bass boost", Modifier.padding(top = 16.dp), style = MaterialTheme.typography.labelLarge)
        Slider(settings.bassBoost.toFloat(), onValueChange = { value -> store.update { it.copy(bassBoost = value.roundToInt()) } }, valueRange = 0f..1000f, enabled = enabled,
            modifier = Modifier.semantics { contentDescription = "Bass boost" }.testTag("bass-boost"))
    }
    TextButton(onClick = { store.update { it.copy(eqPreset = -1, eqLevels = eq.bandsHz.map { 0 }, bassBoost = 0) } }, enabled = enabled,
        modifier = Modifier.padding(top = 8.dp)) { Text("Reset to flat") }
    Text("Uses your phone's own equaliser, so the presets and bands depend on the phone. It applies to this app only.",
        Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

internal fun speedLabel(speed: Float) = (if (speed == speed.toInt().toFloat()) speed.toInt().toString() else speed.toString().trimEnd('0')) + "×"
private fun frequencyLabel(hz: Int) = if (hz >= 1000) "${if (hz % 1000 == 0) (hz / 1000).toString() else "%.1f".format(hz / 1000f)} kHz" else "$hz Hz"
private fun dbLabel(db: Float) = db.roundToInt().let { (if (it > 0) "+" else "") + "$it dB" }
