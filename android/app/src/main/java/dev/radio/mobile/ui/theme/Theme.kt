package dev.radio.mobile.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

@Composable
fun RadioTheme(
    palette: RadioPalette = RadioPalette.Ocean,
    appearance: Appearance = Appearance.System,
    content: @Composable () -> Unit
) {
    val dark = when (appearance) {
        Appearance.System -> isSystemInDarkTheme()
        Appearance.Dark -> true
        Appearance.Light -> false
    }
    val ocean = palette == RadioPalette.Ocean
    val colorScheme = if (dark) darkColorScheme(
        primary = if (ocean) Color(0xFFA8C7FF) else Color(0xFF83D5C9),
        onPrimary = if (ocean) Color(0xFF00316D) else Color(0xFF003731),
        primaryContainer = if (ocean) Color(0xFF274877) else Color(0xFF174B46),
        onPrimaryContainer = if (ocean) OceanContainer else TealContainer,
        secondary = if (ocean) Color(0xFFBBC9E0) else Color(0xFFAFCFC9),
        onSecondary = Color(0xFF213038),
        secondaryContainer = if (ocean) Color(0xFF33445E) else Color(0xFF23413F),
        onSecondaryContainer = Color(0xFFE3EBF0),
        background = if (ocean) OceanNight else TealNight,
        onBackground = Color(0xFFE6EBEF),
        surface = if (ocean) OceanNight else TealNight,
        onSurface = Color(0xFFE6EBEF),
        surfaceVariant = if (ocean) Color(0xFF293649) else Color(0xFF1A3335),
        onSurfaceVariant = Color(0xFFBEC9CE),
        outline = Color(0xFF89989F),
        outlineVariant = if (ocean) Color(0xFF3F4C60) else Color(0xFF345052),
        surfaceContainer = if (ocean) Color(0xFF253246) else Color(0xFF152E30),
        surfaceContainerLow = if (ocean) Color(0xFF223044) else Color(0xFF122B2D),
        surfaceContainerLowest = if (ocean) Color(0xFF172233) else Color(0xFF091E20),
        surfaceContainerHigh = if (ocean) Color(0xFF2B394E) else Color(0xFF1D383A),
        surfaceContainerHighest = if (ocean) Color(0xFF334258) else Color(0xFF244042),
        surfaceBright = if (ocean) Color(0xFF38475D) else Color(0xFF2B4749),
        surfaceDim = if (ocean) OceanNight else TealNight,
        surfaceTint = if (ocean) Color(0xFFA8C7FF) else Color(0xFF83D5C9)
    ) else lightColorScheme(
        primary = if (ocean) OceanAccent else TealAccent,
        onPrimary = Color.White,
        primaryContainer = if (ocean) OceanContainer else TealContainer,
        onPrimaryContainer = if (ocean) Color(0xFF173B72) else Color(0xFF124B46),
        secondary = Color(0xFF52616D),
        onSecondary = Color.White,
        secondaryContainer = if (ocean) Color(0xFFE5EBF3) else Color(0xFFE1ECE9),
        onSecondaryContainer = Color(0xFF23343C),
        background = Color(0xFFFAFBFC),
        onBackground = Color(0xFF1A2229),
        surface = Color(0xFFFAFBFC),
        onSurface = Color(0xFF1A2229),
        surfaceVariant = Color(0xFFE9EDF0),
        onSurfaceVariant = Color(0xFF53616B),
        outline = Color(0xFF73818A),
        outlineVariant = Color(0xFFD6DEE3),
        surfaceContainer = Color(0xFFEEF1F4),
        surfaceContainerLow = Color(0xFFF4F6F8),
        surfaceContainerLowest = Color.White,
        surfaceContainerHigh = Color(0xFFE8EDF0),
        surfaceContainerHighest = Color(0xFFE2E8EC),
        surfaceBright = Color(0xFFFAFBFC),
        surfaceDim = Color(0xFFDBE1E5),
        surfaceTint = if (ocean) OceanAccent else TealAccent
    )
    val view = LocalView.current
    SideEffect {
        (view.context as? Activity)?.window?.let { window ->
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !dark
                isAppearanceLightNavigationBars = !dark
            }
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content
    )
}
