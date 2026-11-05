package dev.radio.mobile.portable

/** The protocol's lexicographic Lamport register order. */
data class PortableRegisterCandidate(val logicalClock: Long, val deviceId: String, val eventId: String, val value: String)

object PortableRegisterOrder {
    fun winner(candidates: Iterable<PortableRegisterCandidate>): PortableRegisterCandidate? =
        candidates.maxWithOrNull(Comparator { a,b ->
            val clock=a.logicalClock.compareTo(b.logicalClock)
            if(clock!=0) clock else {
                val device=compareScalars(a.deviceId,b.deviceId)
                if(device!=0)device else compareScalars(a.eventId,b.eventId)
            }
        })
    private fun compareScalars(a:String,b:String):Int {
        val left=a.codePoints().toArray();val right=b.codePoints().toArray()
        for(index in 0 until minOf(left.size,right.size)) if(left[index]!=right[index]) return left[index].compareTo(right[index])
        return left.size.compareTo(right.size)
    }
}

object PortableSyncBatcher {
    fun <T> batches(events: List<T>, maxBytes:Int = 2 * 1024 * 1024, sizeOf:(T)->Int = { 1 }): List<List<T>> {
        require(events.size <= 1_000_000) { "Too many queued events" }
        require(maxBytes in 1024..(2 * 1024 * 1024))
        val result=mutableListOf<List<T>>();var batch=mutableListOf<T>();var bytes=64
        for(event in events){
            val size=sizeOf(event)
            require(size in 1..4096) { "Invalid event size" }
            if(batch.size==500 || bytes+size+1>maxBytes){if(batch.isNotEmpty())result+=batch;batch=mutableListOf();bytes=64}
            batch+=event;bytes+=size+1
        }
        if(batch.isNotEmpty())result+=batch
        return result
    }
}

object PortableProfileKey {
    fun forLocalOnly(deviceId: String, localId: String): String {
        require(deviceId.isNotBlank() && localId.isNotBlank())
        return "local:[${jsonString(deviceId)},${jsonString(localId)}]"
    }
    private fun jsonString(value:String):String=buildString {
        append('"')
        for(char in value) when(char){
            '"'->append("\\\"");'\\'->append("\\\\");'\b'->append("\\b");'\u000C'->append("\\f");'\n'->append("\\n");'\r'->append("\\r");'\t'->append("\\t")
            else->if(char<' ' || char.code>0x7e)append("\\u%04x".format(char.code)) else append(char)
        }
        append('"')
    }
}
