package dev.radio.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.music.ArtistCatalog
import dev.radio.mobile.music.ArtistCredit
import dev.radio.mobile.music.Artists
import dev.radio.mobile.playback.PlaySource
import kotlinx.coroutines.launch

@Composable
internal fun ArtistBrowser(
    catalog: ArtistCatalog, artistId: String?, onArtist: (String) -> Unit,
    query: String, onQuery: (String) -> Unit, listState: LazyListState,
    selection: SelectionController, currentId: String?, canPlay: Boolean,
    onSong: (LocalSong, List<LocalSong>, PlaySource) -> Unit,
    onSongAction: (SongAction, LocalSong) -> Unit,
    onPlayAll: (List<LocalSong>, PlaySource, Boolean) -> Unit,
    onRadio: (String, List<LocalSong>) -> Unit,
) {
    val artist = catalog.byId[artistId]
    val songs = artist?.songs.orEmpty()
    val ids = remember(songs) { songs.map { it.id } }
    BindSelection(selection, "artist:$artistId", ids, songs)
    val source = artist?.let { PlaySource(it.name, PlaySource.ARTIST, it.name) }
    val sections = remember(catalog.entries, query) {
        catalog.entries.filter { it.name.contains(query.trim(), ignoreCase = true) }.groupBy(Artists::section)
    }
    val scope = rememberCoroutineScope()
    fun search(next: String) { onQuery(next); scope.launch { listState.scrollToItem(0) } }

    Column(Modifier.fillMaxSize()) {
        if (artist == null) OutlinedTextField(query, ::search, label = { Text("Find an artist") },
            leadingIcon = { RadioIcon(Symbol.Search) },
            trailingIcon = { if (query.isNotEmpty()) IconButton({ search("") }) { RadioIcon(Symbol.Close, "Clear artist search") } },
            singleLine = true, shape = CircleShape, keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("artist-search"))
        LazyColumn(state = listState, modifier = Modifier.fillMaxSize().testTag("artist-list")
            .sweepSelect(listState, selection, enabled = artist != null) { key -> (key as? String)?.takeIf { it in selection.order } }) {
            if (artist == null || source == null) {
                for ((section, entries) in sections) {
                    item(key = "section:$section") { ArtistSection(section) }
                    items(entries, key = { "artist:${it.id}" }) { entry ->
                        Row(Modifier.fillMaxWidth().clickable(role = Role.Button) { onArtist(entry.id) }
                            .heightIn(min = 58.dp).padding(horizontal = 16.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Surface(Modifier.size(38.dp), shape = CircleShape, color = MaterialTheme.colorScheme.secondaryContainer) {
                                Box(contentAlignment = Alignment.Center) {
                                    if (entry.id.isEmpty()) RadioIcon(Symbol.Person, modifier = Modifier.size(18.dp))
                                    else Text(entry.name.split(' ').take(2).mapNotNull { it.firstOrNull()?.uppercaseChar() }.joinToString(""),
                                        style = MaterialTheme.typography.labelLarge)
                                }
                            }
                            Column(Modifier.weight(1f)) {
                                Text(entry.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                val count = entry.songs.size
                                Text("$count ${if (count == 1) "song" else "songs"}", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            RadioIcon(Symbol.Forward, modifier = Modifier.size(18.dp))
                        }
                    }
                }
                if (sections.isEmpty()) item { EmptyState(Symbol.Person, if (query.isBlank()) "No artists yet" else "No matching artists", "") }
            } else {
                val leads = songs.filter { it.id in artist.leadSongIds }
                val guests = songs.filterNot { it.id in artist.leadSongIds }
                val ordered = leads + guests
                val playable = ordered.filter { it.available }
                if (canPlay && playable.isNotEmpty()) item(key = "artist-actions") {
                    Column {
                        PlayAllRow(playable.size, { onPlayAll(ordered, source, false) }, { onPlayAll(ordered, source, true) })
                        if (artist.id.isNotEmpty()) FilledTonalButton({ onRadio(artist.name, playable) },
                            Modifier.fillMaxWidth().padding(horizontal = 16.dp).testTag("artist-radio")) {
                            RadioIcon(Symbol.Radio, modifier = Modifier.size(16.dp)); Spacer(Modifier.width(8.dp)); Text("Start Radio")
                        }
                    }
                }
                for ((label, tracks) in listOf("Songs" to leads, "Appears on" to guests)) {
                    if (tracks.isEmpty()) continue
                    item(key = "artist-tracks:$label") { ArtistSection(label) }
                    items(tracks, key = { it.id }) { song ->
                        val credit = song.artist.ifBlank { catalog.credits[song.id]?.names.orEmpty().joinToString(" & ") }
                        SelectableSongRow(song, listOf(credit, songSubtitle(song, showArtist = false)).filter(String::isNotBlank).joinToString(" · "),
                            selection, onSongAction, playing = song.id == currentId) { onSong(song, ordered, source) }
                    }
                }
            }
        }
    }
}

@Composable
private fun ArtistSection(label: String) {
    Text(label, Modifier.padding(start = 16.dp, top = 12.dp, bottom = 4.dp).semantics { heading() },
        color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleSmall)
}

@Composable
internal fun ArtistCreditLink(song: LocalSong, artists: List<String>, onArtist: (String) -> Unit) {
    var expanded by remember(song.id) { mutableStateOf(false) }
    Box {
        Text(song.artist.ifBlank { artists.joinToString(" & ") }, color = MaterialTheme.colorScheme.primary,
            style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.testTag("now-playing-artists").clickable(role = Role.Button, onClickLabel = "Browse credited artists") {
                if (artists.size == 1) onArtist(Artists.key(artists.single())) else expanded = true
            }.padding(vertical = 6.dp))
        if (artists.size > 1) DropdownMenu(expanded, { expanded = false }) {
            artists.forEach { name -> DropdownMenuItem({ Text(name) }, { expanded = false; onArtist(Artists.key(name)) }) }
        }
    }
}

@Composable
internal fun ArtistCreditsDialog(song: LocalSong, credit: ArtistCredit, corrected: Boolean, sharedCount: Int,
    onSave: (ArtistCredit?) -> Unit, onDismiss: () -> Unit) {
    var lead by rememberSaveable(song.id) { mutableStateOf(credit.lead.joinToString("; ")) }
    var guests by rememberSaveable(song.id) { mutableStateOf(credit.guests.joinToString("; ")) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Edit artist credits") }, text = {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(song.artist.ifBlank { song.title })
            Text("Separate names with semicolons. ${if (sharedCount > 1) "Applies to $sharedCount songs with this credit." else "Applies to this song."}",
                style = MaterialTheme.typography.bodySmall)
            OutlinedTextField(lead, { lead = it }, label = { Text("Lead artists") }, modifier = Modifier.fillMaxWidth().testTag("credit-lead"))
            OutlinedTextField(guests, { guests = it }, label = { Text("Featured artists") }, modifier = Modifier.fillMaxWidth().testTag("credit-guests"))
            if (corrected) TextButton({ onSave(null) }) { Text("Reset to automatic") }
        }
    }, confirmButton = {
        TextButton({ onSave(ArtistCredit(Artists.names(lead), Artists.names(guests))) }, enabled = Artists.names(lead).isNotEmpty()) { Text("Save") }
    }, dismissButton = { TextButton(onDismiss) { Text("Cancel") } })
}
