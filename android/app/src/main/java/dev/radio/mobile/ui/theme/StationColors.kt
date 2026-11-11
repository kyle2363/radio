package dev.radio.mobile.ui.theme

/**
 * The dark "station" surface Radio uses on every screen while on air, one set per palette. Plain ARGB values so the
 * contrast rules can be checked in JVM tests; Compose wraps them with Color(...). Same in Light, Dark and System.
 */
data class StationColors(
    val name: String,
    val base: Long,
    val panel: Long,
    val tile: Long,
    val muted: Long,
    val progress: Long,
    val track: Long,
    val outline: Long,
    val onAir: Long = 0xFFFB923C,
    val onAirText: Long = 0xFFFDBA74,
) {
    companion object {
        val Ocean = StationColors("Ocean Blue", base = 0xFF1E293B, panel = 0xFF2B394E, tile = 0xFF334258, muted = 0xFFC9D5E6,
            progress = 0xFFA8C7FF, track = 0xFF3F4C60, outline = 0xFF4B5B74)
        val Teal = StationColors("Deep Teal", base = 0xFF0D2426, panel = 0xFF183A3D, tile = 0xFF22494C, muted = 0xFFBFD8D5,
            progress = 0xFF7DD3C8, track = 0xFF2D5154, outline = 0xFF3E6366)
        val all = listOf(Ocean, Teal)
        fun of(palette: RadioPalette) = if (palette == RadioPalette.Teal) Teal else Ocean
    }
}
