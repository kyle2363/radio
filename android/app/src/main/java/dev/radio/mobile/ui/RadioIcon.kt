package dev.radio.mobile.ui

import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

// Native vectors with consistent geometry; no icon font or extra UI dependency.
enum class Symbol(val path: String, val filled: Boolean = false) {
    Refresh("M20 7 A9 9 0 1 0 21 14 M20 2 V8 H14"),
    Play("M8 5 L19 12 L8 19 Z", true),
    Pause("M7 5 H10 V19 H7 Z M14 5 H17 V19 H14 Z", true),
    Next("M5 5 L15 12 L5 19 Z M17 5 H20 V19 H17 Z", true),
    Previous("M19 5 L9 12 L19 19 Z M4 5 H7 V19 H4 Z", true),
    Search("M21 21 L16 16 M18 10 A8 8 0 1 1 2 10 A8 8 0 1 1 18 10"),
    Back("M19 12 H5 M12 5 L5 12 L12 19"),
    Folder("M3 6 H10 L12 8 H21 V20 H3 Z"),
    Music("M9 18 V5 L20 3 V16 M9 5 L20 3 M9 17 C2 14 2 23 9 20 Z M20 15 C13 12 13 21 20 18 Z"),
    Library("M4 4 V20 M9 4 V20 M14 4 V20 M18 4 L22 20"),
    Radio("M3 9 H21 V21 H3 Z M5 9 L18 3 M7 13 H17 M8 17 H8 M16 16 V18"),
    Settings("M4 6 H20 M4 12 H20 M4 18 H20 M8 3 V9 M16 9 V15 M10 15 V21"),
    Queue("M4 5 H20 M4 10 H20 M4 15 H13 M17 14 L22 18 L17 22 Z"),
    Heart("M12 20 L3 11 C-1 3 8 0 12 7 C16 0 25 3 21 11 Z"),
    Shuffle("M3 6 H6 C12 6 12 18 18 18 H21 M18 15 L21 18 L18 21 M3 18 H6 C8 18 9 16 10 14 M14 10 C16 6 18 6 21 6 M18 3 L21 6 L18 9"),
    Lock("M6 10 H18 V21 H6 Z M8 10 V6 A4 4 0 0 1 16 6 V10"),
    Minimize("M4 8 H9 V3 M20 16 H15 V21"),
    Expand("M9 3 H3 V9 M15 21 H21 V15")
}

private val vectors = Symbol.entries.associateWith { symbol ->
    ImageVector.Builder(symbol.name, 24.dp, 24.dp, 24f, 24f).addPath(
        pathData = PathParser().parsePathString(symbol.path).toNodes(),
        pathFillType = PathFillType.NonZero,
        fill = if (symbol.filled) SolidColor(Color.Black) else null,
        stroke = if (symbol.filled) null else SolidColor(Color.Black),
        strokeLineWidth = 1.8f
    ).build()
}

@Composable
fun RadioIcon(symbol: Symbol, description: String? = null, modifier: Modifier = Modifier) {
    Icon(vectors.getValue(symbol), contentDescription = description, modifier = modifier)
}
