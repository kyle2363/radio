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
fun LibraryStatus(state: LibraryState, choose: () -> Unit, rescan: () -> Unit, cancel: () -> Unit, showScanSummary: Boolean = true) {
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
        // The Library header already shows the song count, so "N MP3s scanned" there would say it twice.
        state.message != null && (showScanSummary || !state.message.endsWith("MP3s scanned")) -> Text(state.message, Modifier.padding(horizontal = 20.dp, vertical = 4.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
    artist: String? = null, onArtist: (String) -> Unit = {},
    /** Long-press song rows to select several; the app bars act on the selection. */
    selection: SelectionController = remember { SelectionController() }
) {
    val ordered = sortedSongs ?: remember(state.songs, sort) { orderSongs(state.songs, sort) }
    val parent = folderId ?: state.root?.folderId
    // Hidden folders saved by scans before they were skipped (.thumbnails) never show.
    val childFolders = remember(state.folders, state.songs, parent, sort) { orderFolders(state.folders, state.songs, parent, sort).filterNot { dev.radio.mobile.music.isHiddenEntry(it.name) } }
    // Computed once per library/folder change, not on every recomposition of the list.
    val browsingFolders = folderId != null || category == "Folders"
    val songCounts = remember(ordered) { ordered.groupingBy { it.folderId }.eachCount() }
    val visible = remember(ordered, browsingFolders, parent) { if (browsingFolders) ordered.filter { it.folderId == parent } else ordered }
    val artists = remember(state.songs, category) { if (category == "Artists") Artists.index(state.songs) else emptyList() }
    Column(modifier.fillMaxSize()) {
        LibraryStatus(state, choose, rescan, cancel, showScanSummary = false)
        if (state.root == null && !state.loading && !state.scanning) {
            Column(Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                Surface(Modifier.size(72.dp), color = MaterialTheme.colorScheme.secondaryContainer, shape = RadioShapes.Squircle) {
                    Box(contentAlignment = Alignment.Center) { RadioIcon(Symbol.Folder, modifier = Modifier.size(32.dp)) }
                }
                Text("Your music, on this phone", Modifier.padding(top = 20.dp), style = MaterialTheme.typography.titleLarge, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                Text("Choose Music to include its MP3s and subfolders. The app reads your files without moving or changing them.", Modifier.padding(top = 8.dp, bottom = 20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
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
            else if (folderId == null && category == "Artists") {
                val open = artists.firstOrNull { it.name == artist }
                val openSongs = open?.songs.orEmpty()
                val openIds = remember(openSongs) { openSongs.map { it.id } }
                BindSelection(selection, "artist:$artist", openIds, openSongs)
                LazyColumn(state = listState, modifier = Modifier.fillMaxSize().sweepSelect(listState, selection, enabled = open != null) { key -> (key as? String)?.takeIf { it in selection.order } }.testTag("artist-list")) {
                if (artist == null) items(artists, key = { "artist:${it.name}" }) { entry ->
                    val count = entry.songs.count { it.available }
                    LibraryRow(entry.name, "$count song${if (count == 1) "" else "s"}", Symbol.Person) { onArtist(entry.name) }
                } else if (open != null) {
                    val playable = open.songs.count { it.available }
                    if (playable > 1) item(key = "play-all") { PlayAllRow(playable, { onPlayAll(open.songs, PlaySource(open.name, PlaySource.ARTIST, open.name), false) }, { onPlayAll(open.songs, PlaySource(open.name, PlaySource.ARTIST, open.name), true) }) }
                    items(open.songs, key = { it.id }) { song -> SelectableSongRow(song, songSubtitle(song, showArtist = false), selection, onSongAction, playing = song.id == currentId) {
                        onSong(song, open.songs, PlaySource(open.name, PlaySource.ARTIST, open.name)) } }
                }
                if (artists.isEmpty()) item { EmptyState(Symbol.Person, "No artists yet", "Artists come from file names like “Artist - Title.mp3”.") }
                else if (artist == null && artists.size == 1 && artists[0].name == Artists.UNKNOWN) item {
                    Text("Artists come from file names like “Artist - Title.mp3”; none of yours follow that pattern.", Modifier.padding(20.dp), style = MaterialTheme.typography.bodySmall)
                }
            } }
            else {
                // Folder rows come first and stand for every song under them, so they can be selected alongside songs.
                val shownFolders = if (browsingFolders) childFolders else emptyList()
                val folderSongs = remember(state.folders, ordered, shownFolders) { folderGroups(state.folders, ordered, shownFolders.map { it.id }) }
                val visibleIds = remember(visible, shownFolders) { shownFolders.map { folderKey(it.id) } + visible.map { it.id } }
                BindSelection(selection, "songs:$folderId/$category", visibleIds, visible, groups = folderSongs)
                val selecting = selection.active
                LazyColumn(state = listState, modifier = Modifier.fillMaxSize().sweepSelect(listState, selection) { key -> (key as? String)?.takeIf { it in selection.order } }.testTag("library-list")) {
                    // The sort control scrolls away with the list; beside Play all / Shuffle when they show, alone otherwise.
                    val playable = visible.count { it.available }
                    if (showSort && playable <= 1) item(key = "sort-control") {
                        Box(Modifier.fillMaxWidth().padding(horizontal = 8.dp), contentAlignment = Alignment.CenterEnd) { SortButton(sort, onSort) }
                    }
                    if (browsingFolders) items(childFolders, key = { folderKey(it.id) }) { item ->
                        val count = songCounts[item.id] ?: 0
                        val detail = if (count > 0) "$count song${if (count == 1) "" else "s"}" else if (item.path != item.name) item.path else "Folder"
                        val key = folderKey(item.id)
                        LibraryRow(item.name, if (!item.available) "Folder unavailable · saved listing" else detail, Symbol.Folder,
                            selected = if (selecting) key in selection.selection.ids else null, onSelect = { selection.begin(key) }) {
                            if (selecting) selection.toggle(key) else onFolder(item.id)
                        }
                    }
                    val source = if (!browsingFolders) PlaySource.AllSongs else PlaySource(state.folders.firstOrNull { it.id == parent }?.name ?: "Music", PlaySource.FOLDER, parent)
                    if (playable > 1) item(key = "play-all") { PlayAllRow(playable, { onPlayAll(visible, source, false) }, { onPlayAll(visible, source, true) }, trailing = if (showSort) ({ SortButton(sort, onSort) }) else null) }
                    // Inside a folder every row shares the folder, so rows show artist and length only.
                    items(visible, key = { it.id }) { song -> SelectableSongRow(song, songSubtitle(song, showFolder = !browsingFolders), selection, onSongAction, playing = song.id == currentId) {
                        onSong(song, visible, source) } }
                    if (visible.isEmpty() && (!browsingFolders || childFolders.isEmpty())) item {
                        EmptyState(Symbol.Music, "No MP3s in this ${if (browsingFolders) "folder" else "library"}", "Try another folder, or rescan after adding files.")
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
    if (showFolder || song.artist.isBlank() && song.durationMs == null) folderLabel(song) else null,
    song.durationMs?.let { "%d:%02d".format(it / 60000, (it / 1000) % 60) }
).joinToString(" · ")

/** The song's folder as people see it; songs at the top of the chosen folder say so, the same on every screen. */
fun folderLabel(song: LocalSong): String = song.folderPath.ifEmpty { "Top folder" }

/** The current song's second line in lists: who it is by (or where it lives), then the playback status. */
fun nowPlayingLine(song: LocalSong, status: String): String = "${song.artist.ifBlank { folderLabel(song) }} · $status"

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
            RadioIcon(Symbol.Play, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(4.dp)); Text("Play all · $count", maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, style = MaterialTheme.typography.labelLarge)
        }
        OutlinedButton(onClick = onShuffle, contentPadding = PaddingValues(horizontal = 10.dp), modifier = Modifier.weight(if (trailing != null) 0.8f else 1f).heightIn(min = 40.dp).testTag("shuffle-all")) {
            RadioIcon(Symbol.Shuffle, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(4.dp)); Text("Shuffle", maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, style = MaterialTheme.typography.labelLarge)
        }
        trailing?.invoke()
    }
}
