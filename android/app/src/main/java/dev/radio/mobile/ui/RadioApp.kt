package dev.radio.mobile.ui

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.foundation.lazy.LazyListState
import dev.radio.mobile.data.*
import dev.radio.mobile.music.*
import dev.radio.mobile.playback.*
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.ui.theme.*
import kotlinx.coroutines.launch

private val destinations = listOf("Radio" to Symbol.Radio, "Library" to Symbol.Library, "Settings" to Symbol.Settings)

@Composable
fun RadioApp(repository: LibraryRepository = LibraryRepository.get(LocalContext.current), playback: PlaybackController = rememberPlaybackController(), openPlayerRequest: Int = 0) {
    val audio by playback.state.collectAsState()
    val library by repository.state.collectAsState()
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri -> if (uri != null) repository.chooseFolder(uri) }
    fun chooseFolder() { picker.launch(library.root?.uri?.let(Uri::parse) ?: DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Music")) }
    val lifecycle = LocalLifecycleOwner.current
    DisposableEffect(repository, lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) repository.checkAccess()
        }
        lifecycle.lifecycle.addObserver(observer)
        repository.checkAccess()
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    // Only shell preferences are stored here. Library and playback persistence arrive in later phases.
    val prefs = LocalContext.current.getSharedPreferences("radio_shell", Context.MODE_PRIVATE)
    var palette by rememberSaveable { mutableStateOf(prefs.getString("palette", "Ocean") ?: "Ocean") }
    var appearance by rememberSaveable { mutableStateOf(prefs.getString("appearance", "System") ?: "System") }
    var opening by rememberSaveable { mutableStateOf(prefs.getString("opening", "Last screen") ?: "Last screen") }
    var minimal by rememberSaveable { mutableStateOf(prefs.getBoolean("minimal", false)) }
    var backdrop by rememberSaveable { mutableStateOf(prefs.getString("backdrop", "Static") ?: "Static") }
    var page by rememberSaveable { mutableStateOf(when (opening) {
        "Last screen" -> prefs.getString("last_screen", "Library") ?: "Library"
        else -> opening
    }) }
    var searchReturnPage by rememberSaveable { mutableStateOf("Library") }
    var playerReturnPage by rememberSaveable { mutableStateOf("Library") }
    var category by rememberSaveable { mutableStateOf("Folders") }
    var folder by rememberSaveable { mutableStateOf<String?>(null) }
    var sortName by rememberSaveable { mutableStateOf(prefs.getString("sort", LibrarySort.ModifiedNewest.name) ?: LibrarySort.ModifiedNewest.name) }
    val sort = LibrarySort.entries.firstOrNull { it.name == sortName || it.label == sortName } ?: LibrarySort.ModifiedNewest
    var query by rememberSaveable { mutableStateOf("") }
    var scrubMs by remember(audio.currentId) { mutableStateOf<Long?>(null) }
    val position = (scrubMs ?: audio.positionMs) / 1000f
    var liked by rememberSaveable { mutableStateOf(false) }
    var locked by rememberSaveable { mutableStateOf(false) }
    var showQueue by rememberSaveable { mutableStateOf(false) }
    var showSkip by rememberSaveable { mutableStateOf(false) }
    val songs = remember(library.songs, sort) { orderSongs(library.songs, sort) }
    val song = audio.current
    val listStates = remember { mutableMapOf<String, LazyListState>() }
    val listState = listStates.getOrPut("${library.root?.uri}/$folder/$category") { LazyListState() }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    fun message(text: String) { scope.launch { snackbar.showSnackbar(text) } }
    fun playSong(item: LocalSong, context: List<LocalSong>, source: String) {
        if (library.accessLost || !item.available) { message("This file is unavailable. Check folder access and rescan."); return }
        playback.playFrom(context, item.id, source)
    }
    fun navigate(target: String) {
        if (target == "Search") searchReturnPage = page
        if (target == "Now playing") playerReturnPage = page
        page = target
        if (target != "Search") prefs.edit().putString("last_screen", target).apply()
    }
    fun back() {
        if (page == "Now playing" && showQueue) { showQueue = false; return }
        if (page == "Search") { focus.clearFocus(); keyboard?.hide() }
        when (page) {
            "Search" -> page = searchReturnPage
            "Now playing" -> page = playerReturnPage
            else -> folder = library.folders.firstOrNull { it.id == folder }?.parentId?.takeUnless { it == library.root?.folderId }
        }
    }
    LaunchedEffect(openPlayerRequest) { if (openPlayerRequest > 0) navigate("Now playing") }
    LaunchedEffect(library.root?.uri) { folder = null }
    LaunchedEffect(library.songs, library.accessLost, library.loading, library.scanning) {
        if (!library.loading && !library.scanning) playback.reconcileLibrary(library.songs, library.accessLost)
    }
    LaunchedEffect(audio.currentId) { liked = false; locked = false }
    val showBack = page == "Search" || page == "Now playing" || (page == "Library" && folder != null)
    BackHandler(showBack) { back() }
    RadioTheme(
        palette = RadioPalette.entries.firstOrNull { it.name == palette } ?: RadioPalette.Ocean,
        appearance = Appearance.entries.firstOrNull { it.name == appearance } ?: Appearance.System
    ) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                Row(Modifier.fillMaxWidth().statusBarsPadding().heightIn(min = 64.dp).padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (showBack) IconButton(onClick = { back() }) { RadioIcon(Symbol.Back, "Back") }
                    Column(Modifier.weight(1f).padding(start = if (!showBack) 12.dp else 0.dp)) {
                        Text(if (page == "Library" && folder != null) library.folders.firstOrNull { it.id == folder }?.name ?: "Folder" else if (page == "Now playing" && showQueue) "Queue" else page, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(if (page == "Now playing" && showQueue) audio.source else if (library.scanning) "Scanning…" else if (library.root == null) "Connect your music" else "${library.songs.count { it.available }} MP3s", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (page == "Library") {
                        if (library.root != null) IconButton(onClick = { repository.rescan() }, enabled = !library.scanning && !library.loading) { RadioIcon(Symbol.Refresh, "Rescan library") }
                        IconButton(onClick = { query = ""; navigate("Search") }, enabled = library.root != null) { RadioIcon(Symbol.Search, "Search library") }
                    }
                    if (page == "Now playing" && !showQueue) IconButton(onClick = { minimal = !minimal; prefs.edit().putBoolean("minimal", minimal).apply() }) {
                        RadioIcon(if (minimal) Symbol.Expand else Symbol.Minimize, if (minimal) "Full controls" else "Minimal mode")
                    }
                }
            },
            bottomBar = {
                if (page != "Search" && page != "Now playing") Column {
                    if (song != null) Surface(color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(start = 16.dp, end = 4.dp).heightIn(min = 64.dp), verticalAlignment = Alignment.CenterVertically) {
                            Artwork(40)
                            Column(Modifier.weight(1f).clickable(role = Role.Button, onClickLabel = "Open player") { navigate("Now playing") }.padding(horizontal = 12.dp, vertical = 8.dp)) {
                                Text(song.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                                Text("${audio.status} · ${song.artist}", maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            IconButton(onClick = { playback.toggle() }, enabled = !library.accessLost && song.available) { RadioIcon(if (audio.playWhenReady) Symbol.Pause else Symbol.Play, if (audio.playWhenReady) "Pause" else "Play") }
                            IconButton(onClick = { playback.next() }, enabled = audio.canNext, modifier = Modifier.testTag("mini-next")) { RadioIcon(Symbol.Next, "Next song") }
                        }
                    }
                    NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                        destinations.forEach { (label, symbol) -> NavigationBarItem(selected = page == label, onClick = { navigate(label) }, modifier = Modifier.testTag("nav-$label"), icon = { RadioIcon(symbol) }, label = { Text(label) }) }
                    }
                }
            },
            snackbarHost = { SnackbarHost(snackbar) }
        ) { insets ->
            when (page) {
                "Library" -> LibraryContent(library, folder, category, onCategory = { category = it }, sort, onSort = { sortName = it.name; prefs.edit().putString("sort", it.name).apply() }, listState,
                    onFolder = { folder = it }, onSong = { item, context, source -> playSong(item, context, source); navigate("Now playing") },
                    choose = { chooseFolder() }, rescan = { repository.rescan() }, cancel = { repository.cancelScan() }, modifier = Modifier.padding(insets))
                "Search" -> Column(Modifier.padding(insets).imePadding()) {
                    OutlinedTextField(query, onValueChange = { query = it }, placeholder = { Text("Search songs and filenames") }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("search-input"))
                    val results = remember(songs, query) { songs.filter { query.isNotBlank() && (it.title + " " + it.fileName + " " + it.folderPath).contains(query, ignoreCase = true) } }
                    LazyColumn { items(results, key = { it.id }) { item ->
                        LibraryRow(item.title, songSubtitle(item), Symbol.Music) { playSong(item, results, "Search results"); focus.clearFocus(); keyboard?.hide(); navigate("Now playing") }
                    } }
                    if (query.isNotBlank() && results.isEmpty()) Text("No matching songs", Modifier.padding(20.dp))
                }
                "Now playing" -> if (song == null) Column(Modifier.padding(insets).padding(20.dp)) {
                    audio.error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(bottom = 12.dp)) }
                    Text("Choose a song from your library.")
                    TextButton(onClick = { navigate("Library") }) { Text("Browse music") }
                } else BoxWithConstraints(Modifier.padding(insets).consumeWindowInsets(insets).fillMaxSize()) {
                    val compact = maxHeight < 520.dp
                    PlayerBackground(PlayerBackdrop.entries.firstOrNull { it.name == backdrop } ?: PlayerBackdrop.Static, audio.isPlaying, Modifier.matchParentSize())
                    Column(Modifier.fillMaxSize().navigationBarsPadding()) {
                        if (showQueue) {
                            IntegratedQueue(audio, !library.accessLost, onSelect = { playback.jumpTo(it) }, modifier = Modifier.fillMaxWidth().weight(1f))
                        } else {
                            Column(Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                Spacer(Modifier.height(20.dp))
                                Artwork(if (minimal || compact) 160 else 220)
                                Spacer(Modifier.height(24.dp))
                                Text(song.title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.fillMaxWidth())
                                Text(songSubtitle(song), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().padding(top = 4.dp))
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    FilterChip(liked, onClick = { liked = !liked }, label = { Text("Like") }, leadingIcon = { RadioIcon(Symbol.Heart) })
                                    FilterChip(locked, onClick = { locked = !locked }, label = { Text("Vibe lock") }, leadingIcon = { RadioIcon(Symbol.Lock) })
                                }
                                if (!minimal) {
                                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                                        TextButton(onClick = { message("Dislikes will affect recommendations in the radio phase.") }) { Text("Dislike") }
                                        TextButton(onClick = { showSkip = true }) { Text("Skip reason") }
                                        TextButton(onClick = { message("Sleep timer arrives with reliable background playback.") }) { Text("Sleep") }
                                    }
                                    HorizontalDivider(Modifier.padding(top = 8.dp))
                                    Text(audio.status + " · Local file\n" + song.folderPath.ifEmpty { library.root?.name ?: "Music" }, Modifier.fillMaxWidth().padding(vertical = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                        if (audio.error != null) Text(audio.error!!, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp).testTag("playback-error"))
                        PlayerTransport(audio, playback, position, onScrub = { scrubMs = (it * 1000).toLong() },
                            onScrubFinished = { scrubMs?.let { playback.seek(it) }; scrubMs = null },
                            canRead = !library.accessLost && song.available, queueVisible = showQueue,
                            onQueueToggle = { showQueue = !showQueue }, modifier = Modifier.padding(horizontal = 24.dp), compact = compact)
                    }
                }
                "Settings" -> Column(Modifier.padding(insets).fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)) {
                    SectionLabel("Music library")
                    Text(library.root?.name ?: "No folder selected", style = MaterialTheme.typography.titleSmall)
                    Text("${library.songs.count { it.available }} MP3s · ${library.songs.count { !it.available }} missing", style = MaterialTheme.typography.bodySmall)
                    LibraryStatus(library, { chooseFolder() }, { repository.rescan() }, { repository.cancelScan() })
                    Row { TextButton(onClick = { chooseFolder() }, enabled = !library.scanning && !library.loading) { Text("Choose folder") }; TextButton(onClick = { repository.rescan() }, enabled = library.root != null && !library.scanning && !library.loading && !library.accessLost) { Text("Rescan") } }
                    SectionLabel("Color scheme")
                    RadioPalette.entries.forEach { option ->
                        Row(Modifier.fillMaxWidth().selectable(selected = palette == option.name, role = Role.RadioButton, onClick = { palette = option.name; prefs.edit().putString("palette", palette).apply() }).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(palette == option.name, onClick = null)
                            Text(option.label, Modifier.padding(start = 12.dp))
                        }
                    }
                    SectionLabel("Appearance")
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { Appearance.entries.forEach { option ->
                        FilterChip(appearance == option.name, onClick = { appearance = option.name; prefs.edit().putString("appearance", appearance).apply() }, label = { Text(option.label) })
                    } }
                    SectionLabel("Player background")
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { PlayerBackdrop.entries.forEach { option ->
                        FilterChip(backdrop == option.name, onClick = { backdrop = option.name; prefs.edit().putString("backdrop", backdrop).apply() }, label = { Text(option.label) })
                    } }
                    Text("Animated moves while music plays. Motion stops when paused or hidden, and follows the phone’s animation setting.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    SectionLabel("Opening screen")
                    listOf("Last screen", "Library", "Radio", "Now playing").forEach { option ->
                        Row(Modifier.fillMaxWidth().selectable(selected = opening == option, role = Role.RadioButton, onClick = { opening = option; prefs.edit().putString("opening", opening).apply() }).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(opening == option, onClick = null); Text(option, Modifier.padding(start = 12.dp))
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("Minimal player"); Switch(minimal, onCheckedChange = { minimal = it; prefs.edit().putBoolean("minimal", minimal).apply() })
                    }
                    HorizontalDivider()
                    Text("Foreground playback", Modifier.padding(top = 16.dp), style = MaterialTheme.typography.titleSmall)
                    Text("Music pauses when you leave the app or lock the screen for this checkpoint. Background playback, lock-screen controls and restoring playback after reopening come next.", Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                else -> Column(Modifier.padding(insets).padding(20.dp)) {
                    Text("Your local radio", style = MaterialTheme.typography.headlineSmall)
                    Text("Radio recommendations arrive after local playback is ready.", Modifier.padding(vertical = 16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick = { navigate("Library") }) { Text("Browse music") }
                }
            }
            if (showSkip) AlertDialog(
                onDismissRequest = { showSkip = false }, title = { Text("Skip reason") },
                text = { Column {
                    Text("This skips the song. Saving feedback arrives with radio recommendations.", style = MaterialTheme.typography.bodySmall)
                    listOf("Too intense", "Too slow", "Too similar", "Wrong mood", "Just skip").forEach { reason ->
                        TextButton(onClick = { showSkip = false; playback.next() }, enabled = audio.canNext) { Text(reason) }
                    }
                } }, confirmButton = { TextButton(onClick = { showSkip = false }) { Text("Close") } }
            )

        }
    }
}

@Composable
private fun Artwork(size: Int) {
    Box(Modifier.size(size.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(if (size > 100) 12.dp else 6.dp)), contentAlignment = Alignment.Center) {
        RadioIcon(Symbol.Music, modifier = Modifier.size((size / 3).dp))
    }
}

@Composable
internal fun LibraryRow(title: String, subtitle: String, symbol: Symbol, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).heightIn(min = 56.dp).padding(horizontal = 20.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(36.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp)), contentAlignment = Alignment.Center) { RadioIcon(symbol, modifier = Modifier.size(20.dp)) }
        Column(Modifier.weight(1f).padding(start = 14.dp)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            Text(subtitle, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SectionLabel(label: String) { Text(label, Modifier.padding(top = 20.dp, bottom = 8.dp), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary) }
private fun time(seconds: Int) = "%d:%02d".format(seconds / 60, seconds % 60)
