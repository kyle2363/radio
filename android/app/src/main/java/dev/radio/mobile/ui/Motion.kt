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
import androidx.compose.ui.input.nestedscroll.nestedScroll

/**
 * App motion: short enough that nothing feels like it waits on an animation. Compose scales these with the phone's
 * animator duration setting, so "Remove animations" makes them instant.
 */
internal object Motion {
    const val SHORT = 150
    const val MEDIUM = 220
    fun <T> quick() = tween<T>(SHORT, easing = FastOutSlowInEasing)
    fun <T> standard() = tween<T>(MEDIUM, easing = FastOutSlowInEasing)
    /** Things coming to rest after a gesture (a dropped row, a released pill): critically damped, no bounce. */
    fun <T> settle() = androidx.compose.animation.core.spring<T>(dampingRatio = 1f, stiffness = androidx.compose.animation.core.Spring.StiffnessMediumLow)
    /** Press-in and release of a control: quick and firm. */
    fun <T> press() = androidx.compose.animation.core.spring<T>(dampingRatio = 1f, stiffness = androidx.compose.animation.core.Spring.StiffnessMedium)
    /** Elements moving between screens (shared player art and title): a touch of softness, no visible overshoot. */
    fun <T> spatial() = androidx.compose.animation.core.spring<T>(dampingRatio = .9f, stiffness = 380f)
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
        boundsTransform = { _, _ -> Motion.spatial() }) }
}

/**
 * Pull down to close (Now Playing): once the content is scrolled to its top, a further downward drag moves the screen
 * with the finger; letting go far enough (or flinging down) calls [onClose], otherwise it springs back. Works through
 * nested scrolling, so the screen's own scrolling, the queue list and sideways gestures such as the seek bar behave
 * exactly as before.
 */
@Composable
internal fun Modifier.pullDownToClose(enabled: Boolean, onClose: () -> Unit): Modifier {
    var pull by remember { mutableFloatStateOf(0f) }
    val threshold = with(androidx.compose.ui.platform.LocalDensity.current) { 120.dp.toPx() }
    val close by rememberUpdatedState(onClose)
    val connection = remember(enabled, threshold) {
        object : androidx.compose.ui.input.nestedscroll.NestedScrollConnection {
            override fun onPreScroll(available: androidx.compose.ui.geometry.Offset, source: androidx.compose.ui.input.nestedscroll.NestedScrollSource): androidx.compose.ui.geometry.Offset {
                // While pulled down, dragging back up first undoes the pull.
                if (pull > 0f && available.y < 0f) {
                    val used = maxOf(available.y, -pull)
                    pull += used
                    return androidx.compose.ui.geometry.Offset(0f, used)
                }
                return androidx.compose.ui.geometry.Offset.Zero
            }
            override fun onPostScroll(consumed: androidx.compose.ui.geometry.Offset, available: androidx.compose.ui.geometry.Offset, source: androidx.compose.ui.input.nestedscroll.NestedScrollSource): androidx.compose.ui.geometry.Offset {
                if (!enabled || source != androidx.compose.ui.input.nestedscroll.NestedScrollSource.UserInput || available.y <= 0f) return androidx.compose.ui.geometry.Offset.Zero
                pull += available.y * .6f // a little resistance, so it reads as a deliberate pull
                return androidx.compose.ui.geometry.Offset(0f, available.y)
            }
            override suspend fun onPreFling(available: androidx.compose.ui.unit.Velocity): androidx.compose.ui.unit.Velocity {
                if (pull <= 0f) return androidx.compose.ui.unit.Velocity.Zero
                if (pull > threshold || available.y > 2500f) { close(); pull = 0f }
                else androidx.compose.animation.core.animate(pull, 0f, animationSpec = Motion.settle()) { value, _ -> pull = value }
                return available
            }
        }
    }
    return nestedScroll(connection).graphicsLayer {
        val p = (pull / (threshold * 3)).coerceIn(0f, 1f)
        translationY = pull
        scaleX = 1f - .06f * p; scaleY = 1f - .06f * p
        shape = androidx.compose.foundation.shape.RoundedCornerShape(28.dp * p); clip = pull > 0f
    }
}
