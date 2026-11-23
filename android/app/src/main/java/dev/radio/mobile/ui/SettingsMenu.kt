package dev.radio.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.LoudnessRepository
import dev.radio.mobile.playback.AudioSettingsStore

/** Pages opened from the Settings menu. Back returns to Settings and the Settings tab stays selected. */
internal val SETTINGS_PAGES = setOf("Server", "Matching", "Equaliser", "Stats", "Feels", "Music library", "Sound", "Appearance", "Desktop similarity")

/** One row on the Settings menu: icon, name, a one-line summary of its current state, and a chevron. */
@Composable
internal fun SettingsRow(symbol: Symbol, title: String, summary: String, tag: String, summaryColor: Color = Color.Unspecified, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onClick).heightIn(min = 64.dp).padding(horizontal = 16.dp, vertical = 10.dp).testTag(tag),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioIcon(symbol, modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f).padding(start = 16.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(summary, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                color = if (summaryColor == Color.Unspecified) MaterialTheme.colorScheme.onSurfaceVariant else summaryColor)
        }
        RadioIcon(Symbol.Forward, modifier = Modifier.size(18.dp))
    }
}

/** The Sound row. Collects its own state so normalization progress never recomposes the whole app. */
@Composable
internal fun SoundSettingsRow(store: AudioSettingsStore, loudness: LoudnessRepository, onClick: () -> Unit) {
    val settings by store.settings.collectAsState()
    val volume by loudness.state.collectAsState()
    val parts = buildList {
        if (volume.enabled) add("Normalization on")
        if (settings.crossfadeSeconds > 0) add("Crossfade ${settings.crossfadeSeconds} s")
        if (settings.speed != 1f) add("${speedLabel(settings.speed)} speed")
        if (settings.eqEnabled) add("Equaliser on")
        if (settings.skipSilence) add("Skip silence")
    }
    SettingsRow(Symbol.Speaker, "Sound", if (parts.isEmpty()) "Speed, crossfade, equaliser, normalization" else parts.joinToString(" · "), "settings-sound", onClick = onClick)
}
