package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.LoudnessRepository
import dev.radio.mobile.data.LibraryState

@Composable
fun NormalizationSettings(repository: LoudnessRepository, library: LibraryState, currentId: String?) {
    val state by repository.state.collectAsState()
    val analysis by repository.analysis.collectAsState()
    Column(Modifier.fillMaxWidth().padding(top = 16.dp).testTag("normalization-settings")) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("Volume normalization", Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
            Switch(state.enabled, onCheckedChange = repository::setEnabled, modifier = Modifier.testTag("normalization-switch"))
        }
        Text("Lowers louder measured songs toward −18 LUFS. Quiet, silent and unmeasured songs stay unchanged. Your phone volume and music files stay under your control.", style = MaterialTheme.typography.bodySmall)
        Text("${state.measured} / ${state.available} songs measured" + if (state.failed > 0) " · ${state.failed} need retry" else "", Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.labelMedium)
        if (currentId != null && state.enabled) Text(if (state.profile.gain(currentId) < 1f) "Current song: volume reduced" else "Current song: original level", style = MaterialTheme.typography.bodySmall)
        if (analysis.running) {
            LinearProgressIndicator(progress = { analysis.completed.toFloat() / analysis.total.coerceAtLeast(1) }, modifier = Modifier.fillMaxWidth())
            Text("${analysis.completed} / ${analysis.total} · ${analysis.song}", style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = repository::pauseAnalysis) { Text("Pause analysis") }
        } else {
            Row {
                TextButton(onClick = { repository.analyze() }, enabled = state.loaded && !library.loading && !library.scanning && !library.accessLost && state.available > 0) { Text("Analyze unmeasured songs") }
                if (currentId != null) TextButton(onClick = { repository.analyze(setOf(currentId)) }, enabled = state.loaded && !library.loading && !library.scanning && !library.accessLost) { Text("Current song") }
            }
        }
        analysis.message?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        Text("Analysis is silent and reads one file at a time. It pauses when you leave the app; finished measurements are saved for next time.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
