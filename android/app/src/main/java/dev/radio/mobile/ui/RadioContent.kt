package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.LibraryState
import dev.radio.mobile.playback.PlaybackController
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RadioContent(radio:RadioState,repository:RadioRepository,audio:PlaybackState,playback:PlaybackController,library:LibraryState,onLibrary:()->Unit,onPlayer:()->Unit,modifier:Modifier=Modifier) {
    var tuning by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    val ready=radio.loaded && !radio.busy && radio.suggestions.isNotEmpty() && !library.accessLost && !library.scanning
    fun start(){playback.startRadio();onPlayer()}
    LazyColumn(modifier.fillMaxSize().testTag("radio-screen"),contentPadding=PaddingValues(bottom=16.dp)) {
        item {
            Column(Modifier.padding(horizontal=20.dp,vertical=12.dp)) {
                Text("On this phone",style=MaterialTheme.typography.labelLarge,color=MaterialTheme.colorScheme.primary)
                Text(radio.status,Modifier.padding(top=4.dp),style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
                Row(Modifier.fillMaxWidth().padding(top=16.dp),verticalAlignment=Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)){Text(radio.mood.replaceFirstChar{it.uppercase()},style=MaterialTheme.typography.titleLarge);Text("Exploration ${(radio.exploration*100).toInt()}%",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}
                    OutlinedButton(onClick={tuning=true},modifier=Modifier.testTag("tune-radio")){Text("Tune")}
                }
                Button(onClick={if(audio.queue.isNotEmpty())confirm=true else start()},enabled=ready,modifier=Modifier.fillMaxWidth().padding(top=16.dp).testTag("start-radio")){RadioIcon(Symbol.Play);Spacer(Modifier.width(8.dp));Text(if(audio.radioActive)"Restart Radio" else "Start Radio")}
                if(audio.radioActive)TextButton(onClick=onPlayer,modifier=Modifier.align(Alignment.CenterHorizontally)){Text("Now playing · Radio")}
                if(radio.vibeLocalId!=null){Text("Vibe lock · ${((radio.vibeUntil-System.currentTimeMillis()).coerceAtLeast(0)/60000)+1} min · ${radio.vibeSongsLeft} songs left",Modifier.padding(top=8.dp),style=MaterialTheme.typography.bodySmall);TextButton(onClick=repository::clearVibe){Text("Release vibe lock")}}
                radio.error?.let{Text(it,Modifier.padding(top=12.dp),color=MaterialTheme.colorScheme.error)}
            }
        }
        item {Row(Modifier.fillMaxWidth().padding(start=20.dp,end=8.dp),verticalAlignment=Alignment.CenterVertically){Text("Suggested next",Modifier.weight(1f),style=MaterialTheme.typography.titleMedium);TextButton(onClick=repository::refresh,enabled=!radio.busy,modifier=Modifier.testTag("refresh-radio")){Text(if(radio.busy)"Refreshing…" else "Refresh")}}}
        itemsIndexed(radio.suggestions,key={_,row->row.song.id}) { index,row->
            LibraryRow(row.song.title,"${index+1} · ${if(row.pick.vectorFallback)"Local choice" else when(row.pick.slot){"exploit"->"For your taste";"explore"->"Explore";"wildcard"->"Something different";"context"->"Fits the mood";else->"Local choice"}}",Symbol.Music,trailing={IconButton(onClick={playback.addToQueue(listOf(row.song))},enabled=ready,modifier=Modifier.testTag("radio-add-${row.song.id}")){RadioIcon(Symbol.Add,"Add ${row.song.title} to queue")}}){if(ready)playback.addToQueue(listOf(row.song))}
        }
        item {Column(Modifier.padding(horizontal=20.dp,vertical=8.dp)){
            if(radio.suggestions.isEmpty() && radio.loaded && !radio.busy){Text(if(library.accessLost)"Choose your Music folder again to continue." else "No eligible songs. Add local music or change feedback in Now Playing.",style=MaterialTheme.typography.bodyMedium);TextButton(onClick=onLibrary){Text("Browse music")}}
            if(radio.suggestions.isNotEmpty())TextButton(onClick={playback.addToQueue(radio.suggestions.map{it.song})},enabled=ready,modifier=Modifier.testTag("add-radio-recommendations")){Text("Add recommendations to queue")}
            Text("Refresh and tuning change suggestions. Start Radio begins a new queue and keeps adding local music as it plays.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
        }}
    }
    if(confirm)AlertDialog(onDismissRequest={confirm=false},title={Text("Start a new Radio queue?")},text={Text("This replaces the current queue and starts the first recommendation.")},confirmButton={TextButton(onClick={confirm=false;start()},modifier=Modifier.testTag("confirm-start-radio")){Text("Start Radio")}},dismissButton={TextButton(onClick={confirm=false}){Text("Cancel")}})
    if(tuning)ModalBottomSheet(onDismissRequest={tuning=false}) {
        Column(Modifier.padding(horizontal=24.dp).padding(bottom=24.dp).testTag("radio-tuning")) {
            Text("Tune Radio",style=MaterialTheme.typography.titleLarge)
            Text("Mood",Modifier.padding(top=20.dp,bottom=8.dp),style=MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement=Arrangement.spacedBy(8.dp)){OfflineRecommender.MOODS.keys.forEach { mood->FilterChip(radio.mood==mood,onClick={repository.setMood(mood)},label={Text(mood.replaceFirstChar{it.uppercase()})},modifier=Modifier.testTag("radio-mood-$mood"))}}
            Text("Exploration",Modifier.padding(top=16.dp),style=MaterialTheme.typography.titleSmall)
            var exploration by remember(radio.exploration){mutableFloatStateOf(radio.exploration.toFloat())}
            Slider(exploration,onValueChange={exploration=it},onValueChangeFinished={repository.setExploration(exploration.toDouble())},modifier=Modifier.testTag("radio-exploration"))
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){Text("Familiar",style=MaterialTheme.typography.bodySmall);Text("Adventurous",style=MaterialTheme.typography.bodySmall)}
            Text("Applies to future suggestions. Your current song and queue stay in place.",Modifier.padding(top=16.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick={tuning=false},modifier=Modifier.align(Alignment.End)){Text("Done")}
        }
    }
}
