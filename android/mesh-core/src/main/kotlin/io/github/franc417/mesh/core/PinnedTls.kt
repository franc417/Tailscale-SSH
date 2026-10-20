package io.github.franc417.mesh.core

import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Trusts exactly one certificate: the one whose SHA-256 fingerprint the phone learned
 * over the SSH pairing step. There is no certificate authority and no hostname to
 * check (computers are reached by IP), so this pin is what stops someone sitting
 * between the phone and the computer, even on an open network.
 */
object PinnedTls {

    fun fingerprint(cert: X509Certificate): String = sha256Hex(cert.encoded)

    fun sha256Hex(data: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(data).toHex()

    /** True if `pin` (hex, any case, optional colons) is the fingerprint of `cert`. */
    fun matches(cert: X509Certificate, pin: String): Boolean {
        val want = normalise(pin)
        return MessageDigest.isEqual(fingerprint(cert).toByteArray(), want.toByteArray())  // constant time
    }

    fun normalise(pin: String): String = pin.trim().lowercase().replace(":", "")

    fun trustManager(pin: String): X509TrustManager {
        require(normalise(pin).matches(Regex("[0-9a-f]{64}"))) { "the pinned fingerprint must be 64 hex characters" }
        return object : X509TrustManager {
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                val leaf = chain?.firstOrNull() ?: throw CertificateException("the computer sent no certificate")
                if (!matches(leaf, pin)) {
                    throw CertificateException("the computer's certificate does not match the one paired with this phone (someone may be intercepting the connection, or the computer was re-installed)")
                }
            }

            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                throw CertificateException("this side does not accept client certificates")
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
    }

    fun context(pin: String): SSLContext = SSLContext.getInstance("TLS").apply {
        init(null, arrayOf(trustManager(pin)), SecureRandom())
    }
}
