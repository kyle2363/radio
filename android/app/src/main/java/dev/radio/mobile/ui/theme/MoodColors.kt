package dev.radio.mobile.ui.theme

/**
 * One colour set per Radio mood, used only while on air: the selected mood chip, the mini-player and the glow behind
 * Now Playing. Plain ARGB values so the contrast rules can be checked in JVM tests; Compose wraps them with Color(...).
 *
 * [accent] is a light tint readable on the station surface, [onAccent] is the text colour on top of it, and [field] is
 * a dark tone that keeps white text readable (mini-player fill and the background glow).
 */
data class MoodColors(val mood: String, val accent: Long, val onAccent: Long, val field: Long) {
    companion object {
        val all = listOf(
            MoodColors("chill", accent = 0xFF5EEAD4, onAccent = 0xFF0B2F2B, field = 0xFF0F5E5A),
            MoodColors("neutral", accent = 0xFFCBD5E1, onAccent = 0xFF1E293B, field = 0xFF3F4C60),
            MoodColors("energetic", accent = 0xFFFDBA74, onAccent = 0xFF431407, field = 0xFF8A3412),
            MoodColors("focus", accent = 0xFFA5B4FC, onAccent = 0xFF1E1B4B, field = 0xFF312E81),
            MoodColors("melancholy", accent = 0xFFC4B5FD, onAccent = 0xFF2E1065, field = 0xFF3B1F6B),
            MoodColors("discovery", accent = 0xFFF9A8D4, onAccent = 0xFF500724, field = 0xFF7A1D4E),
        )
        private val byMood = all.associateBy { it.mood }
        /** Unknown or blank moods fall back to neutral, so a new server mood never crashes the UI. */
        fun of(mood: String): MoodColors = byMood[mood.trim().lowercase()] ?: byMood.getValue("neutral")
    }
}
