package dev.radio.mobile.ui

import dev.radio.mobile.playback.PlaySource

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
import androidx.activity.compose.PredictiveBackHandler
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.togetherWith
import androidx.compose.ui.unit.Dp
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.key
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.radio.mobile.ui.theme.*
import kotlinx.coroutines.launch
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map

private val destinations = listOf("Radio" to Symbol.Radio, "Library" to Symbol.Library, "Settings" to Symbol.Settings)
/** The three pages that sit side by side in the pager, in bottom-bar order. */
private val tabPages = destinations.map { it.first }

@OptIn(androidx.compose.animation.ExperimentalSharedTransitionApi::class)
@Composable
fun RadioApp(repository: LibraryRepository = LibraryRepository.get(LocalContext.current), playback: PlaybackController = rememberPlaybackController(), openPlayerRequest: Int = 0, shortcut: ShortcutRequest? = null,
    pairingRepository: dev.radio.mobile.connection.PairingRepository? = null, lyricsRepositoryOverride: LyricsRepository? = null) {
    val appContext=LocalContext.current.applicationContext
    val lyricsRepository = remember(appContext, repository.db, lyricsRepositoryOverride) { lyricsRepositoryOverride ?: LyricsRepository.get(appContext, repository.db) }
    val pairing= remember(pairingRepository,appContext) { pairingRepository ?: dev.radio.mobile.connection.EndpointPolicy(dev.radio.mobile.BuildConfig.DEBUG).let { p -> dev.radio.mobile.connection.PairingRepository(dev.radio.mobile.connection.PairedServerStore(appContext,p),dev.radio.mobile.connection.RadioApiClient(p),p) } }
    val discovery= remember(pairing,appContext) { dev.radio.mobile.connection.DiscoveryRepository(appContext,pairing) }
    val lifecycleScope=rememberCoroutineScope()
    // The player publishes its position 4 times a second. Collecting that here would recompose the whole app (top bar,
    // pager, library list) on every tick, so the shell sees the state with position held at zero (and the sleep
    // countdown reduced to on/off); only the progress rows and sleep labels read the live values.
    val audio by playback.slice(::withoutTicks)
    val livePosition = playback.slice { it.positionMs }
    // The sleep countdown, to the second; like the position, only the labels that show it read it.
    val liveSleep = playback.slice { it.sleepTimer.copy(remainingMs = (it.sleepTimer.remainingMs + 999) / 1000 * 1000) }
    val library by repository.state.collectAsState()
    val playlists by repository.playlists.state.collectAsState()
    val journal by repository.journal.state.collectAsState()
    val radio by repository.radio.state.collectAsState()
    // Untracked listening: plays and skips aren't recorded while on.
    val untracked by repository.untracked.state.collectAsState()
    val tag = if (untracked != null) "Not tracking · " else ""
    val connected by repository.radio.connectedState.collectAsState()
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri -> if (uri != null) repository.chooseFolder(uri) }
    fun chooseFolder() { picker.launch(library.root?.uri?.let(Uri::parse) ?: DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Music")) }
    val lifecycle = LocalLifecycleOwner.current
    val activity = LocalContext.current.playbackActivity()
    var appInForeground by remember(lifecycle) { mutableStateOf(lifecycle.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) }
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
    var rescanOnOpen by rememberSaveable { mutableStateOf(prefs.getBoolean("rescan_on_open", true)) }
    var backdrop by rememberSaveable { mutableStateOf(prefs.getString("backdrop", "Animated") ?: "Animated") }
    var page by rememberSaveable { mutableStateOf(when (opening) {
        "Last screen" -> prefs.getString("last_screen", "Library") ?: "Library"
        else -> opening
    }) }
    var searchReturnPage by rememberSaveable { mutableStateOf("Library") }
    var playerReturnPage by rememberSaveable { mutableStateOf("Library") }
    var category by rememberSaveable { mutableStateOf("Folders") }
    var folder by rememberSaveable { mutableStateOf<String?>(null) }
    // The library root the folder belongs to; only a real change of root (not a rotation) resets the folder.
    var folderRoot by rememberSaveable { mutableStateOf<String?>(null) }
    var playlistId by rememberSaveable { mutableStateOf<String?>(null) }
    var artist by rememberSaveable { mutableStateOf<String?>(null) }
    val audioSettings = remember(appContext) { AudioSettingsStore.get(appContext) }
    val favorites = remember(journal.feedback) { journal.feedback.values.filter { it.favorite }.mapTo(HashSet()) { it.localId } }
    var sortName by rememberSaveable { mutableStateOf(prefs.getString("sort", LibrarySort.ModifiedNewest.name) ?: LibrarySort.ModifiedNewest.name) }
    val sort = LibrarySort.entries.firstOrNull { it.name == sortName || it.label == sortName } ?: LibrarySort.ModifiedNewest
    var handledShortcut by rememberSaveable { mutableIntStateOf(0) }
    var query by rememberSaveable { mutableStateOf("") }
    var recentSearches by remember { mutableStateOf(runCatching { org.json.JSONArray(prefs.getString("recentSearches", "[]")).let { a -> (0 until a.length()).map(a::getString) } }.getOrDefault(emptyList())) }
    var scrubMs by remember(audio.currentId) { mutableStateOf<Long?>(null) }
    // Read lazily by the progress rows only; see [audio].
    val positionMs: () -> Long = { scrubMs ?: livePosition.value }
    val liked = journal.feedback[audio.currentId]?.favorite == true
    val disliked = journal.feedback[audio.currentId]?.disliked == true
    val locked = radio.vibeLocalId != null
    var playerMenu by remember { mutableStateOf(false) }
    var showQueue by rememberSaveable { mutableStateOf(false) }
    var showSkip by rememberSaveable { mutableStateOf(false) }
    var showLyricsSheet by rememberSaveable { mutableStateOf(false) }
    var showSleep by remember { mutableStateOf(false) }
    var addQueueSongs by remember { mutableStateOf(false) }
    var playlistSongIds by remember { mutableStateOf<List<String>?>(null) }
    var statsSong by remember { mutableStateOf<LocalSong?>(null) }
    // Checkpoint 9B feels.
    val feelsState by repository.feels.state.collectAsState()
    var feelsNav by remember { mutableStateOf(FeelsNav()) }
    var feelSheet by remember { mutableStateOf(false) }
    val preview = (playback as? dev.radio.mobile.playback.ServicePlaybackController)?.preview
    // Checkpoint 4.2 Radio surfaces.
    val pairingState by pairing.state.collectAsState()
    val paired = pairingState.paired && !pairingState.blocked && !pairingState.quarantined
    var endSheet by remember { mutableStateOf(false) }
    var whySheet by remember { mutableStateOf(false) }
    var openTune by remember { mutableStateOf(false) }
    var pendingPlay by remember { mutableStateOf<PendingPlay?>(null) }
    val songs = remember(library.songs, sort) { orderSongs(library.songs, sort) }
    val song = audio.current
    // Radio was asked for and is still choosing its first songs: show the station, never the old queue's controls.
    val tuningIn = audio.radioStarting && !audio.radioActive
    val stationMode = audio.radioActive || tuningIn
    val listStates = remember { mutableMapOf<String, LazyListState>() }
    // Scroll positions per folder/category, kept across rotation and theme changes.
    val savedPositions = rememberSaveable { HashMap<String, IntArray>() }
    val listKey = "${library.root?.uri}/$folder/$category/$artist"
    val listState = listStates.getOrPut(listKey) { savedPositions[listKey].let { LazyListState(it?.getOrNull(0) ?: 0, it?.getOrNull(1) ?: 0) } }
    LaunchedEffect(listState, listKey) {
        snapshotFlow { listState.firstVisibleItemIndex to listState.firstVisibleItemScrollOffset }
            .collect { (index, offset) -> savedPositions[listKey] = intArrayOf(index, offset) }
    }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    fun message(text: String) { scope.launch { snackbar.showSnackbar(text) } }
    fun songAction(action: SongAction, item: LocalSong) {
        if (action == SongAction.AddToPlaylist) { playlistSongIds = listOf(item.id); return }
        if (action == SongAction.Stats) { statsSong = item; return }
        if (library.accessLost || !item.available) { message("Check Music folder access before adding songs."); return }
        playback.addToQueue(listOf(item), action == SongAction.PlayNext)
        message(if (action == SongAction.PlayNext) "Will play next" else "Added to queue")
    }
    /** Plays now, or asks first while Radio is on air. Returns true when playback was replaced. */
    fun playSong(item: LocalSong, context: List<LocalSong>, source: PlaySource): Boolean {
        if (library.accessLost || !item.available) { message("This file is unavailable. Check folder access and rescan."); return false }
        if (stationMode) { pendingPlay = PendingPlay(item, context, source); return false }
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
        if (target != "Search") prefs.edit().putString("last_screen", if (target in listOf("Server", "Matching", "Equaliser", "Stats", "Feels")) "Settings" else target).apply()
    }
    fun back() {
        if (page == "Now playing" && showQueue) { showQueue = false; return }
        if (page == "Search") { focus.clearFocus(); keyboard?.hide() }
        when (page) {
            "Search" -> page = searchReturnPage
            "Now playing" -> page = playerReturnPage
            "Server", "Equaliser", "Stats" -> page = "Settings"
            "Feels" -> if (feelsNav.sprint) feelsNav = feelsNav.copy(sprint = false) else if (feelsNav.feelId != null) feelsNav = FeelsNav() else page = "Settings"
            "Matching" -> page = "Server"
            "Library" -> if (category == "Playlists" && playlistId != null) playlistId = null else if (category == "Artists" && artist != null) artist = null else folder = library.folders.firstOrNull { it.id == folder }?.parentId?.takeUnless { it == library.root?.folderId }
            else -> folder = library.folders.firstOrNull { it.id == folder }?.parentId?.takeUnless { it == library.root?.folderId }
        }
    }
    fun startAll(first: LocalSong, context: List<LocalSong>, source: PlaySource, shuffle: Boolean) {
        playback.playFrom(context, first.id, source)
        playback.shuffle(shuffle)
        navigate("Now playing")
    }
    /** Play all or shuffle a list from its header, asking first while Radio is on air. */
    fun playAll(context: List<LocalSong>, source: PlaySource, shuffle: Boolean) {
        val playable = context.filter { it.available }
        if (library.accessLost || playable.isEmpty()) { message("Check Music folder access before playing."); return }
        val first = if (shuffle) playable.random() else playable.first()
        if (stationMode) { pendingPlay = PendingPlay(first, context, source, all = true, shuffle = shuffle); return }
        startAll(first, context, source, shuffle)
    }
    // Pick up files added since the last scan, once per app process. Durations are cached, so a rescan is quick.
    LaunchedEffect(library.loading, library.root?.uri, library.accessLost) {
        val root = library.root ?: return@LaunchedEffect
        if (!rescanOnOpen || library.loading || library.scanning || library.accessLost || AutoRescan.done) return@LaunchedEffect
        AutoRescan.done = true
        if (System.currentTimeMillis() - root.scannedAt > AutoRescan.MIN_AGE_MS) repository.rescan()
    }
    // Checkpoint 8C: milestone notices appear once, ever, as a short in-app message (never a system notification).
    val milestoneHead = journal.history.firstOrNull()?.eventId
    LaunchedEffect(milestoneHead, library.loading) {
        if (library.loading || milestoneHead == null) return@LaunchedEffect
        kotlinx.coroutines.delay(1500)
        val songsNow = library.songs
        val reached = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
            val rows = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { repository.journal.statsAttempts() }
            unannouncedMilestones(appContext, FunStats.milestones(rows, songsNow, System.currentTimeMillis(), java.time.ZoneId.systemDefault()))
        }
        reached.forEach { message("Milestone reached · ${it.title}. ${it.detail}") }
    }
    LaunchedEffect(openPlayerRequest) { if (openPlayerRequest > 0) navigate("Now playing") }
    // Checkpoint 11: launcher shortcuts. Shuffle all waits for the library to load.
    LaunchedEffect(shortcut?.serial, library.loading) {
        val request = shortcut ?: return@LaunchedEffect
        if (request.serial <= handledShortcut) return@LaunchedEffect
        when (request.action) {
            ShortcutRequest.RADIO -> navigate("Radio")
            ShortcutRequest.LIKED -> { category = "Playlists"; playlistId = dev.radio.mobile.data.SmartList.Liked.id; navigate("Library") }
            ShortcutRequest.SHUFFLE -> { if (library.loading) return@LaunchedEffect; playAll(library.songs, PlaySource.AllSongs, true); if (!stationMode) navigate("Now playing") }
            // play(), not toggle(): the UI's state is still empty before the service binds, and toggle would pause music
            // that is already playing.
            ShortcutRequest.RESUME -> { playback.play(); navigate("Now playing") }
        }
        handledShortcut = request.serial
    }
    LaunchedEffect(library.root?.uri, library.loading) {
        if (library.loading) return@LaunchedEffect
        val root = library.root?.uri
        if (folderRoot != null && folderRoot != root) { folder = null; playlistId = null; artist = null; savedPositions.clear(); listStates.clear() }
        folderRoot = root
    }
    // One-off playback messages, such as a Radio start that couldn't complete. Shown once, not again after rotation.
    var seenNotice by rememberSaveable { mutableStateOf(audio.notice?.id ?: 0L) }
    LaunchedEffect(audio.notice?.id) {
        val notice = audio.notice ?: return@LaunchedEffect
        if (notice.id <= seenNotice) return@LaunchedEffect
        seenNotice = notice.id
        snackbar.showSnackbar(notice.message)
    }
    LaunchedEffect(library.songs, library.accessLost, library.loading, library.scanning) {
        if (!library.loading && !library.scanning) playback.reconcileLibrary(library.songs, library.accessLost)
    }
    LaunchedEffect(audio.currentId) { repository.radio.updatePlayback(audio) }
    LaunchedEffect(showLyricsSheet, song, page, appInForeground) {
        if (showLyricsSheet && song != null && page == "Now playing" && appInForeground) {
            try {
                lyricsRepository.open(song)
                awaitCancellation()
            } finally {
                lyricsRepository.close()
            }
        } else lyricsRepository.close()
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_STOP -> appInForeground = false
                Lifecycle.Event.ON_START -> appInForeground = true
                else -> Unit
            }
        }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    val showBack = page == "Server" || page == "Matching" || page == "Equaliser" || page == "Stats" || page == "Feels" || page == "Search" || page == "Now playing" || (page == "Library" && (folder != null || category == "Playlists" && playlistId != null || category == "Artists" && artist != null))
    // Checkpoint 10: predictive back. The screen shrinks and leans toward the swipe edge while the gesture is held, then
    // the normal back() runs on release; a cancelled gesture springs back. Reduced motion skips the preview.
    var backProgress by remember { mutableFloatStateOf(0f) }
    // Checkpoint 10: the screen AnimatedContent's scope per screen, for the mini-player → Now Playing shared elements.
    val screenScopes = remember { HashMap<String, androidx.compose.animation.AnimatedVisibilityScope>() }
    val reducedMotion = remember(appContext) { android.provider.Settings.Global.getFloat(appContext.contentResolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
    var backFromRight by remember { mutableStateOf(false) }
    val backReset = remember { androidx.compose.animation.core.Animatable(0f) }
    PredictiveBackHandler(showBack) { events ->
        try {
            events.collect { event -> backFromRight = event.swipeEdge == androidx.activity.BackEventCompat.EDGE_RIGHT; if (!reducedMotion) backProgress = event.progress }
            backProgress = 0f
            back()
        } catch (cancelled: kotlinx.coroutines.CancellationException) {
            backReset.snapTo(backProgress)
            backReset.animateTo(0f, androidx.compose.animation.core.spring()) { backProgress = value }
            throw cancelled
        }
    }
    RadioTheme(
        palette = RadioPalette.entries.firstOrNull { it.name == palette } ?: RadioPalette.Ocean,
        appearance = Appearance.entries.firstOrNull { it.name == appearance } ?: Appearance.System
    ) { StationTheme(StationColors.of(RadioPalette.entries.firstOrNull { it.name == palette } ?: RadioPalette.Ocean), enabled = stationMode && page == "Now playing" && (song != null || tuningIn)) { androidx.compose.animation.SharedTransitionLayout { val shared = this
        Scaffold(
            // Lets the benchmark and baseline-profile journeys find Compose test tags as resource ids.
            modifier = Modifier.semantics { testTagsAsResourceId = true },
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                Row(Modifier.fillMaxWidth().statusBarsPadding().heightIn(min = if (page == "Now playing") 56.dp else 64.dp).padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (showBack) IconButton(onClick = { back() }) { RadioIcon(Symbol.Back, "Back") }
                    Column(Modifier.weight(1f).padding(start = if (!showBack) 12.dp else 0.dp)) {
                        Text(if (page == "Library" && category == "Playlists" && playlistId != null) SmartList.of(playlistId)?.title ?: playlists.playlists.firstOrNull { it.id == playlistId }?.name ?: "Playlist" else if (page == "Library" && category == "Artists" && artist != null) artist!! else if (page == "Library" && folder != null) library.folders.firstOrNull { it.id == folder }?.name ?: "Folder" else if (page == "Now playing" && showQueue) "Queue" else page, style = if (page == "Now playing") MaterialTheme.typography.titleMedium else if (page in tabPages && !showBack) MaterialTheme.typography.headlineMedium else MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (page == "Now playing" && tuningIn) OnAirPill("Tuning in…", Modifier.padding(top = 2.dp))
                        else if (page == "Now playing" && audio.radioActive) OnAirPill("${tag}On air · ${if (radio.vibeLocalId != null) "Vibe locked" else repository.radio.feelChoices.value.firstOrNull { it.feelId == radio.feel }?.name ?: moodLabel(radio.mood)}", Modifier.padding(top = 2.dp))
                        else if (page == "Radio") Text(when {
                            library.accessLost -> "Music folder access was lost"
                            library.scanning -> "Scanning your library…"
                            radio.loaded -> "%,d songs ready on this phone".format(radio.diagnostics.eligibleTracks)
                            else -> radio.status
                        }, style = MaterialTheme.typography.labelSmall, color = if (library.accessLost) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("radio-status"))
                        else Text(if (page == "Now playing") tag + audio.source.ifBlank{"Local music"} else if (page == "Equaliser") "Sound for this app" else if (page == "Stats") "Only on this phone" else if (page == "Feels") (if (feelsNav.sprint) "Feel sprint" else if (feelsNav.pairs) "This or that" else "How songs feel to you") else if (page == "Library" && category == "Playlists" && playlistId != null) SmartList.of(playlistId)?.let { "Automatic · ${it.description}" } ?: "${playlists.songIds(playlistId!!).size} songs · Playlist" else if (page == "Library" && category == "Artists" && artist != null) "Artist" else if (library.scanning) "Scanning…" else if (library.root == null) "Connect your music" else "${library.songs.count { it.available }} MP3s", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (page == "Radio" && audio.radioActive) IconButton(onClick = { openTune = true }, modifier = Modifier.testTag("tune-radio")) { RadioIcon(Symbol.Settings, "Tune station") }
                    if (page == "Library") {
                        IconButton(onClick = { query = ""; navigate("Search") }, enabled = library.root != null) { RadioIcon(Symbol.Search, "Search library") }
                    }
                    if (page == "Now playing" && !showQueue && !stationMode) IconButton(onClick = { minimal = !minimal; prefs.edit().putBoolean("minimal", minimal).apply() }) {
                        RadioIcon(if (minimal) Symbol.Expand else Symbol.Minimize, if (minimal) "Full controls" else "Minimal mode")
                    }
                    if (page == "Now playing" && song != null && !tuningIn) Box {
                        IconButton(onClick = { playerMenu = true }, modifier=Modifier.testTag("player-options")) { RadioIcon(Symbol.More, "Player options") }
                        DropdownMenu(playerMenu,{playerMenu=false}) {
                            DropdownMenuItem(text={Text("Lyrics")},onClick={playerMenu=false;showLyricsSheet=true},modifier=Modifier.testTag("open-lyrics"))
                            DropdownMenuItem(text={Text("Song stats")},onClick={playerMenu=false;statsSong=song},modifier=Modifier.testTag("open-song-stats"))
                            HorizontalDivider()
                            if(audio.radioActive){
                                DropdownMenuItem(text={Text("Sleep timer")},onClick={playerMenu=false;showSleep=true},trailingIcon={Text(if(liveSleep.value.active)sleepLabel(liveSleep.value) else "Off",style=MaterialTheme.typography.labelSmall)})
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
                            HorizontalDivider()
                            DropdownMenuItem(text={Text(if(untracked!=null)"Track listening again" else "Don't track this session")},
                                onClick={playerMenu=false;if(untracked!=null)repository.untracked.stop() else { repository.untracked.start(); scope.launch{snackbar.showSnackbar("Not tracking plays or skips. Ends after 2 hours or 15 minutes stopped.")} }},
                                modifier=Modifier.testTag("untracked-toggle"))
                        }
                    }
                }
            },
            bottomBar = {
                AnimatedVisibility(page != "Search" && page != "Now playing", enter = fadeIn(Motion.standard()), exit = fadeOut(Motion.quick())) { val barScope = this; Column(Modifier.background(MaterialTheme.colorScheme.surfaceContainer)) {
                  val miniSong = rememberLastNonNull(song)
                  AnimatedVisibility(song != null, enter = expandVertically(Motion.standard()) + fadeIn(Motion.standard()), exit = shrinkVertically(Motion.quick()) + fadeOut(Motion.quick())) {
                  val song = song ?: miniSong ?: return@AnimatedVisibility
                  AnimatedContent(stationMode, transitionSpec = { fadeIn(Motion.standard()) togetherWith fadeOut(Motion.quick()) }, label = "mini-player") { onAir ->
                    if (onAir) StationMiniPlayer(song, if (tuningIn) "Tuning in…" else "${tag}On air · ${if (radio.vibeLocalId != null) "Vibe locked" else moodLabel(radio.mood)}", audio.playWhenReady,
                        canPlay = !library.accessLost && song.available, canNext = audio.canNext && !tuningIn, onOpen = { navigate("Now playing") }, onToggle = { playback.toggle() }, onNext = { playback.next() }, mood = radio.mood)
                    // Normal playback: a tonal pill in the app's own palette. The mood-coloured pill above means "on air".
                    else Surface(color = MaterialTheme.colorScheme.primaryContainer, contentColor = MaterialTheme.colorScheme.onPrimaryContainer, shape = RoundedCornerShape(18.dp),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp)) {
                        Row(Modifier.padding(start = 12.dp, end = 6.dp).heightIn(min = 64.dp), verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.sharedPlayer(shared, barScope, "art").clickable(role = Role.Button, onClickLabel = "Open player") { navigate("Now playing") }) { Artwork(40) }
                            Column(Modifier.weight(1f).testTag("mini-open").clickable(role = Role.Button, onClickLabel = "Open player") { navigate("Now playing") }.padding(horizontal = 12.dp, vertical = 8.dp)) {
                                Text(song.title, Modifier.sharedPlayer(shared, barScope, "title"), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                                Text("$tag${audio.status} · ${song.artist.ifBlank { song.folderPath.ifBlank { "Local music" } }}", maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                            }
                            FilledIconButton(onClick = { playback.toggle() }, enabled = !library.accessLost && song.available, shape = RadioShapes.Soft) { RadioIcon(if (audio.playWhenReady) Symbol.Pause else Symbol.Play, if (audio.playWhenReady) "Pause" else "Play") }
                            IconButton(onClick = { playback.next() }, enabled = audio.canNext, modifier = Modifier.testTag("mini-next")) { RadioIcon(Symbol.Next, "Next song") }
                        }
                    }
                  }
                  }
                    NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer, windowInsets = WindowInsets(0), modifier = Modifier.navigationBarsPadding().height(68.dp)) {
                        destinations.forEach { (label, symbol) ->
                            val selected = page == label || label == "Settings" && page in listOf("Server", "Matching", "Equaliser", "Stats", "Feels")
                            NavigationBarItem(selected = selected, onClick = { navigate(label) }, modifier = Modifier.testTag("nav-$label"), icon = { RadioIcon(symbol) },
                                label = { Text(label, fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium) })
                        }
                    }
                } }
            },
            snackbarHost = { SnackbarHost(snackbar) }
        ) { insets ->
            @Composable fun PageBody(target: String) { when (target) {
                "Library" -> LibraryContent(library, folder, category, onCategory = { category = it; playlistId = null; artist = null }, sort, onSort = { sortName = it.name; prefs.edit().putString("sort", it.name).apply() }, listState,
                    onFolder = { folder = it }, onSong = { item, context, source -> if (playSong(item, context, source)) navigate("Now playing") },
                    choose = { chooseFolder() }, rescan = { repository.rescan() }, cancel = { repository.cancelScan() }, modifier = Modifier.padding(insets), onSongAction = ::songAction,
                    playlistContent = { PlaylistBrowser(playlists, repository.playlists, library.songs, !library.accessLost, playlistId, { playlistId = it },
                        onPlay = { item, context, source -> if (playSong(item, context, source)) navigate("Now playing") }, onSongAction = ::songAction,
                        currentId = audio.currentId, onPlayAll = ::playAll, smartRows = repository.journal.smartListRows, favorites = favorites) },
                    sortedSongs = songs, currentId = audio.currentId, onPlayAll = ::playAll, artist = artist, onArtist = { artist = it })
                "Radio" -> RadioContent(radio,repository.radio,audio,playback,library,onLibrary={navigate("Library")},onPlayer={navigate("Now playing")},connected=connected,modifier=Modifier.padding(insets),
                    paired=paired,onChooseFolder={chooseFolder()},onRescan={repository.rescan()},onFullQueue={showQueue=true;navigate("Now playing")},onEnd={endSheet=true},openTune=openTune,onTuneOpened={openTune=false},onServer={navigate("Server")},positionMs=positionMs)
                "Search" -> Column(Modifier.padding(insets).imePadding()) {
                    OutlinedTextField(query, onValueChange = { query = it }, placeholder = { Text("Search songs and filenames") }, singleLine = true, shape = RoundedCornerShape(28.dp), leadingIcon = { RadioIcon(Symbol.Search) }, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("search-input"))
                    // Checkpoint 11: typo-tolerant matching; recent searches show while the box is empty.
                    val results = remember(songs, query) { dev.radio.mobile.music.SongSearch.filter(songs, query) { listOf(it.title, it.artist, it.fileName, it.folderPath) } }
                    fun rememberQuery() { recentSearches = dev.radio.mobile.music.SongSearch.remember(recentSearches, query); prefs.edit().putString("recentSearches", org.json.JSONArray(recentSearches).toString()).apply() }
                    if (query.isBlank() && recentSearches.isNotEmpty()) {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text("Recent searches", Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
                            TextButton(onClick = { recentSearches = emptyList(); prefs.edit().remove("recentSearches").apply() }) { Text("Clear") }
                        }
                        recentSearches.forEach { recent ->
                            LibraryRow(recent, "", Symbol.Search) { query = recent }
                        }
                    }
                    LazyColumn { items(results, key = { it.id }) { item ->
                        SwipeSongRow(item, ::songAction) {
                            LibraryRow(item.title, songSubtitle(item), Symbol.Music, playing = item.id == audio.currentId, trailing = { SongActions(item, ::songAction) }) { focus.clearFocus(); keyboard?.hide(); rememberQuery(); if (playSong(item, results, PlaySource.Search)) navigate("Now playing") }
                        }
                    } }
                    if (query.isNotBlank() && results.isEmpty()) Text("No matching songs", Modifier.padding(20.dp))
                }
                "Now playing" -> if (song == null && !tuningIn) NothingPlaying(repository, library, audio.error, onResume = { playback.play() },
                    onRadio = { playback.startRadio(null) }, onBrowse = { navigate("Library") }, modifier = Modifier.padding(insets)) else BoxWithConstraints(Modifier.consumeWindowInsets(insets).fillMaxSize()) {
                    // The backdrop fills the whole screen (behind the transparent top bar); only the content is inset.
                    val compact = maxHeight - insets.calculateTopPadding() - insets.calculateBottomPadding() < 520.dp
                    // On air the glow takes the station's mood colour; normal playback keeps the palette colour.
                    PlayerBackground(PlayerBackdrop.entries.firstOrNull { it.name == backdrop } ?: PlayerBackdrop.Animated, audio.isPlaying, Modifier.matchParentSize(),
                        tint = if (stationMode) Color(MoodColors.of(radio.mood).field) else null)
                    // Tuning in → on air, artwork ↔ queue and standard ↔ Radio crossfade instead of snapping.
                    val mode = when { tuningIn -> "tuning"; showQueue -> "queue"; audio.radioActive -> "radio"; else -> "standard" }
                    val artSize by animateDpAsState(if (minimal || compact) 160.dp else 220.dp, Motion.standard(), label = "artwork-size")
                    AnimatedContent(mode, Modifier.fillMaxSize(), transitionSpec = { fadeIn(Motion.standard()) togetherWith fadeOut(Motion.quick()) }, label = "player-mode") { shown ->
                    val song = song
                    if (shown == "tuning" || song == null) RadioTuningIn(radio.mood, Modifier.fillMaxSize().padding(insets).navigationBarsPadding())
                    else Column(Modifier.fillMaxSize().padding(insets).navigationBarsPadding()) {
                        if (shown == "radio") {
                            val options = dev.radio.mobile.recommendation.ConnectedPolicy.visibleOptions(connected, audio.currentId, audio.radioActive, audio.playOrder.toSet(), repository.radio::isDisliked) { id -> library.songs.any { it.id == id && it.available } }
                            RadioNowPlaying(audio, song, radio, positionMs, liked, feedbackReady = journal.feedbackLoaded && journal.pendingWrites == 0, canRead = !library.accessLost && song.available,
                                minimal = minimal, compact = compact, options = options, optionsSummary = connected.set?.let(dev.radio.mobile.recommendation.ConnectedPolicy::summary),
                                onScrub = { scrubMs = (it * 1000).toLong() }, onScrubFinished = { scrubMs?.let { playback.seek(it) }; scrubMs = null },
                                onLike = { repository.journal.setFavorite(song, !liked) }, onNotForMe = ::notForMe,
                                onPrevious = { playback.previous() }, onToggle = { playback.toggle() }, onNext = { playback.next() },
                                onVibe = { if (locked) repository.radio.clearVibe() else repository.radio.lockVibe() },
                                onShowQueue = { showQueue = true }, onLyrics = { showLyricsSheet = true }, onSleep = { showSleep = true }, sleep = { liveSleep.value }, onPlayNext = { pick -> playback.addToQueue(listOf(pick.song), true) },
                                onRescan = { repository.rescan() }, journalError = journal.error, onRetryJournal = { repository.journal.retry() }, modifier = Modifier.weight(1f),
                                feelChip = { FeelChip(song, feelsState, onClick = { feelSheet = true }) })
                        } else if (shown == "queue") {
                            IntegratedQueue(audio, !library.accessLost, onSelect = { playback.jumpTo(it) }, modifier = Modifier.fillMaxWidth().weight(1f),
                                playback = playback, onAdd = { addQueueSongs = true }, onSave = { playlistSongIds = it }, serverAiIds = connected.serverAiIds)
                        } else {
                            Column(Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                Spacer(Modifier.height(20.dp))
                                Box(Modifier.sharedPlayer(shared, screenScopes["Now playing"], "art")) { Artwork(artSize) }
                                Spacer(Modifier.height(24.dp))
                                SongTitle(song.title, Modifier.sharedPlayer(shared, screenScopes["Now playing"], "title").fillMaxWidth())
                                Text(songSubtitle(song), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().padding(top = 4.dp))
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    FilterChip(liked, onClick = { repository.journal.setFavorite(song, !liked) }, enabled = journal.feedbackLoaded && journal.pendingWrites == 0,
                                        modifier = Modifier.testTag("favorite-song"), label = { Text(if (liked) "Liked" else "Like", fontWeight = if (liked) FontWeight.Bold else FontWeight.Normal) },
                                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = MaterialTheme.colorScheme.primary, selectedLabelColor = MaterialTheme.colorScheme.onPrimary, selectedLeadingIconColor = MaterialTheme.colorScheme.onPrimary),
                                        leadingIcon = { RadioIcon(if (liked) Symbol.HeartFilled else Symbol.Heart) })
                                    FeelChip(song, feelsState, onClick = { feelSheet = true })
                                }
                                AnimatedVisibility(!minimal, enter = expandVertically(Motion.standard()) + fadeIn(Motion.standard()), exit = shrinkVertically(Motion.quick()) + fadeOut(Motion.quick())) {
                                  Column {
                                    HorizontalDivider(Modifier.padding(top = 8.dp))
                                    Text(audio.status + " · Local file\n" + song.folderPath.ifEmpty { library.root?.name ?: "Music" }, Modifier.fillMaxWidth().padding(vertical = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                  }
                                }
                            }
                        }
                        if (shown != "radio") journal.error?.let { failure ->
                            Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(failure, Modifier.weight(1f).testTag("journal-error"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                                TextButton(onClick = { repository.journal.retry() }, modifier = Modifier.testTag("retry-journal")) { Text("Retry") }
                            }
                        }
                        if (audio.error != null && shown != "radio") Text(audio.error!!, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp).testTag("playback-error"))
                        if (shown != "radio") PlayerTransport(audio, playback, positionMs, onScrub = { scrubMs = (it * 1000).toLong() },
                            onScrubFinished = { scrubMs?.let { playback.seek(it) }; scrubMs = null },
                            canRead = !library.accessLost && song.available, queueVisible = shown == "queue",
                            onQueueToggle = { showQueue = !showQueue }, modifier = Modifier.padding(horizontal = 24.dp), compact = compact)
                        if (!audio.radioActive) PlayerExtras(audio.repeat, liveSleep, showLyrics = shown != "queue",
                            onRepeat = { playback.setRepeat(when (audio.repeat) { RepeatMode.OFF -> RepeatMode.ALL; RepeatMode.ALL -> RepeatMode.ONE; RepeatMode.ONE -> RepeatMode.OFF }) },
                            onSleep = { showSleep = true }, onLyrics = { showLyricsSheet = true }, modifier = Modifier.padding(horizontal = 24.dp))
                    }
                    }
                }
                "Server" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).imePadding().fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).testTag("server-scroll")) {
                    ServerSettings(pairing, onOpenMatching = { navigate("Matching") }, discoveryRepository = discovery, radioRepository = repository.radio)
                }
                "Feels" -> FeelsScreen(repository.feels, feelsState, library.songs, feelsNav, { feelsNav = it }, preview,
                    sessions = { dev.radio.mobile.recommendation.FeelSprint.sessions(journal.history.filter { it.kind == "song_started" }.map { it.localId to it.occurredAt }) },
                    onMessage = ::message, modifier = Modifier.padding(insets).consumeWindowInsets(insets))
                "Stats" -> StatsScreen(repository.journal, library.songs, onSong = { statsSong = it }, modifier = Modifier.padding(insets).consumeWindowInsets(insets),
                    radio = repository.radio, feels = feelsState,
                    onPlay = { item -> if (playSong(item, listOf(item), PlaySource("Taste map", PlaySource.ALL_SONGS))) navigate("Now playing") },
                    onRadio = { item -> playback.startRadio(item.id); navigate("Now playing") },
                    playlistName = { id -> playlists.playlists.firstOrNull { it.id == id }?.name })
                "Equaliser" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).testTag("equalizer-scroll")) {
                    EqualizerScreen(audioSettings)
                }
                "Matching" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).imePadding().fillMaxSize().padding(horizontal = 16.dp)) {
                    ServerSettings(pairing, matchingScreen = true, discoveryRepository = discovery)
                }
                "Settings" -> Column(Modifier.padding(insets).consumeWindowInsets(insets).imePadding().fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).testTag("settings-scroll"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    SettingsCard {
                    SectionLabel("Music library")
                    Text(library.root?.name ?: "No folder selected", style = MaterialTheme.typography.titleSmall)
                    Text("${library.songs.count { it.available }} MP3s · ${library.songs.count { !it.available }} missing", style = MaterialTheme.typography.bodySmall)
                    LibraryStatus(library, { chooseFolder() }, { repository.rescan() }, { repository.cancelScan() })
                    Row { TextButton(onClick = { chooseFolder() }, enabled = !library.scanning && !library.loading) { Text("Choose folder") }; TextButton(onClick = { repository.rescan() }, enabled = library.root != null && !library.scanning && !library.loading && !library.accessLost) { Text("Rescan") } }
                    Row(Modifier.fillMaxWidth().padding(bottom = 4.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Rescan when the app opens")
                            Text("Finds new or removed MP3s if the last scan is over an hour old.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Switch(rescanOnOpen, onCheckedChange = { rescanOnOpen = it; prefs.edit().putBoolean("rescan_on_open", it).apply() }, modifier = Modifier.testTag("rescan-on-open"))
                    }
                    }
                    SettingsCard {
                    SectionLabel("Radio")
                    RadioSimilaritySettings(radio,repository.radio)
                    }
                    SettingsCard {
                    SectionLabel("Color scheme")
                    RadioPalette.entries.forEach { option ->
                        Row(Modifier.fillMaxWidth().selectable(selected = palette == option.name, role = Role.RadioButton, onClick = { palette = option.name; prefs.edit().putString("palette", palette).apply() }).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(palette == option.name, onClick = null)
                            Box(Modifier.padding(start = 8.dp).size(24.dp).background(if (option == RadioPalette.Teal) TealAccent else OceanAccent, RadioShapes.Leaf))
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
                    Text("Applies to the player and Radio. Moving drifts while music plays; it stops when paused or hidden, and follows the phone’s animation setting.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    SettingsCard {
                    SectionLabel("Opening screen")
                    listOf("Last screen", "Library", "Radio", "Now playing").forEach { option ->
                        Row(Modifier.fillMaxWidth().selectable(selected = opening == option, role = Role.RadioButton, onClick = { opening = option; prefs.edit().putString("opening", opening).apply() }).heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(opening == option, onClick = null); Text(option, Modifier.padding(start = 12.dp))
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("Minimal player"); Switch(minimal, onCheckedChange = { minimal = it; prefs.edit().putBoolean("minimal", minimal).apply() })
                    }
                    }
                    SettingsCard {
                    SectionLabel("Playback")
                    Text("Music keeps playing in the background. After a restart, your last queue returns paused. Sleep timers clear on restart.", Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    NormalizationSettings(repository.loudness, library, audio.currentId)
                    }
                    SettingsCard {
                    SectionLabel("Listening stats")
                    Text("Your listening calendar, top songs, streaks and where your music comes from. Worked out on this phone.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    TextButton(onClick = { navigate("Stats") }, modifier = Modifier.testTag("open-stats")) { Text("Open stats") }
                    }
                    SettingsCard {
                    SectionLabel("Your feels")
                    Text("Tag songs by how they feel to you, rate their energy, and run quick sprints to sort many songs.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    TextButton(onClick = { feelsNav = FeelsNav(); navigate("Feels") }, modifier = Modifier.testTag("open-feels")) { Text(if (feelsState.needsSetup) "Set up feels" else "Open feels") }
                    }
                    SettingsCard {
                    SectionLabel("Sound")
                    SoundSettings(audioSettings) { navigate("Equaliser") }
                    }
                    SettingsCard {
                    SectionLabel("Desktop")
                    Text(if (paired) "Paired with your desktop. Pairing, catalog, matching and sync live on the Server screen." else "Pair with your desktop for catalog matching, lyrics, desktop picks and sync. Playback never needs it.",
                        Modifier.padding(bottom = 8.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    FilledTonalButton(onClick = { navigate("Server") }, modifier = Modifier.padding(bottom = 8.dp).testTag("open-server")) { Text("Server") }
                    }
                    Spacer(Modifier.height(4.dp))
                }
                else -> Column(Modifier.padding(insets).padding(20.dp)) {
                    Text("Your local radio", style = MaterialTheme.typography.headlineSmall)
                    Text("Radio recommendations arrive after local playback is ready.", Modifier.padding(vertical = 16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick = { navigate("Library") }) { Text("Browse music") }
                }
            } }
            // Radio, Library and Settings sit side by side: swipe between them, or tap the bottom bar (which jumps without
            // sliding through the page in between). Swiping is off inside a folder or playlist so Back stays predictable.
            // Screens animate in briefly; the three tabs share one pager, so moving between tabs is the pager's own slide.
            AnimatedContent(if (page in tabPages) "tabs" else page, Modifier.fillMaxSize().graphicsLayer {
                    val p = backPreview(backProgress)
                    scaleX = 1f - .08f * p; scaleY = 1f - .08f * p
                    translationX = (if (backFromRight) -1f else 1f) * 24.dp.toPx() * p
                    shape = RoundedCornerShape(28.dp * p); clip = p > 0f
                }, transitionSpec = { screenTransition() }, label = "screen") { screen ->
            screenScopes[screen] = this
            if (screen == "tabs") {
                val pager = rememberPagerState(initialPage = tabPages.indexOf(page).coerceAtLeast(0)) { tabPages.size }
                // While a bottom-bar tap moves the pager, its intermediate settled page must not count as a swipe.
                var jumpTarget by remember { mutableStateOf<Int?>(null) }
                LaunchedEffect(page) {
                    val target = tabPages.indexOf(page)
                    if (target < 0 || pager.currentPage == target) return@LaunchedEffect
                    jumpTarget = target
                    try {
                        // A tap two tabs away starts next to the target, so it never flashes the page in between.
                        if (kotlin.math.abs(target - pager.currentPage) > 1) pager.scrollToPage(if (target > pager.currentPage) target - 1 else target + 1)
                        pager.animateScrollToPage(target, animationSpec = Motion.standard())
                    } finally { if (jumpTarget == target) jumpTarget = null }
                }
                LaunchedEffect(pager.settledPage, jumpTarget) {
                    if (jumpTarget != null) return@LaunchedEffect
                    val settled = tabPages[pager.settledPage]; if (page in tabPages && page != settled) navigate(settled)
                }
                HorizontalPager(pager, Modifier.fillMaxSize().testTag("tab-pager"), userScrollEnabled = !showBack) { index -> PageBody(tabPages[index]) }
            } else PageBody(screen)
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
            if (showSleep) SleepTimerDialog(liveSleep.value, playback) { showSleep = false }
            if (showLyricsSheet && page == "Now playing" && song != null) {
                val lyrics by lyricsRepository.state.collectAsState()
                val matchesCurrentSong = lyrics.songId == song.id
                val state = if (!matchesCurrentSong) LyricsSheetState(status = "LOADING", loading = true) else when (lyrics.status.name) {
                    "READY" -> LyricsSheetState(status = "READY", text = lyrics.text,
                        source = if (lyrics.fromCache) "Saved on this phone" else "From Desktop",
                        message = lyrics.message, canRefresh = true)
                    "CHECKING_MATCH" -> LyricsSheetState(status = "CHECKING_MATCH", loading = true,
                        message = lyrics.message ?: "Checking this song’s match…")
                    "UNPAIRED" -> LyricsSheetState(status = "UNPAIRED", message = lyrics.message ?: "Connect to your desktop to get lyrics.", action = "SERVER")
                    "UNMATCHED" -> LyricsSheetState(status = "UNMATCHED", message = lyrics.message ?: "Match this song to get desktop lyrics.", action = "MATCHING")
                    "UNSUPPORTED_SERVER" -> LyricsSheetState(status = "UNSUPPORTED_SERVER", message = lyrics.message ?: "Update the desktop app to get lyrics.")
                    "AUTH_REQUIRED" -> LyricsSheetState(status = "AUTH_REQUIRED", message = lyrics.message ?: "Reconnect to your desktop to get lyrics.", action = "SERVER")
                    "REVISION_UNAVAILABLE" -> LyricsSheetState(status = "REVISION_UNAVAILABLE", message = lyrics.message ?: "Prepare and import a new desktop catalog to get lyrics.", action = "SERVER")
                    "LOADING" -> LyricsSheetState(status = "LOADING", loading = true, text = lyrics.text,
                        source = if (lyrics.fromCache) "Saved on this phone" else null, message = lyrics.message)
                    "NO_LYRICS" -> LyricsSheetState(status = "NO_LYRICS", message = lyrics.message ?: "No lyrics available for this song.", canRefresh = true)
                    "ERROR" -> LyricsSheetState(status = "ERROR", text = lyrics.text,
                        source = if (lyrics.text != null) "Saved on this phone" else null,
                        message = lyrics.message ?: "Desktop unavailable. Retry to check for lyrics.", canRefresh = true)
                    else -> LyricsSheetState(status = "LOADING", loading = true)
                }
                key(song.id) {
                    LyricsSheet(song, state,
                        onRefresh = { lifecycleScope.launch { lyricsRepository.refresh() } },
                        onDismiss = { showLyricsSheet = false },
                        onServer = { showLyricsSheet = false; navigate("Server") },
                        onMatching = { showLyricsSheet = false; navigate("Matching") })
                }
            }
            if (addQueueSongs) SongPicker("Add to queue", songs, { addQueueSongs = false }) { chosen -> playback.addToQueue(chosen); addQueueSongs = false }
            if (feelSheet && song != null) FeelSheet(song, repository.feels, feelsState, onSetUp = { feelSheet = false; feelsNav = FeelsNav(); navigate("Feels") },
                onMessage = ::message, onDismiss = { feelSheet = false })
            statsSong?.let { SongStatsSheet(it, repository.journal) { statsSong = null } }
            playlistSongIds?.let { ids -> PlaylistDestinationDialog(ids, playlists, repository.playlists,
                onDismiss = { playlistSongIds = null }, onAdded = { playlistSongIds = null; message("Playlist saved") }) }
            if (endSheet) {
                val upcoming = audio.playOrder.indexOf(audio.currentId).let { if (it < 0) 0 else audio.playOrder.size - it - 1 }
                EndStationSheet(upcoming, onFinish = { endSheet = false; playback.endRadio(false) }, onStopNow = { endSheet = false; playback.endRadio(true) }, onDismiss = { endSheet = false })
            }
            if (whySheet) WhyThisSongSheet(song, audio.origins[audio.currentId], radio) { whySheet = false }
            pendingPlay?.let { pending ->
                val (item, context, source) = pending
                LeaveRadioDialog(if (pending.all) source.label.ifBlank { "your library" } else item.title, source.label.ifBlank { "your library" },
                    onLeave = { pendingPlay = null; if (pending.all) startAll(item, context, source, pending.shuffle) else { playback.playFrom(context, item.id, source); navigate("Now playing") } },
                    onPlayNext = { pendingPlay = null; playback.addToQueue(if (pending.all) context.filter { it.available } else listOf(item), true); message("Will play next in Radio") },
                    onDismiss = { pendingPlay = null })
            }
        }
    } } }
}

@Composable
private fun Artwork(size: Int) = Artwork(size.dp)

@Composable
private fun Artwork(size: Dp) {
    Box(Modifier.size(size).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(if (size > 100.dp) 12.dp else 6.dp)), contentAlignment = Alignment.Center) {
        RadioIcon(Symbol.Music, modifier = Modifier.size(size / 3))
    }
}

@Composable
internal fun LibraryRow(title: String, subtitle: String, symbol: Symbol, subtitleMaxLines: Int = 1, playing: Boolean = false, trailing: @Composable () -> Unit = {}, onClick: () -> Unit) {
    // The song that is playing now gets a filled tile and an accent title, so it is easy to spot in a long list.
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).heightIn(min = 56.dp).padding(horizontal = 20.dp, vertical = 6.dp)
        .semantics { if (playing) stateDescription = "Playing now" }, verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(40.dp).background(if (playing) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.secondaryContainer, RadioShapes.Thumb), contentAlignment = Alignment.Center) {
            CompositionLocalProvider(LocalContentColor provides if (playing) MaterialTheme.colorScheme.onPrimary else LocalContentColor.current) { RadioIcon(if (playing) Symbol.Play else symbol, modifier = Modifier.size(20.dp)) }
        }
        Column(Modifier.weight(1f).padding(start = 14.dp)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, fontWeight = if (playing) FontWeight.SemiBold else FontWeight.Medium,
                color = if (playing) MaterialTheme.colorScheme.primary else Color.Unspecified)
            Text(subtitle, maxLines = subtitleMaxLines, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        trailing()
    }
}

@Composable
private fun SectionLabel(label: String) { Text(label, Modifier.padding(top = 12.dp, bottom = 8.dp), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary) }

/** One rounded group on the Settings page. */
@Composable
private fun SettingsCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), content = content)
    }
}
/** The playback state without its per-tick fields, so equal snapshots between ticks compare equal. */
private fun withoutTicks(state: PlaybackState): PlaybackState =
    state.copy(positionMs = 0, sleepTimer = state.sleepTimer.copy(remainingMs = if (state.sleepTimer.remainingMs > 0) 1 else 0))

/** A play waiting for "Leave Radio?": one song, or a whole list played in order or shuffled. */
private data class PendingPlay(val item: LocalSong, val context: List<LocalSong>, val source: PlaySource, val all: Boolean = false, val shuffle: Boolean = false)

/** Rescan-on-open runs at most once per process. */
private object AutoRescan {
    @Volatile var done = false
    const val MIN_AGE_MS = 60 * 60 * 1000L
}

/** One part of the playback state as Compose state, updated only when that part changes. */
@Composable
private fun <T> PlaybackController.slice(part: (PlaybackState) -> T): State<T> {
    val flow = remember(this) { state.map(part).distinctUntilChanged() }
    return flow.collectAsState(currentPart(this, part))
}

/** Read outside composition: the starting value for [slice] before its flow first emits. */
private fun <T> currentPart(playback: PlaybackController, part: (PlaybackState) -> T): T = part(playback.state.value)


/** A launcher shortcut to act on once; [serial] grows with each new request. */
data class ShortcutRequest(val action: String, val serial: Int) {
    companion object {
        const val RADIO = "dev.radio.mobile.SHORTCUT_RADIO"
        const val LIKED = "dev.radio.mobile.SHORTCUT_LIKED"
        const val SHUFFLE = "dev.radio.mobile.SHORTCUT_SHUFFLE"
        const val RESUME = "dev.radio.mobile.SHORTCUT_RESUME"
        val ACTIONS = setOf(RADIO, LIKED, SHUFFLE, RESUME)
    }
}


/**
 * Repeat, sleep and lyrics under the transport, as icons like the rest of the player. While a timer runs the sleep
 * control itself shows the countdown, so there is one sleep control, not two.
 */
@Composable
private fun PlayerExtras(repeat: RepeatMode, sleep: State<SleepTimerState>, showLyrics: Boolean, onRepeat: () -> Unit, onSleep: () -> Unit, onLyrics: () -> Unit, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
        val repeatLabel = when (repeat) { RepeatMode.OFF -> "Repeat off"; RepeatMode.ALL -> "Repeat all"; RepeatMode.ONE -> "Repeat one" }
        IconToggleButton(repeat != RepeatMode.OFF, onCheckedChange = { onRepeat() }, modifier = Modifier.testTag("player-repeat").semantics { stateDescription = repeatLabel }) {
            RadioIcon(if (repeat == RepeatMode.ONE) Symbol.RepeatOne else Symbol.Repeat, "Repeat")
        }
        val timer = sleep.value
        if (timer.active) FilledTonalButton(onClick = onSleep, contentPadding = PaddingValues(horizontal = 14.dp),
            modifier = Modifier.testTag("sleep-countdown").semantics { contentDescription = "Sleep timer, ${sleepLabel(timer)}" }) {
            RadioIcon(Symbol.Moon, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text(sleepLabel(timer))
        } else IconButton(onClick = onSleep, modifier = Modifier.testTag("player-sleep")) { RadioIcon(Symbol.Moon, "Sleep timer") }
        if (showLyrics) IconButton(onClick = onLyrics, modifier = Modifier.testTag("open-lyrics-button")) { RadioIcon(Symbol.Lyrics, "Lyrics") }
    }
}

/** Now Playing with nothing queued: resume the saved session if there is one, or let Radio pick. */
@Composable
private fun NothingPlaying(repository: LibraryRepository, library: LibraryState, error: String?, onResume: () -> Unit, onRadio: () -> Unit, onBrowse: () -> Unit, modifier: Modifier = Modifier) {
    // A bookmark only survives here when restoration hasn't consumed it; Play then resumes it (see onEmptyPlay).
    val saved by produceState<String?>(null, library.loading) {
        value = runCatching { repository.playbackSnapshots.load() }.getOrNull()?.currentId?.let { id -> library.songs.firstOrNull { it.id == id && it.available }?.title }
    }
    val canPlay = !library.loading && !library.accessLost && library.songs.any { it.available }
    Column(modifier.fillMaxSize().padding(24.dp).testTag("nothing-playing"), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Box(Modifier.size(120.dp).background(MaterialTheme.colorScheme.secondaryContainer, RadioShapes.Squircle), contentAlignment = Alignment.Center) {
            CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onSecondaryContainer) { RadioIcon(Symbol.Music, modifier = Modifier.size(48.dp)) }
        }
        Spacer(Modifier.height(24.dp))
        Text("Nothing playing", style = MaterialTheme.typography.titleLarge)
        Text(if (saved != null) "Pick up where you left off, or let Radio choose." else if (canPlay) "Let Radio pick from your library, or choose a song." else "Choose a music folder to start listening.",
            Modifier.padding(top = 8.dp), color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        error?.let { Text(it, Modifier.padding(top = 12.dp), color = MaterialTheme.colorScheme.error, textAlign = androidx.compose.ui.text.style.TextAlign.Center) }
        Spacer(Modifier.height(24.dp))
        saved?.let { title ->
            Button(onClick = onResume, enabled = canPlay, modifier = Modifier.fillMaxWidth().testTag("nothing-resume")) {
                RadioIcon(Symbol.Play, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                Text("Resume · $title", maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(8.dp))
        }
        val radio: @Composable RowScope.() -> Unit = { RadioIcon(Symbol.Radio, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Start Radio") }
        if (saved == null) Button(onClick = onRadio, enabled = canPlay, modifier = Modifier.fillMaxWidth().testTag("nothing-radio"), content = radio)
        else FilledTonalButton(onClick = onRadio, enabled = canPlay, modifier = Modifier.fillMaxWidth().testTag("nothing-radio"), content = radio)
        TextButton(onClick = onBrowse, modifier = Modifier.padding(top = 4.dp)) { Text("Browse music") }
    }
}
