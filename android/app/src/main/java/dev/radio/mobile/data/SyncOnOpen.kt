package dev.radio.mobile.data

import dev.radio.mobile.connection.ConnectionState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

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

    /** Uploads only if the app is still in the foreground when the slow refresh before it ends, and sync is [due]. */
    suspend fun attempt(foreground: () -> Boolean, due: () -> Boolean, markAttempt: () -> Unit, upload: suspend () -> Unit): Boolean {
        if (!foreground() || !due()) return false
        markAttempt()
        upload()
        return true
    }
}

/**
 * The one sync-on-open run, tied to the started lifecycle: [start] on resume replaces any earlier run, and [stop] on
 * ON_STOP cancels it, so a slow discovery that finishes after the user leaves cannot start an upload.
 */
class SyncOnOpenRunner(private val scope: CoroutineScope) {
    private var job: Job? = null
    @Volatile var foreground = false
        private set

    fun start(block: suspend (foreground: () -> Boolean) -> Unit) {
        foreground = true
        job?.cancel()
        job = scope.launch { block { foreground } }
    }

    fun stop() {
        foreground = false
        job?.cancel()
        job = null
    }
}

/**
 * Runs the manual profile sync if [SyncOnOpen.due] and the app is still in the [foreground]. Called on resume, after
 * pairing and discovery refresh, from a [SyncOnOpenRunner] run.
 */
internal suspend fun syncOnOpen(context: android.content.Context, pairing: dev.radio.mobile.connection.PairingRepository,
                                discovery: dev.radio.mobile.connection.DiscoveryRepository, foreground: () -> Boolean) {
    val prefs = context.getSharedPreferences("radio_shell", android.content.Context.MODE_PRIVATE)
    val sync = PortableSyncRepository.get(context, pairing)
    val now = System.currentTimeMillis()
    val last = prefs.getLong(SyncOnOpen.PREF_LAST_ATTEMPT, -1L).takeIf { it >= 0L }
    val ran = SyncOnOpen.attempt(foreground,
        due = { SyncOnOpen.due(prefs.getBoolean(SyncOnOpen.PREF_ENABLED, false), pairing.state.value, sync.state.value.busy, last, now) },
        markAttempt = { prefs.edit().putLong(SyncOnOpen.PREF_LAST_ATTEMPT, now).apply() },
        upload = { sync.syncNow() })
    if (ran && sync.state.value.error != null && foreground()) discovery.readAfterConnectionError()
}
