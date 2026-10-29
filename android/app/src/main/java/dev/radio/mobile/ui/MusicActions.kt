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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.*
import kotlinx.coroutines.launch

enum class SongAction { PlayNext, AddToQueue, AddToPlaylist }

@Composable
fun SongActions(song: LocalSong, onAction: (SongAction, LocalSong) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }, modifier = Modifier.testTag("song-menu-${song.id}")) { RadioIcon(Symbol.More, "Options for ${song.title}") }
        DropdownMenu(open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("Play next") }, enabled = song.available, onClick = { open = false; onAction(SongAction.PlayNext, song) })
            DropdownMenuItem(text = { Text("Add to queue") }, enabled = song.available, onClick = { open = false; onAction(SongAction.AddToQueue, song) })
            DropdownMenuItem(text = { Text("Add to playlist") }, onClick = { open = false; onAction(SongAction.AddToPlaylist, song) })
        }
    }
}

@Composable
fun SongPicker(title: String, songs: List<LocalSong>, onDismiss: () -> Unit, onConfirm: (List<LocalSong>) -> Unit) {
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf(setOf<String>()) }
    val visible = remember(songs, query) { songs.filter { it.available && (it.title + " " + it.folderPath).contains(query, true) } }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) }, text = {
        Column {
            OutlinedTextField(query, { query = it }, label = { Text("Search songs") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("song-picker-search"))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("${selected.size} selected", style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = { selected = selected + visible.map { it.id } }) { Text("Select results") }
            }
            LazyColumn(Modifier.heightIn(min = 120.dp, max = 340.dp).testTag("song-picker-list")) {
                items(visible, key = { it.id }) { song ->
                    Row(Modifier.fillMaxWidth().toggleable(song.id in selected, role = Role.Checkbox, onValueChange = { checked -> selected = if (checked) selected + song.id else selected - song.id })
                        .heightIn(min = 56.dp).testTag("song-picker-row-${song.id}"), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(song.id in selected, onCheckedChange = null)
                        Column(Modifier.weight(1f).padding(start = 8.dp)) {
                            Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(song.folderPath, maxLines = 1, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
                if (visible.isEmpty()) item { Text("No available songs", Modifier.padding(vertical = 16.dp)) }
            }
        }
    }, confirmButton = { TextButton(onClick = { onConfirm(songs.filter { it.id in selected && it.available }) }, enabled = selected.isNotEmpty()) { Text("Add (${selected.size})") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

@Composable
fun PlaylistDestinationDialog(songIds: List<String>, playlists: PlaylistsState, repository: PlaylistRepository, onDismiss: () -> Unit, onAdded: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Add to playlist") }, text = { Column {
        LazyColumn(Modifier.heightIn(max = 240.dp)) { items(playlists.playlists, key = { it.id }) { playlist ->
            TextButton(onClick = { busy = true; scope.launch {
                repository.add(playlist.id, songIds).onSuccess { onAdded() }.onFailure { error = it.message }; busy = false
            } }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text(playlist.name) }
        } }
        OutlinedTextField(name, { name = it }, label = { Text("New playlist name") }, singleLine = true, enabled = !busy, modifier = Modifier.testTag("playlist-name"))
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    } }, confirmButton = { TextButton(onClick = { busy = true; scope.launch {
        repository.create(name, songIds).onSuccess { onAdded() }.onFailure { error = it.message }; busy = false
    } }, enabled = name.isNotBlank() && !busy) { Text("Create and add") } }, dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}
