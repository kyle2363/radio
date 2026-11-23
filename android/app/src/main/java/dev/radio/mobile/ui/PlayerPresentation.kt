package dev.radio.mobile.ui

import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/** Stored names stay stable for saved prefs; labels are what Settings shows. */
enum class PlayerBackdrop(val label: String) { Animated("Moving"), Static("Still"), Off("Off") }

internal fun shouldAnimateBackdrop(mode: PlayerBackdrop, playing: Boolean, foreground: Boolean, animationsEnabled: Boolean) =
    mode == PlayerBackdrop.Animated && playing && foreground && animationsEnabled

/** Purely decorative; the animation value is read only during drawing. No media work. */
@Composable
fun PlayerBackground(mode: PlayerBackdrop, playing: Boolean, modifier: Modifier = Modifier, tint: Color? = null) {
    val owner = LocalLifecycleOwner.current
    var foreground by remember(owner) { mutableStateOf(owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) }
    val animationsEnabled by rememberAnimationsEnabled()
    DisposableEffect(owner) {
        val lifecycleObserver = LifecycleEventObserver { _, _ -> foreground = owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) }
        owner.lifecycle.addObserver(lifecycleObserver)
        onDispose { owner.lifecycle.removeObserver(lifecycleObserver) }
    }
    val moving = shouldAnimateBackdrop(mode, playing, foreground, animationsEnabled)
    val phase = remember { Animatable(0f) }
    // Checkpoint 10: the glow breathes with the music's loudness while it moves (never when paused or reduced motion).
    DisposableEffect(moving) {
        dev.radio.mobile.playback.AudioLevel.listening = moving
        onDispose { dev.radio.mobile.playback.AudioLevel.listening = false }
    }
    LaunchedEffect(moving) {
        if (!moving) return@LaunchedEffect
        val drift: suspend () -> Unit = {
            while (true) {
                phase.animateTo(1f, tween(((1f - phase.value) * 16000).toInt().coerceAtLeast(1), easing = LinearEasing))
                phase.snapTo(0f) // Same position at both ends of the circular path.
            }
        }
        // The drift never ends, so it is declared as infinite: hosts such as Compose tests can then treat the screen as
        // idle instead of waiting on it forever. Normal app use has no policy and simply runs it.
        coroutineContext[androidx.compose.ui.platform.InfiniteAnimationPolicy]?.onInfiniteOperation(drift) ?: drift()
    }
    val colors = MaterialTheme.colorScheme
    Box(modifier.testTag("player-background").semantics {
        stateDescription = when {
            mode == PlayerBackdrop.Off -> "Background off"
            moving -> "Background moving"
            mode == PlayerBackdrop.Animated -> "Background motion paused"
            else -> "Static background"
        }
    }.clipToBounds().drawWithCache {
        val radius = size.width * 1.15f
        // A mood tint is a dark tone, so it needs more strength than the palette colour to read as a glow.
        val glow = tint ?: colors.primary
        val first = Brush.radialGradient(listOf(glow.copy(alpha = if (tint != null) .85f else .22f), Color.Transparent), center = Offset.Zero, radius = radius)
        val second = Brush.radialGradient(listOf(glow.copy(alpha = if (tint != null) .55f else .13f), Color.Transparent), center = Offset.Zero, radius = radius * .9f)
        onDrawBehind {
            drawRect(colors.background)
            if (mode != PlayerBackdrop.Off) {
                val angle = phase.value * (2 * PI).toFloat()
                // Read every frame the phase animation draws; 0 when still. Up to +25% size with a loud passage.
                val pulse = if (moving) dev.radio.mobile.playback.AudioLevel.level else 0f
                scale(1f + .25f * pulse, pivot = Offset(size.width / 2, size.height / 2)) {
                // Two glows on different loops (1x and 2x) so the field drifts rather than rotating rigidly.
                val x = size.width * (.22f + .30f * cos(angle))
                val y = size.height * (.78f + .16f * sin(angle))
                translate(x, y) { drawCircle(first, radius, center = Offset.Zero) }
                val x2 = size.width * (.80f + .22f * sin(angle * 2))
                val y2 = size.height * (.30f + .20f * cos(angle))
                translate(x2, y2) { drawCircle(second, radius * .9f, center = Offset.Zero) }
                }
            }
        }
    })
}

/**
 * Seek bar with elapsed and total time. The live position is read only here, so the 4-per-second position ticks
 * recompose this row instead of the whole player.
 */
@Composable
fun PlaybackProgress(positionMs: () -> Long, durationMs: Long?, seekable: Boolean, onScrub: (Float) -> Unit, onScrubFinished: () -> Unit,
    timeColor: Color = Color.Unspecified) {
    val ms = positionMs()
    Column {
        PlayerSeekBar(ms / 1000f, (durationMs ?: 0L) / 1000f, onSeek = onScrub, modifier = Modifier.fillMaxWidth(), enabled = seekable, onSeekFinished = onScrubFinished)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(clock(ms), style = MaterialTheme.typography.labelSmall, color = timeColor)
            Text(durationMs?.let(::clock) ?: "--:--", style = MaterialTheme.typography.labelSmall, color = timeColor)
        }
    }
}

/** Material slider gestures, keyboard and accessibility, with a thin native-player appearance. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlayerSeekBar(position: Float, duration: Float, onSeek: (Float) -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, onSeekFinished: (() -> Unit)? = null) {
    val active = MaterialTheme.colorScheme.primary
    val inactive = MaterialTheme.colorScheme.onSurface.copy(alpha = .20f)
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    Slider(
        enabled = enabled,
        value = position.coerceIn(0f, duration.coerceAtLeast(1f)),
        onValueChange = onSeek,
        onValueChangeFinished = onSeekFinished,
        valueRange = 0f..duration.coerceAtLeast(1f),
        modifier = modifier.heightIn(min = 48.dp).testTag("player-seek").semantics { contentDescription = "Song position" },
        thumb = {
            Box(Modifier.size(width = 24.dp, height = 48.dp), contentAlignment = Alignment.Center) {
                Box(Modifier.size(16.dp).background(active, CircleShape))
            }
        },
        track = { state ->
            Canvas(Modifier.fillMaxWidth().height(3.dp)) {
                val fraction = (state.value / duration.coerceAtLeast(1f)).coerceIn(0f, 1f)
                val start = Offset(if (rtl) size.width else 0f, center.y)
                val end = Offset(if (rtl) 0f else size.width, center.y)
                val current = Offset(if (rtl) size.width * (1f - fraction) else size.width * fraction, center.y)
                drawLine(inactive, start, end, strokeWidth = size.height, cap = StrokeCap.Round)
                if (fraction > 0f) drawLine(active, start, current, strokeWidth = size.height, cap = StrokeCap.Round)
            }
        }
    )
}
