package dev.radio.mobile.playback

import android.content.Context
import androidx.core.content.edit
import dev.radio.mobile.recommendation.StationRecord

/**
 * The station beside the paused playback bookmark (checkpoint 4.2). SharedPreferences, not Room: it is small, optional
 * context, and a missing or mismatched record only means the restored queue comes back as a normal queue.
 */
internal class StationStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("radio_station", Context.MODE_PRIVATE)
    @Volatile private var last: String? = prefs.getString(KEY, null)

    fun save(record: StationRecord?) {
        val encoded = record?.encode()
        if (encoded == last) return
        last = encoded
        prefs.edit { if (encoded == null) remove(KEY) else putString(KEY, encoded) }
    }
    fun load(): StationRecord? = StationRecord.decode(prefs.getString(KEY, null))
    fun clear() = save(null)

    private companion object { const val KEY = "station" }
}
