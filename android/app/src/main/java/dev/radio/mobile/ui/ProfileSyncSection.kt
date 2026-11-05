package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import java.text.DateFormat
import java.util.Date

@Composable
fun ProfileSyncSection(
    busy: Boolean,
    pendingEvents: Int,
    lastSuccessAt: Long?,
    message: String?,
    error: String?,
    enabled: Boolean,
    onSync: () -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(top = 16.dp).testTag("profile-sync")) {
        Text("Sync", style = MaterialTheme.typography.titleSmall)
        Text("Share listening history and feedback with your server. Sync runs only when you choose it.",
            Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(if (pendingEvents == 1) "1 event waiting to sync" else "$pendingEvents events waiting to sync",
            Modifier.padding(top = 8.dp).testTag("sync-pending"), style = MaterialTheme.typography.bodySmall)
        val lastSuccess = lastSuccessAt?.let { DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(it)) } ?: "Never"
        Text("Last synced: $lastSuccess", Modifier.testTag("sync-last-success"), style = MaterialTheme.typography.bodySmall)
        message?.takeIf { it.isNotBlank() }?.let {
            Text(it, Modifier.padding(top = 6.dp).testTag("sync-message"), style = MaterialTheme.typography.bodySmall)
        }
        error?.let {
            Text(it, Modifier.padding(top = 6.dp).testTag("sync-error"),
                color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        if (busy) LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp).testTag("sync-progress"))
        TextButton(onClick = onSync, enabled = enabled && !busy, modifier = Modifier.testTag("sync-now")) {
            Text(if (busy) "Syncing…" else "Sync now")
        }
    }
}
