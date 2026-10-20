package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PinnedTlsTest {

    @Test
    fun `a pin must be 64 hex characters`() {
        for (bad in listOf("", "abc", "g".repeat(64), "a".repeat(63))) {
            var rejected = false
            try { PinnedTls.trustManager(bad) } catch (e: IllegalArgumentException) { rejected = true }
            assertTrue("'$bad' should be rejected", rejected)
        }
        PinnedTls.trustManager("a".repeat(64))
    }

    @Test
    fun `pins are normalised so copied formats work`() {
        val hex = "AB".repeat(32)
        assertEquals("ab".repeat(32), PinnedTls.normalise(hex))
        assertEquals("ab".repeat(32), PinnedTls.normalise((0 until 32).joinToString(":") { "AB" }))
        assertEquals("ab".repeat(32), PinnedTls.normalise("  $hex\n"))
    }

    @Test
    fun `sha256 matches the known answer`() {
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", PinnedTls.sha256Hex("abc".toByteArray()))
    }

    @Test
    fun `the trust manager refuses a missing certificate chain and any client certificate`() {
        val tm = PinnedTls.trustManager("a".repeat(64))
        var refusedServer = false
        var refusedClient = false
        try { tm.checkServerTrusted(null, "ECDHE") } catch (e: java.security.cert.CertificateException) { refusedServer = true }
        try { tm.checkClientTrusted(null, "ECDHE") } catch (e: java.security.cert.CertificateException) { refusedClient = true }
        assertTrue(refusedServer)
        assertTrue(refusedClient)
        assertFalse(tm.acceptedIssuers.isNotEmpty())
    }
}
