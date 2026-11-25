package dev.radio.mobile

import dev.radio.mobile.ui.HeatmapLayout
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

class HeatmapLayoutTest {
    @Test fun tapsMapToTheDayUnderTheFinger() {
        // Starts on a Wednesday (offset 2): column 0 rows 0–1 are blank.
        assertNull(HeatmapLayout.dayAt(5f, 5f, cell = 10f, offset = 2, days = 30))
        assertEquals(0, HeatmapLayout.dayAt(5f, 25f, cell = 10f, offset = 2, days = 30))
        assertEquals(5, HeatmapLayout.dayAt(15f, 5f, cell = 10f, offset = 2, days = 30))
        assertNull("below the grid", HeatmapLayout.dayAt(5f, 75f, cell = 10f, offset = 2, days = 30))
        assertNull("past the last day", HeatmapLayout.dayAt(45f, 65f, cell = 10f, offset = 2, days = 30))
        assertNull(HeatmapLayout.dayAt(-1f, 5f, cell = 10f, offset = 2, days = 30))
    }
    @Test fun monthLabelsSitOnTheColumnWhereEachMonthStarts() {
        val start = LocalDate.of(2026, 1, 28) // Wednesday
        val dates = (0L until 40L).map(start::plusDays)
        val labels = HeatmapLayout.monthStarts(dates, offset = 2)
        // Feb 1 (a Sunday) is in column 0, so the partial January needs no label; Mar 1 falls in column 4.
        assertEquals(listOf(0 to 2, 4 to 3), labels.map { it.first to it.second.monthValue })
        val later = HeatmapLayout.monthStarts((0L until 40L).map(LocalDate.of(2026, 1, 5)::plusDays), offset = 0)
        assertEquals("a long partial first month keeps its label", listOf(0 to 1, 3 to 2), later.map { it.first to it.second.monthValue })
    }
    @Test fun cellsAreLargeEnoughToTap() = assertTrue(HeatmapLayout.CELL_DP >= 16f)
}
