package dev.radio.mobile.portable

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Fences already-computed advice before a shared-profile transaction can become visible. */
internal object PortableSyncChanges {
    private val lock = Any()
    private var epoch = 0L
    private var writers = 0
    private val mutable = MutableStateFlow(0L)
    val changes = mutable.asStateFlow()
    val revision: Long get() = synchronized(lock) { epoch }
    val isApplying: Boolean get() = synchronized(lock) { writers > 0 }

    /** Timeline acceptance either finishes before a profile write begins, or is rejected. */
    fun <T> accept(expectedRevision: Long, block: () -> T): T? = synchronized(lock) {
        if (writers > 0 || epoch != expectedRevision) null else block()
    }

    suspend fun <T> applying(block: suspend () -> T): T {
        synchronized(lock) { writers++; mutable.value = ++epoch }
        try { return block() }
        finally {
            synchronized(lock) { writers--; mutable.value = ++epoch }
        }
    }
}
