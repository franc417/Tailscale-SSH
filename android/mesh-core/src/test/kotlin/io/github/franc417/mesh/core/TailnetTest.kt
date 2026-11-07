package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

class TailnetTest {

    private fun tailnet(text: String) = Tailnet.isTailnetAddress(InetAddress.getByName(text))

    @Test
    fun theWholeTailscaleIpv4RangeCounts() {
        for (ip in listOf("100.64.0.1", "100.67.163.21", "100.100.100.100", "100.127.255.254")) assertTrue(ip, tailnet(ip))
    }

    @Test
    fun theTailscaleIpv6RangeCounts() {
        assertTrue(tailnet("fd7a:115c:a1e0::1"))
        assertTrue(tailnet("fd7a:115c:a1e0:ab12:4843:cd96:6243:1234"))
    }

    @Test
    fun everythingElseDoesNot() {
        for (ip in listOf("192.168.1.5", "10.0.0.2", "172.20.10.2", "100.63.255.255", "100.128.0.1", "8.8.8.8", "127.0.0.1", "169.254.1.1")) {
            assertFalse(ip, tailnet(ip))
        }
        assertFalse(tailnet("fd00::1"))
        assertFalse(tailnet("2001:db8::1"))
    }

    @Test
    fun hostsAreFilteredByWhereTheyResolve() {
        assertEquals(listOf("100.67.163.21"), Tailnet.onlyTailnet(listOf("192.168.1.5", "100.67.163.21", "10.0.0.2")))
        assertEquals(emptyList<String>(), Tailnet.onlyTailnet(listOf("192.168.1.5", "this-name-does-not-exist.invalid")))
    }
}
