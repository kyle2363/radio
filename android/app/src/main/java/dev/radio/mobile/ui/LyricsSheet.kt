package dev.radio.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.LocalSong

/** Presentation-only state; the repository owns mapping, caching and request lifecycle. */
data class LyricsSheetState(
    val status: String,
    val loading: Boolean = false,
    val text: String? = null,
    val source: String? = null,
    val message: String? = null,
    val action: String? = null,
    val canRefresh: Boolean = false,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LyricsSheet(
    song: LocalSong,
    state: LyricsSheetState,
    onRefresh: () -> Unit,
    onDismiss: () -> Unit,
    onServer: () -> Unit,
    onMatching: () -> Unit,
) {
    val lyricsScroll = remember(song.id) { ScrollState(0) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            Modifier.fillMaxWidth().heightIn(min = 360.dp).fillMaxHeight(.82f).padding(horizontal = 24.dp).padding(bottom = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Lyrics",
                    Modifier.weight(1f).semantics { heading() }.testTag("lyrics-title"),
                    style = MaterialTheme.typography.titleLarge,
                )
                IconButton(onClick = onDismiss, modifier = Modifier.testTag("lyrics-close")) {
                    RadioIcon(Symbol.Close, "Close lyrics")
                }
            }
            Text(
                song.title,
                Modifier.fillMaxWidth().testTag("lyrics-song-title"),
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.titleMedium,
            )
            Text(song.folderPath, Modifier.fillMaxWidth().testTag("lyrics-folder"),
                maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)

            state.source?.let { source ->
                Text(source, Modifier.fillMaxWidth().testTag("lyrics-source"), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            state.message?.let { message ->
                Text(message, Modifier.fillMaxWidth().testTag("lyrics-message"), style = MaterialTheme.typography.bodyMedium,
                    color = if (state.status == "ERROR") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (state.loading && state.text == null) {
                Box(Modifier.weight(1f).fillMaxWidth().testTag("lyrics-loading"), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else if (state.text != null) {
                SelectionContainer(Modifier.weight(1f).fillMaxWidth().testTag("lyrics-selection")) {
                    Column(Modifier.fillMaxWidth().verticalScroll(lyricsScroll).testTag("lyrics-scroll")) {
                        Text(state.text, Modifier.fillMaxWidth().testTag("lyrics-text"),
                            style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
                    }
                }
            } else {
                Spacer(Modifier.weight(1f))
            }

            Text("Desktop lyric changes appear after preparing and importing a new catalog.",
                Modifier.fillMaxWidth().testTag("lyrics-catalog-note"), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (state.loading && state.text != null) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.height(18.dp).testTag("lyrics-refresh-progress"), strokeWidth = 2.dp)
                    Text("Refreshing…", Modifier.padding(start = 8.dp), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
                when (state.action) {
                    "SERVER" -> TextButton(onClick = onServer, modifier = Modifier.height(48.dp).testTag("lyrics-server")) { Text("Server settings") }
                    "MATCHING" -> TextButton(onClick = onMatching, modifier = Modifier.height(48.dp).testTag("lyrics-matching")) { Text("Matching") }
                }
                TextButton(onClick = onRefresh, enabled = state.canRefresh && !state.loading,
                    modifier = Modifier.height(48.dp).testTag("lyrics-refresh")) {
                    Text(if (state.text != null) "Refresh" else "Retry")
                }
            }
        }
    }
}
