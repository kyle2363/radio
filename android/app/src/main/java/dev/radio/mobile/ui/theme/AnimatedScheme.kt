package dev.radio.mobile.ui.theme

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.material3.ColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** How long colour changes take: palette, light/dark, going on air and mood changes. */
internal const val COLOR_FADE_MS = 400

/** A colour that fades to its new value instead of snapping. The phone's animation setting scales (or removes) it. */
@Composable
fun animatedColor(target: Color, label: String = "color"): Color =
    animateColorAsState(target, tween(COLOR_FADE_MS, easing = FastOutSlowInEasing), label = label).value

/**
 * The whole Material scheme, faded colour by colour, so switching palette or appearance, entering the on-air station
 * look and leaving it all blend instead of snapping.
 */
@Composable
fun animateColorScheme(target: ColorScheme): ColorScheme {
    @Composable fun c(color: Color) = animatedColor(color, "scheme")
    return target.copy(
        primary = c(target.primary), onPrimary = c(target.onPrimary),
        primaryContainer = c(target.primaryContainer), onPrimaryContainer = c(target.onPrimaryContainer),
        inversePrimary = c(target.inversePrimary),
        secondary = c(target.secondary), onSecondary = c(target.onSecondary),
        secondaryContainer = c(target.secondaryContainer), onSecondaryContainer = c(target.onSecondaryContainer),
        tertiary = c(target.tertiary), onTertiary = c(target.onTertiary),
        tertiaryContainer = c(target.tertiaryContainer), onTertiaryContainer = c(target.onTertiaryContainer),
        background = c(target.background), onBackground = c(target.onBackground),
        surface = c(target.surface), onSurface = c(target.onSurface),
        surfaceVariant = c(target.surfaceVariant), onSurfaceVariant = c(target.onSurfaceVariant),
        surfaceTint = c(target.surfaceTint),
        inverseSurface = c(target.inverseSurface), inverseOnSurface = c(target.inverseOnSurface),
        outline = c(target.outline), outlineVariant = c(target.outlineVariant),
        surfaceBright = c(target.surfaceBright), surfaceDim = c(target.surfaceDim),
        surfaceContainer = c(target.surfaceContainer), surfaceContainerHigh = c(target.surfaceContainerHigh),
        surfaceContainerHighest = c(target.surfaceContainerHighest), surfaceContainerLow = c(target.surfaceContainerLow),
        surfaceContainerLowest = c(target.surfaceContainerLowest),
    )
}
