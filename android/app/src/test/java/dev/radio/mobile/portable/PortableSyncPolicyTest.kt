package dev.radio.mobile.portable

import org.junit.Assert.*
import org.junit.Test

class PortableSyncPolicyTest {
    @Test fun mutableRegisterUsesClockThenDeviceThenEventId() {
        val candidates = listOf(
            PortableRegisterCandidate(4, "z-device", "a-event", "false"),
            PortableRegisterCandidate(4, "a-device", "z-event", "true"),
            PortableRegisterCandidate(4, "a-device", "b-event", "false"),
            PortableRegisterCandidate(3, "zz-device", "zz-event", "old"),
        )
        assertEquals("false", PortableRegisterOrder.winner(candidates)!!.value)
        assertEquals("z", PortableRegisterOrder.winner(listOf(
            PortableRegisterCandidate(4, "same-device", "a", "a"),
            PortableRegisterCandidate(4, "same-device", "z", "z"),
        ))!!.value)
        assertEquals("emoji", PortableRegisterOrder.winner(listOf(
            PortableRegisterCandidate(4, "same-device", "\uE000", "bmp"),
            PortableRegisterCandidate(4, "same-device", "\uD83D\uDE00", "emoji"),
        ))!!.value)
    }

    @Test fun batchingNeverChangesEventBytesAndCapsBatchesAtFiveHundred() {
        val payloads = (0 until 1001).map { "{\"event_id\":\"e$it\",\"logical_clock\":$it}" }
        val batches = PortableSyncBatcher.batches(payloads)
        assertEquals(listOf(500, 500, 1), batches.map { it.size })
        assertEquals(payloads, batches.flatten())
        assertEquals(payloads[0], batches.first().first())
    }

    @Test fun localOnlyIdentityIsQualifiedByOriginDevice() {
        assertEquals("local:[\"device-a\",\"same-song\"]", PortableProfileKey.forLocalOnly("device-a", "same-song"))
        assertNotEquals(PortableProfileKey.forLocalOnly("device-a", "same-song"), PortableProfileKey.forLocalOnly("device-b", "same-song"))
    }
}
