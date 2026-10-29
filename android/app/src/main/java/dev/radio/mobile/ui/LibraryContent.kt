package dev.radio.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.*
import dev.radio.mobile.music.*

@Composable
fun LibraryStatus(state: LibraryState, choose: () -> Unit, rescan: () -> Unit, cancel: () -> Unit) {
    when {
        state.loading -> Column(Modifier.padding(20.dp)) { LinearProgressIndicator(Modifier.fillMaxWidth()); Text("Opening saved library…") }
        state.scanning -> Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text("Scanning ${state.scanningFolder} · ${state.scannedCount} MP3s found", style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = cancel) { Text("Cancel scan") }
        }
        state.accessLost || state.error != null -> Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp)) {
            Text(state.error ?: "Folder access was lost. Your saved library is still here.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            Row { TextButton(onClick = choose) { Text("Choose folder") }; if (!state.accessLost && state.root != null) TextButton(onClick = rescan) { Text("Retry scan") } }
        }
        state.message != null -> Text(state.message, Modifier.padding(horizontal = 20.dp, vertical = 4.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun LibraryContent(
    state: LibraryState, folderId: String?, category: String, onCategory: (String) -> Unit,
    sort: LibrarySort, onSort: (LibrarySort) -> Unit, listState: LazyListState,
    onFolder: (String) -> Unit, onSong: (LocalSong, List<LocalSong>, String) -> Unit,
    choose: () -> Unit, rescan: () -> Unit, cancel: () -> Unit, modifier: Modifier = Modifier,
    onSongAction: (SongAction, LocalSong) -> Unit = { _, _ -> }, playlistContent: @Composable () -> Unit = {}
) {
    val ordered = remember(state.songs, sort) { orderSongs(state.songs, sort) }
    val parent = folderId ?: state.root?.folderId
    val childFolders = remember(state.folders, state.songs, parent, sort) { orderFolders(state.folders, state.songs, parent, sort) }
    Column(modifier) {
        LibraryStatus(state, choose, rescan, cancel)
        if (state.root == null && !state.loading && !state.scanning) {
            Column(Modifier.padding(20.dp)) {
                Text("Your music, on this phone", style = MaterialTheme.typography.titleLarge)
                Text("Choose Music to include its MP3s and subfolders. The app reads your files without moving or changing them.", Modifier.padding(vertical = 12.dp))
                Button(onClick = choose) { Text("Choose Music folder") }
            }
        } else if (state.root != null) {
            if (folderId == null) PrimaryTabRow(selectedTabIndex = listOf("Folders", "Playlists", "Songs").indexOf(category)) {
                listOf("Folders", "Playlists", "Songs").forEach { tab -> Tab(selected = category == tab, onClick = { onCategory(tab) }, text = { Text(tab) }, unselectedContentColor = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            var menu by remember { mutableStateOf(false) }
            if (folderId == null && category == "Playlists") playlistContent()
            else {
                Box(Modifier.padding(start = 8.dp)) {
                    TextButton(onClick = { menu = true }) { Text(sort.label, style = MaterialTheme.typography.labelMedium); Text("  ⌄") }
                    DropdownMenu(menu, onDismissRequest = { menu = false }) { LibrarySort.entries.forEach { item ->
                        DropdownMenuItem(text = { Text(item.label) }, onClick = { onSort(item); menu = false })
                    } }
                }
                LazyColumn(state = listState, modifier = Modifier.fillMaxSize().testTag("library-list")) {
                    val browsingFolders = folderId != null || category == "Folders"
                    if (browsingFolders) items(childFolders, key = { "folder:${it.id}" }) { item ->
                        LibraryRow(item.name, if (!item.available) "Folder unavailable · saved listing" else item.path, Symbol.Folder) { onFolder(item.id) }
                    }
                    val visible = if (browsingFolders) ordered.filter { it.folderId == parent } else ordered
                    val source = if (!browsingFolders) "All songs" else state.folders.firstOrNull { it.id == parent }?.name ?: "Music"
                    items(visible, key = { it.id }) { song -> LibraryRow(song.title, songSubtitle(song), Symbol.Music, trailing = { SongActions(song, onSongAction) }) { onSong(song, visible, source) } }
                    if (visible.isEmpty() && (!browsingFolders || childFolders.isEmpty())) item {
                        Text("No MP3s in this ${if (browsingFolders) "folder" else "library"}. Try another folder or rescan after adding files.", Modifier.padding(20.dp))
                    }
                }
            }
        }
    }
}

fun songSubtitle(song: LocalSong): String = listOfNotNull(
    if (!song.available) "Missing file" else null,
    song.folderPath.ifEmpty { "Root folder" },
    song.durationMs?.let { "%d:%02d".format(it / 60000, (it / 1000) % 60) }
).joinToString(" · ")
