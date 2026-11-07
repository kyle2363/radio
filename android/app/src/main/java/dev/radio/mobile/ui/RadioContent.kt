package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.foundation.text.KeyboardOptions
import dev.radio.mobile.data.LibraryState
import dev.radio.mobile.playback.PlaybackController
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RadioContent(radio:RadioState,repository:RadioRepository,audio:PlaybackState,playback:PlaybackController,library:LibraryState,onLibrary:()->Unit,onPlayer:()->Unit,modifier:Modifier=Modifier,connected:ConnectedUi=ConnectedUi()) {
    var tuning by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    var seedPicker by remember { mutableStateOf(false) }
    var selectedSeed by remember { mutableStateOf<String?>(null) }
    var bootstrapPicker by remember { mutableStateOf(false) }
    var showDiagnostics by remember { mutableStateOf(false) }
    val ready=radio.loaded && !radio.busy && radio.suggestions.isNotEmpty() && !library.accessLost && !library.scanning
    fun start(){playback.startRadio(selectedSeed);onPlayer()}
    LazyColumn(modifier.fillMaxSize().testTag("radio-screen"),contentPadding=PaddingValues(bottom=16.dp)) {
        item {
            Column(Modifier.padding(horizontal=20.dp,vertical=12.dp)) {
                Text("On this phone",style=MaterialTheme.typography.labelLarge,color=MaterialTheme.colorScheme.primary)
                Text(radio.status,Modifier.padding(top=4.dp),style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
                Row(Modifier.fillMaxWidth().padding(top=16.dp),verticalAlignment=Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)){Text(radio.mood.replaceFirstChar{it.uppercase()},style=MaterialTheme.typography.titleLarge);Text("Exploration ${(radio.exploration*100).toInt()}%",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}
                    OutlinedButton(onClick={tuning=true},modifier=Modifier.testTag("tune-radio")){Text("Tune")}
                }
                Button(onClick={selectedSeed=null;if(audio.queue.isNotEmpty())confirm=true else start()},enabled=radio.loaded && !radio.busy && radio.diagnostics.eligibleTracks>0 && !library.accessLost && !library.scanning,modifier=Modifier.fillMaxWidth().padding(top=16.dp).testTag("start-radio")){RadioIcon(Symbol.Play);Spacer(Modifier.width(8.dp));Text(if(audio.radioActive)"Restart Radio" else "Pick for me")}
                OutlinedButton(onClick={seedPicker=true},enabled=radio.loaded && !radio.busy && !library.accessLost && !library.scanning,modifier=Modifier.fillMaxWidth().testTag("choose-radio-song")){Text("Choose a song")}
                if(audio.radioActive)TextButton(onClick=onPlayer,modifier=Modifier.align(Alignment.CenterHorizontally)){Text("Now playing · Radio")}
                if (radio.bootstrapLocalIds.isNotEmpty()) {
                    Row(Modifier.fillMaxWidth().padding(top=8.dp), verticalAlignment=Alignment.CenterVertically) {
                        Text("${radio.bootstrapLocalIds.size} temporary starting songs", Modifier.weight(1f), style=MaterialTheme.typography.bodySmall, color=MaterialTheme.colorScheme.onSurfaceVariant)
                        TextButton(onClick={bootstrapPicker=true},modifier=Modifier.testTag("edit-bootstrap-songs")){Text("Edit")}
                        TextButton(onClick=repository::clearBootstrap,modifier=Modifier.testTag("clear-bootstrap")){Text("Clear")}
                    }
                } else if (!radio.diagnostics.hasUsableTaste && radio.bootstrapCandidates.isNotEmpty() && !radio.bootstrapDismissed) {
                    Card(Modifier.fillMaxWidth().padding(top=12.dp)) {
                        Column(Modifier.padding(16.dp)) {
                            Text("Shape your first recommendations", style=MaterialTheme.typography.titleSmall)
                            Text("Choose a few songs as temporary starting points. This does not like or rate them.", Modifier.padding(top=4.dp), style=MaterialTheme.typography.bodySmall, color=MaterialTheme.colorScheme.onSurfaceVariant)
                            Row(Modifier.fillMaxWidth(), horizontalArrangement=Arrangement.End, verticalAlignment=Alignment.CenterVertically) {
                                TextButton(onClick=repository::dismissBootstrap,modifier=Modifier.testTag("dismiss-bootstrap")){Text("Not now")}
                                Button(onClick={bootstrapPicker=true},modifier=Modifier.testTag("choose-bootstrap-songs")){Text("Choose starting songs")}
                            }
                        }
                    }
                }
                TextButton(onClick={showDiagnostics=!showDiagnostics},modifier=Modifier.testTag("recommendation-details")) {
                    Text(if(showDiagnostics) "Hide recommendation details" else "Recommendation details")
                }
                if(showDiagnostics) Column(Modifier.fillMaxWidth().padding(start=4.dp,bottom=4.dp),verticalArrangement=Arrangement.spacedBy(2.dp)) {
                    Text("Eligible tracks · ${radio.diagnostics.eligibleTracks}",style=MaterialTheme.typography.bodySmall)
                    Text("Matched tracks · ${radio.matched}",style=MaterialTheme.typography.bodySmall)
                    Text("Local-only tracks · ${radio.localOnly}",style=MaterialTheme.typography.bodySmall)
                    Text("Tracks with usable text vectors · ${radio.diagnostics.textVectorTracks}",style=MaterialTheme.typography.bodySmall)
                    Text("Tracks with usable audio vectors · ${radio.diagnostics.audioVectorTracks}",style=MaterialTheme.typography.bodySmall)
                    if(radio.legacyVectorTracks > 0) {
                        Text("Legacy desktop vector tracks · ${radio.legacyVectorTracks} (${if(radio.legacyVectorsEnabled) "enabled" else "off"})",style=MaterialTheme.typography.bodySmall)
                        Text("Unique eligible tracks with valid legacy vectors.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Text("Taste events · ${radio.diagnostics.tasteEvents} (${radio.diagnostics.contributingTasteEvents} contributing)",style=MaterialTheme.typography.bodySmall)
                }
                if(radio.vibeLocalId!=null){Text("Vibe lock · ${((radio.vibeUntil-System.currentTimeMillis()).coerceAtLeast(0)/60000)+1} min · ${radio.vibeSongsLeft} songs left",Modifier.padding(top=8.dp),style=MaterialTheme.typography.bodySmall);TextButton(onClick=repository::clearVibe){Text("Release vibe lock")}}
                radio.error?.let{Text(it,Modifier.padding(top=12.dp),color=MaterialTheme.colorScheme.error)}
            }
        }
        item(key="up-next-options") {
            val options=ConnectedPolicy.visibleOptions(connected,audio.currentId,audio.radioActive,audio.playOrder.toSet(),repository::isDisliked){id->library.songs.any{it.id==id && it.available}}
            if(options.isNotEmpty())UpNextOptionsCard(options,{pick->playback.addToQueue(listOf(pick.song),true)},Modifier.padding(horizontal=20.dp,vertical=8.dp))
        }
        item {Row(Modifier.fillMaxWidth().padding(start=20.dp,end=8.dp),verticalAlignment=Alignment.CenterVertically){Text("Suggested next",Modifier.weight(1f),style=MaterialTheme.typography.titleMedium);TextButton(onClick=repository::refreshSuggestions,enabled=!radio.busy,modifier=Modifier.testTag("refresh-radio")){Text(if(radio.busy)"Refreshing…" else "Refresh")}}}
        item {
            radio.suggestionAnchorTitle?.let { title ->
                Text("Continues after $title",Modifier.padding(horizontal=20.dp).padding(bottom=8.dp),
                    style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        itemsIndexed(radio.suggestions,key={_,row->row.song.id}) { index,row->
            LibraryRow(row.song.title,"${index+1} · ${row.pick.reason}" + (row.pick.evidence?.let { " · $it" } ?: ""),Symbol.Music,subtitleMaxLines=2,trailing={IconButton(onClick={playback.addToQueue(listOf(row.song))},enabled=ready,modifier=Modifier.testTag("radio-add-${row.song.id}")){RadioIcon(Symbol.Add,"Add ${row.song.title} to queue")}}){if(ready)playback.addToQueue(listOf(row.song))}
        }
        item {Column(Modifier.padding(horizontal=20.dp,vertical=8.dp)){
            if(radio.suggestions.isEmpty() && radio.loaded && !radio.busy){Text(if(library.accessLost)"Choose your Music folder again to continue." else if(radio.diagnostics.eligibleTracks>0) "No different suggestions left. Pick for me or choose a song to start Radio." else "No eligible songs. Add local music or change feedback in Now Playing.",style=MaterialTheme.typography.bodyMedium);TextButton(onClick=onLibrary){Text("Browse music")}}
            if(radio.suggestions.isNotEmpty())TextButton(onClick={playback.addToQueue(radio.suggestions.map{it.song})},enabled=ready,modifier=Modifier.testTag("add-radio-recommendations")){Text("Add recommendations to queue")}
            Text("Refresh and tuning change suggestions. Start Radio begins a new queue and keeps adding local music as it plays.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
        }}
    }
    if(seedPicker) AlertDialog(onDismissRequest={seedPicker=false},title={Text("Start Radio from a song")},text={
        var query by remember { mutableStateOf("") }
        Column {
            OutlinedTextField(query,{query=it},label={Text("Search your library")},singleLine=true,modifier=Modifier.testTag("radio-seed-search"))
            LazyColumn(Modifier.heightIn(max=320.dp)) {
                items(library.songs.filter{it.available && !repository.isDisliked(it.id) && (it.title.contains(query,true) || it.artist.contains(query,true))},key={it.id}) { song ->
                    TextButton(onClick={selectedSeed=song.id;seedPicker=false;if(audio.queue.isNotEmpty())confirm=true else start()},modifier=Modifier.testTag("radio-seed-${song.id}")){Text(song.title)}
                }
            }
        }
    },confirmButton={},dismissButton={TextButton(onClick={seedPicker=false}){Text("Cancel")}})
    if(bootstrapPicker) BootstrapSongPicker(
        songs=library.songs.filter { it.available && it.id in radio.bootstrapCandidates },
        selected=radio.bootstrapLocalIds,
        onDismiss={bootstrapPicker=false},
        onApply={selected ->
            bootstrapPicker=false
            if(selected.isEmpty()) repository.clearBootstrap() else repository.setBootstrap(selected)
        },
    )
    if(confirm)AlertDialog(onDismissRequest={confirm=false},title={Text("Start a new Radio queue?")},text={Text("This replaces the current queue and starts " + (library.songs.firstOrNull{it.id==selectedSeed}?.title ?: "the first recommendation") + ".")},confirmButton={TextButton(onClick={confirm=false;start()},modifier=Modifier.testTag("confirm-start-radio")){Text("Start Radio")}},dismissButton={TextButton(onClick={confirm=false}){Text("Cancel")}})
    if(tuning)ModalBottomSheet(onDismissRequest={tuning=false}) {
        Column(Modifier.padding(horizontal=24.dp).padding(bottom=24.dp).testTag("radio-tuning")) {
            Text("Tune Radio",style=MaterialTheme.typography.titleLarge)
            Text("Mood",Modifier.padding(top=20.dp,bottom=8.dp),style=MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement=Arrangement.spacedBy(8.dp)){OfflineRecommender.MOODS.keys.forEach { mood->FilterChip(radio.mood==mood,onClick={repository.setMood(mood)},label={Text(mood.replaceFirstChar{it.uppercase()})},modifier=Modifier.testTag("radio-mood-$mood"))}}
            Text("Exploration",Modifier.padding(top=16.dp),style=MaterialTheme.typography.titleSmall)
            var exploration by remember(radio.exploration){mutableFloatStateOf(radio.exploration.toFloat())}
            Slider(exploration,onValueChange={exploration=it},onValueChangeFinished={repository.setExploration(exploration.toDouble())},modifier=Modifier.testTag("radio-exploration"))
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){Text("Familiar",style=MaterialTheme.typography.bodySmall);Text("Adventurous",style=MaterialTheme.typography.bodySmall)}
            Text("At 0%, Radio prioritizes similarity and stays near the starting song. Listening taste breaks near ties.",Modifier.padding(top=8.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Text("Applies to future suggestions. Your current song and queue stay in place.",Modifier.padding(top=16.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick={tuning=false},modifier=Modifier.align(Alignment.End)){Text("Done")}
        }
    }
}

@Composable
private fun BootstrapSongPicker(
    songs: List<dev.radio.mobile.data.LocalSong>,
    selected: Set<String>,
    onDismiss: () -> Unit,
    onApply: (Set<String>) -> Unit,
) {
    var query by remember { mutableStateOf("") }
    var chosen by remember(selected) { mutableStateOf(selected.intersect(songs.map { it.id }.toSet())) }
    val visibleSongs = remember(songs, query) {
        val needle = query.trim().lowercase()
        if (needle.isEmpty()) songs else songs.filter { it.title.lowercase().contains(needle) || it.artist.lowercase().contains(needle) || it.folderPath.lowercase().contains(needle) }
    }
    AlertDialog(
        onDismissRequest=onDismiss,
        title={Text("Choose starting songs")},
        text={
            Column {
                Text("Pick up to 5 songs to guide early suggestions. They are temporary and do not change likes or ratings.", style=MaterialTheme.typography.bodySmall, color=MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(
                    value=query,
                    onValueChange={query=it},
                    modifier=Modifier.fillMaxWidth().padding(top=12.dp).testTag("bootstrap-picker-search"),
                    label={Text("Search your library")},
                    singleLine=true,
                    keyboardOptions=KeyboardOptions(capitalization=KeyboardCapitalization.None),
                )
                Text("${chosen.size} of 5 selected",Modifier.padding(top=8.dp),style=MaterialTheme.typography.labelMedium)
                LazyColumn(Modifier.fillMaxWidth().heightIn(max=320.dp).testTag("bootstrap-picker-list")) {
                    items(visibleSongs, key={it.id}) { song ->
                        val isSelected = song.id in chosen
                        fun toggleSelection() {
                            chosen = if (isSelected) chosen - song.id else if (chosen.size < 5) chosen + song.id else chosen
                        }
                        Row(
                            Modifier.fillMaxWidth().heightIn(min=48.dp).testTag("bootstrap-song-${song.id}")
                                .toggleable(value=isSelected,role=androidx.compose.ui.semantics.Role.Checkbox,onValueChange={toggleSelection()}),
                            verticalAlignment=Alignment.CenterVertically,
                        ) {
                            Checkbox(checked=isSelected,onCheckedChange=null)
                            Column(Modifier.weight(1f).padding(vertical=6.dp)) {
                                Text(song.title,maxLines=1,overflow=TextOverflow.Ellipsis,style=MaterialTheme.typography.bodyMedium)
                                Text(song.folderPath,maxLines=1,overflow=TextOverflow.Ellipsis,style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                    if(visibleSongs.isEmpty()) item { Text("No matching songs",Modifier.padding(16.dp),style=MaterialTheme.typography.bodyMedium) }
                }
            }
        },
        confirmButton={TextButton(onClick={onApply(chosen)},modifier=Modifier.testTag("apply-bootstrap")){Text("Apply")}},
        dismissButton={TextButton(onClick=onDismiss,modifier=Modifier.testTag("cancel-bootstrap")){Text("Cancel")}},
    )
}

@Composable
fun RadioSimilaritySettings(radio:RadioState,repository:RadioRepository) {
                if (radio.legacyVectorTracks > 0) {
                    Card(Modifier.fillMaxWidth().padding(top=12.dp)) {
                        Column(Modifier.padding(16.dp)) {
                            Text("Legacy desktop similarity", style=MaterialTheme.typography.titleSmall)
                            Text(
                                "Existing desktop vectors don't record their original model. Radio can use them within this catalog, but compatibility cannot be verified.",
                                Modifier.padding(top=4.dp),
                                style=MaterialTheme.typography.bodySmall,
                                color=MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Button(
                                onClick={repository.setLegacyVectorsEnabled(!radio.legacyVectorsEnabled)},
                                modifier=Modifier.align(Alignment.End).testTag("use-desktop-similarity"),
                            ) {
                                Text(if(radio.legacyVectorsEnabled) "Stop using legacy vectors" else "Use desktop similarity")
                            }
                        }
                    }
                }
}
