package dev.radio.mobile.playback

import org.junit.Assert.*
import org.junit.Test

class WidgetPaletteTest {
    @Test fun followsTheAppPaletteInLightAndDark() {
        assertEquals(0xFFDBEAFE.toInt(), WidgetPalette.colors("Ocean", dark = false).background)
        assertEquals(0xFF2563EB.toInt(), WidgetPalette.colors("Ocean", dark = false).accent)
        assertEquals(0xFF1E293B.toInt(), WidgetPalette.colors("Ocean", dark = true).background)
        assertEquals(0xFFCFEDEA.toInt(), WidgetPalette.colors("Teal", dark = false).background)
        assertEquals(0xFF0F766E.toInt(), WidgetPalette.colors("Teal", dark = false).accent)
        assertEquals(0xFF0D2426.toInt(), WidgetPalette.colors("Teal", dark = true).background)
    }
    @Test fun unknownPaletteFallsBackToOcean() = assertEquals(WidgetPalette.colors("Ocean", false), WidgetPalette.colors("Sunset", false))
    @Test fun appearanceChoosesLightDarkOrBothForThePhoneToPick() {
        assertEquals(listOf(false, true), WidgetPalette.modes("System"))
        assertEquals(listOf(false, false), WidgetPalette.modes("Light"))
        assertEquals(listOf(true, true), WidgetPalette.modes("Dark"))
        assertEquals(listOf(false, true), WidgetPalette.modes(null))
    }
    @Test fun textStaysReadableOnTheBackground() {
        for (palette in listOf("Ocean", "Teal")) for (dark in listOf(false, true)) {
            val c = WidgetPalette.colors(palette, dark)
            assertTrue("$palette dark=$dark title", contrast(c.title, c.background) >= 7.0)
            assertTrue("$palette dark=$dark subtitle", contrast(c.subtitle, c.background) >= 4.5)
            assertTrue("$palette dark=$dark play icon", contrast(c.onAccent, c.accent) >= 3.0)
        }
    }
    private fun luminance(color: Int): Double {
        fun channel(v: Int) = (v / 255.0).let { if (it <= 0.03928) it / 12.92 else Math.pow((it + 0.055) / 1.055, 2.4) }
        return 0.2126 * channel(color shr 16 and 0xFF) + 0.7152 * channel(color shr 8 and 0xFF) + 0.0722 * channel(color and 0xFF)
    }
    private fun contrast(a: Int, b: Int): Double { val x = luminance(a); val y = luminance(b); return (maxOf(x, y) + .05) / (minOf(x, y) + .05) }
}
