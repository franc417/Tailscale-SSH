package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ComputerFormTest {

    private fun invalid(block: () -> Unit): String {
        try {
            block()
        } catch (e: ComputerForm.Invalid) {
            return e.message!!
        }
        throw AssertionError("expected ComputerForm.Invalid")
    }

    @Test
    fun addressesCanBeSeparatedByNewlinesCommasSemicolonsAndSpaces() {
        val text = "192.168.100.4\n100.64.0.7, mkz.tailnet.example;  10.0.0.2"
        assertEquals(listOf("192.168.100.4", "100.64.0.7", "mkz.tailnet.example", "10.0.0.2"), ComputerForm.parseAddresses(text))
    }

    @Test
    fun duplicatesAreDroppedAndOrderIsKept() {
        assertEquals(listOf("10.0.0.2", "10.0.0.3"), ComputerForm.parseAddresses("10.0.0.2 10.0.0.3\n10.0.0.2"))
    }

    @Test
    fun aPortOrSchemeTypedWithAnAddressIsRemovedLikeEverywhereElse() {
        assertEquals(listOf("10.0.0.2"), ComputerForm.parseAddresses("https://10.0.0.2:8765/"))
    }

    @Test
    fun anEmptyListIsRejected() {
        assertTrue(invalid { ComputerForm.parseAddresses("  \n , ; ") }.contains("at least one"))
    }

    @Test
    fun aBadAddressIsNamedInTheMessage() {
        val message = invalid { ComputerForm.parseAddresses("10.0.0.2\n0.0.0.0") }
        assertTrue(message, message.startsWith("0.0.0.0:"))
    }

    @Test
    fun anAddressThatIsThisPhoneIsRejected() {
        assertTrue(invalid { ComputerForm.parseAddresses("localhost") }.contains("this phone"))
    }

    @Test
    fun anEmptyPortKeepsTheCurrentOne() {
        assertEquals(8765, ComputerForm.parsePort("   ", 8765))
    }

    @Test
    fun aValidPortIsAccepted() {
        assertEquals(1, ComputerForm.parsePort("1", 8765))
        assertEquals(65535, ComputerForm.parsePort(" 65535 ", 8765))
    }

    @Test
    fun badPortsAreRejectedWithAReason() {
        assertTrue(invalid { ComputerForm.parsePort("abc", 8765) }.contains("number"))
        assertTrue(invalid { ComputerForm.parsePort("0", 8765) }.contains("between"))
        assertTrue(invalid { ComputerForm.parsePort("65536", 8765) }.contains("between"))
        assertTrue(invalid { ComputerForm.parsePort("-5", 8765) }.contains("between"))
    }

    @Test
    fun nameFallsBackWhenEmptyAndIsTrimmedOtherwise() {
        assertEquals("mkz", ComputerForm.parseName("   ", "mkz"))
        assertEquals("Office PC", ComputerForm.parseName("  Office PC ", "mkz"))
    }

    @Test
    fun formattingAndParsingAreInverse() {
        val hosts = listOf("192.168.100.4", "100.64.0.7")
        assertEquals(hosts, ComputerForm.parseAddresses(ComputerForm.formatAddresses(hosts)))
    }
}
