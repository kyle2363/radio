package dev.radio.mobile.connection

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class StoredPairing(val record: PairingRecord? = null, val quarantined: Boolean = false)
interface PairingStore {
    fun load(): StoredPairing
    fun installationMarker(): String
    fun save(record: PairingRecord)
    fun forget()
}

/** The identity expectation is backed up; secrets and installation marker deliberately are not. */
class PairedServerStore(context: Context, private val policy: EndpointPolicy, storageNamespace: String? = null) : PairingStore {
    init { require(storageNamespace == null || storageNamespace.matches(Regex("[a-z0-9_]{1,64}"))) }
    private val app = context.applicationContext
    private val suffix = storageNamespace?.let { "_$it" } ?: ""
    private val identity = app.getSharedPreferences("radio_connection_identity$suffix", Context.MODE_PRIVATE)
    private val markerFile = File(app.noBackupFilesDir, "radio_installation_marker$suffix")
    private val credentialFile = AtomicFile(File(app.filesDir, "mobile_credentials/${storageNamespace?.let { "$it/" } ?: ""}pairing.enc"))
    private val keyAlias = "radio.mobile.pairing.aes.v1$suffix"
    private val aad = "radio-mobile-pairing-v1".toByteArray(Charsets.UTF_8)

    private fun existingKey(): SecretKey? = (KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.getKey(keyAlias, null) as? SecretKey)
    private fun encryptionKey(): SecretKey = existingKey() ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
        init(KeyGenParameterSpec.Builder(keyAlias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256).setRandomizedEncryptionRequired(true).build())
        generateKey()
    }
    private fun currentMarker(): String? = runCatching { WireBase64.secret(markerFile.readText(Charsets.UTF_8)) }.getOrNull()

    @Synchronized override fun load(): StoredPairing {
        val expected = identity.getString("installation_marker", null)
        if (expected == null) return StoredPairing(quarantined = credentialFile.baseFile.exists())
        val actual = currentMarker()
        if (installationTrust(expected, actual, true) != InstallTrust.Valid) return StoredPairing(quarantined = true)
        return try {
            val key = existingKey() ?: return StoredPairing(quarantined = true)
            val bytes = credentialFile.readFully()
            if (bytes.size !in 30..32768 || bytes[0] != 1.toByte()) return StoredPairing(quarantined = true)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, bytes.copyOfRange(1, 13)))
            cipher.updateAAD(aad)
            val record = PairingRecord.parse(cipher.doFinal(bytes.copyOfRange(13, bytes.size)).toString(Charsets.UTF_8), policy)
            if (record.installationMarker != actual || record.identityAnchor != identity.getString("identity_anchor", null)) StoredPairing(quarantined = true)
            else StoredPairing(record)
        } catch (_: Exception) { StoredPairing(quarantined = true) }
    }

    @Synchronized override fun installationMarker(): String {
        if (load().quarantined) throw ConnectionFailure("Restored or inaccessible pairing is quarantined. Forget pairing and pair again.")
        currentMarker()?.let { return it }
        val value = WireBase64.encode(ByteArray(32).also { SecureRandom().nextBytes(it) })
        val file = AtomicFile(markerFile)
        val stream = file.startWrite()
        try { stream.write(value.toByteArray(Charsets.UTF_8)); file.finishWrite(stream) }
        catch (error: Exception) { file.failWrite(stream); throw ConnectionFailure("Cannot create secure installation identity.") }
        return value
    }

    @Synchronized override fun save(record: PairingRecord) {
        try {
            if (identity.contains("installation_marker") && load().quarantined) throw ConnectionFailure("Pairing storage is quarantined. Forget pairing and pair again.")
            if (record.installationMarker != currentMarker()) throw ConnectionFailure("Installation identity changed. Repair pairing.")
            val oldAnchor = identity.getString("identity_anchor", null)
            if (oldAnchor != null && oldAnchor != record.identityAnchor) throw ConnectionFailure("Forget the existing pairing before pairing another server.")
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, encryptionKey())
            cipher.updateAAD(aad)
            val encrypted = cipher.doFinal(record.toJson().toString().toByteArray(Charsets.UTF_8))
            // Commit the nonsecret expectation first: any interrupted credential write fails closed.
            if (!identity.edit().putString("installation_marker", record.installationMarker).putString("identity_anchor", record.identityAnchor).commit())
                throw ConnectionFailure("Cannot persist pairing identity.")
            credentialFile.baseFile.parentFile?.mkdirs()
            val stream = credentialFile.startWrite()
            try { stream.write(byteArrayOf(1) + cipher.iv + encrypted); credentialFile.finishWrite(stream) }
            catch (error: Exception) { credentialFile.failWrite(stream); throw error }
        } catch (failure: ConnectionFailure) { throw failure }
        catch (_: Exception) { throw ConnectionFailure("Secure credential storage is unavailable. Pairing was not sent.") }
    }

    @Synchronized override fun forget() {
        // Leave local library/history and this installation's marker untouched.
        credentialFile.delete()
        if (!identity.edit().clear().commit()) throw ConnectionFailure("Cannot forget pairing. Try again.")
        runCatching { KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry(keyAlias) } }
    }
}
