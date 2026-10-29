package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.*
import kotlinx.coroutines.launch

@Composable
fun PlaylistBrowser(state: PlaylistsState, repository: PlaylistRepository, catalog: List<LocalSong>, accessAllowed: Boolean,
    selectedId: String?, onSelect: (String?) -> Unit, onPlay: (LocalSong, List<LocalSong>, String) -> Unit,
    onSongAction: (SongAction, LocalSong) -> Unit, modifier: Modifier = Modifier) {
    val scope = rememberCoroutineScope()
    var nameDialog by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf<LocalPlaylist?>(null) }
    var renaming by remember { mutableStateOf<LocalPlaylist?>(null) }
    var adding by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val selected = state.playlists.firstOrNull { it.id == selectedId }
    val byId = remember(catalog) { catalog.associateBy { it.id } }
    val ids = remember(state.entries, selectedId) { selectedId?.let(state::songIds).orEmpty() }
    val songs = remember(ids, catalog) { ids.mapNotNull(byId::get) }
    fun report(result: Result<Unit>) { result.exceptionOrNull()?.let { message = it.message ?: "Couldn't update playlist." } }
    Column(modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            if (selected == null) {
                Text("${state.playlists.size} playlists", Modifier.weight(1f), style = MaterialTheme.typography.labelLarge)
                TextButton(onClick = { nameDialog = true }) { Text("New playlist") }
            } else {
                Text("${ids.size} songs", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = { adding = true }, enabled = accessAllowed) { Text("Add songs") }
                TextButton(onClick = { songs.firstOrNull { it.available }?.let { onPlay(it, songs, selected.name) } }, enabled = accessAllowed && songs.any { it.available }) { Text("Play") }
                var menu by remember { mutableStateOf(false) }
                Box {
                    IconButton(onClick = { menu = true }) { RadioIcon(Symbol.More, "Playlist options") }
                    DropdownMenu(menu, { menu = false }) {
                        DropdownMenuItem(text = { Text("Rename playlist") }, onClick = { menu = false; renaming = selected })
                        DropdownMenuItem(text = { Text("Delete playlist") }, onClick = { menu = false; deleting = selected })
                    }
                }
            }
        }
        message?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 20.dp)) }
        LazyColumn(Modifier.fillMaxSize().testTag("playlist-list")) {
            if (selected == null) {
                itemsIndexed(state.playlists, key = { _, it -> it.id }) { _, playlist ->
                    LibraryRow(playlist.name, "${state.songIds(playlist.id).size} songs", Symbol.Queue, trailing = {
                        var open by remember { mutableStateOf(false) }
                        Box {
                            IconButton(onClick = { open = true }) { RadioIcon(Symbol.More, "Options for playlist ${playlist.name}") }
                            DropdownMenu(open, { open = false }) {
                                DropdownMenuItem(text = { Text("Rename playlist") }, onClick = { open = false; renaming = playlist })
                                DropdownMenuItem(text = { Text("Delete playlist") }, onClick = { open = false; deleting = playlist })
                            }
                        }
                    }) { onSelect(playlist.id) }
                }
                if (state.playlists.isEmpty()) item { Text("Create a playlist, then add songs in the order you want.", Modifier.padding(20.dp)) }
            } else {
                itemsIndexed(ids, key = { _, it -> it }) { index, id ->
                    val song = byId[id]
                    LibraryRow(song?.title ?: "Unavailable song", song?.let(::songSubtitle) ?: "Not in the selected Music folder", Symbol.Music, trailing = {
                        var open by remember { mutableStateOf(false) }
                        Box {
                            IconButton(onClick = { open = true }, modifier = Modifier.testTag("playlist-song-menu-$id")) { RadioIcon(Symbol.More, "Playlist song options") }
                            DropdownMenu(open, { open = false }) {
                                DropdownMenuItem(text = { Text("Play next") }, enabled = accessAllowed && song?.available == true, onClick = { open = false; song?.let { onSongAction(SongAction.PlayNext, it) } })
                                DropdownMenuItem(text = { Text("Add to queue") }, enabled = accessAllowed && song?.available == true, onClick = { open = false; song?.let { onSongAction(SongAction.AddToQueue, it) } })
                                DropdownMenuItem(text = { Text("Move up") }, enabled = index > 0, onClick = { open = false; scope.launch { report(repository.move(selected.id, id, index - 1)) } })
                                DropdownMenuItem(text = { Text("Move down") }, enabled = index < ids.lastIndex, onClick = { open = false; scope.launch { report(repository.move(selected.id, id, index + 1)) } })
                                DropdownMenuItem(text = { Text("Remove from playlist") }, onClick = { open = false; scope.launch { report(repository.remove(selected.id, id)) } })
                            }
                        }
                    }) { if (accessAllowed && song?.available == true) onPlay(song, songs, selected.name) }
                }
                if (ids.isEmpty()) item { Text("This playlist is empty. Add songs to get started.", Modifier.padding(20.dp)) }
            }
        }
    }
    if (nameDialog || renaming != null) {
        val target = renaming
        var name by remember(target?.id) { mutableStateOf(target?.name ?: "") }
        var error by remember { mutableStateOf<String?>(null) }
        var busy by remember { mutableStateOf(false) }
        AlertDialog(onDismissRequest = { nameDialog = false; renaming = null }, title = { Text(if (target == null) "New playlist" else "Rename playlist") },
            text = { Column { OutlinedTextField(name, { name = it }, label = { Text("Playlist name") }, singleLine = true, modifier = Modifier.testTag("playlist-name")); error?.let { Text(it, color = MaterialTheme.colorScheme.error) } } },
            confirmButton = { TextButton(onClick = { busy = true; scope.launch {
                if (target == null) repository.create(name).onSuccess { onSelect(it); nameDialog = false }.onFailure { error = it.message }
                else repository.rename(target.id, name).onSuccess { renaming = null }.onFailure { error = it.message }
                busy = false
            } }, enabled = name.isNotBlank() && !busy) { Text(if (target == null) "Create" else "Save") } },
            dismissButton = { TextButton(onClick = { nameDialog = false; renaming = null }) { Text("Cancel") } })
    }
    if (adding && selected != null) SongPicker("Add songs", catalog, onDismiss = { adding = false }, onConfirm = { chosen ->
        scope.launch { report(repository.add(selected.id, chosen.map { it.id })); adding = false }
    })
    deleting?.let { target -> AlertDialog(onDismissRequest = { deleting = null }, title = { Text("Delete ${target.name}?") },
        text = { Text("This removes the playlist. Your music files and current playback stay unchanged.") },
        confirmButton = { TextButton(onClick = { scope.launch { repository.delete(target.id).onSuccess { if (selectedId == target.id) onSelect(null); deleting = null }.onFailure { message = it.message } } }) { Text("Delete") } },
        dismissButton = { TextButton(onClick = { deleting = null }) { Text("Cancel") } }) }
}
