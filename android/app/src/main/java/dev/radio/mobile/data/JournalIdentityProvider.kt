package dev.radio.mobile.data

import android.content.Context
import android.util.AtomicFile
import dev.radio.mobile.connection.*
import dev.radio.mobile.portable.MatchingRepository
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.util.UUID

/** Process-owned immutable, conservative mapping cache. capture never touches storage or audio. */
class JournalIdentityProvider(context: Context, db: LibraryDatabase, scope: CoroutineScope) {
    init {
        val app = context.applicationContext
        // A bounded startup read establishes the installation origin before any player callback.
        // This file is deliberately excluded from backup; restored journals retain their old IDs.
        synchronized(lock) {
            val file = AtomicFile(File(app.noBackupFilesDir, "radio_journal_origin"))
            localOrigin = if (file.baseFile.exists()) {
                require(file.baseFile.length() in 1..128) { "Invalid journal installation origin" }
                file.readFully().toString(Charsets.UTF_8).also {
                    require(it.matches(Regex("local:[a-f0-9-]{36}"))) { "Invalid journal installation origin" }
                }
            } else ("local:" + UUID.randomUUID().toString()).also { value ->
                val stream = file.startWrite()
                try { stream.write(value.toByteArray()); file.finishWrite(stream) }
                catch (failure: Exception) { file.failWrite(stream); throw failure }
            }
        }
        val ownerGeneration = pairingGeneration()
        scope.launch(Dispatchers.IO) {
            runCatching {
                val policy = EndpointPolicy(dev.radio.mobile.BuildConfig.DEBUG)
                publishPairing(PairedServerStore(app, policy).load(), ownerGeneration)
                MatchingRepository(app, db).load()
            }
        }
    }
    fun capture(song: LocalSong): JournalIdentity = captureCurrent(song)
    data class Entry(val song: LocalSong, val serverId: String, val catalogId: String, val revision: Long,
        val songId: String, val provenance: String)
    data class MappingSnapshot(val generation: Long = 0, val entries: Map<String, Entry> = emptyMap(), val loaded:Boolean=false)
    private data class Owner(val serverId: String, val deviceId: String, val anchor: String, val marker: String)
    companion object {
      internal fun captureCurrent(song: LocalSong): JournalIdentity = synchronized(lock) {
        val owner = ownership
        val mapping = mutable.value.entries[song.id]?.takeIf { it.song == song && song.available && it.serverId == owner?.serverId }
        if (owner == null) JournalIdentity(localOrigin, localId = song.id)
        else JournalIdentity(owner.deviceId, owner.serverId, mapping?.catalogId, mapping?.songId, song.id,
            listOf("owner",owner.anchor,owner.marker,mapping?.revision?.toString() ?: "none",mapping?.provenance ?: "local_only").joinToString(":"))
    }
        internal fun currentProfileIdentity():JournalIdentity? = synchronized(lock) {
            val owner=ownership ?: return@synchronized null
            JournalIdentity(owner.deviceId,owner.serverId,localId="profile:${owner.serverId}",provenance="paired_profile:${owner.anchor}:${owner.marker}")
        }
        private val lock = Any()
        private var localOrigin = "local:" + UUID.randomUUID().toString()
        private var ownership: Owner? = null
        private var ownerGeneration = 0L
        internal fun pairingGeneration(): Long = synchronized(lock) { ownerGeneration }
        private val mutable = MutableStateFlow(MappingSnapshot())
        val mappings = mutable.asStateFlow()
        fun invalidateMappings(): Long = synchronized(lock) {
            val generation = mutable.value.generation + 1
            mutable.value = MappingSnapshot(generation)
            generation
        }
        fun publishMappings(generation: Long, entries: List<Entry>) = synchronized(lock) {
            if (mutable.value.generation == generation) mutable.value = MappingSnapshot(generation, entries.associateBy { it.song.id }, loaded=true)
        }
        fun publishPairing(snapshot: StoredPairing, expectedGeneration: Long? = null) = synchronized(lock) {
            if (expectedGeneration != null && expectedGeneration != ownerGeneration) return@synchronized
            ownerGeneration++
            val next = snapshot.record?.takeUnless { snapshot.quarantined }?.let { record ->
                record.deviceId?.let { Owner(record.bundle.serverId, it, record.identityAnchor, record.installationMarker) }
            }
            if (ownership != next) { ownership = next; invalidateMappings() }
        }
    }
}
