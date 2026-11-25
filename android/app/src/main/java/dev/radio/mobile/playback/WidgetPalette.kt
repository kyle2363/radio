package dev.radio.mobile.playback

/** The widget's colours as ARGB ints (RemoteViews takes plain colours, not Compose ones). */
data class WidgetColors(val background: Int, val title: Int, val subtitle: Int, val accent: Int, val onAccent: Int)

/**
 * The home-screen widget follows the app's own palette (Ocean Blue or Deep Teal) and appearance (Phone, Light or Dark),
 * so it matches the app rather than always being Ocean Blue. Values mirror the app theme (`ui/theme/Theme.kt`).
 */
object WidgetPalette {
    private val oceanLight = WidgetColors(0xFFDBEAFE.toInt(), 0xFF0F172A.toInt(), 0xFF334155.toInt(), 0xFF2563EB.toInt(), 0xFFFFFFFF.toInt())
    private val oceanDark = WidgetColors(0xFF1E293B.toInt(), 0xFFF1F5F9.toInt(), 0xFFCBD5E1.toInt(), 0xFF93C5FD.toInt(), 0xFF0F172A.toInt())
    private val tealLight = WidgetColors(0xFFCFEDEA.toInt(), 0xFF0F172A.toInt(), 0xFF2F4F4C.toInt(), 0xFF0F766E.toInt(), 0xFFFFFFFF.toInt())
    private val tealDark = WidgetColors(0xFF0D2426.toInt(), 0xFFEFF7F6.toInt(), 0xFFC3D9D6.toInt(), 0xFF83D5C9.toInt(), 0xFF0D2426.toInt())

    fun colors(palette: String?, dark: Boolean): WidgetColors =
        if (palette == "Teal") (if (dark) tealDark else tealLight) else (if (dark) oceanDark else oceanLight)

    /** [light-mode colours are dark?, night-mode colours are dark?]: "Phone" lets the system pick, Light/Dark fix it. */
    fun modes(appearance: String?): List<Boolean> = when (appearance) {
        "Light" -> listOf(false, false)
        "Dark" -> listOf(true, true)
        else -> listOf(false, true)
    }
}
