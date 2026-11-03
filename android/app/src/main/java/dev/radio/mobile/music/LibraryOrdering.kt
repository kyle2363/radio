package dev.radio.mobile.music

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.data.LocalFolder

enum class LibrarySort(val label: String) {
    ModifiedNewest("Date modified · newest"), ModifiedOldest("Date modified · oldest"),
    AddedNewest("Added to app · newest"), Title("Title · A–Z"), Filename("Filename · A–Z"), Duration("Duration · longest")
}

fun orderSongs(songs: List<LocalSong>, sort: LibrarySort): List<LocalSong> {
    val name = compareBy<LocalSong> { it.fileName.lowercase(java.util.Locale.ROOT) }.thenBy { it.id }
    val comparator = when (sort) {
        LibrarySort.ModifiedNewest -> compareBy<LocalSong> { it.modifiedAt == null }.thenByDescending { it.modifiedAt }.then(name)
        LibrarySort.ModifiedOldest -> compareBy<LocalSong> { it.modifiedAt == null }.thenBy { it.modifiedAt }.then(name)
        LibrarySort.AddedNewest -> compareByDescending<LocalSong> { it.firstSeenAt }.then(name)
        LibrarySort.Duration -> compareBy<LocalSong> { it.durationMs == null }.thenByDescending { it.durationMs }.then(name)
        else -> name
    }
    return songs.sortedWith(comparator)
}

fun orderFolders(folders: List<LocalFolder>, songs: List<LocalSong>, parentId: String?, sort: LibrarySort): List<LocalFolder> {
    val children = folders.filter { it.parentId == parentId }
    val name = compareBy<LocalFolder> { it.name.lowercase(java.util.Locale.ROOT) }.thenBy { it.id }
    val comparator = when (sort) {
        LibrarySort.ModifiedNewest -> compareBy<LocalFolder> { it.modifiedAt == null }.thenByDescending { it.modifiedAt }.then(name)
        LibrarySort.ModifiedOldest -> compareBy<LocalFolder> { it.modifiedAt == null }.thenBy { it.modifiedAt }.then(name)
        LibrarySort.AddedNewest, LibrarySort.Duration -> {
            val descendants = folders.groupBy { it.parentId }
            val values = children.associate { child ->
                val ids = mutableSetOf<String>()
                val pending = ArrayDeque<String>().apply { add(child.id) }
                while (pending.isNotEmpty()) {
                    val id = pending.removeFirst()
                    if (ids.add(id)) descendants[id].orEmpty().forEach { pending.add(it.id) }
                }
                val tracks = songs.filter { it.available && it.folderId in ids }
                child.id to if (sort == LibrarySort.AddedNewest) tracks.maxOfOrNull { it.firstSeenAt }
                    else tracks.mapNotNull { it.durationMs }.takeIf { it.isNotEmpty() }?.sum()
            }
            compareBy<LocalFolder> { values[it.id] == null }.thenByDescending { values[it.id] }.then(name)
        }
        else -> name
    }
    return children.sortedWith(comparator)
}
