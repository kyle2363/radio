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
    More("M13.5 5 A1.5 1.5 0 1 1 10.5 5 A1.5 1.5 0 1 1 13.5 5 Z M13.5 12 A1.5 1.5 0 1 1 10.5 12 A1.5 1.5 0 1 1 13.5 12 Z M13.5 19 A1.5 1.5 0 1 1 10.5 19 A1.5 1.5 0 1 1 13.5 19 Z", true),
    Clock("M21 12 A9 9 0 1 1 3 12 A9 9 0 1 1 21 12 M12 7 V12 L15 14"),
    /** A clock with a rewind arrow: "what was that song?" */
    History("M3.5 12 A8.5 8.5 0 1 0 6 6 M3 3 V8 H8 M12 8 V12 L15 14"),
    Moon("M20 14.5 A8.5 8.5 0 1 1 9.5 4 A7 7 0 0 0 20 14.5 Z"),
    Repeat("M4 11 V9 A3 3 0 0 1 7 6 H19 M16 3 L19 6 L16 9 M20 13 V15 A3 3 0 0 1 17 18 H5 M8 21 L5 18 L8 15"),
    RepeatOne("M4 11 V9 A3 3 0 0 1 7 6 H19 M16 3 L19 6 L16 9 M20 13 V15 A3 3 0 0 1 17 18 H5 M8 21 L5 18 L8 15 M11 10 L12.5 9 V15"),
    Lyrics("M4 5 H20 M4 10 H20 M4 15 H12 M4 20 H9 M17 13 V20 M17 13 L21 12"),
    Sparkle("M12 3 L13.8 10.2 L21 12 L13.8 13.8 L12 21 L10.2 13.8 L3 12 L10.2 10.2 Z"),
    Add("M12 4 V20 M4 12 H20"),
    Sort("M7 4 V20 M3 16 L7 20 L11 16 M17 20 V4 M13 8 L17 4 L21 8"),
    Up("M5 14 L12 7 L19 14"),
    Down("M5 10 L12 17 L19 10"),
    Remove("M4 6 H20 M9 6 V3 H15 V6 M6 6 L7 21 H17 L18 6"),
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
    HeartFilled("M12 20 L3 11 C-1 3 8 0 12 7 C16 0 25 3 21 11 Z", true),
    Shuffle("M3 6 H6 C12 6 12 18 18 18 H21 M18 15 L21 18 L18 21 M3 18 H6 C8 18 9 16 10 14 M14 10 C16 6 18 6 21 6 M18 3 L21 6 L18 9"),
    Lock("M4 11 H20 V20 H4 Z M8 11 V8.5 A4 4 0 0 1 16 8.5 V11"),
    Minimize("M4 8 H9 V3 M20 16 H15 V21"),
    Expand("M9 3 H3 V9 M15 21 H21 V15"),
    Stop("M6 6 H18 V18 H6 Z"),
    Close("M6 6 L18 18 M18 6 L6 18"),
    Check("M5 12 L10 17 L19 7"),
    Block("M20 12 A8 8 0 1 1 4 12 A8 8 0 1 1 20 12 M6.5 6.5 L17.5 17.5"),
    Warning("M12 4 L21 20 H3 Z M12 10 V14 M12 17 V17.5"),
    Gem("M6 3 H18 L22 9 L12 21 L2 9 Z M2 9 H22 M12 21 L8 9 L12 3 L16 9 L12 21"),
    Chart("M5 20 V13 M12 20 V5 M19 20 V9 M3 20 H21"),
    Person("M16 8 A4 4 0 1 1 8 8 A4 4 0 1 1 16 8 M4 21 C4 16 8 14 12 14 C16 14 20 16 20 21"),
    Sliders("M6 4 V20 M12 4 V20 M18 4 V20 M3 9 H9 M9 15 H15 M15 7 H21"),
    Forward("M9 5 L16 12 L9 19")
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
