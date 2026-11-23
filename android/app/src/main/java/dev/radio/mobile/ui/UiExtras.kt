package dev.radio.mobile.ui

import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.abs
import kotlinx.coroutines.launch
import androidx.compose.animation.core.AnimationVector2D
import androidx.compose.animation.core.VectorConverter

/**
 * Fades content out over [length] at its edges instead of cutting it off: a scrolling title's sides, or the bottom of a
 * list that runs into the controls below it.
 */
fun Modifier.fadeEdges(length: Dp = 16.dp, horizontal: Boolean = true, start: Boolean = true, end: Boolean = true): Modifier =
    graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }.drawWithContent {
        drawContent()
        val edge = length.toPx()
        val extent = if (horizontal) size.width else size.height
        if (extent <= 0f || edge <= 0f) return@drawWithContent
        val stop = (edge / extent).coerceAtMost(.5f)
        val stops = buildList {
            add(0f to if (start) Color.Transparent else Color.Black)
            add(stop to Color.Black); add(1f - stop to Color.Black)
            add(1f to if (end) Color.Transparent else Color.Black)
        }.toTypedArray()
        val brush = if (horizontal) Brush.horizontalGradient(*stops) else Brush.verticalGradient(*stops)
        drawRect(brush, blendMode = BlendMode.DstIn)
    }

/** A slight press-in for the controls people tap most, on top of the ripple. Follows the phone's animation setting. */
@Composable
fun Modifier.pressScale(source: InteractionSource, pressed: Float = .94f): Modifier {
    val isPressed by source.collectIsPressedAsState()
    val scale by animateFloatAsState(if (isPressed) pressed else 1f, Motion.press(), label = "press-scale")
    return graphicsLayer { scaleX = scale; scaleY = scale }
}

/** Whether the phone's animator setting allows motion, kept current while the app is open. */
@Composable
fun rememberAnimationsEnabled(): State<Boolean> {
    val resolver = LocalContext.current.contentResolver
    val enabled = remember(resolver) { mutableStateOf(Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f) }
    DisposableEffect(resolver) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                enabled.value = Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f
            }
        }
        resolver.registerContentObserver(Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE), false, observer)
        onDispose { resolver.unregisterContentObserver(observer) }
    }
    return enabled
}

/** Play/Pause that cross-fades between the two shapes instead of swapping in one frame. */
@Composable
fun PlayPauseIcon(playing: Boolean, modifier: Modifier = Modifier) {
    Crossfade(playing, animationSpec = Motion.quick(), label = "play-pause") { shown ->
        RadioIcon(if (shown) Symbol.Pause else Symbol.Play, if (shown) "Pause" else "Play", modifier)
    }
}

/** The like heart; it pops once when a song becomes liked (never on first show, so lists don't all bounce). */
@Composable
fun HeartIcon(liked: Boolean, description: String?, modifier: Modifier = Modifier) {
    val scale = remember { Animatable(1f) }
    var previous by remember { mutableStateOf(liked) }
    LaunchedEffect(liked) {
        if (liked && !previous) { scale.animateTo(1.25f, Motion.quick()); scale.animateTo(1f, Motion.settle()) }
        previous = liked
    }
    RadioIcon(if (liked) Symbol.HeartFilled else Symbol.Heart, description, modifier.graphicsLayer { scaleX = scale.value; scaleY = scale.value })
}

/** A quiet, centred empty state: icon, one line saying what's missing, a sentence on what to do, an optional action. */
@Composable
fun EmptyState(symbol: Symbol, title: String, body: String, modifier: Modifier = Modifier, action: String? = null, onAction: () -> Unit = {}) {
    Column(modifier.fillMaxWidth().padding(horizontal = 32.dp, vertical = 32.dp).testTag("empty-state"), horizontalAlignment = Alignment.CenterHorizontally) {
        androidx.compose.material3.Surface(Modifier.size(56.dp), color = MaterialTheme.colorScheme.secondaryContainer, shape = dev.radio.mobile.ui.theme.RadioShapes.Soft) {
            Box(contentAlignment = Alignment.Center) { RadioIcon(symbol, modifier = Modifier.size(26.dp)) }
        }
        Text(title, Modifier.padding(top = 16.dp), style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
        Text(body, Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        if (action != null) TextButton(onClick = onAction, modifier = Modifier.padding(top = 8.dp)) { Text(action) }
    }
}

/**
 * Mini-player gestures: swipe up to open Now Playing, sideways for next/previous. The pill follows the finger a little
 * so the gesture reads as physical, then settles back.
 */
fun Modifier.miniPlayerGestures(onOpen: () -> Unit, onNext: () -> Unit, onPrevious: () -> Unit, canNext: Boolean, canPrevious: Boolean,
    offset: Animatable<Offset, *>, scope: kotlinx.coroutines.CoroutineScope): Modifier = pointerInput(canNext, canPrevious) {
    val threshold = 56.dp.toPx()
    var total = Offset.Zero
    detectDragGestures(
        onDragStart = { total = Offset.Zero },
        onDragEnd = {
            when {
                -total.y > threshold && abs(total.y) > abs(total.x) -> onOpen()
                total.x < -threshold && canNext -> onNext()
                total.x > threshold && canPrevious -> onPrevious()
            }
            scope.launchSettle(offset)
        },
        onDragCancel = { scope.launchSettle(offset) },
    ) { change, amount ->
        change.consume()
        total += amount
        // Dampened: the pill leans with the finger without sliding away.
        scope.launchSnap(offset, Offset((total.x * .35f), (total.y * .25f).coerceAtMost(0f)))
    }
}.graphicsLayer { translationX = offset.value.x; translationY = offset.value.y }

private fun kotlinx.coroutines.CoroutineScope.launchSettle(offset: Animatable<Offset, *>) { launch { offset.animateTo(Offset.Zero, Motion.settle()) } }
private fun kotlinx.coroutines.CoroutineScope.launchSnap(offset: Animatable<Offset, *>, to: Offset) { launch { offset.snapTo(to) } }

/** The mini-player's drag offset while a gesture is held. */
@Composable
fun rememberDragOffset(): Animatable<Offset, AnimationVector2D> = remember { Animatable(Offset.Zero, Offset.VectorConverter) }

/** A 2 dp progress line along the bottom of the mini-player; the position is read while drawing, so ticks only redraw. */
fun Modifier.progressLine(positionMs: () -> Long, durationMs: Long?, color: Color, track: Color): Modifier = drawWithContent {
    drawContent()
    val duration = durationMs ?: return@drawWithContent
    if (duration <= 0L) return@drawWithContent
    val fraction = (positionMs().toFloat() / duration).coerceIn(0f, 1f)
    val stroke = 2.dp.toPx()
    val inset = 18.dp.toPx()
    val y = size.height - stroke / 2 - 1.dp.toPx()
    drawLine(track, Offset(inset, y), Offset(size.width - inset, y), stroke, StrokeCap.Round)
    if (fraction > 0f) drawLine(color, Offset(inset, y), Offset(inset + (size.width - 2 * inset) * fraction, y), stroke, StrokeCap.Round)
}
