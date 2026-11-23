package dev.radio.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.awaitLongPressOrCancellation
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.positionChange
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
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring

/**
 * Long-press a row and drag it to a new place in a LazyColumn. The row follows the finger; the move is committed once,
 * on release, through [onMove] with the target index among the reorderable rows (rows before them are [leading]).
 */
class ReorderState internal constructor(private val list: LazyListState, private val scope: CoroutineScope, private val leading: () -> Int,
    private val onMove: (key: Any, toIndex: Int) -> Unit) {
    var draggingKey by mutableStateOf<Any?>(null); private set
    var offset by mutableFloatStateOf(0f); private set
    /** Checkpoint 10: the released row springs from where the finger left it into its new slot. */
    var settlingKey by mutableStateOf<Any?>(null); private set
    private var settle: Job? = null
    private var scroll: Job? = null
    private var speed = 0f

    internal fun start(key: Any) { settle?.cancel(); settlingKey = null; draggingKey = key; offset = 0f }
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
            val moved = target != null && target.index != item.index && index >= 0
            // After the move the row is laid out at the target's place; keep it visually where it was, then spring home.
            val start = if (moved) offset - (target!!.offset - item.offset) else offset
            if (moved) onMove(key, index)
            speed = 0f; scroll?.cancel(); draggingKey = null
            settlingKey = key; offset = start
            settle = scope.launch {
                Animatable(start).animateTo(0f, Motion.settle()) { offset = value }
                settlingKey = null
            }
            return
        }
        cancel()
    }
    internal fun cancel() { draggingKey = null; settlingKey = null; settle?.cancel(); offset = 0f; speed = 0f; scroll?.cancel() }
}

@Composable
fun rememberReorderState(list: LazyListState, leading: () -> Int = { 0 }, onMove: (key: Any, toIndex: Int) -> Unit): ReorderState {
    val scope = rememberCoroutineScope()
    val move by rememberUpdatedState(onMove)
    val lead by rememberUpdatedState(leading)
    return remember(list) { ReorderState(list, scope, { lead() }) { key, index -> move(key, index) } }
}

/**
 * Lifts and moves the dragged row; long-press anywhere on the row starts the drag. With [onPress], a long-press that
 * is released without moving calls it instead (playlists use it to start selecting). After the long-press the gesture
 * is taken in the initial pass, so the release never also counts as a tap on the row.
 */
fun Modifier.reorderable(state: ReorderState, key: Any, enabled: Boolean = true, onStart: () -> Unit = {}, onPress: (() -> Unit)? = null): Modifier {
    val dragging = state.draggingKey == key
    val settling = state.settlingKey == key
    val lifted = this.zIndex(if (dragging || settling) 1f else 0f).graphicsLayer {
        translationY = if (dragging || settling) state.offset else 0f
        if (dragging) { shadowElevation = 12f; scaleX = 1.02f; scaleY = 1.02f }
    }
    if (!enabled) return lifted
    return lifted.pointerInput(state, key) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            val press = awaitLongPressOrCancellation(down.id) ?: return@awaitEachGesture
            onStart(); state.start(key)
            var travelled = 0f
            var released = false
            try {
                while (true) {
                    val change = awaitPointerEvent(PointerEventPass.Initial).changes.firstOrNull { it.id == press.id } ?: break
                    if (!change.pressed) { change.consume(); released = true; break }
                    val delta = change.positionChange()
                    change.consume()
                    travelled += kotlin.math.abs(delta.y)
                    state.drag(delta.y)
                }
            } finally {
                when {
                    !released -> state.cancel()
                    onPress != null && travelled < viewConfiguration.touchSlop -> { state.cancel(); onPress() }
                    else -> state.end()
                }
            }
        }
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
        // Draw nothing until the row is actually being swiped, so the queue stays transparent at rest.
        if (state.dismissDirection == SwipeToDismissBoxValue.Settled) return@SwipeToDismissBox
        Box(Modifier.fillMaxSize().revealedBy(state).padding(horizontal = 8.dp).background(MaterialTheme.colorScheme.errorContainer, RoundedCornerShape(20.dp)).padding(horizontal = 20.dp),
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

/** Checkpoint 10: other rows glide into place with a spring when the list order changes. */
@Composable
fun androidx.compose.foundation.lazy.LazyItemScope.springPlacement(): Modifier =
    Modifier.animateItem(fadeInSpec = null, fadeOutSpec = null,
        placementSpec = Motion.settle())
