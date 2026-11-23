package dev.radio.mobile.ui

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.awaitLongPressOrCancellation
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.radio.mobile.data.LocalSong
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Songs chosen in a list. A long-press [begin]s a sweep at one row; dragging [sweep]s every row between it and the
 * finger into the selection, on top of what was already chosen ([base]). Taps [toggle]. Empty means not selecting.
 */
data class SongSelection(val ids: Set<String> = emptySet(), val anchor: String? = null, val base: Set<String> = emptySet()) {
    val active: Boolean get() = ids.isNotEmpty()
    fun begin(id: String) = SongSelection(ids + id, anchor = id, base = ids)
    fun toggle(id: String) = SongSelection(if (id in ids) ids - id else ids + id)
    fun sweep(order: List<String>, to: String): SongSelection {
        val from = order.indexOf(anchor ?: return this)
        val until = order.indexOf(to)
        if (from < 0 || until < 0) return this
        return copy(ids = base + order.subList(minOf(from, until), maxOf(from, until) + 1))
    }
    fun selectAll(order: List<String>) = SongSelection(ids + order)
    fun allOf(order: List<String>) = order.isNotEmpty() && ids.containsAll(order)
    /** The chosen songs in list order, whatever order they were tapped in. */
    fun ordered(order: List<String>): List<String> = order.filter { it in ids }.distinct()
    fun retain(valid: Set<String>) = if (ids.all { it in valid }) this else SongSelection(ids.filterTo(LinkedHashSet()) { it in valid })
}

enum class BulkAction { PlayNow, Shuffle, PlayNext, AddToQueue, AddToPlaylist, Radio, RemoveFromPlaylist }

/**
 * The one selection on screen, shared by the list that owns it (which [bind]s its rows) and the app's top and bottom
 * bars, which turn into the selection header and action bar while it is active.
 */
@Stable
class SelectionController {
    var selection by mutableStateOf(SongSelection())
    /** The owning list's song ids in order, its songs, and the playlist it is (for Remove from playlist). */
    var order by mutableStateOf<List<String>>(emptyList()); private set
    var songs by mutableStateOf<List<LocalSong>>(emptyList()); private set
    var playlistId by mutableStateOf<String?>(null); private set
    private var owner: String? = null
    val active: Boolean get() = selection.active

    /** Called by the list on screen. A list that doesn't own an active selection leaves it alone. */
    fun bind(owner: String, order: List<String>, songs: List<LocalSong>, playlistId: String? = null) {
        if (active && owner != this.owner) return
        this.owner = owner; this.order = order; this.songs = songs; this.playlistId = playlistId
        if (active) selection = selection.retain(order.toSet())
    }
    fun begin(id: String) { selection = selection.begin(id) }
    fun toggle(id: String) { selection = selection.toggle(id) }
    fun clear() { selection = SongSelection() }
    fun selectAll() { selection = selection.selectAll(order) }
    val allSelected: Boolean get() = selection.allOf(order)
    fun chosenIds(): List<String> = selection.ordered(order)
    fun chosenSongs(): List<LocalSong> { val byId = songs.associateBy { it.id }; return chosenIds().mapNotNull(byId::get) }
}

/** Binds the list's rows to [controller] whenever they change. */
@Composable
fun BindSelection(controller: SelectionController, owner: String, order: List<String>, songs: List<LocalSong>, playlistId: String? = null) {
    SideEffect { controller.bind(owner, order, songs, playlistId) }
}

/**
 * Long-press a song row to start selecting it, then keep the finger down and drag to sweep the rows in between into
 * the selection; the list scrolls when the finger nears its top or bottom edge. Once the long-press fires the gesture
 * is taken in the initial pass, so the row underneath neither scrolls, swipes nor counts the release as a tap.
 * [idAt] maps a list item key to its song id, or null for rows that aren't songs (folders, headers).
 */
@Composable
fun Modifier.sweepSelect(list: LazyListState, controller: SelectionController, enabled: Boolean = true, idAt: (Any) -> String?): Modifier {
    if (!enabled) return this
    val scope = rememberCoroutineScope()
    val haptic = LocalHapticFeedback.current
    val keyToId by rememberUpdatedState(idAt)
    return pointerInput(list, controller) {
        val edge = 64.dp.toPx()
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            val press = awaitLongPressOrCancellation(down.id) ?: return@awaitEachGesture
            fun idUnder(y: Float): String? = list.layoutInfo.visibleItemsInfo
                .firstOrNull { y >= it.offset && y < it.offset + it.size }?.let { keyToId(it.key) }
            val start = idUnder(press.position.y) ?: return@awaitEachGesture
            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
            controller.begin(start)
            var y = press.position.y
            var speed = 0f
            var autoScroll: Job? = null
            fun sweepTo() { idUnder(y)?.let { controller.selection = controller.selection.sweep(controller.order, it) } }
            try {
                while (true) {
                    val event = awaitPointerEvent(PointerEventPass.Initial)
                    val change = event.changes.firstOrNull { it.id == press.id } ?: break
                    change.consume()
                    if (!change.pressed) break
                    y = change.position.y
                    sweepTo()
                    speed = when { y < edge -> -14f; y > size.height - edge -> 14f; else -> 0f }
                    if (speed != 0f && autoScroll?.isActive != true) autoScroll = scope.launch {
                        while (isActive && speed != 0f) { list.scrollBy(speed); sweepTo(); delay(16) }
                    }
                }
            } finally { speed = 0f; autoScroll?.cancel() }
        }
    }
}

