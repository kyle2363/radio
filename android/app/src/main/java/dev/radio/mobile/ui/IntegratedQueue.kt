package dev.radio.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.playback.PlaybackController
import dev.radio.mobile.playback.PlaybackState
import kotlinx.coroutines.launch

@Composable
fun IntegratedQueue(audio: PlaybackState, accessAllowed: Boolean, onSelect: (String) -> Unit, modifier: Modifier = Modifier,
    playback: PlaybackController? = null, onAdd: () -> Unit = {}, onSave: (List<String>) -> Unit = {}, serverAiIds: Set<String> = emptySet()) {
    val origins = audio.origins
    val byId = remember(audio.queue) { audio.queue.associateBy { it.id } }
    val currentIndex = remember(audio.playOrder, audio.currentId) { audio.playOrder.indexOf(audio.currentId) }
    val list = rememberLazyListState(initialFirstVisibleItemIndex = currentIndex.coerceAtLeast(0))
    val scope = rememberCoroutineScope()
    var editing by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    // Long-press a row to drag it; swipe it sideways to remove it. Edit mode keeps one-step buttons for accessibility.
    val reorder = rememberReorderState(list) { key, index -> playback?.moveInQueue(key as String, index) }
    val haptic = rememberDragHaptic()
    // Start at the current song when opened, then leave browsing under the user's control.
    // Selecting or advancing a track updates the highlight without moving the viewport.
    Column(modifier) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text(if (audio.radioActive) "Radio adds more as these play" else if (audio.shuffled) "Shuffled order" else "In order", modifier = Modifier.weight(1f), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { if (currentIndex >= 0) scope.launch { list.animateScrollToItem(currentIndex) } }, enabled = currentIndex >= 0,
                modifier = Modifier.testTag("queue-position").semantics { stateDescription = "Track ${currentIndex + 1} of ${audio.playOrder.size}. Show current song." }) {
                Text("${currentIndex + 1} / ${audio.playOrder.size}")
            }
            if (playback != null) {
                if (editing) TextButton(onClick = { editing = false }) { Text("Done") }
                IconButton(onClick = onAdd, enabled = accessAllowed) { RadioIcon(Symbol.Add, "Add songs to queue") }
                Box {
                    IconButton(onClick = { menu = true }) { RadioIcon(Symbol.More, "Queue options") }
                    DropdownMenu(menu, { menu = false }) {
                        DropdownMenuItem(text = { Text("Edit queue") }, onClick = { menu = false; editing = true })
                        DropdownMenuItem(text = { Text("Save queue to playlist") }, onClick = { menu = false; onSave(audio.playOrder) })
                        DropdownMenuItem(text = { Text("Clear upcoming songs") }, enabled = audio.canNext, onClick = { menu = false; playback.clearUpcoming() })
                    }
                }
            }
        }
        LazyColumn(state = list, modifier = Modifier.fillMaxWidth().weight(1f).testTag("playback-queue"), contentPadding = PaddingValues(bottom = 12.dp)) {
            itemsIndexed(audio.playOrder, key = { _, id -> id }) { index, id ->
                val song = byId[id] ?: return@itemsIndexed
                val current = id == audio.currentId
                // Connected talk: the queued clip shows as its own removable row just before the song it introduces.
                audio.talk?.takeIf { !it.current && it.nextId == id }?.let { talk ->
                    // Same geometry as a song row (8 + 12 dp inset, 40 dp tile, 60 dp height) so the columns line up.
                    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp).heightIn(min = 60.dp).padding(horizontal = 12.dp, vertical = 8.dp).testTag("queue-talk"),
                        verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(40.dp).background(MaterialTheme.colorScheme.surfaceVariant, dev.radio.mobile.ui.theme.RadioShapes.Thumb), contentAlignment = Alignment.Center) {
                            CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onSurfaceVariant) { RadioIcon(Symbol.Person, null, Modifier.size(20.dp)) }
                        }
                        Column(Modifier.weight(1f).padding(start = 14.dp)) {
                            Text(talk.info.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                            Text("${talk.info.typeLabel} · Talk", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (playback != null) IconButton(onClick = { playback.removeFromQueue(talk.mediaId) }, modifier = Modifier.testTag("queue-talk-remove")) { RadioIcon(Symbol.Remove, "Remove talk") }
                    }
                }
                val tint = if (current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                SwipeToRemove(enabled = playback != null && !editing && !current && reorder.draggingKey == null, onRemove = { playback?.removeFromQueue(id) },
                    modifier = (if (reorder.settlingKey == id || reorder.draggingKey == id) Modifier else springPlacement()).reorderable(reorder, id, enabled = playback != null && !editing, onStart = haptic)) {
                // The current song sits on a rounded tonal block so it is easy to find in a long queue.
                Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp).clip(RoundedCornerShape(20.dp))
                    .background(if (reorder.draggingKey == id) MaterialTheme.colorScheme.surfaceContainerHigh else androidx.compose.ui.graphics.Color.Transparent)
                    .background(if (current) MaterialTheme.colorScheme.surfaceContainerHighest else androidx.compose.ui.graphics.Color.Transparent)
                    .selectable(selected = current, enabled = accessAllowed && song.available, role = Role.Button, onClick = { onSelect(id) })
                    .heightIn(min = 60.dp).padding(horizontal = 12.dp, vertical = 8.dp).testTag("queue-row-$id")
                    .semantics { if (current) stateDescription = "Current song. ${audio.status}" }, verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(40.dp).background(MaterialTheme.colorScheme.surfaceVariant, dev.radio.mobile.ui.theme.RadioShapes.Thumb), contentAlignment = Alignment.Center) {
                        CompositionLocalProvider(LocalContentColor provides tint) { RadioIcon(Symbol.Music, modifier = Modifier.size(20.dp)) }
                    }
                    Column(Modifier.weight(1f).padding(start = 14.dp)) {
                        Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium,
                            fontWeight = if (current) FontWeight.Medium else FontWeight.Normal, color = if (current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            val origin = origins[id]
                            val reason = origin?.takeIf { it != dev.radio.mobile.recommendation.StationOrigin.Desktop && it != dev.radio.mobile.recommendation.StationOrigin.User }?.label
                            Text(if (current) audio.status else if (!song.available) "Unavailable" else songSubtitle(song) + (reason?.let { " · $it" } ?: ""), Modifier.weight(1f, fill = false), maxLines = 1, overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.bodySmall, color = tint)
                            if ((id in serverAiIds || origin == dev.radio.mobile.recommendation.StationOrigin.Desktop) && !current) ServerAiMarker(Modifier.padding(start = 8.dp).testTag("queue-server-ai-$id"))
                            else if (origin == dev.radio.mobile.recommendation.StationOrigin.User && !current) OriginTag("Added by you", Modifier.padding(start = 8.dp).testTag("queue-user-$id"))
                        }
                    }
                    if (playback != null) {
                        if (editing) {
                            IconButton(onClick = { playback.moveInQueue(id, index - 1) }, enabled = index > 0) { RadioIcon(Symbol.Up, "Move ${song.title} up") }
                            IconButton(onClick = { playback.moveInQueue(id, index + 1) }, enabled = index < audio.playOrder.lastIndex) { RadioIcon(Symbol.Down, "Move ${song.title} down") }
                            IconButton(onClick = { playback.removeFromQueue(id) }) { RadioIcon(Symbol.Remove, "Remove ${song.title} from queue") }
                        } else {
                            var options by remember { mutableStateOf(false) }
                            Box {
                                IconButton(onClick = { options = true }, modifier = Modifier.testTag("queue-menu-$id")) { RadioIcon(Symbol.More, "Queue options for ${song.title}") }
                                DropdownMenu(options, { options = false }) {
                                    DropdownMenuItem(text = { Text("Play next") }, enabled = !current && accessAllowed && song.available, onClick = { options = false; playback.addToQueue(listOf(song), true) })
                                    DropdownMenuItem(text = { Text("Move up") }, enabled = index > 0, onClick = { options = false; playback.moveInQueue(id, index - 1) })
                                    DropdownMenuItem(text = { Text("Move down") }, enabled = index < audio.playOrder.lastIndex, onClick = { options = false; playback.moveInQueue(id, index + 1) })
                                    DropdownMenuItem(text = { Text("Add to playlist") }, onClick = { options = false; onSave(listOf(id)) })
                                    DropdownMenuItem(text = { Text("Remove from queue") }, onClick = { options = false; playback.removeFromQueue(id) })
                                }
                            }
                        }
                    }
                }
                }
            }
        }
    }
}

