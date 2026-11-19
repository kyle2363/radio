package dev.radio.mobile.ui

import dev.radio.mobile.playback.PlaySource

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import dev.radio.mobile.ui.theme.RadioShapes
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
    onFolder: (String) -> Unit, onSong: (LocalSong, List<LocalSong>, PlaySource) -> Unit,
    choose: () -> Unit, rescan: () -> Unit, cancel: () -> Unit, modifier: Modifier = Modifier,
    onSongAction: (SongAction, LocalSong) -> Unit = { _, _ -> }, playlistContent: @Composable () -> Unit = {},
    /** The library already sorted by [sort], when the caller has it, so it is not sorted twice. */
    sortedSongs: List<LocalSong>? = null, currentId: String? = null,
    onPlayAll: (List<LocalSong>, PlaySource, Boolean) -> Unit = { _, _, _ -> },
    /** The artist open in the Artists tab, if any. */
    artist: String? = null, onArtist: (String) -> Unit = {}
) {
    val ordered = sortedSongs ?: remember(state.songs, sort) { orderSongs(state.songs, sort) }
    val parent = folderId ?: state.root?.folderId
    val childFolders = remember(state.folders, state.songs, parent, sort) { orderFolders(state.folders, state.songs, parent, sort) }
    // Computed once per library/folder change, not on every recomposition of the list.
    val browsingFolders = folderId != null || category == "Folders"
    val songCounts = remember(ordered) { ordered.groupingBy { it.folderId }.eachCount() }
    val visible = remember(ordered, browsingFolders, parent) { if (browsingFolders) ordered.filter { it.folderId == parent } else ordered }
    val artists = remember(state.songs, category) { if (category == "Artists") Artists.index(state.songs) else emptyList() }
    Column(modifier) {
        LibraryStatus(state, choose, rescan, cancel)
        if (state.root == null && !state.loading && !state.scanning) {
            Column(Modifier.padding(20.dp)) {
                Text("Your music, on this phone", style = MaterialTheme.typography.titleLarge)
                Text("Choose Music to include its MP3s and subfolders. The app reads your files without moving or changing them.", Modifier.padding(vertical = 12.dp))
                Button(onClick = choose) { Text("Choose Music folder") }
            }
        } else if (state.root != null) {
            val showSort = folderId != null || category != "Playlists" && category != "Artists"
            val topLevel = folderId == null && !(category == "Artists" && artist != null)
            // Category chips with mixed shapes; the sort control shares their row (and stands alone inside a folder).
            if (topLevel) Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                // Four categories can be wider than a small phone: they scroll sideways, the sort control stays put.
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("Folders" to CircleShape, "Playlists" to CircleShape, "Songs" to CircleShape, "Artists" to CircleShape).forEach { (tab, shape) ->
                    val chosen = category == tab
                    FilterChip(chosen, onClick = { onCategory(tab) }, label = { Text(tab, fontWeight = if (chosen) FontWeight.Bold else FontWeight.Medium) }, shape = shape,
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = MaterialTheme.colorScheme.primary, selectedLabelColor = MaterialTheme.colorScheme.onPrimary),
                        modifier = Modifier.heightIn(min = 40.dp).testTag("library-category-$tab"))
                }
                }
            }
            // Opening a folder, going back or switching category fades the new list in.
            // The list sits on a tonal sheet with rounded top corners, which separates it from the header above.
            Surface(Modifier.fillMaxWidth().weight(1f), color = MaterialTheme.colorScheme.surfaceContainerLow,
                shape = androidx.compose.foundation.shape.RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp)) {
            FadeInOnChange("$folderId/$category/$artist") {
            if (folderId == null && category == "Playlists") playlistContent()
            else if (folderId == null && category == "Artists") LazyColumn(state = listState, modifier = Modifier.fillMaxSize().testTag("artist-list")) {
                val open = artists.firstOrNull { it.name == artist }
                if (artist == null) items(artists, key = { "artist:${it.name}" }) { entry ->
                    val count = entry.songs.count { it.available }
                    LibraryRow(entry.name, "$count song${if (count == 1) "" else "s"}", Symbol.Person) { onArtist(entry.name) }
                } else if (open != null) {
                    val playable = open.songs.count { it.available }
                    if (playable > 1) item(key = "play-all") { PlayAllRow(playable, { onPlayAll(open.songs, PlaySource(open.name, PlaySource.ARTIST, open.name), false) }, { onPlayAll(open.songs, PlaySource(open.name, PlaySource.ARTIST, open.name), true) }) }
                    items(open.songs, key = { it.id }) { song -> SwipeSongRow(song, onSongAction) { val songMenu = remember { mutableStateOf(false) }; LibraryRow(song.title, songSubtitle(song, showArtist = false), Symbol.Music, playing = song.id == currentId,
                        onLongClick = { songMenu.value = true }, trailing = { SongActions(song, onSongAction, songMenu) }) { onSong(song, open.songs, PlaySource(open.name, PlaySource.ARTIST, open.name)) } } }
                }
                if (artists.isEmpty()) item { Text("No MP3s yet. Artists come from file names like “Artist - Title.mp3”.", Modifier.padding(20.dp)) }
                else if (artist == null && artists.size == 1 && artists[0].name == Artists.UNKNOWN) item {
                    Text("Artists come from file names like “Artist - Title.mp3”; none of yours follow that pattern.", Modifier.padding(20.dp), style = MaterialTheme.typography.bodySmall)
                }
            }
            else {
                LazyColumn(state = listState, modifier = Modifier.fillMaxSize().testTag("library-list")) {
                    // The sort control scrolls away with the list; beside Play all / Shuffle when they show, alone otherwise.
                    val playable = visible.count { it.available }
                    if (showSort && playable <= 1) item(key = "sort-control") {
                        Box(Modifier.fillMaxWidth().padding(horizontal = 8.dp), contentAlignment = Alignment.CenterEnd) { SortButton(sort, onSort) }
                    }
                    if (browsingFolders) items(childFolders, key = { "folder:${it.id}" }) { item ->
                        val count = songCounts[item.id] ?: 0
                        val detail = if (count > 0) "$count song${if (count == 1) "" else "s"}" else if (item.path != item.name) item.path else "Folder"
                        LibraryRow(item.name, if (!item.available) "Folder unavailable · saved listing" else detail, Symbol.Folder) { onFolder(item.id) }
                    }
                    val source = if (!browsingFolders) PlaySource.AllSongs else PlaySource(state.folders.firstOrNull { it.id == parent }?.name ?: "Music", PlaySource.FOLDER, parent)
                    if (playable > 1) item(key = "play-all") { PlayAllRow(playable, { onPlayAll(visible, source, false) }, { onPlayAll(visible, source, true) }, trailing = if (showSort) ({ SortButton(sort, onSort) }) else null) }
                    // Inside a folder every row shares the folder, so rows show artist and length only.
                    items(visible, key = { it.id }) { song -> SwipeSongRow(song, onSongAction) { val songMenu = remember { mutableStateOf(false) }; LibraryRow(song.title, songSubtitle(song, showFolder = !browsingFolders), Symbol.Music, playing = song.id == currentId,
                        onLongClick = { songMenu.value = true }, trailing = { SongActions(song, onSongAction, songMenu) }) { onSong(song, visible, source) } } }
                    if (visible.isEmpty() && (!browsingFolders || childFolders.isEmpty())) item {
                        Text("No MP3s in this ${if (browsingFolders) "folder" else "library"}. Try another folder or rescan after adding files.", Modifier.padding(20.dp))
                    }
                }
            }
            }
            }
        }
    }
}

