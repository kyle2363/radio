package dev.radio.mobile.playback

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.RemoteViews
import androidx.core.content.ContextCompat
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import dev.radio.mobile.MainActivity
import dev.radio.mobile.R

/** What the home-screen widget shows. */
data class WidgetState(val title: String? = null, val subtitle: String? = null, val playing: Boolean = false)

/**
 * Home-screen player: the current song with Previous, Play/Pause and Next. Tapping the song opens the player.
 * The buttons talk to the playback service through its media session, the same way the lock screen does.
 */
class PlayerWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        manager.updateAppWidget(ids, views(context, saved(context)))
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action !in COMMANDS) { super.onReceive(context, intent); return }
        // A widget tap lets the app start playback from the background for a short while; finish well inside it.
        val pending = goAsync()
        send(context, action!!, widget = true) { pending.finish() }
    }

    companion object {
        private const val TAG = "PlayerWidget"
        private const val PREFS = "player_widget"
        const val HINT_WIDGET = "dev.radio.mobile.widget.CONTROLLER"
        const val ACTION_TOGGLE = "dev.radio.mobile.widget.TOGGLE"
        const val ACTION_NEXT = "dev.radio.mobile.widget.NEXT"
        const val ACTION_PREVIOUS = "dev.radio.mobile.widget.PREVIOUS"
        private val COMMANDS = setOf(ACTION_TOGGLE, ACTION_NEXT, ACTION_PREVIOUS)

        /**
         * Sends one transport command through the media session, the same way the lock screen does. Used by the
         * widget and the Quick Settings tile. Retries while the service is still restoring before its session exists.
         */
        fun send(context: Context, action: String, widget: Boolean = false, onDone: () -> Unit = {}) {
            val finished = java.util.concurrent.atomic.AtomicBoolean(false)
            val done = { if (finished.compareAndSet(false, true)) onDone() }
            val app = context.applicationContext
            val main = Handler(Looper.getMainLooper())
            val timeout = Runnable { done() }
            main.postDelayed(timeout, 9_000)
            fun connect(attempt: Int) {
                val future = MediaController.Builder(app, SessionToken(app, ComponentName(app, RadioPlaybackService::class.java)))
                    .setConnectionHints(Bundle().apply { putBoolean(HINT_WIDGET, widget) }).buildAsync()
                future.addListener({
                    val controller = try { future.get() } catch (error: Exception) { null }
                    if (controller == null) {
                        if (attempt < 8) main.postDelayed({ connect(attempt + 1) }, 600) else { Log.w(TAG, "Couldn't reach playback"); main.removeCallbacks(timeout); done() }
                        return@addListener
                    }
                    when (action) {
                        ACTION_TOGGLE -> if (controller.playWhenReady) controller.pause() else controller.play()
                        ACTION_NEXT -> controller.seekToNext()
                        ACTION_PREVIOUS -> controller.seekToPrevious()
                    }
                    // Give the command a moment to reach the session before letting go.
                    main.postDelayed({ controller.release(); main.removeCallbacks(timeout); done() }, 400)
                }, ContextCompat.getMainExecutor(app))
            }
            connect(0)
        }

        /** The last song and play state, for the Quick Settings tile. */
        fun last(context: Context): WidgetState {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            return WidgetState(prefs.getString("title", null), prefs.getString("subtitle", null), prefs.getBoolean("playing", false))
        }

        /** Called by the playback service whenever the song or play state changes. Cheap when no widget is placed. */
        fun update(context: Context, state: WidgetState) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("title", state.title).putString("subtitle", state.subtitle).putBoolean("playing", state.playing).apply()
            val manager = AppWidgetManager.getInstance(context) ?: return
            val ids = manager.getAppWidgetIds(ComponentName(context, PlayerWidget::class.java))
            if (ids.isNotEmpty()) manager.updateAppWidget(ids, views(context, state))
            PlaybackTile.refresh(context)
        }

        /** Redraws placed widgets with the current song, e.g. after the palette or appearance changes in the app. */
        fun refresh(context: Context) {
            val manager = AppWidgetManager.getInstance(context) ?: return
            val ids = manager.getAppWidgetIds(ComponentName(context, PlayerWidget::class.java))
            if (ids.isNotEmpty()) manager.updateAppWidget(ids, views(context, last(context)))
        }

        /** Colours from the app's palette and appearance; with "Phone" both sets are given and the launcher picks. */
        private fun RemoteViews.applyTheme(context: Context) {
            val shell = context.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
            val palette = shell.getString("palette", "Ocean")
            val (dayDark, nightDark) = WidgetPalette.modes(shell.getString("appearance", "System"))
            val day = WidgetPalette.colors(palette, dayDark)
            val night = WidgetPalette.colors(palette, nightDark)
            fun tint(view: Int, method: String, pick: (WidgetColors) -> Int) =
                setColorStateList(view, method, android.content.res.ColorStateList.valueOf(pick(day)), android.content.res.ColorStateList.valueOf(pick(night)))
            tint(R.id.widget_root, "setBackgroundTintList") { it.background }
            tint(R.id.widget_toggle, "setBackgroundTintList") { it.accent }
            tint(R.id.widget_toggle, "setImageTintList") { it.onAccent }
            tint(R.id.widget_previous, "setImageTintList") { it.title }
            tint(R.id.widget_next, "setImageTintList") { it.title }
            setColorInt(R.id.widget_title, "setTextColor", day.title, night.title)
            setColorInt(R.id.widget_subtitle, "setTextColor", day.subtitle, night.subtitle)
        }

        private fun saved(context: Context): WidgetState {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            // After a restart nothing is playing, whatever was saved.
            return WidgetState(prefs.getString("title", null), prefs.getString("subtitle", null), playing = false)
        }

        private fun views(context: Context, state: WidgetState) = RemoteViews(context.packageName, R.layout.widget_player).apply {
            applyTheme(context)
            setTextViewText(R.id.widget_title, state.title ?: context.getString(R.string.widget_empty))
            setTextViewText(R.id.widget_subtitle, state.subtitle ?: context.getString(R.string.widget_empty_hint))
            setImageViewResource(R.id.widget_toggle, if (state.playing) R.drawable.ic_widget_pause else R.drawable.ic_widget_play)
            setContentDescription(R.id.widget_toggle, context.getString(if (state.playing) R.string.widget_pause else R.string.widget_play))
            val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_NOW_PLAYING)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            setOnClickPendingIntent(R.id.widget_open, open)
            val hasSong = state.title != null
            listOf(R.id.widget_toggle to ACTION_TOGGLE, R.id.widget_next to ACTION_NEXT, R.id.widget_previous to ACTION_PREVIOUS).forEachIndexed { index, (view, action) ->
                // With nothing queued Play still works (it resumes or starts Radio); Previous/Next open the app.
                setOnClickPendingIntent(view, if (!hasSong && action != ACTION_TOGGLE) open else PendingIntent.getBroadcast(context, index + 1,
                    Intent(context, PlayerWidget::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            }
        }
    }
}
