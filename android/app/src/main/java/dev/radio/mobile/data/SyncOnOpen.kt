package dev.radio.mobile.data

import dev.radio.mobile.connection.ConnectionState

/**
 * Optional sync when the app comes to the foreground (checkpoint 7C). Default off; at most once per 15 minutes.
 * It runs the same manual sync, foreground only, and never touches playback. Failures show only in the sync status row.
 */
object SyncOnOpen {
    const val PREF_ENABLED = "sync_on_open"
    const val PREF_LAST_ATTEMPT = "sync_on_open_last_attempt"
    const val MIN_INTERVAL_MS = 15 * 60_000L

    fun due(enabled: Boolean, connection: ConnectionState, syncBusy: Boolean, lastAttemptAt: Long?, now: Long): Boolean {
        if (!enabled || syncBusy) return false
        if (!connection.loaded || !connection.paired || connection.pending || connection.quarantined ||
            connection.blocked || connection.busy) return false
        // A clock moved backwards counts as due rather than blocking sync for hours.
        return lastAttemptAt == null || lastAttemptAt > now || now - lastAttemptAt >= MIN_INTERVAL_MS
    }
}

/** Runs the manual profile sync if [SyncOnOpen.due]. Called on resume, after pairing and discovery refresh. */
internal suspend fun syncOnOpen(context: android.content.Context, pairing: dev.radio.mobile.connection.PairingRepository,
                                discovery: dev.radio.mobile.connection.DiscoveryRepository) {
    val prefs = context.getSharedPreferences("radio_shell", android.content.Context.MODE_PRIVATE)
    val sync = PortableSyncRepository.get(context, pairing)
    val now = System.currentTimeMillis()
    val last = prefs.getLong(SyncOnOpen.PREF_LAST_ATTEMPT, -1L).takeIf { it >= 0L }
    if (!SyncOnOpen.due(prefs.getBoolean(SyncOnOpen.PREF_ENABLED, false), pairing.state.value, sync.state.value.busy, last, now)) return
    prefs.edit().putLong(SyncOnOpen.PREF_LAST_ATTEMPT, now).apply()
    sync.syncNow()
    if (sync.state.value.error != null) discovery.readAfterConnectionError()
}
