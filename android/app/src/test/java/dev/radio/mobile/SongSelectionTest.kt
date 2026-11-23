package dev.radio.mobile

import dev.radio.mobile.ui.SongSelection
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SongSelectionTest {
    private val order = listOf("a", "b", "c", "d", "e", "f")

    @Test fun startsInactiveAndLongPressSelectsTheRow() {
        val empty = SongSelection()
        assertFalse(empty.active)
        val started = empty.begin("c")
        assertTrue(started.active)
        assertEquals(setOf("c"), started.ids)
    }

    @Test fun tapsToggleAndDeselectingTheLastSongEndsSelection() {
        val selection = SongSelection().begin("a").toggle("b")
        assertEquals(setOf("a", "b"), selection.ids)
        val fewer = selection.toggle("a").toggle("b")
        assertFalse(fewer.active)
    }

    @Test fun dragSweepsARangeFromTheLongPressedRowInEitherDirection() {
        val down = SongSelection().begin("b").sweep(order, "e")
        assertEquals(setOf("b", "c", "d", "e"), down.ids)
        // Moving back shrinks the sweep to the new range instead of keeping rows the finger left.
        val back = down.sweep(order, "c")
        assertEquals(setOf("b", "c"), back.ids)
        val up = SongSelection().begin("d").sweep(order, "a")
        assertEquals(setOf("a", "b", "c", "d"), up.ids)
    }

    @Test fun aSecondSweepKeepsWhatWasAlreadySelected() {
        val first = SongSelection().begin("a").toggle("f")
        val swept = first.begin("c").sweep(order, "d")
        assertEquals(setOf("a", "c", "d", "f"), swept.ids)
    }

    @Test fun sweepWithUnknownRowsChangesNothing() {
        val selection = SongSelection().begin("b")
        assertEquals(selection, selection.sweep(order, "zzz"))
        assertEquals(SongSelection(), SongSelection().sweep(order, "c"))
    }

    @Test fun selectAllAndOrderedFollowTheListNotTheTapOrder() {
        val tapped = SongSelection().begin("e").toggle("a").toggle("c")
        assertEquals(listOf("a", "c", "e"), tapped.ordered(order))
        assertEquals(order.toSet(), tapped.selectAll(order).ids)
        assertTrue(tapped.selectAll(order).allOf(order))
        assertFalse(tapped.allOf(order))
    }

    @Test fun retainDropsSongsThatLeftTheList() {
        val selection = SongSelection().begin("a").toggle("b").retain(setOf("b", "c"))
        assertEquals(setOf("b"), selection.ids)
        assertFalse(SongSelection().begin("a").retain(emptySet()).active)
    }
}
