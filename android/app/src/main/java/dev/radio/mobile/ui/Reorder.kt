package dev.radio.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Long-press a row and drag it to a new place in a LazyColumn. The row follows the finger; the move is committed once,
 * on release, through [onMove] with the target index among the reorderable rows (rows before them are [leading]).
 */
class ReorderState internal constructor(private val list: LazyListState, private val scope: CoroutineScope, private val leading: () -> Int,
    private val onMove: (key: Any, toIndex: Int) -> Unit) {
    var draggingKey by mutableStateOf<Any?>(null); private set
    var offset by mutableFloatStateOf(0f); private set
    private var scroll: Job? = null
    private var speed = 0f

    internal fun start(key: Any) { draggingKey = key; offset = 0f }
    internal fun drag(delta: Float) {
        offset += delta
        val item = list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == draggingKey } ?: return
        val top = item.offset + offset
        val bottom = top + item.size
        val edge = 64.dp.value * 3
        speed = when {
            top < list.layoutInfo.viewportStartOffset + edge -> -12f
            bottom > list.layoutInfo.viewportEndOffset - edge -> 12f
            else -> 0f
        }
        if (speed != 0f && scroll?.isActive != true) scroll = scope.launch {
            while (isActive && speed != 0f && draggingKey != null) {
                val moved = list.scrollBy(speed)
                // The row is laid out where it was; keep it under the finger while the list scrolls.
                offset += moved
                delay(16)
            }
        }
    }
    internal fun end() {
        val key = draggingKey
        val items = list.layoutInfo.visibleItemsInfo
        val item = items.firstOrNull { it.key == key }
        if (key != null && item != null) {
            val center = item.offset + offset + item.size / 2
            val target = items.firstOrNull { center >= it.offset && center < it.offset + it.size }
                ?: if (center < (items.firstOrNull()?.offset ?: 0)) items.firstOrNull() else items.lastOrNull()
            val index = (target?.index ?: item.index) - leading()
            if (target != null && target.index != item.index && index >= 0) onMove(key, index)
        }
        cancel()
    }
    internal fun cancel() { draggingKey = null; offset = 0f; speed = 0f; scroll?.cancel() }
}

@Composable
fun rememberReorderState(list: LazyListState, leading: () -> Int = { 0 }, onMove: (key: Any, toIndex: Int) -> Unit): ReorderState {
    val scope = rememberCoroutineScope()
    val move by rememberUpdatedState(onMove)
    val lead by rememberUpdatedState(leading)
    return remember(list) { ReorderState(list, scope, { lead() }) { key, index -> move(key, index) } }
}

/** Lifts and moves the dragged row; long-press anywhere on the row starts the drag. */
fun Modifier.reorderable(state: ReorderState, key: Any, enabled: Boolean = true, onStart: () -> Unit = {}): Modifier {
    val dragging = state.draggingKey == key
    val lifted = this.zIndex(if (dragging) 1f else 0f).graphicsLayer {
        translationY = if (dragging) state.offset else 0f
        if (dragging) { shadowElevation = 12f; scaleX = 1.02f; scaleY = 1.02f }
    }
    if (!enabled) return lifted
    return lifted.pointerInput(state, key) {
        detectDragGesturesAfterLongPress(
            onDragStart = { onStart(); state.start(key) },
            onDrag = { change, amount -> change.consume(); state.drag(amount.y) },
            onDragEnd = { state.end() },
            onDragCancel = { state.cancel() })
    }
}

/** Swipe a row sideways to remove it; a red band with a bin shows underneath. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SwipeToRemove(enabled: Boolean, onRemove: () -> Unit, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    if (!enabled) { Box(modifier) { content() }; return }
    val state = rememberSwipeToDismissBoxState()
    val remove by rememberUpdatedState(onRemove)
    val scope = rememberCoroutineScope()
    SwipeToDismissBox(state, modifier = modifier, backgroundContent = {
        val start = state.dismissDirection == SwipeToDismissBoxValue.StartToEnd
        Box(Modifier.fillMaxSize().padding(horizontal = 8.dp).background(MaterialTheme.colorScheme.errorContainer, RoundedCornerShape(20.dp)).padding(horizontal = 20.dp),
            contentAlignment = if (start) Alignment.CenterStart else Alignment.CenterEnd) {
            CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onErrorContainer) { RadioIcon(Symbol.Remove, "Remove") }
        }
    }, onDismiss = {
        remove()
        // Normally the row is gone by now; if the removal didn't happen, bring it back.
        scope.launch { delay(700); state.reset() }
    }) { content() }
}

/** A short buzz when a drag starts. */
@Composable
fun rememberDragHaptic(): () -> Unit {
    val haptics = LocalHapticFeedback.current
    return remember(haptics) { { haptics.performHapticFeedback(HapticFeedbackType.LongPress) } }
}
