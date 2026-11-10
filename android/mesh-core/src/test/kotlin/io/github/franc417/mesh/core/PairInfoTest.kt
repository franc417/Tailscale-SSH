package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PairInfoTest {

    private val fp = "ab".repeat(32)
    private val tok = "cd".repeat(32)
    private val agentOutput = """{"version": 1, "port": 47600, "fingerprint": "$fp", "token": "$tok", "host": "mkz"}"""

    private fun rejected(text: String): String {
        try {
            PairInfo.parse(text)
        } catch (e: PairInfo.ParseException) {
            return e.message!!
        }
        throw AssertionError("should have been rejected: $text")
    }

    @Test
    fun `the agent's own output parses`() {
        val info = PairInfo.parse(agentOutput)
        assertEquals(PairInfo(1, 47600, fp, tok, "mkz"), info)
        assertEquals(32, info.token.size)
    }

    @Test
    fun `pasted text with a prompt line and spacing around it still parses`() {
        val pasted = "[mkz@mkz ~]$ python3 mesh_input.py pair-info\n  $agentOutput  \n[mkz@mkz ~]$ "
        assertEquals(fp, PairInfo.parse(pasted).fingerprint)
    }

    @Test
    fun `pretty printed json parses`() {
        val pretty = "{\n  \"version\": 1,\n  \"port\": 4711,\n  \"fingerprint\": \"$fp\",\n  \"token\": \"$tok\"\n}"
        val info = PairInfo.parse(pretty)
        assertEquals(4711, info.port)
        assertEquals("", info.hostName)
    }

    @Test
    fun `fingerprints are normalised`() {
        val upper = agentOutput.replace(fp, fp.uppercase())
        assertEquals(fp, PairInfo.parse(upper).fingerprint)
        val colons = agentOutput.replace(fp, (0 until 32).joinToString(":") { "AB" })
        assertEquals(fp, PairInfo.parse(colons).fingerprint)
    }

    @Test
    fun `things that are not pairing info are refused with advice`() {
        assertTrue(rejected("hello").contains("pair-info"))
        assertTrue(rejected("").contains("pair-info"))
        assertTrue(rejected("{}").contains("pair-info"))
    }

    @Test
    fun `bad values are refused with a plain reason`() {
        assertTrue(rejected(agentOutput.replace("\"token\": \"$tok\"", "\"token\": \"abcd\"")).contains("token"))
        assertTrue(rejected(agentOutput.replace(fp, "abcd")).contains("fingerprint"))
        assertTrue(rejected(agentOutput.replace("47600", "70000")).contains("port"))
        assertTrue(rejected(agentOutput.replace("47600", "0")).contains("port"))
        assertTrue(rejected(agentOutput.replace("\"version\": 1", "\"version\": 7")).contains("version"))
        assertTrue(rejected(agentOutput.replace("\"version\": 1, ", "")).contains("version"))
        assertTrue(rejected(agentOutput.replace("\"port\": 47600, ", "")).contains("port"))
        assertTrue(rejected(agentOutput.replace("g", "g").replace(tok, "z".repeat(64))).contains("token"))
    }

    @Test
    fun `the addresses the computer lists are read in order`() {
        val withAddresses = agentOutput.replace("\"host\": \"mkz\"", "\"host\": \"mkz\", \"addresses\": [\"100.1.2.3\", \"192.168.1.9\"]")
        assertEquals(listOf("100.1.2.3", "192.168.1.9"), PairInfo.parse(withAddresses).addresses)
    }

    @Test
    fun `unusable addresses in the list are dropped`() {
        val text = agentOutput.replace("\"host\": \"mkz\"", "\"host\": \"mkz\", \"addresses\": [\"0.0.0.0\", \"127.0.0.1\", \"192.168.1.9\", \"192.168.1.9\", \"not an ip\"]")
        assertEquals(listOf("192.168.1.9"), PairInfo.parse(text).addresses)
    }

    @Test
    fun `no addresses field means an empty list`() {
        assertEquals(emptyList<String>(), PairInfo.parse(agentOutput).addresses)
    }

    @Test
    fun `an empty or pretty printed list parses`() {
        assertEquals(emptyList<String>(), PairInfo.parse(agentOutput.replace("\"host\": \"mkz\"", "\"host\": \"mkz\", \"addresses\": []")).addresses)
        val pretty = "{\n \"version\": 1,\n \"port\": 47600,\n \"fingerprint\": \"$fp\",\n \"token\": \"$tok\",\n \"addresses\": [\n  \"100.1.2.3\",\n  \"10.0.0.5\"\n ]\n}"
        assertEquals(listOf("100.1.2.3", "10.0.0.5"), PairInfo.parse(pretty).addresses)
    }

    // ---- the short link in the QR code (the same string is GOLDEN_URI in tests/test_meshd.py: change both together)

    private val link = "mesh://pair?v=1&p=47600&f=q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s" +
        "&t=zc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc0&h=mkz&a=192.168.1.5,100.67.163.21"

    @Test
    fun `the compact link printed by meshd pair is read`() {
        assertEquals(PairInfo(1, 47600, fp, tok, "mkz", listOf("192.168.1.5", "100.67.163.21")), PairInfo.parse(link))
    }

    @Test
    fun `a link with text around it still parses`() {
        assertEquals(fp, PairInfo.parse("scanned: $link  (thanks)").fingerprint)
        assertEquals(tok, PairInfo.parse("\"$link\"").tokenHex)
    }

    @Test
    fun `a link without a name or addresses parses with both empty`() {
        val info = PairInfo.parse("mesh://pair?v=1&p=4711&f=q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s&t=zc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc0")
        assertEquals("", info.hostName)
        assertTrue(info.addresses.isEmpty())
        assertEquals(4711, info.port)
    }

    @Test
    fun `an escaped name in a link is unescaped`() {
        assertEquals("my pc&co", PairInfo.parse(link.replace("h=mkz", "h=my%20pc%26co")).hostName)
    }

    @Test
    fun `addresses that cannot work are dropped from a link`() {
        assertEquals(listOf("192.168.1.5"), PairInfo.parse(link.replace("a=192.168.1.5,100.67.163.21", "a=0.0.0.0,192.168.1.5,192.168.1.5")).addresses)
    }

    @Test
    fun `a link missing its token or fingerprint is refused with a plain message`() {
        assertTrue(rejected(link.replace(Regex("&t=[^&]*"), "")).contains("pairing token"))
        assertTrue(rejected(link.replace(Regex("&f=[^&]*"), "")).contains("fingerprint"))
    }

    @Test
    fun `a damaged or short value in a link is refused`() {
        assertTrue(rejected(link.replace("f=q6urq6ur", "f=!!!!q6ur")).contains("damaged"))
        assertTrue(rejected(link.replace(Regex("t=[^&]*"), "t=zc3N")).contains("32 bytes"))
    }

    @Test
    fun `a link for another protocol version names both versions`() {
        val message = rejected(link.replace("v=1", "v=9"))
        assertTrue(message.contains("version 9"))
    }

    @Test
    fun `a link with no port is refused`() {
        assertTrue(rejected(link.replace("&p=47600", "")).contains("port"))
    }

    @Test
    fun `the scanner accepts either form of pairing info and ignores anything else`() {
        assertTrue(PairInfo.looksLikePairing(link))
        assertTrue(PairInfo.looksLikePairing(agentOutput))
        assertTrue(!PairInfo.looksLikePairing("https://example.com/menu"))
        assertTrue(!PairInfo.looksLikePairing("WIFI:S:home;T:WPA;P:secret;;"))
    }
}

