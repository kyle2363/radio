package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.foundation.text.KeyboardOptions
import dev.radio.mobile.data.LibraryState
import dev.radio.mobile.playback.PlaybackController
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*

/**
 * The Radio tab (checkpoint 4.2). Off air: the station card, first-run starting songs, mood and how far to wander.
 * On air: the station card, Coming up, and "Add to the station" (local suggestions plus desktop options).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RadioContent(radio:RadioState,repository:RadioRepository,audio:PlaybackState,playback:PlaybackController,library:LibraryState,onLibrary:()->Unit,onPlayer:()->Unit,modifier:Modifier=Modifier,connected:ConnectedUi=ConnectedUi(),
    paired:Boolean=false,onChooseFolder:()->Unit={},onRescan:()->Unit={},onFullQueue:()->Unit={},onEnd:()->Unit={},openTune:Boolean=false,onTuneOpened:()->Unit={},onServer:()->Unit={}) {
    var tuning by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    var seedPicker by remember { mutableStateOf(false) }
    var bootstrapPicker by remember { mutableStateOf(false) }
    var showDiagnostics by remember { mutableStateOf(false) }
    LaunchedEffect(openTune) { if(openTune){tuning=true;onTuneOpened()} }
    val onAir=audio.radioActive
    val canStart=radio.loaded && !radio.busy && radio.diagnostics.eligibleTracks>0 && !library.accessLost && !library.scanning
    val block=when {
        library.accessLost -> StationBlock.AccessLost
        library.scanning -> StationBlock.Scanning
        !radio.loaded -> StationBlock.Loading
        radio.diagnostics.eligibleTracks==0 -> StationBlock.Empty
        else -> null
    }
    fun start(seed:String?){playback.startRadio(seed);onPlayer()}
    LazyColumn(modifier.fillMaxSize().testTag("radio-screen"),contentPadding=PaddingValues(start=16.dp,end=16.dp,bottom=16.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
        item(key="header") {
            Row(Modifier.fillMaxWidth().padding(start=4.dp,top=12.dp),verticalAlignment=Alignment.Bottom) {
                Column(Modifier.weight(1f)) {
                    Text("Radio",Modifier.semantics{heading()},style=MaterialTheme.typography.headlineMedium)
                    Text(when {
                        library.accessLost -> "Music folder access was lost"
                        library.scanning -> "Scanning your library…"
                        radio.loaded -> "%,d songs ready on this phone".format(radio.diagnostics.eligibleTracks)
                        else -> radio.status
                    },style=MaterialTheme.typography.bodySmall,color=if(library.accessLost)MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,modifier=Modifier.testTag("radio-status"))
                }
                if(onAir)OutlinedButton(onClick={tuning=true},modifier=Modifier.testTag("tune-radio")){RadioIcon(Symbol.Settings,modifier=Modifier.size(16.dp));Spacer(Modifier.width(6.dp));Text("Tune")}
            }
        }
        item(key="station") {
            when {
                onAir -> OnAirStationCard(audio,radio,onOpenPlayer=onPlayer,onVibe={if(radio.vibeLocalId!=null)repository.clearVibe() else repository.lockVibe()},
                    onNewMix={playback.newMix()},onEnd=onEnd)
                block!=null -> BlockedStationCard(block,library.scannedCount,library.songs.size,onChooseFolder,onLibrary,onRescan)
                else -> OffAirStationCard(canStart,onPickForMe={if(audio.queue.isNotEmpty())confirm=true else start(null)},onFromSong={seedPicker=true})
            }
        }
        radio.error?.let{error->item(key="error"){Text(error,Modifier.padding(horizontal=4.dp),color=MaterialTheme.colorScheme.error)}}
        if(!onAir) {
            item(key="bootstrap") {
                if (radio.bootstrapLocalIds.isNotEmpty()) {
                    Row(Modifier.fillMaxWidth().padding(horizontal=4.dp), verticalAlignment=Alignment.CenterVertically) {
                        Text("${radio.bootstrapLocalIds.size} temporary starting songs", Modifier.weight(1f), style=MaterialTheme.typography.bodySmall, color=MaterialTheme.colorScheme.onSurfaceVariant)
                        TextButton(onClick={bootstrapPicker=true},modifier=Modifier.testTag("edit-bootstrap-songs")){Text("Edit")}
                        TextButton(onClick=repository::clearBootstrap,modifier=Modifier.testTag("clear-bootstrap")){Text("Clear")}
                    }
                } else if (!radio.diagnostics.hasUsableTaste && radio.bootstrapCandidates.isNotEmpty() && !radio.bootstrapDismissed && block==null) {
                    Card(Modifier.fillMaxWidth(),colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.surfaceContainer)) {
                        Column(Modifier.padding(16.dp)) {
                            Text("Shape your first recommendations", style=MaterialTheme.typography.titleSmall)
                            Text("Radio hasn't learned your taste yet. Choose up to 5 songs as starting points. This doesn't like or rate them.", Modifier.padding(top=4.dp), style=MaterialTheme.typography.bodySmall, color=MaterialTheme.colorScheme.onSurfaceVariant)
                            Row(Modifier.fillMaxWidth().padding(top=8.dp), horizontalArrangement=Arrangement.End, verticalAlignment=Alignment.CenterVertically) {
                                TextButton(onClick=repository::dismissBootstrap,modifier=Modifier.testTag("dismiss-bootstrap")){Text("Not now")}
                                Button(onClick={bootstrapPicker=true},modifier=Modifier.testTag("choose-bootstrap-songs")){Text("Choose starting songs")}
                            }
                        }
                    }
                }
            }
            item(key="mood") {
                Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    Text("Mood",Modifier.padding(horizontal=4.dp).semantics{heading()},style=MaterialTheme.typography.titleSmall)
                    MoodChips(radio.mood,repository::setMood,enabled=!library.accessLost)
                }
            }
            item(key="wander") {
                Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    Text("How far to wander",Modifier.padding(horizontal=4.dp).semantics{heading()},style=MaterialTheme.typography.titleSmall)
                    ExplorationPresets(radio.exploration,{repository.setExploration(it.value)},enabled=!library.accessLost)
                    Text(if(ExplorationPreset.exact(radio.exploration)==null) "Custom: ${ExplorationPreset.summary(radio.exploration)}. Fine-tune it in Tune once you're on air."
                        else "Fine-tune it and desktop picks in Tune once you're on air.",Modifier.padding(horizontal=4.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            item(key="coming-up") { ComingUp(audio,onRemove={playback.removeFromQueue(it)},onFullQueue=onFullQueue) }
            if(connected.status.enabled && connected.status.pendingAnchor!=null && connected.status.pendingAnchor==audio.currentId) item(key="desktop-asking") {
                Text("Asking your desktop for picks… Radio keeps going if it doesn't answer.",Modifier.fillMaxWidth().padding(horizontal=4.dp).testTag("desktop-asking"),
                    style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            }
            item(key="add-to-station") {
                val queued=audio.playOrder.toSet()
                val desktop=ConnectedPolicy.visibleOptions(connected,audio.currentId,audio.radioActive,queued,repository::isDisliked){id->library.songs.any{it.id==id && it.available}}
                    .map{StationAddRow(it.song,slotLabel(it.slot),true)}
                val local=radio.suggestions.filter{it.song.id !in queued && it.song.available}.map{StationAddRow(it.song,StationOrigin.of(it.pick).label,false)}
                val rows=(desktop+local).distinctBy{it.song.id}.take(5)
                AddToStation(rows,enabled=!library.accessLost,refreshing=radio.busy,onAdd={playback.addToQueue(listOf(it.song),true)},onRefresh=repository::refreshSuggestions)
            }
        }
        item(key="details") {
            TextButton(onClick={showDiagnostics=true},modifier=Modifier.testTag("recommendation-details")){Text("Station details")}
        }
    }
    if(seedPicker) StartFromSongSheet(library.songs.filter{it.available && !repository.isDisliked(it.id)},audio.queue.size,audio.source,
        onStart={id->seedPicker=false;start(id)},onDismiss={seedPicker=false})
    if(bootstrapPicker) BootstrapSongPicker(
        songs=library.songs.filter { it.available && it.id in radio.bootstrapCandidates },
        selected=radio.bootstrapLocalIds,
        onDismiss={bootstrapPicker=false},
        onApply={selected ->
            bootstrapPicker=false
            if(selected.isEmpty()) repository.clearBootstrap() else repository.setBootstrap(selected)
        },
    )
    if(showDiagnostics) ModalBottomSheet(onDismissRequest={showDiagnostics=false}) {
        Column(Modifier.verticalScroll(androidx.compose.foundation.rememberScrollState()).padding(horizontal=24.dp).padding(bottom=24.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Text("Station details",style=MaterialTheme.typography.titleLarge)
            StationDetails(radio)
            if(radio.bootstrapLocalIds.isNotEmpty()) Row(verticalAlignment=Alignment.CenterVertically) {
                Text("${radio.bootstrapLocalIds.size} starting songs",Modifier.weight(1f),style=MaterialTheme.typography.bodyMedium)
                TextButton(onClick={showDiagnostics=false;bootstrapPicker=true}){Text("Edit")}
                TextButton(onClick=repository::clearBootstrap){Text("Clear")}
            }
            TextButton(onClick={showDiagnostics=false},modifier=Modifier.align(Alignment.End)){Text("Done")}
        }
    }
    if(confirm)AlertDialog(onDismissRequest={confirm=false},title={Text("Start a new Radio queue?")},text={Text("This replaces your current queue (${audio.queue.size} ${if(audio.queue.size==1)"song" else "songs"}) and starts a station Radio picks for you. The song playing now stops.")},
        confirmButton={TextButton(onClick={confirm=false;start(null)},modifier=Modifier.testTag("confirm-pick-for-me")){Text("Start Radio")}},dismissButton={TextButton(onClick={confirm=false}){Text("Cancel")}})
    if(tuning)ModalBottomSheet(onDismissRequest={tuning=false}) {
        Column(Modifier.verticalScroll(androidx.compose.foundation.rememberScrollState()).padding(horizontal=24.dp).padding(bottom=24.dp).testTag("radio-tuning")) {
            Row(verticalAlignment=Alignment.CenterVertically) {
                Text("Tune station",Modifier.weight(1f),style=MaterialTheme.typography.titleLarge)
                Button(onClick={tuning=false},modifier=Modifier.testTag("tune-done")){Text("Done")}
            }
            Text("MOOD",Modifier.padding(top=20.dp,bottom=8.dp),style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
            MoodChips(radio.mood,repository::setMood)
            var exploration by remember(radio.exploration){mutableFloatStateOf(radio.exploration.toFloat())}
            Row(Modifier.fillMaxWidth().padding(top=16.dp),verticalAlignment=Alignment.CenterVertically) {
                Text("EXPLORATION",Modifier.weight(1f),style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
                Text(ExplorationPreset.summary(exploration.toDouble()),style=MaterialTheme.typography.labelLarge,modifier=Modifier.testTag("radio-exploration-label"))
            }
            Slider(exploration,onValueChange={exploration=it},onValueChangeFinished={repository.setExploration(exploration.toDouble())},modifier=Modifier.testTag("radio-exploration"))
            Row(Modifier.fillMaxWidth()){ExplorationPreset.entries.forEachIndexed{index,preset->
                Text(preset.label,Modifier.weight(1f),style=MaterialTheme.typography.bodySmall,
                    textAlign=when(index){0->androidx.compose.ui.text.style.TextAlign.Start;ExplorationPreset.entries.lastIndex->androidx.compose.ui.text.style.TextAlign.End;else->androidx.compose.ui.text.style.TextAlign.Center},
                    color=if(ExplorationPreset.nearest(exploration.toDouble())==preset)MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)}}
            Text("At Familiar, Radio stays near the starting song. Listening taste breaks near ties.",Modifier.padding(top=8.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            HorizontalDivider(Modifier.padding(top=16.dp))
            val connectedTuning by repository.connectedTuning.collectAsState()
            DesktopPicksSection(connected,paired,connectedTuning,onToggle=repository::setConnectedEnabled,onChange=repository::setConnectedTuning,onPair=onServer)
            Text("Changes apply from the next song. Your current song and queue stay put. Only this phone's picks change; your desktop's own Radio is untouched.",
                Modifier.padding(top=16.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
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