/** One transport shared by the artwork and integrated queue views. */
@Composable
fun PlayerTransport(
    audio: PlaybackState, playback: PlaybackController, positionMs: () -> Long,
    onScrub: (Float) -> Unit, onScrubFinished: () -> Unit,
    canRead: Boolean, queueVisible: Boolean, onQueueToggle: () -> Unit,
    modifier: Modifier = Modifier, compact: Boolean = false
) {
    Column(modifier.fillMaxWidth().padding(bottom = 12.dp).testTag("player-transport")) {
        PlaybackProgress(positionMs, audio.durationMs, audio.seekable, onScrub, onScrubFinished)
        Row(Modifier.fillMaxWidth().padding(vertical = if (compact) 4.dp else 12.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            if(audio.radioActive)Spacer(Modifier.size(48.dp)) else IconToggleButton(audio.shuffled, onCheckedChange = { playback.shuffle(it) }) { RadioIcon(Symbol.Shuffle, "Shuffle") }
            FilledTonalIconButton(onClick = { playback.previous() }, enabled = audio.canPrevious, modifier = Modifier.size(52.dp)) { RadioIcon(Symbol.Previous, "Previous song") }
            FilledIconButton(onClick = { playback.toggle() }, enabled = canRead, modifier = Modifier.size(width = if (compact) 76.dp else 92.dp, height = if (compact) 56.dp else 72.dp), shape = dev.radio.mobile.ui.theme.RadioShapes.Squircle) {
                RadioIcon(if (audio.playWhenReady) Symbol.Pause else Symbol.Play, if (audio.playWhenReady) "Pause" else "Play", Modifier.size(32.dp))
            }
            FilledTonalIconButton(onClick = { playback.next() }, enabled = audio.canNext, modifier = Modifier.size(52.dp).testTag("player-next")) { RadioIcon(Symbol.Next, "Next song") }
            IconToggleButton(queueVisible, onCheckedChange = { onQueueToggle() }) {
                RadioIcon(if (queueVisible) Symbol.Music else Symbol.Queue, if (queueVisible) "Show artwork" else "Queue")
            }
        }
    }
}

