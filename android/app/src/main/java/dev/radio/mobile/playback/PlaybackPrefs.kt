package dev.radio.mobile.playback

import android.content.Context

/**
 * Repeat survives process death while the queue itself comes from the Room bookmark.
 * It is kept out of the snapshot schema so that bookmark stays a pure record of what was
 * playing, and so the schema does not need another migration for a playback preference.
 */
internal class PlaybackPrefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var repeat: RepeatMode
        get() = when (prefs.getString(KEY_REPEAT, null)) {
            VALUE_ALL -> RepeatMode.ALL
            VALUE_ONE -> RepeatMode.ONE
            else -> RepeatMode.OFF
        }
        set(value) {
            prefs.edit().putString(KEY_REPEAT, when (value) {
                RepeatMode.OFF -> VALUE_OFF
                RepeatMode.ALL -> VALUE_ALL
                RepeatMode.ONE -> VALUE_ONE
            }).apply()
        }

    companion object {
        const val FILE = "playback_prefs"
        private const val KEY_REPEAT = "repeat_mode"
        private const val VALUE_OFF = "off"
        private const val VALUE_ALL = "all"
        private const val VALUE_ONE = "one"
    }
}
