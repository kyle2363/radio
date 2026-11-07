package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import dev.radio.mobile.BuildConfig
import dev.radio.mobile.connection.*
import dev.radio.mobile.portable.CatalogRepository
import dev.radio.mobile.portable.MatchingRepository
import dev.radio.mobile.data.PortableSyncRepository
import kotlinx.coroutines.launch

@Composable
fun ServerSettings(repository: PairingRepository? = null, matchingScreen: Boolean = false, onOpenMatching: () -> Unit = {}, discoveryRepository: DiscoveryRepository? = null, radioRepository: dev.radio.mobile.recommendation.RadioRepository? = null) {
    val context = LocalContext.current.applicationContext
    val connection = remember(repository, context) {
        repository ?: EndpointPolicy(BuildConfig.DEBUG).let { policy -> PairingRepository(PairedServerStore(context, policy), RadioApiClient(policy), policy) }
    }
    val state by connection.state.collectAsState()
    val catalog = remember(context, connection) { CatalogRepository.get(context, connection) }
    val imported by catalog.state.collectAsState()
    val matching = remember(context) { MatchingRepository.get(context) }
    val matches by matching.state.collectAsState()
    val scope = rememberCoroutineScope()
    val discovery = remember(context, connection, discoveryRepository) { discoveryRepository ?: DiscoveryRepository(context, connection) }
    val discoveryState by discovery.state.collectAsState()
    val pairingForm = remember { BringIntoViewRequester() }
    var bundleFocused by remember { mutableStateOf(false) }
    val imeBottom = WindowInsets.ime.getBottom(LocalDensity.current)
    // Bundle text contains a one-time secret: never persist it with rememberSaveable.
    var bundle by remember { mutableStateOf("") }
    var endpointDescriptor by remember { mutableStateOf("") }
    var showEndpointUpdate by remember { mutableStateOf(false) }
    LaunchedEffect(connection) { connection.load() }
    LaunchedEffect(discovery) { discovery.load() }
    LaunchedEffect(catalog) { catalog.load() }
    LaunchedEffect(matching, imported.ready) { matching.load() }
    // Scroll the entire form after IME resizing, including the action below the
    // focused field. Text-field relocation alone can leave Pair under the IME.
    LaunchedEffect(bundleFocused, imeBottom) {
        if (bundleFocused && imeBottom > 0) pairingForm.bringIntoView()
    }
    if (matchingScreen) {
        MatchingReviewSection(matches.results, matches.busy,
            enabled = matches.loaded && imported.loaded && imported.ready != null && !imported.busy && !state.busy,
            progress = matches.progress, error = matches.error,
            onMatch = { scope.launch { matching.matchLocalSongs() } },
            onConfirm = { local, song -> scope.launch { matching.confirm(local, song) } },
            onReject = { local, song -> scope.launch { matching.reject(local, song) } },
            onReset = { local, song -> scope.launch { matching.clearDecision(local, song) } },
            onCancel = { matching.cancel() },
            onConfirmSelected = { chosen -> scope.launch { matching.batchConfirm(chosen) } })
        return
    }
    val synchronizer = remember(context, connection) { PortableSyncRepository.get(context, connection) }
    val sync by synchronizer.state.collectAsState()
    LaunchedEffect(synchronizer, state.serverId, state.paired) { synchronizer.load() }
    Column(Modifier.fillMaxWidth().padding(top = 20.dp).testTag("server-settings")) {
        Text("Server", style = MaterialTheme.typography.titleSmall)
        Text("Pair for a verified server connection. Local music works independently.", Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        state.serverId?.let { Text("Server: $it", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall) }
        state.endpoint?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        Text(state.message, Modifier.padding(vertical = 8.dp).testTag("server-status"), style = MaterialTheme.typography.bodySmall)
        if (!state.paired && !state.pending && !state.quarantined) {
            Column(Modifier.fillMaxWidth().bringIntoViewRequester(pairingForm)) {
                OutlinedTextField(value = bundle, onValueChange = { if (it.length <= 8192) bundle = it },
                    label = { Text("One-time pairing bundle") }, modifier = Modifier.fillMaxWidth().onFocusChanged { bundleFocused = it.isFocused }.testTag("pairing-bundle"),
                    visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
                    singleLine = true, enabled = state.loaded && !state.busy && !imported.busy && !matches.busy && !sync.busy)
                TextButton(onClick = { val submitted = bundle; bundle = ""; scope.launch { connection.pair(submitted) } },
                    enabled = state.loaded && !state.busy && !imported.busy && !matches.busy && !sync.busy && bundle.isNotBlank(), modifier = Modifier.testTag("pair-server")) { Text("Pair") }
            }
        }
        if (state.paired || state.pending || state.quarantined) {
            Row {
                if (!state.quarantined) TextButton(onClick = { scope.launch { discovery.readDuringSession(); connection.check(); if (connection.state.value.error) discovery.readAfterConnectionError() } }, enabled = !state.busy && !state.blocked && !imported.busy && !matches.busy && !sync.busy) {
                    Text(if (state.pending) "Retry pairing" else "Check connection")
                }
                TextButton(onClick = { bundle = ""; scope.launch { connection.forget() } }, enabled = !state.busy && !imported.busy && !matches.busy && !sync.busy) { Text("Forget pairing") }
            }
        }
        if (!state.quarantined) DiscoverySettings(discovery, discoveryState)
        if (state.paired && !state.quarantined) {
            TextButton(onClick = { showEndpointUpdate = !showEndpointUpdate; endpointDescriptor = "" },
                enabled = !state.busy && !imported.busy && !matches.busy && !sync.busy,
                modifier = Modifier.testTag("show-endpoint-update")) { Text("Update server address") }
            if (showEndpointUpdate) {
                Text("Paste the current signed descriptor from the trusted desktop. The saved pairing and local data stay on this phone.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(value = endpointDescriptor, onValueChange = { if (it.length <= 16384) endpointDescriptor = it },
                    label = { Text("Signed server descriptor") }, modifier = Modifier.fillMaxWidth().testTag("endpoint-descriptor"),
                    visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
                    singleLine = true, enabled = !state.busy && !imported.busy && !matches.busy && !sync.busy)
                TextButton(onClick = { val submitted = endpointDescriptor; endpointDescriptor = ""; showEndpointUpdate = false; scope.launch { connection.updateConnection(submitted) } },
                    enabled = endpointDescriptor.isNotBlank() && !state.busy && !imported.busy && !matches.busy && !sync.busy,
                    modifier = Modifier.testTag("verify-update-endpoint")) { Text("Verify and update") }
            }
            if (BuildConfig.DEBUG && state.trustedUsb && state.endpoint != "http://127.0.0.1:18766") {
                TextButton(onClick = { scope.launch { connection.returnToTrustedUsb() } },
                    enabled = !state.busy && !imported.busy && !matches.busy && !sync.busy,
                    modifier = Modifier.testTag("return-trusted-usb")) { Text("Reconnect over trusted USB") }
            }
        }
        if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        ProfileSyncSection(sync.busy, sync.pendingEvents, sync.lastSuccessAt, sync.message, sync.error,
            enabled = state.loaded && state.paired && !state.pending && !state.quarantined && !state.blocked && !state.busy && !imported.busy && !matches.busy,
            onSync = { scope.launch { discovery.readDuringSession(); synchronizer.syncNow(); if(sync.error != null) discovery.readAfterConnectionError() } })
        if (radioRepository != null) {
            val connectedUi by radioRepository.connectedState.collectAsState()
            ConnectedRadioSection(connectedUi.status, paired = state.paired && !state.blocked && !state.quarantined,
                onToggle = { radioRepository.setConnectedEnabled(it) }, onCheck = { scope.launch { radioRepository.connected.checkAccess() } },
                summary = connectedUi.set?.let(dev.radio.mobile.recommendation.ConnectedPolicy::summary))
        }
        Text("Catalog", Modifier.padding(top = 12.dp), style = MaterialTheme.typography.titleSmall)
        val ready = imported.ready
        if (ready != null) {
            Text("Revision ${ready.revision} · ${ready.songCount} songs · ${ready.vectorCount} vectors", Modifier.padding(top = 6.dp).testTag("imported-catalog"), style = MaterialTheme.typography.bodySmall)
            if (state.serverId != null && state.serverId != ready.serverId) Text("Stored catalog is from a different server.", style = MaterialTheme.typography.bodySmall)
        } else Text(if (imported.loaded) "No imported catalog" else "Reading imported catalog…", Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall)
        Text("Imports metadata and vectors for local matching and offline Radio.", Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (imported.busy) {
            Text(imported.progress, Modifier.padding(top = 8.dp).testTag("catalog-progress"), style = MaterialTheme.typography.bodySmall)
            LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 6.dp))
            TextButton(onClick = { catalog.cancelImport() }, modifier = Modifier.testTag("cancel-catalog-import")) { Text("Cancel import") }
        } else {
            TextButton(onClick = { scope.launch { catalog.importCatalog() } }, enabled = imported.loaded && state.loaded && state.paired && !state.busy && !state.pending && !state.blocked && !state.quarantined && !matches.busy && !sync.busy,
                modifier = Modifier.testTag("import-catalog")) { Text("Import catalog") }
        }
        imported.error?.let { Text(it, Modifier.padding(top = 6.dp).testTag("catalog-error"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        imported.message?.let { Text(it, Modifier.padding(top = 6.dp).testTag("catalog-message"), style = MaterialTheme.typography.bodySmall) }
        TextButton(onClick = onOpenMatching, modifier = Modifier.testTag("open-matching")) { Text("Local matching") }
        Text("Review and confirm matches on the Matching screen.", style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(16.dp))
    }
}
