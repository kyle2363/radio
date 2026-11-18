package dev.radio.mobile.playback

import android.content.ComponentName
import android.content.Context
import android.graphics.drawable.Icon
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import dev.radio.mobile.R

/**
 * Checkpoint 11: Quick Settings tile. Tap plays or pauses (with nothing queued, Play resumes or starts Radio);
 * long-press opens the app (MainActivity handles QS_TILE_PREFERENCES). The subtitle shows the current song.
 */
class PlaybackTile : TileService() {
    override fun onStartListening() { render() }

    override fun onClick() {
        val playing = PlayerWidget.last(this).playing
        // Show the expected state at once; the service's next update confirms it.
        qsTile?.let { it.state = if (playing) Tile.STATE_INACTIVE else Tile.STATE_ACTIVE; it.updateTile() }
        PlayerWidget.send(this, PlayerWidget.ACTION_TOGGLE)
    }

    private fun render() {
        val tile = qsTile ?: return
        val state = PlayerWidget.last(this)
        tile.label = getString(R.string.app_name)
        tile.subtitle = state.title ?: getString(R.string.widget_empty)
        tile.state = if (state.playing) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.icon = Icon.createWithResource(this, if (state.playing) R.drawable.ic_widget_pause else R.drawable.ic_widget_play)
        tile.contentDescription = getString(if (state.playing) R.string.widget_pause else R.string.widget_play)
        tile.updateTile()
    }

    companion object {
        /** Asks the system to redraw the tile if it is placed; harmless otherwise. */
        fun refresh(context: Context) {
            runCatching { TileService.requestListeningState(context, ComponentName(context, PlaybackTile::class.java)) }
        }
    }
}
