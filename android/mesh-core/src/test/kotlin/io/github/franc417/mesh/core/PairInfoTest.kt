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
}
