package dev.radio.mobile.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/** The station colours for the selected palette, available everywhere (the Radio tab's card and mini-player use them in light mode too). */
val LocalStationColors = staticCompositionLocalOf { StationColors.Ocean }

/**
 * Radio's on-air surface: a dark Material scheme built from [StationColors], so every component inside (transport,
 * queue, menus' anchors, dialogs launched from it) follows the station look whatever the app appearance is.
 */
@Composable
fun StationTheme(colors: StationColors, enabled: Boolean = true, content: @Composable () -> Unit) {
    val view = LocalView.current
    if (!enabled) {
        // Leaving the station surface: give the system bars back to the app's own appearance.
        val light = MaterialTheme.colorScheme.background.luminance() > .5f
        SideEffect {
            (view.context as? Activity)?.window?.let { window ->
                WindowCompat.getInsetsController(window, view).apply { isAppearanceLightStatusBars = light; isAppearanceLightNavigationBars = light }
            }
        }
        CompositionLocalProvider(LocalStationColors provides colors, content = content); return
    }
    val base = Color(colors.base)
    val scheme = darkColorScheme(
        primary = Color(colors.progress), onPrimary = base,
        primaryContainer = Color(colors.tile), onPrimaryContainer = Color.White,
        secondary = Color(colors.muted), onSecondary = base,
        secondaryContainer = Color(colors.tile), onSecondaryContainer = Color.White,
        tertiary = Color(colors.onAirText), onTertiary = base,
        background = base, onBackground = Color.White,
        surface = base, onSurface = Color.White,
        surfaceVariant = Color(colors.tile), onSurfaceVariant = Color(colors.muted),
        surfaceContainerLowest = base, surfaceContainerLow = base,
        surfaceContainer = Color(colors.panel), surfaceContainerHigh = Color(colors.panel), surfaceContainerHighest = Color(colors.tile),
        surfaceBright = Color(colors.tile), surfaceDim = base, surfaceTint = Color(colors.progress),
        outline = Color(colors.outline), outlineVariant = Color(colors.track),
        error = Color(0xFFFCA5A5), onError = Color(0xFF450A0A),
        errorContainer = Color(0xFF3A2A2A), onErrorContainer = Color(0xFFF3D0D0),
    )
    SideEffect {
        (view.context as? Activity)?.window?.let { window ->
            WindowCompat.getInsetsController(window, view).apply { isAppearanceLightStatusBars = false; isAppearanceLightNavigationBars = false }
        }
    }
    CompositionLocalProvider(LocalStationColors provides colors) {
        MaterialTheme(colorScheme = scheme, typography = MaterialTheme.typography, content = content)
    }
}
