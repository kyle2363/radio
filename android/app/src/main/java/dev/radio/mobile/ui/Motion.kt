package dev.radio.mobile.ui

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.ContentTransform
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp

/**
 * App motion: short enough that nothing feels like it waits on an animation. Compose scales these with the phone's
 * animator duration setting, so "Remove animations" makes them instant.
 */
internal object Motion {
    const val SHORT = 150
    const val MEDIUM = 220
    fun <T> quick() = tween<T>(SHORT, easing = FastOutSlowInEasing)
    fun <T> standard() = tween<T>(MEDIUM, easing = FastOutSlowInEasing)
}

/** Screen-level transition: Now playing rises from below, pushed screens slide in from the side, tabs crossfade. */
internal fun AnimatedContentTransitionScope<String>.screenTransition(): ContentTransform {
    fun depth(screen: String) = when (screen) { "Now playing" -> 2; "Search", "Server", "Matching" -> 1; else -> 0 }
    val forward = depth(targetState) >= depth(initialState)
    return when {
        targetState == "Now playing" -> (fadeIn(Motion.standard()) + slideInVertically(Motion.standard()) { it / 10 }) togetherWith fadeOut(Motion.quick())
        initialState == "Now playing" -> fadeIn(Motion.standard()) togetherWith (fadeOut(Motion.quick()) + slideOutVertically(Motion.quick()) { it / 10 })
        else -> (fadeIn(Motion.standard()) + slideInHorizontally(Motion.standard()) { if (forward) it / 12 else -it / 12 }) togetherWith
            (fadeOut(Motion.quick()) + slideOutHorizontally(Motion.quick()) { if (forward) -it / 12 else it / 12 })
    }
}

/**
 * Fades (with a small rise) new content in whenever [key] changes. Enter only, so a hoisted list state is never
 * attached to two lists at once the way a two-sided crossfade would.
 */
@Composable
internal fun FadeInOnChange(key: Any?, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    key(key) {
        val progress = remember { Animatable(0f) }
        LaunchedEffect(Unit) { progress.animateTo(1f, Motion.standard()) }
        Box(modifier.graphicsLayer { alpha = progress.value; translationY = (1f - progress.value) * 12.dp.toPx() }) { content() }
    }
}

/** The last non-null [value], so content can finish its exit animation after its subject goes away. */
@Composable
internal fun <T : Any> rememberLastNonNull(value: T?): T? {
    val holder = remember { object { var last: T? = null } }
    if (value != null) holder.last = value
    return holder.last
}

/** Predictive back preview amount (0..1): eased so the first part of the swipe already reads as "going back". */
internal fun backPreview(progress: Float): Float = FastOutSlowInEasing.transform(progress.coerceIn(0f, 1f))

/**
 * Checkpoint 10: the mini-player's art and title move into place in Now Playing (and back) instead of cross-fading.
 * A no-op without a visibility scope (first frame) so nothing ever disappears.
 */
@OptIn(androidx.compose.animation.ExperimentalSharedTransitionApi::class)
@Composable
internal fun Modifier.sharedPlayer(shared: androidx.compose.animation.SharedTransitionScope, visibility: androidx.compose.animation.AnimatedVisibilityScope?, key: String): Modifier {
    if (visibility == null) return this
    return with(shared) { this@sharedPlayer.sharedElement(rememberSharedContentState("player-$key"), visibility,
        boundsTransform = { _, _ -> androidx.compose.animation.core.spring(dampingRatio = 0.85f, stiffness = 380f) }) }
}
