package dev.radio.mobile.playback

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** How the music sounds. Kept on this phone only; none of it is synced. */
data class AudioSettings(
    val speed: Float = 1f,
    val skipSilence: Boolean = false,
    /** Seconds the end of one song overlaps the start of the next on automatic changes; 0 is off. */
    val crossfadeSeconds: Int = 0,
    val eqEnabled: Boolean = false,
    /** A built-in preset of the phone's equaliser, or -1 for the user's own band levels. */
    val eqPreset: Int = -1,
    /** Millibels per band, used when [eqPreset] is -1. */
    val eqLevels: List<Int> = emptyList(),
    /** 0–1000; only applies while the equaliser is on. */
    val bassBoost: Int = 0,
) {
    companion object {
        val SPEEDS = listOf(0.75f, 1f, 1.25f, 1.5f, 1.75f, 2f)
    }
}

/** What this phone's equaliser offers. Null until playback has started once, or when the phone has none. */
data class EqualizerInfo(
    val bandsHz: List<Int>,
    val minLevel: Int,
    val maxLevel: Int,
    val presets: List<String>,
    /** Band levels of each preset, in [presets] order. */
    val presetLevels: List<List<Int>>,
    val bassBoost: Boolean,
)

/** One per process: the screens edit it, the playback service applies it. */
class AudioSettingsStore private constructor(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PlaybackPrefs.FILE, Context.MODE_PRIVATE)
    private val mutable = MutableStateFlow(read())
    val settings: StateFlow<AudioSettings> = mutable.asStateFlow()
    private val info = MutableStateFlow<EqualizerInfo?>(null)
    val equalizer: StateFlow<EqualizerInfo?> = info.asStateFlow()
    private val unavailable = MutableStateFlow(false)
    /** True once the phone has refused to create an equaliser. */
    val equalizerUnavailable: StateFlow<Boolean> = unavailable.asStateFlow()

    fun update(transform: (AudioSettings) -> AudioSettings) {
        mutable.update { sanitize(transform(it)) }
        write(mutable.value)
    }

    internal fun publishEqualizer(value: EqualizerInfo?) { info.value = value; unavailable.value = value == null }

    private fun sanitize(value: AudioSettings) = value.copy(
        speed = value.speed.coerceIn(0.5f, 2f),
        crossfadeSeconds = value.crossfadeSeconds.coerceIn(0, 12),
        bassBoost = value.bassBoost.coerceIn(0, 1000))

    private fun read() = AudioSettings(
        speed = prefs.getFloat(KEY_SPEED, 1f),
        skipSilence = prefs.getBoolean(KEY_SKIP_SILENCE, false),
        crossfadeSeconds = prefs.getInt(KEY_CROSSFADE, 0),
        eqEnabled = prefs.getBoolean(KEY_EQ, false),
        eqPreset = prefs.getInt(KEY_EQ_PRESET, -1),
        eqLevels = prefs.getString(KEY_EQ_LEVELS, "").orEmpty().split(',').mapNotNull { it.trim().toIntOrNull() },
        bassBoost = prefs.getInt(KEY_BASS, 0)).let(::sanitize)

    private fun write(value: AudioSettings) {
        prefs.edit().putFloat(KEY_SPEED, value.speed).putBoolean(KEY_SKIP_SILENCE, value.skipSilence)
            .putInt(KEY_CROSSFADE, value.crossfadeSeconds).putBoolean(KEY_EQ, value.eqEnabled).putInt(KEY_EQ_PRESET, value.eqPreset)
            .putString(KEY_EQ_LEVELS, value.eqLevels.joinToString(",")).putInt(KEY_BASS, value.bassBoost).apply()
    }

    companion object {
        private const val KEY_SPEED = "speed"
        private const val KEY_SKIP_SILENCE = "skip_silence"
        private const val KEY_CROSSFADE = "crossfade_seconds"
        private const val KEY_EQ = "eq_enabled"
        private const val KEY_EQ_PRESET = "eq_preset"
        private const val KEY_EQ_LEVELS = "eq_levels"
        private const val KEY_BASS = "bass_boost"
        @Volatile private var instance: AudioSettingsStore? = null
        fun get(context: Context): AudioSettingsStore = instance ?: synchronized(this) { instance ?: AudioSettingsStore(context).also { instance = it } }
    }
}