/** A song row that takes part in selection: tap toggles while selecting, and the swipe actions pause. */
@Composable
fun SelectableSongRow(song: LocalSong, subtitle: String, controller: SelectionController, onSongAction: (SongAction, LocalSong) -> Unit,
    title: String = song.title, symbol: Symbol = Symbol.Music, playing: Boolean = false, onClick: () -> Unit) {
    val selecting = controller.active
    SwipeSongRow(song, onSongAction, enabled = !selecting) {
        val songMenu = remember { mutableStateOf(false) }
        LibraryRow(title, subtitle, symbol, playing = playing, selected = if (selecting) song.id in controller.selection.ids else null,
            onSelect = { controller.begin(song.id) }, trailing = { SongActions(song, onSongAction, songMenu) }) {
            if (selecting) controller.toggle(song.id) else onClick()
        }
    }
}

/** The top bar while selecting: close, how many, and Select all. */
@Composable
fun SelectionHeader(controller: SelectionController, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().statusBarsPadding().heightIn(min = 64.dp).padding(horizontal = 8.dp).testTag("selection-header"), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = { controller.clear() }, modifier = Modifier.testTag("selection-close")) { RadioIcon(Symbol.Close, "Stop selecting") }
        val count = controller.selection.ids.size
        Text("$count selected", Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.titleLarge)
        if (controller.allSelected) TextButton(onClick = { controller.clear() }, modifier = Modifier.testTag("selection-none")) { Text("Select none") }
        else TextButton(onClick = { controller.selectAll() }, modifier = Modifier.testTag("selection-all")) { Text("Select all") }
    }
}

/** Takes the bottom bar's place while selecting: the main actions with labels, the rest under More. */
@Composable
fun SelectionActionBar(controller: SelectionController, height: androidx.compose.ui.unit.Dp, canPlay: Boolean, onAction: (BulkAction) -> Unit) {
    val chosen = controller.chosenSongs()
    val playable = canPlay && chosen.any { it.available }
    Row(Modifier.fillMaxWidth().navigationBarsPadding().height(height).padding(horizontal = 4.dp).testTag("selection-actions"),
        horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
        @Composable fun Action(symbol: Symbol, label: String, tag: String, enabled: Boolean, onClick: () -> Unit) {
            TextButton(onClick = onClick, enabled = enabled, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 2.dp), modifier = Modifier.testTag(tag)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    RadioIcon(symbol, modifier = Modifier.size(22.dp))
                    Text(label, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Medium, maxLines = 1)
                }
            }
        }
        Action(Symbol.Play, "Play", "selection-play", playable) { onAction(BulkAction.PlayNow) }
        Action(Symbol.Next, "Play next", "selection-next", playable) { onAction(BulkAction.PlayNext) }
        Action(Symbol.Queue, "Queue", "selection-queue", playable) { onAction(BulkAction.AddToQueue) }
        Action(Symbol.Add, "Playlist", "selection-playlist", controller.selection.active) { onAction(BulkAction.AddToPlaylist) }
        var more by remember { mutableStateOf(false) }
        Box {
            Action(Symbol.More, "More", "selection-more", true) { more = true }
            DropdownMenu(more, onDismissRequest = { more = false }) {
                DropdownMenuItem(text = { Text("Shuffle") }, leadingIcon = { RadioIcon(Symbol.Shuffle) }, enabled = playable && chosen.count { it.available } > 1,
                    onClick = { more = false; onAction(BulkAction.Shuffle) }, modifier = Modifier.testTag("selection-shuffle"))
                DropdownMenuItem(text = { Text("Start radio from these") }, leadingIcon = { RadioIcon(Symbol.Radio) }, enabled = playable,
                    onClick = { more = false; onAction(BulkAction.Radio) }, modifier = Modifier.testTag("selection-radio"))
                if (controller.playlistId != null) DropdownMenuItem(text = { Text("Remove from playlist") }, leadingIcon = { RadioIcon(Symbol.Remove) },
                    onClick = { more = false; onAction(BulkAction.RemoveFromPlaylist) }, modifier = Modifier.testTag("selection-remove"))
            }
        }
    }
}
