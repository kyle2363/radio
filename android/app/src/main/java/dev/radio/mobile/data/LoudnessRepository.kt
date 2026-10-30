package dev.radio.mobile.data

import android.content.Context
import android.net.Uri
import androidx.room.*
import dev.radio.mobile.music.*
import dev.radio.mobile.playback.NormalizationProfile
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

@Entity(tableName = "local_loudness")
data class LocalLoudness(@PrimaryKey val songId: String, val uri: String, val modifiedAt: Long?, val byteSize: Long?,
    val catalogScan: Long, val algorithm: String, val integratedLufs: Double?, val samplePeak: Double?, val status: String, val measuredAt: Long) {
    fun matches(song: LocalSong, scan: Long): Boolean = algorithm == LOUDNESS_ALGORITHM && uri == song.uri && modifiedAt == song.modifiedAt && byteSize == song.byteSize &&
        (song.modifiedAt != null || (song.byteSize ?: 0) > 0 || catalogScan == scan)
}

@Dao
interface LoudnessDao {
    @Query("SELECT * FROM local_loudness") fun observe(): Flow<List<LocalLoudness>>
    @Upsert suspend fun save(value: LocalLoudness)
}

data class LoudnessAnalysis(val running: Boolean = false, val completed: Int = 0, val total: Int = 0,
    val song: String = "", val positionMs: Long = 0, val failed: Int = 0, val message: String? = null)
data class LoudnessState(val enabled: Boolean = false, val measured: Int = 0, val available: Int = 0,
    val failed: Int = 0, val profile: NormalizationProfile = NormalizationProfile(), val loaded: Boolean = false)

/** Explicit foreground analysis, one read-only decoder at a time. Completed work is cached. */
class LoudnessRepository(context: Context, private val dao: LoudnessDao, private val scope: CoroutineScope, private val library: StateFlow<LibraryState>) {
    private val resolver = context.applicationContext.contentResolver
    private val prefs = context.applicationContext.getSharedPreferences("radio_audio", Context.MODE_PRIVATE)
    private val enabled = MutableStateFlow(prefs.getBoolean("normalization", false))
    private val records = dao.observe().map { it to true }.stateIn(scope, SharingStarted.Eagerly, emptyList<LocalLoudness>() to false)
    private val mutableAnalysis = MutableStateFlow(LoudnessAnalysis())
    val analysis: StateFlow<LoudnessAnalysis> = mutableAnalysis.asStateFlow()
    val state = combine(enabled, records, library) { on, saved, catalog ->
        val byId = saved.first.associateBy { it.songId }
        val available = catalog.songs.filter { it.available }
        val valid = available.mapNotNull { song -> byId[song.id]?.takeIf { it.matches(song, catalog.root?.scannedAt ?: 0) } }
        val gains = valid.filter { it.status == "measured" }.associate { it.songId to normalizationGain(it.integratedLufs) }
        LoudnessState(on, valid.count { it.status == "measured" || it.status == "silent" }, available.size,
            valid.count { it.status == "failed" }, NormalizationProfile(on, gains), loaded = saved.second && !catalog.loading)
    }.stateIn(scope, SharingStarted.Eagerly, LoudnessState(enabled.value))
    @Volatile private var job: Job? = null
    fun setEnabled(value: Boolean) { enabled.value = value; prefs.edit().putBoolean("normalization", value).apply() }
    fun pauseAnalysis() { job?.cancel() }
    @Synchronized fun analyze(songIds: Set<String>? = null) {
        if (job?.isCompleted == false) return
        val catalog = library.value
        if (catalog.loading || catalog.scanning || catalog.accessLost) {
            mutableAnalysis.value = LoudnessAnalysis(message = "Check Music folder access and finish scanning first."); return
        }
        if (!state.value.loaded) { mutableAnalysis.value = LoudnessAnalysis(message = "Opening saved loudness measurements…"); return }
        val saved = records.value.first.associateBy { it.songId }
        val scan = catalog.root?.scannedAt ?: 0L
        val candidates = catalog.songs.filter { it.available && (songIds == null || it.id in songIds) }
            .filter { song -> saved[song.id]?.let { it.matches(song, scan) && it.status != "failed" } != true }
        mutableAnalysis.value = LoudnessAnalysis(running = candidates.isNotEmpty(), total = candidates.size,
            message = if (candidates.isEmpty()) "Available songs are already measured." else null)
        if (candidates.isEmpty()) return
        val launched = scope.launch(Dispatchers.Default) {
            var completed = 0; var failed = 0
            try {
                val analyzer = LocalLoudnessAnalyzer(resolver)
                for (song in candidates) {
                    ensureActive()
                    val current = library.value.songs.firstOrNull { it.id == song.id }
                    if (library.value.accessLost || current == null || !current.available || current.uri != song.uri || current.modifiedAt != song.modifiedAt || current.byteSize != song.byteSize) continue
                    mutableAnalysis.value = LoudnessAnalysis(true, completed, candidates.size, song.title, failed = failed)
                    val record = try {
                        val result = analyzer.analyze(Uri.parse(song.uri)) { ms -> mutableAnalysis.update { it.copy(positionMs = ms) } }
                        LocalLoudness(song.id, song.uri, song.modifiedAt, song.byteSize, scan, LOUDNESS_ALGORITHM,
                            result.integratedLufs, result.samplePeak, if (result.integratedLufs == null) "silent" else "measured", System.currentTimeMillis())
                    } catch (cancelled: CancellationException) { throw cancelled }
                    catch (_: Exception) {
                        failed++
                        LocalLoudness(song.id, song.uri, song.modifiedAt, song.byteSize, scan, LOUDNESS_ALGORITHM, null, null, "failed", System.currentTimeMillis())
                    }
                    ensureActive()
                    dao.save(record)
                    completed++
                    mutableAnalysis.update { it.copy(completed = completed, failed = failed) }
                    yield()
                }
                mutableAnalysis.value = LoudnessAnalysis(completed = completed, total = candidates.size, failed = failed,
                    message = "Analysis complete. $completed checked" + if (failed > 0) " · $failed could not be measured." else ".")
            } catch (cancelled: CancellationException) {
                mutableAnalysis.update { it.copy(running = false, message = "Analysis paused. Completed measurements are saved.") }
                throw cancelled
            } catch (_: Exception) {
                mutableAnalysis.update { it.copy(running = false, message = "Couldn't save loudness measurements. Try again.") }
            }
        }
        job = launched
        launched.invokeOnCompletion { cause ->
            // Cancellation before the coroutine starts must also clear its running UI state.
            if (cause is CancellationException && job === launched) mutableAnalysis.update {
                it.copy(running = false, message = "Analysis paused. Completed measurements are saved.")
            }
        }
    }
}
