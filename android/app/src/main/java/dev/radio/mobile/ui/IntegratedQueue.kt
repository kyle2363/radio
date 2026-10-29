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
    playback: PlaybackController? = null, onAdd: () -> Unit = {}, onSave: (List<String>) -> Unit = {}) {
    val byId = remember(audio.queue) { audio.queue.associateBy { it.id } }
    val currentIndex = remember(audio.playOrder, audio.currentId) { audio.playOrder.indexOf(audio.currentId) }
    val list = rememberLazyListState(initialFirstVisibleItemIndex = currentIndex.coerceAtLeast(0))
    val scope = rememberCoroutineScope()
    var editing by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    // Start at the current song when opened, then leave browsing under the user's control.
    // Selecting or advancing a track updates the highlight without moving the viewport.
    Column(modifier) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text(if (audio.shuffled) "Shuffled order" else "In order", modifier = Modifier.weight(1f), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
                val tint = if (current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                Row(Modifier.fillMaxWidth().selectable(selected = current, enabled = accessAllowed && song.available, role = Role.Button, onClick = { onSelect(id) })
                    .heightIn(min = 60.dp).padding(horizontal = 20.dp, vertical = 8.dp).testTag("queue-row-$id")
                    .semantics { if (current) stateDescription = "Current song. ${audio.status}" }, verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(36.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp)), contentAlignment = Alignment.Center) {
                        CompositionLocalProvider(LocalContentColor provides tint) { RadioIcon(Symbol.Music, modifier = Modifier.size(20.dp)) }
                    }
                    Column(Modifier.weight(1f).padding(start = 14.dp)) {
                        Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium,
                            fontWeight = if (current) FontWeight.Medium else FontWeight.Normal, color = if (current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                        Text(if (current) audio.status else if (!song.available) "Unavailable" else songSubtitle(song), maxLines = 1, overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.bodySmall, color = tint)
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

/** One transport shared by the artwork and integrated queue views. */
@Composable
fun PlayerTransport(
    audio: PlaybackState, playback: PlaybackController, position: Float,
    onScrub: (Float) -> Unit, onScrubFinished: () -> Unit,
    canRead: Boolean, queueVisible: Boolean, onQueueToggle: () -> Unit,
    modifier: Modifier = Modifier, compact: Boolean = false
) {
    Column(modifier.fillMaxWidth().padding(bottom = 12.dp).testTag("player-transport")) {
        PlayerSeekBar(position, (audio.durationMs ?: 0L) / 1000f, onSeek = onScrub, modifier = Modifier.fillMaxWidth(), enabled = audio.seekable, onSeekFinished = onScrubFinished)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(playerTime((position * 1000).toLong()), style = MaterialTheme.typography.labelSmall)
            Text(audio.durationMs?.let(::playerTime) ?: "--:--", style = MaterialTheme.typography.labelSmall)
        }
        Row(Modifier.fillMaxWidth().padding(vertical = if (compact) 4.dp else 12.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            IconToggleButton(audio.shuffled, onCheckedChange = { playback.shuffle(it) }) { RadioIcon(Symbol.Shuffle, "Shuffle") }
            IconButton(onClick = { playback.previous() }, enabled = audio.canPrevious) { RadioIcon(Symbol.Previous, "Previous song") }
            FilledIconButton(onClick = { playback.toggle() }, enabled = canRead, modifier = Modifier.size(if (compact) 56.dp else 64.dp)) {
                RadioIcon(if (audio.playWhenReady) Symbol.Pause else Symbol.Play, if (audio.playWhenReady) "Pause" else "Play", Modifier.size(32.dp))
            }
            IconButton(onClick = { playback.next() }, enabled = audio.canNext, modifier = Modifier.testTag("player-next")) { RadioIcon(Symbol.Next, "Next song") }
            IconToggleButton(queueVisible, onCheckedChange = { onQueueToggle() }) {
                RadioIcon(if (queueVisible) Symbol.Music else Symbol.Queue, if (queueVisible) "Show artwork" else "Queue")
            }
        }
    }
}

private fun playerTime(ms: Long): String = "%d:%02d".format(ms / 60000, (ms / 1000) % 60)
