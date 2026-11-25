package dev.radio.mobile.music

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.data.LocalFolder

enum class LibrarySort(val label: String, val short: String) {
    ModifiedNewest("Date modified · newest", "Newest"), ModifiedOldest("Date modified · oldest", "Oldest"),
    AddedNewest("Added to app · newest", "Added"), Title("Title · A–Z", "Title"), Filename("Filename · A–Z", "File"), Duration("Duration · longest", "Length")
}

fun orderSongs(songs: List<LocalSong>, sort: LibrarySort): List<LocalSong> {
    val name = compareBy(NaturalOrder) { it: LocalSong -> it.fileName }.thenBy { it.id }
    val title = compareBy(NaturalOrder) { it: LocalSong -> it.title }.then(name)
    val comparator = when (sort) {
        LibrarySort.ModifiedNewest -> compareBy<LocalSong> { it.modifiedAt == null }.thenByDescending { it.modifiedAt }.then(name)
        LibrarySort.ModifiedOldest -> compareBy<LocalSong> { it.modifiedAt == null }.thenBy { it.modifiedAt }.then(name)
        LibrarySort.AddedNewest -> compareByDescending<LocalSong> { it.firstSeenAt }.then(name)
        LibrarySort.Duration -> compareBy<LocalSong> { it.durationMs == null }.thenByDescending { it.durationMs }.then(name)
        LibrarySort.Title -> title
        LibrarySort.Filename -> name
    }
    return songs.sortedWith(comparator)
}

fun orderFolders(folders: List<LocalFolder>, songs: List<LocalSong>, parentId: String?, sort: LibrarySort): List<LocalFolder> {
    val children = folders.filter { it.parentId == parentId }
    val name = compareBy(NaturalOrder) { it: LocalFolder -> it.name }.thenBy { it.id }
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
