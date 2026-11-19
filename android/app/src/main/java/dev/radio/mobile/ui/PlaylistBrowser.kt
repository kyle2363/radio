package dev.radio.mobile.ui

import dev.radio.mobile.playback.PlaySource

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun PlaylistBrowser(state: PlaylistsState, repository: PlaylistRepository, catalog: List<LocalSong>, accessAllowed: Boolean,
    selectedId: String?, onSelect: (String?) -> Unit, onPlay: (LocalSong, List<LocalSong>, PlaySource) -> Unit,
    onSongAction: (SongAction, LocalSong) -> Unit, modifier: Modifier = Modifier,
    currentId: String? = null, onPlayAll: (List<LocalSong>, PlaySource, Boolean) -> Unit = { list, source, _ -> list.firstOrNull { it.available }?.let { onPlay(it, list, source) } },
    /** Listening history for the automatic playlists; without it they are not shown. */
    smartRows: Flow<List<SmartListRow>>? = null, favorites: Set<String> = emptySet()) {
    val scope = rememberCoroutineScope()
    var nameDialog by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf<LocalPlaylist?>(null) }
    var renaming by remember { mutableStateOf<LocalPlaylist?>(null) }
    var adding by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val selected = state.playlists.firstOrNull { it.id == selectedId }
    val byId = remember(catalog) { catalog.associateBy { it.id } }
    // Top 100, Recently played and Liked songs, rebuilt off the main thread whenever the journal changes.
    val smart = SmartList.of(selectedId)
    val available = remember(catalog) { catalog.filter { it.available }.mapTo(HashSet()) { it.id } }
    val smartLists by produceState<Map<SmartList, List<String>>?>(null, smartRows, available, favorites) {
        smartRows?.collectLatest { rows -> value = withContext(Dispatchers.Default) { SmartLists.build(rows, favorites, available) } }
    }
    val heard by produceState<List<Pair<LocalSong, Long>>>(emptyList(), smartRows, available, smart, byId) {
        if (smart == SmartList.Heard) smartRows?.collectLatest { rows -> value = withContext(Dispatchers.Default) { SmartLists.heard(rows, available).mapNotNull { (id, at) -> byId[id]?.let { it to at } } } }
    }
    // Recently heard keeps repeats in its own list; the play buttons use each song once.
    val smartSongs = remember(smartLists, smart, byId) { smart?.let { smartLists?.get(it) }.orEmpty().distinct().mapNotNull(byId::get) }
    val ids = remember(state.entries, selectedId) { selectedId?.let(state::songIds).orEmpty() }
    val songs = remember(ids, catalog) { ids.mapNotNull(byId::get) }
    fun report(result: Result<Unit>) { result.exceptionOrNull()?.let { message = it.message ?: "Couldn't update playlist." } }
    val listState = rememberLazyListState()
    // Inside a playlist: long-press a song to drag it, swipe it sideways to remove it.
    val reorder = rememberReorderState(listState) { key, index -> selected?.let { playlist -> scope.launch { report(repository.move(playlist.id, key as String, index)) } } }
    val haptic = rememberDragHaptic()
    Column(modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            if (smart != null) {
                Text(if (smartLists == null) "Loading…" else "${smartSongs.size} songs", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = { onPlayAll(smartSongs, PlaySource(smart.title, PlaySource.SMART_LIST, smart.id), false) }, enabled = accessAllowed && smartSongs.isNotEmpty(), modifier = Modifier.testTag("smart-play")) { Text("Play") }
                TextButton(onClick = { onPlayAll(smartSongs, PlaySource(smart.title, PlaySource.SMART_LIST, smart.id), true) }, enabled = accessAllowed && smartSongs.size > 1, modifier = Modifier.testTag("smart-shuffle")) { Text("Shuffle") }
            } else if (selected == null) {
                Text("${state.playlists.size} playlists", Modifier.weight(1f), style = MaterialTheme.typography.labelLarge)
                TextButton(onClick = { nameDialog = true }) { Text("New playlist") }
            } else {
                Text("${ids.size} songs", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = { adding = true }, enabled = accessAllowed) { Text("Add songs") }
                TextButton(onClick = { onPlayAll(songs, PlaySource(selected.name, PlaySource.PLAYLIST, selected.id), false) }, enabled = accessAllowed && songs.any { it.available }) { Text("Play") }
                TextButton(onClick = { onPlayAll(songs, PlaySource(selected.name, PlaySource.PLAYLIST, selected.id), true) }, enabled = accessAllowed && songs.count { it.available } > 1, modifier = Modifier.testTag("playlist-shuffle")) { Text("Shuffle") }
                var menu by remember { mutableStateOf(false) }
                Box {
                    IconButton(onClick = { menu = true }) { RadioIcon(Symbol.More, "Playlist options") }
                    DropdownMenu(menu, { menu = false }) {
                        SyncMenuItem(selected, state.paired) { menu = false; scope.launch { report(if (selected.synced) repository.stopSharing(selected.id) else repository.share(selected.id)) } }
                        DropdownMenuItem(text = { Text("Rename playlist") }, onClick = { menu = false; renaming = selected })
                        DropdownMenuItem(text = { Text("Delete playlist") }, onClick = { menu = false; deleting = selected })
                    }
                }
            }
        }
        message?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 20.dp)) }
        if (selected?.remoteDeleted == true) Card(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp).testTag("playlist-remote-deleted")) {
            Column(Modifier.padding(12.dp)) {
                Text("This playlist was deleted on another device.", style = MaterialTheme.typography.bodyMedium)
                Row {
                    TextButton(onClick = { deleting = selected }) { Text("Remove from phone") }
                    TextButton(onClick = { scope.launch { report(repository.stopSharing(selected.id)) } }) { Text("Keep on phone") }
                }
            }
        }
        LazyColumn(Modifier.fillMaxSize().testTag("playlist-list"), state = listState) {
            if (smart != null) {
                if (smart == SmartList.Heard) itemsIndexed(heard, key = { index, _ -> "heard:$index" }) { _, (song, at) ->
                    // Repeats are kept; one tap plays that song again (alone), the time says when it was heard.
                    SwipeSongRow(song, onSongAction) { LibraryRow(song.title, heardTime(at) + " · " + songSubtitle(song), Symbol.History, playing = song.id == currentId,
                        trailing = { SongActions(song, onSongAction) }) { if (accessAllowed) onPlay(song, listOf(song), PlaySource(smart.title, PlaySource.SMART_LIST, smart.id)) } }
                } else itemsIndexed(smartSongs, key = { _, it -> it.id }) { index, song ->
                    // The Top 100 shows each song's place.
                    SwipeSongRow(song, onSongAction) { LibraryRow(if (smart == SmartList.Top) "${index + 1}. ${song.title}" else song.title, songSubtitle(song), Symbol.Music, playing = song.id == currentId,
                        trailing = { SongActions(song, onSongAction) }) { if (accessAllowed) onPlay(song, smartSongs, PlaySource(smart.title, PlaySource.SMART_LIST, smart.id)) } }
                }
                if (smartLists != null && smartSongs.isEmpty()) item {
                    Text(when (smart) {
                        SmartList.Top -> "Songs you finish, or play at least halfway, show up here. The more you play one, the higher it goes."
                        SmartList.Recent -> "Songs you play show up here."
                        SmartList.Liked -> "Like a song from the player and it shows up here."
                        SmartList.Gems -> "Songs you liked or always finish show up here once you haven't played them for two months."
                        SmartList.Heard -> "Every song you start shows up here with the time you heard it."
                    }, Modifier.padding(20.dp))
                }
            } else if (selected == null) {
                if (smartRows != null) {
                    item { Text("Made from your listening", Modifier.padding(start = 20.dp, top = 8.dp, bottom = 4.dp), style = MaterialTheme.typography.labelLarge) }
                    itemsIndexed(SmartList.entries, key = { _, it -> it.id }) { _, list ->
                        val count = smartLists?.get(list)?.size
                        // Recently heard keeps repeats, so its count is plays, not songs.
                        LibraryRow(list.title, if (count == null) list.description else "$count ${if (list == SmartList.Heard) (if (count == 1) "play" else "plays") else (if (count == 1) "song" else "songs")} · ${list.description}",
                            when (list) { SmartList.Top -> Symbol.Chart; SmartList.Recent -> Symbol.Clock; SmartList.Liked -> Symbol.HeartFilled; SmartList.Gems -> Symbol.Gem; SmartList.Heard -> Symbol.History }) { onSelect(list.id) }
                    }
                    item { Text("Your playlists", Modifier.padding(start = 20.dp, top = 16.dp, bottom = 4.dp), style = MaterialTheme.typography.labelLarge) }
                }
                itemsIndexed(state.playlists, key = { _, it -> it.id }) { _, playlist ->
                    LibraryRow(playlist.name, "${state.songIds(playlist.id).size} songs" + when {
                        playlist.remoteDeleted -> " · Deleted on desktop"
                        playlist.synced -> " · Synced"
                        else -> ""
                    }, Symbol.Queue, trailing = {
                        var open by remember { mutableStateOf(false) }
                        Box {
                            IconButton(onClick = { open = true }) { RadioIcon(Symbol.More, "Options for playlist ${playlist.name}") }
                            DropdownMenu(open, { open = false }) {
                                SyncMenuItem(playlist, state.paired) { open = false; scope.launch { report(if (playlist.synced) repository.stopSharing(playlist.id) else repository.share(playlist.id)) } }
                                DropdownMenuItem(text = { Text("Rename playlist") }, onClick = { open = false; renaming = playlist })
                                DropdownMenuItem(text = { Text("Delete playlist") }, onClick = { open = false; deleting = playlist })
                            }
                        }
                    }) { onSelect(playlist.id) }
                }
                if (state.playlists.isEmpty()) item { Text("Create a playlist, then add songs in the order you want.", Modifier.padding(20.dp)) }
                if (state.remote.isNotEmpty()) {
                    item { Text("From your desktop", Modifier.padding(start = 20.dp, top = 16.dp, bottom = 4.dp), style = MaterialTheme.typography.labelLarge) }
                    itemsIndexed(state.remote, key = { _, it -> "remote-${it.id}" }) { _, remote ->
                        LibraryRow(remote.name, "${remote.songCount} songs · Not on this phone", Symbol.Queue, trailing = {
                            TextButton(onClick = { scope.launch { report(repository.addRemote(remote.id)) } },
                                modifier = Modifier.testTag("playlist-add-remote-${remote.id}")) { Text("Add") }
                        }) { scope.launch { report(repository.addRemote(remote.id)) } }
                    }
                    item { Text("Added playlists stay in sync each time you sync your profile.", Modifier.padding(horizontal = 20.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall) }
                }
            } else {
                itemsIndexed(ids, key = { _, it -> it }) { index, id ->
                    val song = byId[id]
                    SwipeToRemove(enabled = reorder.draggingKey == null, onRemove = { scope.launch { report(repository.remove(selected.id, id)) } },
                        modifier = (if (reorder.settlingKey == id || reorder.draggingKey == id) Modifier else springPlacement()).reorderable(reorder, id, onStart = haptic)) {
                    LibraryRow(song?.title ?: "Unavailable song", song?.let(::songSubtitle) ?: "Not in the selected Music folder", Symbol.Music, playing = id == currentId, trailing = {
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
                    }) { if (accessAllowed && song?.available == true) onPlay(song, songs, PlaySource(selected.name, PlaySource.PLAYLIST, selected.id)) }
                    }
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

/** Share or stop sharing one playlist with the paired desktop. Hidden until the phone is paired. */
@Composable
private fun SyncMenuItem(playlist: LocalPlaylist, paired: Boolean, onClick: () -> Unit) {
    if (!paired && !playlist.synced) return
    DropdownMenuItem(text = { Text(if (playlist.synced) "Stop syncing" else "Sync with desktop") },
        modifier = Modifier.testTag("playlist-sync-${playlist.id}"), onClick = onClick)
}


/** "Today 21:04", "Yesterday 08:12" or "12 Nov 18:30". */
internal fun heardTime(at: Long, now: Long = System.currentTimeMillis()): String {
    val zone = java.time.ZoneId.systemDefault()
    val time = java.time.Instant.ofEpochMilli(at).atZone(zone)
    val today = java.time.Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    val clock = time.format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))
    return when (time.toLocalDate()) {
        today -> "Today $clock"
        today.minusDays(1) -> "Yesterday $clock"
        else -> time.format(java.time.format.DateTimeFormatter.ofPattern("d MMM HH:mm"))
    }
}
