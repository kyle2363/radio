package dev.radio.mobile

import dev.radio.mobile.data.*
import dev.radio.mobile.connection.*
import org.junit.Assert.*
import org.junit.After
import org.junit.Test

class JournalIdentityProviderTest {
    private val song = LocalSong("local", "root", "folder", "content://song", "song.mp3", "", 10, 1, 100, 2000)
    private fun owner(device: String = "device", install: String = "install") = StoredPairing(PairingRecord(
        PairingBundle("server", "pin", "endpoint", "ticket", "secret", "phone", 100), "bearer", device, install, device, 100))
    private fun map(): Long = JournalIdentityProvider.invalidateMappings().also { generation ->
        JournalIdentityProvider.publishMappings(generation, listOf(JournalIdentityProvider.Entry(song, "server", "catalog", 1, "remote", "confirmed:manifest:hash")))
    }
    @After fun clean() { JournalIdentityProvider.publishPairing(StoredPairing()); JournalIdentityProvider.invalidateMappings() }
    @Test fun capturedAttemptDoesNotAcquireLaterMappingOrOwner() {
        JournalIdentityProvider.publishPairing(owner())
        val before = JournalIdentityProvider.captureCurrent(song)
        map()
        val mapped = JournalIdentityProvider.captureCurrent(song)
        assertNull(before.songId)
        assertEquals("remote", mapped.songId)
        JournalIdentityProvider.publishPairing(owner("replacement"))
        assertEquals("device", mapped.deviceId)
        assertEquals("remote", mapped.songId)
        assertNull(JournalIdentityProvider.captureCurrent(song).songId)
    }
    @Test fun staleFingerprintAndLatePublicationCannotAcquireAuthority() {
        JournalIdentityProvider.publishPairing(owner())
        val generation = map()
        assertNull(JournalIdentityProvider.captureCurrent(song.copy(modifiedAt = 11)).songId)
        JournalIdentityProvider.invalidateMappings()
        JournalIdentityProvider.publishMappings(generation, listOf(JournalIdentityProvider.Entry(song,"server","catalog",1,"remote","confirmed")))
        assertNull(JournalIdentityProvider.captureCurrent(song).songId)
    }
    @Test fun quarantineKeepsLocalFeedbackIndependentFromRepairedPairing() {
        JournalIdentityProvider.publishPairing(owner())
        map()
        JournalIdentityProvider.publishPairing(StoredPairing(quarantined = true))
        val quarantined = JournalIdentityProvider.captureCurrent(song)
        val localOrigin = quarantined.deviceId
        assertNull(quarantined.serverId)
        assertNull(quarantined.songId)
        JournalIdentityProvider.publishPairing(owner("new", "new-install"))
        assertEquals(localOrigin, quarantined.deviceId)
        assertEquals("new", JournalIdentityProvider.captureCurrent(song).deviceId)
        assertNull(JournalIdentityProvider.captureCurrent(song).songId)
    }
    @Test fun delayedStartupReadCannotRestoreForgottenPairing() {
        JournalIdentityProvider.publishPairing(owner())
        val generation = JournalIdentityProvider.pairingGeneration()
        JournalIdentityProvider.publishPairing(StoredPairing())
        JournalIdentityProvider.publishPairing(owner(), generation)
        assertNull(JournalIdentityProvider.captureCurrent(song).serverId)
    }
}
