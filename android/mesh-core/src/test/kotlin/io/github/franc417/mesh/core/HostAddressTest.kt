package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket

class HostAddressTest {

    /** A server that accepts connections on 127.0.0.1 ONLY (a plain ServerSocket(0) listens on every local address). */
    private fun onlyLoopback() = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))

    private fun rejected(input: String): String {
        try {
            HostAddress.parse(input)
        } catch (e: HostAddress.Invalid) {
            return e.message!!
        }
        throw AssertionError("should have been rejected: '$input'")
    }

    @Test
    fun `ordinary addresses and names are accepted`() {
        assertEquals("192.168.1.5", HostAddress.parse("192.168.1.5"))
        assertEquals("100.101.102.103", HostAddress.parse("  100.101.102.103  "))
        assertEquals("mkz", HostAddress.parse("mkz"))
        assertEquals("mkz.tail1234.ts.net", HostAddress.parse("mkz.tail1234.ts.net"))
        assertEquals("my-pc.local", HostAddress.parse("my-pc.local"))
    }

    @Test
    fun `a scheme, a trailing slash and a port are removed`() {
        assertEquals("192.168.1.5", HostAddress.parse("ws://192.168.1.5:47600/"))
        assertEquals("mkz", HostAddress.parse("tcp://mkz:8000"))
        assertEquals("192.168.1.5", HostAddress.parse("192.168.1.5:47600"))
        assertEquals("mkz", HostAddress.parse("HTTPS://mkz"))
    }

    @Test
    fun `the wildcard that caused the first failure is refused with an explanation`() {
        val message = rejected("0.0.0.0")
        assertTrue(message, message.contains("every address"))
        assertTrue(rejected("0.0.0.0:47600").contains("every address"))
    }

    @Test
    fun `addresses of the phone itself are refused`() {
        for (bad in listOf("localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1", "::")) {
            assertTrue(bad, rejected(bad).contains("this phone itself"))
        }
    }

    @Test
    fun `link local, ipv6 and malformed input are refused with a reason`() {
        assertTrue(rejected("169.254.1.1").contains("link-local"))
        assertTrue(rejected("fe80::1").contains("IPv6"))
        assertTrue(rejected("").contains("enter"))
        assertTrue(rejected("   ").contains("enter"))
        assertTrue(rejected("my pc").contains("spaces"))
        assertTrue(rejected("300.1.1.1").contains("not a valid IP"))
        assertTrue(rejected("-bad-.example").contains("not a valid"))
        assertTrue(rejected("a_b").contains("not a valid"))
    }

    @Test
    fun `isUsable agrees with parse`() {
        assertTrue(HostAddress.isUsable("10.0.0.1"))
        assertTrue(!HostAddress.isUsable("0.0.0.0"))
        assertTrue(!HostAddress.isUsable("garbage here"))
    }

    // ---- which address answers

    @Test
    fun `the reachable address is found among ones that do not answer`() {
        onlyLoopback().use { server ->
            val port = server.localPort
            // 127.0.0.2 is loopback too but nothing listens there on this port, so it is refused at once.
            assertEquals("127.0.0.1", Reach.firstReachable(listOf("127.0.0.2", "127.0.0.1"), port, 1000))
        }
    }

    @Test
    fun `nothing reachable gives null`() {
        val closed = ServerSocket(0).use { it.localPort }
        assertNull(Reach.firstReachable(listOf("127.0.0.1", "127.0.0.2"), closed, 500))
        assertNull(Reach.firstReachable(emptyList(), 1234, 500))
    }

    @Test
    fun `a slow unreachable address does not delay the working one`() {
        onlyLoopback().use { server ->
            val start = System.nanoTime()
            // 10.255.255.1 normally just times out (or fails at once where there is no route): either way it must not hold things up.
            val found = Reach.firstReachable(listOf("10.255.255.1", "127.0.0.1"), server.localPort, 3000)
            val millis = (System.nanoTime() - start) / 1_000_000
            assertEquals("127.0.0.1", found)
            assertTrue("took $millis ms", millis < 2000)
        }
    }

    @Test
    fun `the first host that answers wins even if it is later in the list`() {
        onlyLoopback().use { server ->
            assertEquals("127.0.0.1", Reach.firstReachable(listOf("127.0.0.3", "127.0.0.4", "127.0.0.1"), server.localPort, 1000))
        }
    }
}
