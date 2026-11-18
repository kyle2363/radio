package dev.radio.mobile.playback

/**
 * Where a queue came from (checkpoint 8A). [label] is what the player shows; [kind] and [id] are recorded,
 * locally only, with each actual start so stats can say where listening comes from.
 */
data class PlaySource(val label: String, val kind: String, val id: String? = null) {
    companion object {
        const val RADIO = "radio"; const val PLAYLIST = "playlist"; const val SMART_LIST = "smart_list"
        const val FOLDER = "folder"; const val ARTIST = "artist"; const val ALL_SONGS = "all_songs"
        const val SEARCH = "search"; const val QUEUE_EDIT = "queue_edit"; const val RESTORE = "restore"
        val Radio = PlaySource("Radio", RADIO)
        val AllSongs = PlaySource("All songs", ALL_SONGS)
        val Search = PlaySource("Search results", SEARCH)
        val CustomQueue = PlaySource("Custom queue", QUEUE_EDIT)
        /** Best guess for callers that only have a label (tests, older call sites). */
        fun of(label: String) = when (label) {
            "Radio" -> Radio; "All songs" -> AllSongs; "Search results" -> Search; "Custom queue" -> CustomQueue
            else -> PlaySource(label, FOLDER)
        }
    }
}

/** What made an attempt start. Media buttons cover the notification, lock screen, headset and car. */
object PlayTrigger {
    const val TAP = "tap"; const val AUTO_ADVANCE = "auto_advance"; const val CROSSFADE = "crossfade"
    const val NEXT = "next"; const val PREVIOUS = "previous"; const val MEDIA_BUTTON = "media_button"; const val WIDGET = "widget"

    /**
     * The trigger recorded for an attempt: a command from outside the app (notification, headset, widget) wins over the
     * in-app meaning of the same command, and an attempt nobody chose (restored or found mid-queue) takes the command that
     * actually made it audible.
     */
    fun resolve(begun: String?, external: String?, latestCommand: String?): String =
        when {
            begun == AUTO_ADVANCE || begun == CROSSFADE -> begun
            external != null -> external
            begun != null -> begun
            latestCommand != null -> latestCommand
            else -> AUTO_ADVANCE
        }
}

/** The local-only context captured for one audible start. */
data class PlayContext(val sourceKind: String, val sourceId: String?, val stationOrigin: String?, val trigger: String)
