package dev.radio.mobile

import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.playback.PlayerWidget
import dev.radio.mobile.playback.WidgetState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class PlayerWidgetTest {
    @Test fun smallestWidgetKeepsArtworkAndResumeWithoutCrowdingTheTitle() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        instrumentation.runOnMainSync {
            val widget = PlayerWidget.views(context, WidgetState("After Hours", "Night Atlas"))
                .apply(context, FrameLayout(context))
            assertNotNull(widget.findViewById<ImageView>(R.id.widget_artwork).drawable)
            assertEquals("After Hours", widget.findViewById<TextView>(R.id.widget_title).text.toString())
            assertEquals(View.GONE, widget.findViewById<View>(R.id.widget_previous).visibility)
            assertEquals(View.GONE, widget.findViewById<View>(R.id.widget_next).visibility)
            assertEquals(context.getString(R.string.widget_resume, "After Hours"),
                widget.findViewById<View>(R.id.widget_toggle).contentDescription)
        }
    }
}
