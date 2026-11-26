package dev.radio.mobile.data

import android.content.Context
import dev.radio.mobile.music.ArtistCredit
import dev.radio.mobile.music.Artists
import org.json.JSONObject

/** Local browsing corrections, keyed by the original credit (or by song when the credit was empty). */
internal class ArtistCreditStore(context: Context) {
    private val prefs = context.getSharedPreferences("artist_credits", Context.MODE_PRIVATE)

    fun load(): Map<String, ArtistCredit> = buildMap {
        for ((key, value) in prefs.all) {
            val saved = value as? String ?: continue
            val credit = runCatching {
                val fields = JSONObject(saved)
                ArtistCredit(Artists.names(fields.getString("lead")), Artists.names(fields.getString("guests")))
            }.getOrNull() ?: continue
            put(key, credit)
        }
    }

    fun save(key: String, credit: ArtistCredit?) {
        val edit = prefs.edit()
        if (credit == null) edit.remove(key)
        else edit.putString(key, JSONObject().put("lead", credit.lead.joinToString(";"))
            .put("guests", credit.guests.joinToString(";")).toString())
        edit.apply()
    }
}
