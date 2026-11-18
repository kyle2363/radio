package dev.radio.mobile.playback

import android.media.audiofx.BassBoost
import android.media.audiofx.Equalizer
import android.util.Log

/**
 * The phone's equaliser and bass boost on the player's audio session. Both are optional: some phones and emulators
 * refuse them, and playback must carry on unchanged when they do.
 */
internal class AudioEffects(private val sessionId: Int) {
    private var equalizer: Equalizer? = null
    private var bass: BassBoost? = null
    private var tried = false
    var info: EqualizerInfo? = null
        private set

    private fun create() {
        if (tried) return
        tried = true
        equalizer = try { Equalizer(0, sessionId) } catch (error: Exception) { Log.w(TAG, "Equaliser unavailable", error); null }
        bass = try { BassBoost(0, sessionId).takeIf { it.strengthSupported } } catch (error: Exception) { Log.w(TAG, "Bass boost unavailable", error); null }
        val eq = equalizer ?: return
        info = try {
            val bands = (0 until eq.numberOfBands).map { it.toShort() }
            val range = eq.bandLevelRange
            val presets = (0 until eq.numberOfPresets).map { eq.getPresetName(it.toShort()) }
            // Read each preset's levels once so the screen can show them without touching the effect.
            val levels = presets.indices.map { preset -> eq.usePreset(preset.toShort()); bands.map { eq.getBandLevel(it).toInt() } }
            EqualizerInfo(bands.map { eq.getCenterFreq(it) / 1000 }, range[0].toInt(), range[1].toInt(), presets, levels, bass != null)
        } catch (error: Exception) { Log.w(TAG, "Couldn't read the equaliser", error); release(); null }
    }

    fun apply(settings: AudioSettings) {
        if (!settings.eqEnabled && !tried) return // Never create effects for a listener who doesn't use them.
        create()
        val eq = equalizer ?: return
        val described = info ?: return
        try {
            if (settings.eqEnabled) {
                if (settings.eqPreset in described.presets.indices) eq.usePreset(settings.eqPreset.toShort())
                else described.bandsHz.indices.forEach { band ->
                    val level = settings.eqLevels.getOrNull(band) ?: 0
                    eq.setBandLevel(band.toShort(), level.coerceIn(described.minLevel, described.maxLevel).toShort())
                }
            }
            eq.enabled = settings.eqEnabled
            bass?.let { boost ->
                val on = settings.eqEnabled && settings.bassBoost > 0
                if (on) boost.setStrength(settings.bassBoost.toShort())
                boost.enabled = on
            }
        } catch (error: Exception) { Log.w(TAG, "Couldn't apply the equaliser", error) }
    }

    /** Creates the effects to describe them, leaving them off. */
    fun describe(): EqualizerInfo? { create(); return info }

    fun release() {
        try { equalizer?.release() } catch (_: Exception) {}
        try { bass?.release() } catch (_: Exception) {}
        equalizer = null; bass = null
    }

    private companion object { const val TAG = "AudioEffects" }
}
