package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.net.ServerSocket
import java.nio.file.Files
import java.util.Collections
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Runs the REAL host agent (mesh_input.py, in --dry-run so it prints each event instead of
 * injecting it) and drives it with this library over real TLS. It proves the two sides
 * interoperate: framing, the certificate pin, the pairing token, takeover, keep-alive and
 * the release-everything-on-disconnect safety. Skipped automatically where python3 or
 * openssl are missing.
 */
class MipIntegrationTest {

    private val script = listOf(File("../../mesh_input.py"), File("mesh_input.py")).firstOrNull { it.exists() }

    private fun available(vararg cmd: String): Boolean = try {
        val p = ProcessBuilder(*cmd).redirectErrorStream(true).start()
        p.inputStream.readBytes()
        p.waitFor(5, TimeUnit.SECONDS) && p.exitValue() == 0
    } catch (e: Exception) {
        false
    }

    private val canRun: Boolean by lazy { script != null && available("python3", "--version") && available("openssl", "version") }

    private class Agent(val process: Process, val dir: File, val port: Int, val fingerprint: String, val token: ByteArray) {
        val lines: MutableList<String> = Collections.synchronizedList(mutableListOf())

        fun waitFor(timeoutMs: Long = 5000, predicate: (List<String>) -> Boolean): Boolean {
            val end = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < end) {
                if (predicate(synchronized(lines) { lines.toList() })) return true
                Thread.sleep(25)
            }
            return false
        }

        fun events(): List<String> = synchronized(lines) { lines.filter { it.startsWith("EVENT ") }.map { it.removePrefix("EVENT ") } }

