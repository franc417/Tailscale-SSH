package io.github.franc417.mesh.stream

import android.content.Context
import android.util.Base64
import java.io.ByteArrayInputStream
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

/**
 * Remembers the certificate each paired computer presented while pairing, so later HTTPS requests can pin to it.
 * Keyed by the computer's unique id (not its address), so the same computer reached over the LAN, the USB cable or
 * Tailscale is recognised as paired.
 */
class ServerCertStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("mesh_stream_certs", Context.MODE_PRIVATE)

    fun get(hostId: String): X509Certificate? {
        val encoded = prefs.getString(KEY_PREFIX + hostId, null) ?: return null
        return try {
            CertificateFactory.getInstance("X.509")
                .generateCertificate(ByteArrayInputStream(Base64.decode(encoded, Base64.NO_WRAP))) as X509Certificate
        } catch (e: Exception) {
            null // a damaged entry behaves like "not paired", which leads to pairing again
        }
    }

    fun put(hostId: String, cert: X509Certificate) {
        prefs.edit().putString(KEY_PREFIX + hostId, Base64.encodeToString(cert.encoded, Base64.NO_WRAP)).apply()
    }

    fun remove(hostId: String) {
        prefs.edit().remove(KEY_PREFIX + hostId).apply()
    }

    private companion object {
        const val KEY_PREFIX = "cert:"
    }
}
