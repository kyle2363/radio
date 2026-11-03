package dev.radio.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.portable.MatchResult

/** List membership is frozen during decisions so reviewing never shifts the next row. */
@Composable
fun MatchingReviewSection(
    rows: List<MatchResult>, busy: Boolean, enabled: Boolean,
    progress: String, error: String?, onMatch: () -> Unit,
    onConfirm: (String, String) -> Unit, onReject: (String, String) -> Unit,
    onReset: (String, String) -> Unit, onCancel: () -> Unit,
    onConfirmSelected: (List<Pair<String, String>>) -> Unit = { chosen -> chosen.forEach { onConfirm(it.first, it.second) } }
) {
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableStateOf("All") }
    var filterEpoch by remember { mutableIntStateOf(0) }
    var detail by rememberSaveable { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<Map<String, String>>(emptyMap()) }
    // Changes in result IDs mean a new scan, but confirmations retain membership.
    val ids = remember(rows) { rows.map { it.localId } }
    val membership = remember(filter, filterEpoch, query, ids) {
        rows.filter { row ->
            val statusMatches = when (filter) {
                "Review" -> row.status !in listOf("confirmed", "high_confidence", "unmatched")
                "Unmatched" -> row.status == "unmatched"
                "Matched" -> row.status in listOf("confirmed", "high_confidence")
                else -> true
            }
            statusMatches && (row.fileName + " " + row.candidates.joinToString(" ") { it.title + " " + it.artist }).contains(query.trim(), true)
        }.map { it.localId }.toSet()
    }
    val shown = remember(rows, membership) { rows.filter { it.localId in membership } }
    val matched = rows.count { it.status == "confirmed" || it.status == "high_confidence" }
    val unmatched = rows.count { it.status == "unmatched" }
    Column(Modifier.fillMaxSize().testTag("matching-screen")) {
        Text("$matched matched · ${rows.size - matched - unmatched} needs review · $unmatched unmatched", Modifier.testTag("matching-counts"), style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(query, { query = it }, label = { Text("Search filenames, titles, artists") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp).testTag("matching-search"))
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            listOf("All", "Review", "Unmatched", "Matched").forEach { option ->
                FilterChip(filter == option, { filter = option; filterEpoch++ }, label = { Text(option, style = MaterialTheme.typography.labelSmall) })
            }
        }
        // Reserve feedback and action space so a confirmation cannot move list rows.
        Box(Modifier.fillMaxWidth().height(4.dp)) { if (busy) LinearProgressIndicator(Modifier.fillMaxWidth()) }
        Text(progress, Modifier.height(20.dp).testTag("matching-progress"), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
        Row(Modifier.height(48.dp), verticalAlignment = Alignment.CenterVertically) {
            if (busy) {
                TextButton(onCancel, modifier = Modifier.testTag("cancel-matching")) { Text("Cancel matching") }
            } else {
                TextButton(onMatch, enabled = enabled, modifier = Modifier.testTag("match-local-songs")) { Text("Match local songs") }
                TextButton({ onConfirmSelected(selected.toList()); selected = emptyMap() }, enabled = enabled && selected.isNotEmpty(), modifier = Modifier.testTag("confirm-selected-matches")) { Text("Confirm selected (${selected.size})") }
            }
        }
        error?.let { Text(it, Modifier.testTag("matching-error"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        Text("${shown.size} songs · Tap a row to choose a candidate", style = MaterialTheme.typography.labelSmall)
        LazyColumn(Modifier.weight(1f).fillMaxWidth().testTag("matching-list")) {
            items(shown, key = { it.localId }) { row ->
                val chosen = selected[row.localId]
                val unique = row.candidates.filter { "missing_catalog_recording" !in it.reasons }.singleOrNull()
                val mapped = row.candidates.firstOrNull { it.songId == (chosen ?: row.songId) } ?: row.candidates.firstOrNull()
                Row(Modifier.fillMaxWidth().heightIn(min = 80.dp).clickable { focus.clearFocus(); keyboard?.hide(); detail = row.localId }.padding(vertical = 8.dp).testTag("matching-row-${row.localId}"), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(row.fileName, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                        Text(mapped?.let { "${it.title} · ${it.artist}" } ?: "${row.candidates.size} candidates · Choose a match", maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                        Text(if (chosen != null) "Selected for confirmation" else row.status.replace('_', ' '), style = MaterialTheme.typography.labelSmall)
                    }
                    if (chosen != null || unique != null) Checkbox(chosen != null, { checked ->
                        selected = if (checked && unique != null) selected + (row.localId to unique.songId) else selected - row.localId
                    }, enabled = enabled && !busy, modifier = Modifier.testTag("select-match-${row.localId}"))
                }
                HorizontalDivider()
            }
            if (shown.isEmpty()) item { Text("No songs in this view.", Modifier.padding(vertical = 20.dp)) }
        }
    }
    val row = rows.firstOrNull { it.localId == detail }
    if (row != null) AlertDialog(onDismissRequest = { detail = null },
        modifier = Modifier.heightIn(max = 620.dp), title = { Text(row.fileName, maxLines = 2, overflow = TextOverflow.Ellipsis) },
        text = {
            Column(Modifier.fillMaxWidth().height(360.dp).verticalScroll(rememberScrollState())) {
                Text("Choose the recording explicitly before confirming. Duplicate candidates are never selected automatically.", style = MaterialTheme.typography.bodySmall)
                if (row.candidates.isEmpty()) Text("No candidates in the imported catalog.")
                row.candidates.forEach { candidate ->
                    val available = enabled && !busy && "missing_catalog_recording" !in candidate.reasons
                    Column(Modifier.fillMaxWidth().padding(top = 12.dp)) {
                        Text("${candidate.title.ifBlank { candidate.songId }} · ${candidate.artist}", style = MaterialTheme.typography.titleSmall)
                        Text("Score ${(candidate.confidence * 100).toInt()}/100 · ${candidate.reasons.joinToString(", ").replace('_', ' ')}", style = MaterialTheme.typography.bodySmall)
                        Text(candidate.songId, style = MaterialTheme.typography.labelSmall)
                        if (row.songId == candidate.songId) Text("Current match", style = MaterialTheme.typography.labelSmall)
                        FilterChip(selected[row.localId] == candidate.songId, { selected = if (selected[row.localId] == candidate.songId) selected - row.localId else selected + (row.localId to candidate.songId) }, enabled = available, label = { Text("Select for batch") })
                        Row {
                            TextButton({ onConfirm(row.localId, candidate.songId) }, enabled = available) { Text("Confirm") }
                            TextButton({ onReject(row.localId, candidate.songId) }, enabled = available) { Text("Reject") }
                        }
                        TextButton({ onReset(row.localId, candidate.songId) }, enabled = enabled && !busy) { Text("Reset decision") }
                    }
                }
            }
        }, confirmButton = { TextButton({ detail = null }) { Text("Done") } })
}