        fun stop() {
            process.destroy()
            process.waitFor(3, TimeUnit.SECONDS)
            dir.deleteRecursively()
        }
    }

    private fun startAgent(): Agent {
        val dir = Files.createTempDirectory("mesh-agent-test").toFile()
        val port = ServerSocket(0).use { it.localPort }
        val info = ProcessBuilder("python3", script!!.path, "pair-info", "--dir", dir.path, "--port", port.toString())
            .redirectErrorStream(true).start().let { p ->
                val out = p.inputStream.bufferedReader().readText()
                p.waitFor(10, TimeUnit.SECONDS)
                out
            }
        val fingerprint = Regex("\"fingerprint\": \"([0-9a-f]{64})\"").find(info)!!.groupValues[1]
        val token = Regex("\"token\": \"([0-9a-f]{64})\"").find(info)!!.groupValues[1].hexToBytes()
        val process = ProcessBuilder("python3", script.path, "run", "--dry-run", "--dir", dir.path, "--port", port.toString())
            .redirectErrorStream(true).start()
        val agent = Agent(process, dir, port, fingerprint, token)
        Thread {
            try {
                process.inputStream.bufferedReader().forEachLine { agent.lines.add(it) }
            } catch (e: java.io.IOException) {
                // the agent was stopped and its output closed: expected at the end of a test
            }
        }.apply { isDaemon = true }.start()
        assertTrue("the agent did not start: ${agent.lines}", agent.waitFor(10000) { l -> l.any { "listening" in it } })
        return agent
    }

    private fun config(a: Agent, token: ByteArray = a.token, pin: String = a.fingerprint, ping: Long = 5000, idle: Int = 15000, name: String = "test phone") =
        MipClient.Config("127.0.0.1", a.port, token, pin, name, pingIntervalMs = ping, idleTimeoutMs = idle)

    private class Recorder : MipClient.Listener {
        val errors = CopyOnWriteArrayList<Pair<Int, String>>()
        val disconnects = CopyOnWriteArrayList<String>()
        val rtts = CopyOnWriteArrayList<Long>()
        override fun onError(code: Int, text: String) { errors.add(code to text) }
        override fun onDisconnected(reason: String) { disconnects.add(reason) }
        override fun onRtt(millis: Long) { rtts.add(millis) }
    }

    private fun waitUntil(timeoutMs: Long = 5000, cond: () -> Boolean): Boolean {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            if (cond()) return true
            Thread.sleep(25)
        }
        return false
    }

    /** In CI (MESH_REQUIRE_INTEGRATION=1) a missing python3/openssl is a failure, never a silent pass. */
    private val required = System.getenv("MESH_REQUIRE_INTEGRATION") == "1"

    private fun withAgent(block: (Agent) -> Unit) {
        if (!canRun) {
            check(!required) { "the live agent tests are required here but python3, openssl or mesh_input.py is missing" }
            return
        }
        val agent = startAgent()
        try { block(agent) } finally { agent.stop() }
    }

    private fun moveSum(agent: Agent, axis: Int): Int = agent.events().filter { it.startsWith("[\"move\"") }.sumOf {
        it.trim('[', ']').split(",").map { s -> s.trim() }[axis].toInt()
    }

    @Test
    fun `a paired phone connects and learns the host's name and features`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        val hello = client.connect()
        assertEquals(Mip.VERSION, hello.version)
        assertTrue(hello.features and Mip.FEATURE_TEXT != 0 && hello.features and Mip.FEATURE_HIRES_SCROLL != 0)
        assertTrue(hello.features and Mip.FEATURE_SUNSHINE_PAIR != 0)
        assertTrue(client.canPairSunshine)
        assertTrue(hello.hostName.isNotEmpty())
        assertTrue(client.isConnected)
        client.close()
    }

    @Test
    fun `the real agent answers a sunshine pairing request, here with how to set the login up`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        val answer = AtomicReference<HostMessage.SunshineResult?>(null)
        client.pairSunshine("1234", "Test phone") { answer.set(it) }
        val end = System.currentTimeMillis() + 8000
        while (answer.get() == null && System.currentTimeMillis() < end) Thread.sleep(25)
        val result = answer.get()
        assertNotNull("the agent never answered", result)
        // This agent has no saved Sunshine login, so the answer says how to get one; nothing was sent to a Sunshine.
        assertEquals(Mip.SUNSHINE_NOT_CONFIGURED, result!!.status)
        assertTrue(result.text, result.text.contains("sunshine-login"))
        assertTrue("the channel still works afterwards", client.isConnected)
        client.close()
    }

    @Test
    fun `keys buttons and text arrive as real events in order`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        client.key("ControlLeft", true)
        client.key("KeyC", true)
        client.key("KeyC", false)
        client.key("ControlLeft", false)
        client.button(Mip.BUTTON_LEFT, true)
        client.button(Mip.BUTTON_LEFT, false)
        client.text("ok")
        assertTrue(agent.waitFor { l -> l.count { it.startsWith("EVENT") } >= 10 })
        val e = agent.events()
        assertEquals(listOf("[\"key\", \"ControlLeft\", true]", "[\"key\", \"KeyC\", true]", "[\"key\", \"KeyC\", false]", "[\"key\", \"ControlLeft\", false]",
            "[\"button\", 1, true]", "[\"button\", 1, false]"), e.take(6))
        assertEquals("[\"key\", \"KeyO\", true]", e[6])
        assertTrue(e.contains("[\"key\", \"KeyK\", false]"))
        client.close()
    }

    @Test
    fun `slow sub pixel motion still moves the cursor and nothing is lost`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        repeat(50) { client.move(0.3f, -0.2f) }
        assertTrue(agent.waitFor { l -> l.count { it.startsWith("EVENT [\"move\"") } >= 1 })
        Thread.sleep(300)
        assertTrue(moveSum(agent, 1) in 14..15)
        assertTrue(moveSum(agent, 2) in -10..-9)
        client.close()
    }

    @Test
    fun `a flood of moves is summed not lost`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        repeat(5000) { client.move(1f, 1f) }
        assertTrue(waitUntil(8000) { moveSum(agent, 1) >= 4999 })
        assertEquals(5000, moveSum(agent, 1))
        assertEquals(5000, moveSum(agent, 2))
        client.close()
    }

    @Test
    fun `motion sent before a click arrives before it`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        client.move(7f, 0f)
        client.button(Mip.BUTTON_LEFT, true)
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"button\"") } })
        val e = agent.events()
        assertTrue("move must precede the click: $e", e.indexOfFirst { it.startsWith("[\"move\"") } < e.indexOfFirst { it.startsWith("[\"button\"") })
        client.close()
    }

    @Test
    fun `scroll reaches the host`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        client.scroll(1.5f, -0.5f)
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"scroll\", 1.5, -0.5") } })
        client.close()
    }

    @Test
    fun `long unicode text is delivered whole`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        client.text("a".repeat(2500))   // more than one frame
        assertTrue(waitUntil(15000) { agent.events().count { it == "[\"key\", \"KeyA\", true]" } >= 2500 })
        assertEquals(2500, agent.events().count { it == "[\"key\", \"KeyA\", true]" })
        client.close()
    }

    @Test
    fun `a wrong token is refused with a clear reason and nothing is injected`() = withAgent { agent ->
        val client = MipClient(config(agent, token = ByteArray(32)), Recorder())
        var error: MipException? = null
        try { client.connect() } catch (e: MipException) { error = e }
        assertNotNull(error)
        assertEquals(Mip.E_BAD_TOKEN, error!!.code)
        assertTrue(error.message!!.contains("pair again"))
        Thread.sleep(200)
        assertTrue(agent.events().isEmpty())
    }

    @Test
    fun `a certificate that does not match the pin is refused before anything is sent`() = withAgent { agent ->
        val client = MipClient(config(agent, pin = "0".repeat(64)), Recorder())
        var error: MipException? = null
        try { client.connect() } catch (e: MipException) { error = e }
        assertNotNull(error)
        assertTrue("message was: ${error!!.message}", error.message!!.contains("does not match"))
        Thread.sleep(200)
        assertTrue(agent.events().isEmpty())
        assertFalse(agent.lines.any { "connected" in it && "disconnected" !in it })   // it never got as far as HELLO
    }

    @Test
    fun `nothing listening gives a plain error`() {
        if (!canRun) {
            check(!required) { "the live agent tests are required here but python3, openssl or mesh_input.py is missing" }
            return
        }
        val port = ServerSocket(0).use { it.localPort }
        var error: MipException? = null
        try {
            MipClient(MipClient.Config("127.0.0.1", port, ByteArray(32), "a".repeat(64), "p", connectTimeoutMs = 1000), Recorder()).connect()
        } catch (e: MipException) { error = e }
        assertNotNull(error)
        assertTrue(error!!.message!!.contains("could not reach"))
    }

    @Test
    fun `a second phone takes over and the first is told, with its held keys released`() = withAgent { agent ->
        val first = Recorder()
        val a = MipClient(config(agent, name = "first"), first)
        a.connect()
        a.key("ShiftLeft", true)
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"ShiftLeft\", true") } })
        val b = MipClient(config(agent, name = "second"), Recorder())
        b.connect()
        assertTrue(waitUntil { first.errors.isNotEmpty() })
        assertEquals(Mip.E_REPLACED, first.errors[0].first)
        assertTrue(waitUntil { first.disconnects.isNotEmpty() })
        assertTrue(first.disconnects[0].contains("took over"))
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"ShiftLeft\", false") } })   // not stuck down
        b.key("KeyA", true)
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"KeyA\", true") } })
        b.close()
    }

    @Test
    fun `closing the session releases whatever was held`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        client.key("ControlLeft", true)
        client.button(Mip.BUTTON_LEFT, true)
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"button\", 1, true") } })
        client.close()
        assertTrue(agent.waitFor { l -> l.any { it.contains("\"ControlLeft\", false") } && l.any { it.contains("\"button\", 1, false") } })
    }

    @Test
    fun `the round trip time is measured`() = withAgent { agent ->
        val rec = Recorder()
        val client = MipClient(config(agent, ping = 100), rec)
        client.connect()
        assertTrue(waitUntil { rec.rtts.size >= 2 })
        assertTrue(rec.rtts.all { it in 0..1000 })
        client.close()
    }

    @Test
    fun `the phone notices the host disappearing`() = withAgent { agent ->
        val rec = Recorder()
        val client = MipClient(config(agent), rec)
        client.connect()
        agent.process.destroy()
        assertTrue(waitUntil(8000) { rec.disconnects.isNotEmpty() })
        assertFalse(client.isConnected)
        assertEquals(1, rec.disconnects.size)   // reported exactly once
    }

    @Test
    fun `sending after the session ended is harmless`() = withAgent { agent ->
        val client = MipClient(config(agent), Recorder())
        client.connect()
        client.close()
        client.move(1f, 1f)
        client.key("KeyA", true)
        client.text("x")
        client.close()   // twice is fine
    }
}