/** "Artist · Folder · 3:45". Inside a folder the folder is the same on every row, so [showFolder] leaves it out. */
fun songSubtitle(song: LocalSong, showFolder: Boolean = true, showArtist: Boolean = true): String = listOfNotNull(
    if (!song.available) "Missing file" else null,
    if (showArtist) song.artist.ifBlank { null } else null,
    if (showFolder || song.artist.isBlank() && song.durationMs == null) song.folderPath.ifEmpty { "Root folder" } else null,
    song.durationMs?.let { "%d:%02d".format(it / 60000, (it / 1000) % 60) }
).joinToString(" · ")

/** "Play all" and "Shuffle" above a list of songs. */
@Composable
private fun SortButton(sort: LibrarySort, onSort: (LibrarySort) -> Unit) {
    var menu by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { menu = true }, contentPadding = PaddingValues(horizontal = 4.dp), modifier = Modifier.heightIn(min = 40.dp).testTag("sort-button")) {
            RadioIcon(Symbol.Sort, "Sort: ${sort.label}", Modifier.size(16.dp)); Spacer(Modifier.width(4.dp))
            Text(sort.short, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Medium, maxLines = 1, softWrap = false)
        }
        DropdownMenu(menu, onDismissRequest = { menu = false }) { LibrarySort.entries.forEach { item ->
            DropdownMenuItem(text = { Text(item.label) }, onClick = { onSort(item); menu = false })
        } }
    }
}

@Composable
fun PlayAllRow(count: Int, onPlay: () -> Unit, onShuffle: () -> Unit, modifier: Modifier = Modifier, trailing: (@Composable () -> Unit)? = null) {
    Row(modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        FilledTonalButton(onClick = onPlay, contentPadding = PaddingValues(horizontal = 10.dp), modifier = Modifier.weight(if (trailing != null) 0.8f else 1f).heightIn(min = 40.dp).testTag("play-all")) {
            RadioIcon(Symbol.Play, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(4.dp)); Text("Play all · $count", maxLines = 1, softWrap = false, style = MaterialTheme.typography.labelLarge)
        }
        OutlinedButton(onClick = onShuffle, contentPadding = PaddingValues(horizontal = 10.dp), modifier = Modifier.weight(if (trailing != null) 0.8f else 1f).heightIn(min = 40.dp).testTag("shuffle-all")) {
            RadioIcon(Symbol.Shuffle, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(4.dp)); Text("Shuffle", maxLines = 1, softWrap = false, style = MaterialTheme.typography.labelLarge)
        }
        trailing?.invoke()
    }
}
