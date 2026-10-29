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

enum class PlayerBackdrop(val label: String) { Off("Off"), Static("Static"), Animated("Animated") }

internal fun shouldAnimateBackdrop(mode: PlayerBackdrop, playing: Boolean, foreground: Boolean, animationsEnabled: Boolean) =
    mode == PlayerBackdrop.Animated && playing && foreground && animationsEnabled

/** Purely decorative; the animation value is read only during drawing. No media work. */
@Composable
fun PlayerBackground(mode: PlayerBackdrop, playing: Boolean, modifier: Modifier = Modifier) {
    val owner = LocalLifecycleOwner.current
    val resolver = LocalContext.current.contentResolver
    var foreground by remember(owner) { mutableStateOf(owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) }
    var animationsEnabled by remember { mutableStateOf(Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f) }
    DisposableEffect(owner, resolver) {
        val lifecycleObserver = LifecycleEventObserver { _, _ -> foreground = owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) }
        val motionObserver = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                animationsEnabled = Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f
            }
        }
        owner.lifecycle.addObserver(lifecycleObserver)
        resolver.registerContentObserver(Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE), false, motionObserver)
        onDispose { owner.lifecycle.removeObserver(lifecycleObserver); resolver.unregisterContentObserver(motionObserver) }
    }
    val moving = shouldAnimateBackdrop(mode, playing, foreground, animationsEnabled)
    val phase = remember { Animatable(0f) }
    LaunchedEffect(moving) {
        if (moving) while (true) {
            phase.animateTo(1f, tween(((1f - phase.value) * 24000).toInt().coerceAtLeast(1), easing = LinearEasing))
            phase.snapTo(0f) // Same position at both ends of the circular path.
        }
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
        val first = Brush.radialGradient(listOf(colors.primary.copy(alpha = .22f), Color.Transparent), center = Offset.Zero, radius = radius)
        val second = Brush.radialGradient(listOf(colors.primary.copy(alpha = .13f), Color.Transparent), center = Offset.Zero, radius = radius * .9f)
        onDrawBehind {
            drawRect(colors.background)
            if (mode != PlayerBackdrop.Off) {
                val angle = phase.value * (2 * PI).toFloat()
                val x = size.width * (.18f + .18f * cos(angle))
                val y = size.height * (.83f + .10f * sin(angle))
                translate(x, y) { drawCircle(first, radius, center = Offset.Zero) }
                translate(size.width - x * .6f, size.height * .96f - (y - size.height * .83f)) {
                    drawCircle(second, radius * .9f, center = Offset.Zero)
                }
            }
        }
    })
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
