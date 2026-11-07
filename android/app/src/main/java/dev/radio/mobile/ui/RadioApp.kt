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
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.ui.theme.*
import kotlinx.coroutines.launch

private val destinations = listOf("Radio" to Symbol.Radio, "Library" to Symbol.Library, "Settings" to Symbol.Settings)

@Composable
fun RadioApp(repository: LibraryRepository = LibraryRepository.get(LocalContext.current), playback: PlaybackController = rememberPlaybackController(), openPlayerRequest: Int = 0,
    pairingRepository: dev.radio.mobile.connection.PairingRepository? = null) {
    val appContext=LocalContext.current.applicationContext
    val pairing= remember(pairingRepository,appContext) { pairingRepository ?: dev.radio.mobile.connection.EndpointPolicy(dev.radio.mobile.BuildConfig.DEBUG).let { p -> dev.radio.mobile.connection.PairingRepository(dev.radio.mobile.connection.PairedServerStore(appContext,p),dev.radio.mobile.connection.RadioApiClient(p),p) } }
    val discovery= remember(pairing,appContext) { dev.radio.mobile.connection.DiscoveryRepository(appContext,pairing) }
    val lifecycleScope=rememberCoroutineScope()
    val audio by playback.state.collectAsState()
    val library by repository.state.collectAsState()
    val playlists by repository.playlists.state.collectAsState()
    val journal by repository.journal.state.collectAsState()
    val radio by repository.radio.state.collectAsState()
    val connected by repository.radio.connectedState.collectAsState()
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri -> if (uri != null) repository.chooseFolder(uri) }
    fun chooseFolder() { picker.launch(library.root?.uri?.let(Uri::parse) ?: DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Music")) }
    val lifecycle = LocalLifecycleOwner.current
    val activity = LocalContext.current.playbackActivity()
    LaunchedEffect(pairing,discovery) { pairing.load(); discovery.load() }
    DisposableEffect(repository, lifecycle, discovery) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) { repository.checkAccess(); lifecycleScope.launch { pairing.load(); discovery.load(); discovery.readOnResume() } }
            if (event == Lifecycle.Event.ON_STOP && activity?.isChangingConfigurations != true) repository.loudness.pauseAnalysis()
        }
        lifecycle.lifecycle.addObserver(observer)
        repository.checkAccess()
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    // Shell preferences live here; library, playlists and playback bookmarks live in Room.
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
    var playlistId by rememberSaveable { mutableStateOf<String?>(null) }
    var sortName by rememberSaveable { mutableStateOf(prefs.getString("sort", LibrarySort.ModifiedNewest.name) ?: LibrarySort.ModifiedNewest.name) }
    val sort = LibrarySort.entries.firstOrNull { it.name == sortName || it.label == sortName } ?: LibrarySort.ModifiedNewest
    var query by rememberSaveable { mutableStateOf("") }
    var scrubMs by remember(audio.currentId) { mutableStateOf<Long?>(null) }
    val position = (scrubMs ?: audio.positionMs) / 1000f
    val liked = journal.feedback[audio.currentId]?.favorite == true
    val disliked = journal.feedback[audio.currentId]?.disliked == true
    val locked = radio.vibeLocalId != null
    var playerMenu by remember { mutableStateOf(false) }
    var showQueue by rememberSaveable { mutableStateOf(false) }
    var showSkip by rememberSaveable { mutableStateOf(false) }
    var showSleep by remember { mutableStateOf(false) }
    var addQueueSongs by remember { mutableStateOf(false) }
    var playlistSongIds by remember { mutableStateOf<List<String>?>(null) }
    // Checkpoint 4.2 Radio surfaces.
    val pairingState by pairing.state.collectAsState()
    val paired = pairingState.paired && !pairingState.blocked && !pairingState.quarantined
    var endSheet by remember { mutableStateOf(false) }
    var whySheet by remember { mutableStateOf(false) }
    var openTune by remember { mutableStateOf(false) }
    var pendingPlay by remember { mutableStateOf<Triple<LocalSong, List<LocalSong>, String>?>(null) }
    val songs = remember(library.songs, sort) { orderSongs(library.songs, sort) }
    val song = audio.current
    val listStates = remember { mutableMapOf<String, LazyListState>() }
    val listState = listStates.getOrPut("${library.root?.uri}/$folder/$category") { LazyListState() }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    fun message(text: String) { scope.launch { snackbar.showSnackbar(text) } }
    fun songAction(action: SongAction, item: LocalSong) {
        if (action == SongAction.AddToPlaylist) { playlistSongIds = listOf(item.id); return }
        if (library.accessLost || !item.available) { message("Check Music folder access before adding songs."); return }
        playback.addToQueue(listOf(item), action == SongAction.PlayNext)
        message(if (action == SongAction.PlayNext) "Will play next" else "Added to queue")
    }
    /** Plays now, or asks first while Radio is on air. Returns true when playback was replaced. */
    fun playSong(item: LocalSong, context: List<LocalSong>, source: String): Boolean {
        if (library.accessLost || !item.available) { message("This file is unavailable. Check folder access and rescan."); return false }
        if (audio.radioActive) { pendingPlay = Triple(item, context, source); return false }
        playback.playFrom(context, item.id, source)
        return true
    }
    fun notForMe() {
        val current = song ?: return
        repository.journal.setDisliked(current, true)
        playback.next()
        scope.launch {
            val result = snackbar.showSnackbar("Skipped “${current.title}”. Radio won't pick it again.", actionLabel = "Undo", duration = SnackbarDuration.Short)
            if (result == SnackbarResult.ActionPerformed) repository.journal.setDisliked(current, false)
        }
    }
    fun navigate(target: String) {
        if (target == "Search") searchReturnPage = page
        if (target == "Now playing") playerReturnPage = page
        page = target
        if (target != "Search") prefs.edit().putString("last_screen", if (target in listOf("Server", "Matching")) "Settings" else target).apply()
    }
    fun back() {
        if (page == "Now playing" && showQueue) { showQueue = false; return }
        if (page == "Search") { focus.clearFocus(); keyboard?.hide() }
        when (page) {
            "Search" -> page = searchReturnPage
            "Now playing" -> page = playerReturnPage
            "Server" -> page = "Settings"
            "Matching" -> page = "Server"
            "Library" -> if (category == "Playlists" && playlistId != null) playlistId = null else folder = library.folders.firstOrNull { it.id == folder }?.parentId?.takeUnless { it == library.root?.folderId }
            else -> folder = library.folders.firstOrNull { it.id == folder }?.parentId?.takeUnless { it == library.root?.folderId }
        }
    }
    LaunchedEffect(openPlayerRequest) { if (openPlayerRequest > 0) navigate("Now playing") }
    LaunchedEffect(library.root?.uri) { folder = null }
    LaunchedEffect(library.songs, library.accessLost, library.loading, library.scanning) {
        if (!library.loading && !library.scanning) playback.reconcileLibrary(library.songs, library.accessLost)
    }
    LaunchedEffect(audio.currentId) { repository.radio.updatePlayback(audio) }
    val showBack = page == "Server" || page == "Matching" || page == "Search" || page == "Now playing" || (page == "Library" && (folder != null || category == "Playlists" && playlistId != null))
    BackHandler(showBack) { back() }
    RadioTheme(
        palette = RadioPalette.entries.firstOrNull { it.name == palette } ?: RadioPalette.Ocean,
        appearance = Appearance.entries.firstOrNull { it.name == appearance } ?: Appearance.System
    ) { StationTheme(StationColors.of(RadioPalette.entries.firstOrNull { it.name == palette } ?: RadioPalette.Ocean), enabled = audio.radioActive && page == "Now playing" && song != null) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                Row(Modifier.fillMaxWidth().statusBarsPadding().heightIn(min = 64.dp).padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (showBack) IconButton(onClick = { back() }) { RadioIcon(Symbol.Back, "Back") }
                    Column(Modifier.weight(1f).padding(start = if (!showBack) 12.dp else 0.dp)) {
                        Text(if (page == "Library" && category == "Playlists" && playlistId != null) playlists.playlists.firstOrNull { it.id == playlistId }?.name ?: "Playlist" else if (page == "Library" && folder != null) library.folders.firstOrNull { it.id == folder }?.name ?: "Folder" else if (page == "Now playing" && showQueue) "Queue" else page, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (page == "Now playing" && audio.radioActive) OnAirPill("On air · ${if (radio.vibeLocalId != null) "Vibe locked" else moodLabel(radio.mood)}", Modifier.padding(top = 2.dp))
                        else Text(if (page == "Now playing") audio.source.ifBlank{"Local music"} else if (page == "Library" && category == "Playlists" && playlistId != null) "${playlists.songIds(playlistId!!).size} songs · Playlist" else if (library.scanning) "Scanning…" else if (library.root == null) "Connect your music" else "${library.songs.count { it.available }} MP3s", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (page == "Library") {
                        if (library.root != null) IconButton(onClick = { repository.rescan() }, enabled = !library.scanning && !library.loading) { RadioIcon(Symbol.Refresh, "Rescan library") }
                        IconButton(onClick = { query = ""; navigate("Search") }, enabled = library.root != null) { RadioIcon(Symbol.Search, "Search library") }
                    }
                    if (page == "Now playing" && !showQueue && !audio.radioActive) IconButton(onClick = { minimal = !minimal; prefs.edit().putBoolean("minimal", minimal).apply() }) {
                        RadioIcon(if (minimal) Symbol.Expand else Symbol.Minimize, if (minimal) "Full controls" else "Minimal mode")
                    }
                    if (page == "Now playing" && song != null) Box {
                        IconButton(onClick = { playerMenu = true }, modifier=Modifier.testTag("player-options")) { RadioIcon(Symbol.More, "Player options") }
                        DropdownMenu(playerMenu,{playerMenu=false}) {
                            if(audio.radioActive){
                                DropdownMenuItem(text={Text("Sleep timer")},onClick={playerMenu=false;showSleep=true},trailingIcon={Text(if(audio.sleepTimer.active)sleepLabel(audio.sleepTimer) else "Off",style=MaterialTheme.typography.labelSmall)})
                                DropdownMenuItem(text={Text("Replay this song")},onClick={playerMenu=false;playback.replay()},modifier=Modifier.testTag("replay-song"))
                                DropdownMenuItem(text={Text("Skip reason")},onClick={playerMenu=false;showSkip=true})
                                DropdownMenuItem(text={Text(if(showQueue)"Show artwork" else "Show queue")},onClick={playerMenu=false;showQueue=!showQueue},modifier=Modifier.testTag("show-queue"))
                                DropdownMenuItem(text={Text("Tune station")},onClick={playerMenu=false;openTune=true;navigate("Radio")},modifier=Modifier.testTag("tune-radio-player"))
                                DropdownMenuItem(text={Text(if(minimal)"Full view" else "Minimal view")},onClick={playerMenu=false;minimal=!minimal;prefs.edit().putBoolean("minimal",minimal).apply()})
                                DropdownMenuItem(text={Text("Why this song?")},onClick={playerMenu=false;whySheet=true},modifier=Modifier.testTag("why-song"))
                                HorizontalDivider()
                                DropdownMenuItem(text={Text("End station…")},onClick={playerMenu=false;endSheet=true},modifier=Modifier.testTag("end-station"))
                            } else {
                                DropdownMenuItem(text={Text("Replay")},onClick={playerMenu=false;playback.replay()},modifier=Modifier.testTag("replay-song"))
                                DropdownMenuItem(text={Text("Skip reason")},onClick={playerMenu=false;showSkip=true})
                                DropdownMenuItem(text={Text("Sleep timer")},onClick={playerMenu=false;showSleep=true})
                                DropdownMenuItem(text={Text(if(disliked)"Remove dislike" else "Dislike")},onClick={playerMenu=false;song?.let{repository.journal.setDisliked(it,!disliked)}},enabled=journal.feedbackLoaded && journal.pendingWrites==0,modifier=Modifier.testTag("dislike-song"))
                            }
                        }
                    }
                }
            },
            bottomBar = {
                if (page != "Search" && page != "Now playing") Column {
                    if (song != null && audio.radioActive) StationMiniPlayer(song, "On air · ${if (radio.vibeLocalId != null) "Vibe locked" else moodLabel(radio.mood)}", audio.playWhenReady,
                        canPlay = !library.accessLost && song.available, canNext = audio.canNext, onOpen = { navigate("Now playing") }, onToggle = { playback.toggle() }, onNext = { playback.next() })
                    else if (song != null) Surface(color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
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
                        destinations.forEach { (label, symbol) -> NavigationBarItem(selected = page == label || label == "Settings" && page in listOf("Server", "Matching"), onClick = { navigate(label) }, modifier = Modifier.testTag("nav-$label"), icon = { RadioIcon(symbol) }, label = { Text(label) }) }
                    }
                }
            },
            snackbarHost = { SnackbarHost(snackbar) }
        ) { insets ->
            when (page) {
                "Library" -> LibraryContent(library, folder, category, onCategory = { category = it; playlistId = null }, sort, onSort = { sortName = it.name; prefs.edit().putString("sort", it.name).apply() }, listState,
                    onFolder = { folder = it }, onSong = { item, context, source -> if (playSong(item, context, source)) navigate("Now playing") },
                    choose = { chooseFolder() }, rescan = { repository.rescan() }, cancel = { repository.cancelScan() }, modifier = Modifier.padding(insets), onSongAction = ::songAction,
                    playlistContent = { PlaylistBrowser(playlists, repository.playlists, library.songs, !library.accessLost, playlistId, { playlistId = it },
                        onPlay = { item, context, source -> if (playSong(item, context, source)) navigate("Now playing") }, onSongAction = ::songAction) })
                "Radio" -> RadioContent(radio,repository.radio,audio,playback,library,onLibrary={navigate("Library")},onPlayer={navigate("Now playing")},connected=connected,modifier=Modifier.padding(insets),
                    paired=paired,onChooseFolder={chooseFolder()},onRescan={repository.rescan()},onFullQueue={showQueue=true;navigate("Now playing")},onEnd={endSheet=true},openTune=openTune,onTuneOpened={openTune=false},onServer={navigate("Server")})
                "Search" -> Column(Modifier.padding(insets).imePadding()) {
                    OutlinedTextField(query, onValueChange = { query = it }, placeholder = { Text("Search songs and filenames") }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("search-input"))
                    val results = remember(songs, query) { songs.filter { query.isNotBlank() && (it.title + " " + it.fileName + " " + it.folderPath).contains(query, ignoreCase = true) } }
                    LazyColumn { items(results, key = { it.id }) { item ->
                        LibraryRow(item.title, songSubtitle(item), Symbol.Music, trailing = { SongActions(item, ::songAction) }) { focus.clearFocus(); keyboard?.hide(); if (playSong(item, results, "Search results")) navigate("Now playing") }
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
                        if (audio.radioActive && !showQueue) {
                            val options = dev.radio.mobile.recommendation.ConnectedPolicy.visibleOptions(connected, audio.currentId, audio.radioActive, audio.playOrder.toSet(), repository.radio::isDisliked) { id -> library.songs.any { it.id == id && it.available } }
                            RadioNowPlaying(audio, song, radio, position, liked, feedbackReady = journal.feedbackLoaded && journal.pendingWrites == 0, canRead = !library.accessLost && song.available,
                                minimal = minimal, compact = compact, options = options, optionsSummary = connected.set?.let(dev.radio.mobile.recommendation.ConnectedPolicy::summary),
                                onScrub = { scrubMs = (it * 1000).toLong() }, onScrubFinished = { scrubMs?.let { playback.seek(it) }; scrubMs = null },
                                onLike = { repository.journal.setFavorite(song, !liked) }, onNotForMe = ::notForMe,
                                onPrevious = { playback.previous() }, onToggle = { playback.toggle() }, onNext = { playback.next() },
                                onVibe = { if (locked) repository.radio.clearVibe() else repository.radio.lockVibe() },
                                onShowQueue = { showQueue = true }, onSleep = { showSleep = true }, onPlayNext = { pick -> playback.addToQueue(listOf(pick.song), true) },
                                onRescan = { repository.rescan() }, journalError = journal.error, onRetryJournal = { repository.journal.retry() }, modifier = Modifier.weight(1f))
                        } else if (showQueue) {
                            IntegratedQueue(audio, !library.accessLost, onSelect = { playback.jumpTo(it) }, modifier = Modifier.fillMaxWidth().weight(1f),
                                playback = playback, onAdd = { addQueueSongs = true }, onSave = { playlistSongIds = it }, serverAiIds = connected.serverAiIds)
                        } else {
                            Column(Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                Spacer(Modifier.height(20.dp))
                                Artwork(if (minimal || compact) 160 else 220)
                                Spacer(Modifier.height(24.dp))
                                Text(song.title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.fillMaxWidth())
                                Text(songSubtitle(song), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().padding(top = 4.dp))
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    FilterChip(liked, onClick = { repository.journal.setFavorite(song, !liked) }, enabled = journal.feedbackLoaded && journal.pendingWrites == 0,
                                        modifier = Modifier.testTag("favorite-song"), label = { Text(if (liked) "Liked" else "Like", fontWeight = if (liked) FontWeight.Bold else FontWeight.Normal) },
                                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = MaterialTheme.colorScheme.primary, selectedLabelColor = MaterialTheme.colorScheme.onPrimary, selectedLeadingIconColor = MaterialTheme.colorScheme.onPrimary),
                                        leadingIcon = { RadioIcon(Symbol.Heart) })
                                }
                                if (!minimal) {
                                    HorizontalDivider(Modifier.padding(top = 8.dp))
                                    Text(audio.status + " · Local file\n" + song.folderPath.ifEmpty { library.root?.name ?: "Music" }, Modifier.fillMaxWidth().padding(vertical = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                        if (!audio.radioActive || showQueue) journal.error?.let { failure ->
                            Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(failure, Modifier.weight(1f).testTag("journal-error"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                                TextButton(onClick = { repository.journal.retry() }, modifier = Modifier.testTag("retry-journal")) { Text("Retry") }
                            }
                        }
                        if (audio.error != null && (!audio.radioActive || showQueue)) Text(audio.error!!, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp).testTag("playback-error"))
                        if (audio.sleepTimer.active && !audio.radioActive) TextButton(onClick = { showSleep = true }, modifier = Modifier.align(Alignment.CenterHorizontally).testTag("sleep-countdown")) { Text("Sleep · ${sleepLabel(audio.sleepTimer)}") }
                        if (!audio.radioActive || showQueue) PlayerTransport(audio, playback, position, onScrub = { scrubMs = (it * 1000).toLong() },
                            onScrubFinished = { scrubMs?.let { playback.seek(it) }; scrubMs = null },
                            canRead = !library.accessLost && song.available, queueVisible = showQueue,
                            onQueueToggle = { showQueue = !showQueue }, modifier = Modifier.padding(horizontal = 24.dp), compact = compact)
                        if(!audio.radioActive)Row(Modifier.fillMaxWidth().padding(horizontal=24.dp),horizontalArrangement=Arrangement.SpaceEvenly,verticalAlignment=Alignment.CenterVertically){
                            run {TextButton(onClick={playback.setRepeat(when(audio.repeat){RepeatMode.OFF->RepeatMode.ALL;RepeatMode.ALL->RepeatMode.ONE;RepeatMode.ONE->RepeatMode.OFF})},modifier=Modifier.testTag("player-repeat")){Text("Repeat · ${audio.repeat.name.lowercase()}")};TextButton(onClick={showSleep=true},modifier=Modifier.semantics{contentDescription="Sleep timer"}){Text("Sleep")}}
                        }
                    }
                }
                "Server" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).imePadding().fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).testTag("server-scroll")) {
                    ServerSettings(pairing, onOpenMatching = { navigate("Matching") }, discoveryRepository = discovery, radioRepository = repository.radio)
                }
                "Matching" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).imePadding().fillMaxSize().padding(horizontal = 16.dp)) {
                    ServerSettings(pairing, matchingScreen = true, discoveryRepository = discovery)
                }
                "Settings" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).imePadding().fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).testTag("settings-scroll")) {
                    SectionLabel("Music library")
                    Text(library.root?.name ?: "No folder selected", style = MaterialTheme.typography.titleSmall)
                    Text("${library.songs.count { it.available }} MP3s · ${library.songs.count { !it.available }} missing", style = MaterialTheme.typography.bodySmall)
                    LibraryStatus(library, { chooseFolder() }, { repository.rescan() }, { repository.cancelScan() })
                    Row { TextButton(onClick = { chooseFolder() }, enabled = !library.scanning && !library.loading) { Text("Choose folder") }; TextButton(onClick = { repository.rescan() }, enabled = library.root != null && !library.scanning && !library.loading && !library.accessLost) { Text("Rescan") } }
                    SectionLabel("Radio")
                    RadioSimilaritySettings(radio,repository.radio)
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
                    Text("Playback", Modifier.padding(top = 16.dp), style = MaterialTheme.typography.titleSmall)
                    Text("Music keeps playing in the background. After a restart, your last queue returns paused. Sleep timers clear on restart.", Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    NormalizationSettings(repository.loudness, library, audio.currentId)
                    TextButton(onClick = { navigate("Server") }, modifier = Modifier.testTag("open-server")) { Text("Server") }
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
                    Text("This skips the song and helps tune future recommendations on this phone.", style = MaterialTheme.typography.bodySmall)
                    listOf("Too intense", "Too slow", "Too similar", "Wrong mood", "Just skip").forEach { reason ->
                        TextButton(onClick = { showSkip = false; playback.next(when(reason) { "Too intense" -> "too_intense"; "Too slow" -> "too_slow"; "Too similar" -> "too_samey"; "Wrong mood" -> "wrong_mood"; else -> "just_skip" }) }, enabled = audio.canNext) { Text(reason) }
                    }
                } }, confirmButton = { TextButton(onClick = { showSkip = false }) { Text("Close") } }
            )
            if (showSleep) SleepTimerDialog(audio.sleepTimer, playback) { showSleep = false }
            if (addQueueSongs) SongPicker("Add to queue", songs, { addQueueSongs = false }) { chosen -> playback.addToQueue(chosen); addQueueSongs = false }
            playlistSongIds?.let { ids -> PlaylistDestinationDialog(ids, playlists, repository.playlists,
                onDismiss = { playlistSongIds = null }, onAdded = { playlistSongIds = null; message("Playlist saved") }) }
            if (endSheet) {
                val upcoming = audio.playOrder.indexOf(audio.currentId).let { if (it < 0) 0 else audio.playOrder.size - it - 1 }
                EndStationSheet(upcoming, onFinish = { endSheet = false; playback.endRadio(false) }, onStopNow = { endSheet = false; playback.endRadio(true) }, onDismiss = { endSheet = false })
            }
            if (whySheet) WhyThisSongSheet(song, audio.origins[audio.currentId], radio) { whySheet = false }
            pendingPlay?.let { (item, context, source) ->
                LeaveRadioDialog(item.title, source.ifBlank { "your library" },
                    onLeave = { pendingPlay = null; playback.playFrom(context, item.id, source); navigate("Now playing") },
                    onPlayNext = { pendingPlay = null; playback.addToQueue(listOf(item), true); message("Will play next in Radio") },
                    onDismiss = { pendingPlay = null })
            }
        }
    } }
}

@Composable
private fun Artwork(size: Int) {
    Box(Modifier.size(size.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(if (size > 100) 12.dp else 6.dp)), contentAlignment = Alignment.Center) {
        RadioIcon(Symbol.Music, modifier = Modifier.size((size / 3).dp))
    }
}

@Composable
internal fun LibraryRow(title: String, subtitle: String, symbol: Symbol, subtitleMaxLines: Int = 1, trailing: @Composable () -> Unit = {}, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).heightIn(min = 56.dp).padding(horizontal = 20.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(36.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp)), contentAlignment = Alignment.Center) { RadioIcon(symbol, modifier = Modifier.size(20.dp)) }
        Column(Modifier.weight(1f).padding(start = 14.dp)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            Text(subtitle, maxLines = subtitleMaxLines, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        trailing()
    }
}

@Composable
private fun SectionLabel(label: String) { Text(label, Modifier.padding(top = 20.dp, bottom = 8.dp), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary) }
private fun time(seconds: Int) = "%d:%02d".format(seconds / 60, seconds % 60)
